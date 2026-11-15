"""Conservative, source-aware event provenance for the market observer.

This module does not fetch news or infer factual agreement from price moves.
Callers must explicitly adjudicate whether a source supports or contradicts a
specific claim. A ticker or alias match remains a candidate association.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping
from urllib.parse import urlparse


class ProvenanceStatus(str, Enum):
    PRIMARY_CONFIRMED = "PRIMARY_CONFIRMED"
    INDEPENDENTLY_CORROBORATED = "INDEPENDENTLY_CORROBORATED"
    SAME_ORIGIN_REPRINT = "SAME_ORIGIN_REPRINT"
    SINGLE_SOURCE = "SINGLE_SOURCE"
    CONFLICTING = "CONFLICTING"
    UNVERIFIED = "UNVERIFIED"


class ClaimRelation(str, Enum):
    SUPPORTS = "SUPPORTS"
    CONTRADICTS = "CONTRADICTS"
    UNREVIEWED = "UNREVIEWED"


@dataclass(frozen=True)
class SourceEvidence:
    """One observed item about one claim, with its original origin preserved."""

    item_id: str
    claim_id: str
    platform: str
    origin_id: str | None
    origin_kind: str  # OFFICIAL_PRIMARY, INDEPENDENT_REPORTING, REPRINT, UNKNOWN
    relation: ClaimRelation
    original_url: str | None
    source_published_at: datetime | None
    first_received_at: datetime
    revised_at: datetime | None = None
    primary_document_verified: bool = False

    def validate(self) -> None:
        if not self.item_id or not self.claim_id or not self.platform:
            raise ValueError("item, claim and platform identifiers are required")
        if self.origin_kind not in {
            "OFFICIAL_PRIMARY", "INDEPENDENT_REPORTING", "REPRINT", "UNKNOWN"
        }:
            raise ValueError("unknown origin kind")
        if not isinstance(self.relation, ClaimRelation):
            raise ValueError("claim relation requires explicit adjudication")
        _utc(self.first_received_at)
        if self.source_published_at is not None:
            _utc(self.source_published_at)
        if self.revised_at is not None:
            _utc(self.revised_at)
        if self.original_url is not None:
            parts = urlparse(self.original_url)
            if parts.scheme not in {"http", "https"} or not parts.netloc:
                raise ValueError("original URL must be an absolute HTTP(S) URL")
        if self.primary_document_verified and self.origin_kind != "OFFICIAL_PRIMARY":
            raise ValueError("only a verified official primary document may confirm a claim")


@dataclass(frozen=True)
class ProvenanceDecision:
    claim_id: str
    status: ProvenanceStatus
    supporting_item_ids: tuple[str, ...]
    contradicting_item_ids: tuple[str, ...]
    distinct_supporting_origins: int
    reason: str


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must include a timezone")
    return value.astimezone(timezone.utc)


def _stored_utc(value: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError("stored source timestamp must explicitly end in Z")
    return _utc(datetime.fromisoformat(value[:-1] + "+00:00"))


def source_evidence_from_claim_row(row: Mapping[str, Any], *, platform: str) -> SourceEvidence:
    """Read one evidence row; the store's claim_group_id is the logical claim."""

    item = SourceEvidence(
        item_id=str(row["claim_id"]),
        claim_id=str(row["claim_group_id"]),
        platform=platform,
        origin_id=row["origin_id"],
        origin_kind=row["source_type"] or "UNKNOWN",
        relation=ClaimRelation(row["relation"]),
        original_url=row["source_url"],
        source_published_at=None if row["provider_time"] is None
        else _stored_utc(row["provider_time"]),
        first_received_at=_stored_utc(row["received_at"]),
        primary_document_verified=bool(row["primary_document_verified"]),
    )
    item.validate()
    return item


def classify_claim(claim_id: str, evidence: list[SourceEvidence]) -> ProvenanceDecision:
    """Classify a *reviewed* claim; multiple platforms sharing one origin count once.

    Independent corroboration is permitted only when origin identities and
    relations have been reviewed. An unreviewed item cannot confirm a claim.
    """

    if not claim_id:
        raise ValueError("claim_id is required")
    selected = [item for item in evidence if item.claim_id == claim_id]
    for item in selected:
        item.validate()
    if len({item.item_id for item in selected}) != len(selected):
        raise ValueError("duplicate evidence item IDs")
    supporting = [item for item in selected if item.relation is ClaimRelation.SUPPORTS]
    contradicting = [item for item in selected if item.relation is ClaimRelation.CONTRADICTS]
    verified_origins = {
        item.origin_id for item in supporting
        if item.origin_id and item.origin_kind in {"OFFICIAL_PRIMARY", "INDEPENDENT_REPORTING"}
    }
    base = dict(
        claim_id=claim_id,
        supporting_item_ids=tuple(item.item_id for item in supporting),
        contradicting_item_ids=tuple(item.item_id for item in contradicting),
        distinct_supporting_origins=len(verified_origins),
    )
    if supporting and contradicting:
        return ProvenanceDecision(**base, status=ProvenanceStatus.CONFLICTING,
                                  reason="Reviewed sources explicitly conflict about this claim")
    if any(item.primary_document_verified for item in supporting):
        return ProvenanceDecision(**base, status=ProvenanceStatus.PRIMARY_CONFIRMED,
                                  reason="Verified official primary document directly supports the claim")
    if len(verified_origins) >= 2:
        return ProvenanceDecision(**base, status=ProvenanceStatus.INDEPENDENTLY_CORROBORATED,
                                  reason="At least two reviewed, distinct reporting origins support the claim")
    if len(supporting) >= 2 and len({item.origin_id for item in supporting}) == 1 \
            and supporting[0].origin_id is not None:
        return ProvenanceDecision(**base, status=ProvenanceStatus.SAME_ORIGIN_REPRINT,
                                  reason="Multiple items trace to the same identified origin")
    if len(supporting) == 1 or (supporting and len(verified_origins) == 1):
        return ProvenanceDecision(**base, status=ProvenanceStatus.SINGLE_SOURCE,
                                  reason="One reviewed origin supports the claim")
    return ProvenanceDecision(**base, status=ProvenanceStatus.UNVERIFIED,
                              reason="No reviewed source currently supports the claim")
