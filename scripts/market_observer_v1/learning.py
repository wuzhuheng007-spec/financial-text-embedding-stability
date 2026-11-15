"""Review queue and offline-learning eligibility without automatic training.

Extraction correctness and subsequent market movement are independent labels.
This module creates review candidates and versioned dataset manifests; it
never changes a live model or turns a model's own draft into human ground truth.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

from .evaluation import OutcomeResult, digest, utc, utc_text
from .events import ProvenanceStatus


REVIEW_CATEGORIES = frozenset({
    "SOURCE_CONFLICT", "FIELD_MISMATCH", "INSUFFICIENT_QUOTE", "MISSING_FACT",
    "PRICE_ANOMALY", "PREDICTION_MISS", "DATA_GAP", "LABEL_PENDING",
})


@dataclass(frozen=True)
class ReviewItem:
    review_id: str
    event_id: str
    prediction_id: str | None
    category: str
    status: str
    evidence_refs: tuple[str, ...]
    model_prediction: str | None
    objective_outcome: str | None
    human_ruling: None


def propose_reviews(*, event_id: str, prediction_id: str | None,
                    provenance: ProvenanceStatus,
                    extraction_flags: Iterable[str] = (),
                    quality_flags: Iterable[str] = (),
                    outcome: OutcomeResult | None = None,
                    evidence_refs: Iterable[str] = ()) -> tuple[ReviewItem, ...]:
    """Create deterministic open queue items; never fabricate a human ruling."""

    if not event_id:
        raise ValueError("event_id is required")
    flags = set(extraction_flags)
    if flags - {"FIELD_MISMATCH", "INSUFFICIENT_QUOTE", "MISSING_FACT"}:
        raise ValueError("unknown extraction flag")
    quality = set(quality_flags)
    if quality - {"PRICE_ANOMALY", "DATA_GAP", "LABEL_PENDING"}:
        raise ValueError("unknown market data quality flag")
    categories = flags | quality
    if provenance is ProvenanceStatus.CONFLICTING:
        categories.add("SOURCE_CONFLICT")
    if outcome is not None:
        if outcome.status in {"DATA_GAP", "PRICE_BASIS_MISMATCH",
                              "ACTION_ADJUSTMENT_PENDING", "ACTION_COVERAGE_PENDING",
                              "FX_CONVERSION_PENDING", "UNSUPPORTED_MARKET_PROTOCOL"}:
            categories.add("DATA_GAP")
        elif outcome.status in {"PENDING", "CROSSES_CLOSE", "NO_REGULAR_WINDOW"}:
            categories.add("LABEL_PENDING")
        if outcome.status == "MATURED" and outcome.direction_hit is False:
            categories.add("PREDICTION_MISS")
    refs = tuple(sorted(set(evidence_refs)))
    return tuple(
        ReviewItem(
            review_id=digest((event_id, prediction_id, category, refs)),
            event_id=event_id,
            prediction_id=prediction_id,
            category=category,
            status="OPEN",
            evidence_refs=refs,
            model_prediction=None if outcome is None else outcome.prediction_id,
            objective_outcome=None if outcome is None or outcome.input_hash is None
            else outcome.input_hash,
            human_ruling=None,
        )
        for category in sorted(categories)
    )


def review_to_store_record(item: ReviewItem, *, created_at: datetime) -> dict[str, object]:
    """Preserve model, objective outcome and human adjudication as distinct fields."""

    if item.category not in REVIEW_CATEGORIES or item.status != "OPEN":
        raise ValueError("only a valid new review item can be inserted")
    return {
        "review_id": item.review_id,
        "event_id": item.event_id,
        "prediction_id": item.prediction_id,
        "category": item.category,
        "status": item.status,
        "created_at": utc_text(created_at),
        "evidence_json": {"refs": list(item.evidence_refs)},
        "model_prediction_json": None if item.model_prediction is None
        else {"prediction_id": item.model_prediction},
        "objective_outcome_json": None if item.objective_outcome is None
        else {"outcome_input_hash": item.objective_outcome},
        "human_ruling_json": None,
    }


@dataclass(frozen=True)
class TrainingCandidate:
    example_id: str
    event_group_id: str
    input_hash: str
    input_available_at: datetime
    label_available_at: datetime | None
    source_rights: str  # TRAINING_ALLOWED, UNKNOWN, PROHIBITED
    source_rights_record: str | None
    label_kind: str  # EXTRACTION or MARKET
    label_source: str  # HUMAN_ADJUDICATION or MARKET_OBSERVATION
    label_version: str | None
    review_status: str  # ACCEPTED, PENDING, REJECTED
    development_reused: bool
    point_in_time_verified: bool
    label_value: str | None

    def validate(self) -> None:
        if not all((self.example_id, self.event_group_id, self.input_hash)):
            raise ValueError("example, event group and input hash are required")
        if self.source_rights not in {"TRAINING_ALLOWED", "UNKNOWN", "PROHIBITED"}:
            raise ValueError("source rights must be explicit")
        if self.label_kind not in {"EXTRACTION", "MARKET"}:
            raise ValueError("extraction and market labels require separate kinds")
        if self.review_status not in {"ACCEPTED", "PENDING", "REJECTED"}:
            raise ValueError("review status must be explicit")
        if self.label_kind == "EXTRACTION" and self.label_source != "HUMAN_ADJUDICATION":
            raise ValueError("model self-review is not a human extraction label")
        if self.label_kind == "MARKET" and self.label_source != "MARKET_OBSERVATION":
            raise ValueError("market label must come from an observed outcome")
        if self.label_available_at is not None and \
                utc(self.label_available_at) < utc(self.input_available_at):
            raise ValueError("label availability cannot precede input availability")
        utc(self.input_available_at)


@dataclass(frozen=True)
class EligibilityDecision:
    example_id: str
    eligible: bool
    reasons: tuple[str, ...]


def training_eligibility(candidate: TrainingCandidate, *, freeze_at: datetime) -> EligibilityDecision:
    """Fail closed on rights, time, independent review and reused dev samples."""

    candidate.validate()
    frozen = utc(freeze_at)
    reasons: list[str] = []
    if candidate.source_rights != "TRAINING_ALLOWED" or not candidate.source_rights_record:
        reasons.append("SOURCE_TRAINING_RIGHTS_UNCONFIRMED")
    if candidate.label_available_at is None or utc(candidate.label_available_at) > frozen:
        reasons.append("LABEL_NOT_AVAILABLE_AT_FREEZE")
    if utc(candidate.input_available_at) > frozen:
        reasons.append("INPUT_NOT_AVAILABLE_AT_FREEZE")
    if candidate.review_status != "ACCEPTED":
        reasons.append("REVIEW_NOT_ACCEPTED")
    if not candidate.point_in_time_verified:
        reasons.append("POINT_IN_TIME_UNVERIFIED")
    if candidate.label_value is None or candidate.label_version is None:
        reasons.append("LABEL_MISSING_OR_UNVERSIONED")
    if candidate.development_reused:
        reasons.append("REUSED_DEVELOPMENT_SAMPLE")
    return EligibilityDecision(candidate.example_id, not reasons, tuple(reasons))


@dataclass(frozen=True)
class ManifestRow:
    example_id: str
    event_group_id: str
    label_kind: str
    split: str  # TRAIN, VALIDATION, FUTURE_TEST, or EXCLUDED
    eligible: bool
    exclusion_reasons: tuple[str, ...]
    input_hash: str
    label_version: str | None
    input_available_at: str
    label_available_at: str | None


def build_temporal_manifest(candidates: Iterable[TrainingCandidate], *,
                            train_end: datetime, validation_end: datetime,
                            freeze_at: datetime) -> tuple[ManifestRow, ...]:
    """Group an event and all its reprints in one chronological split.

    The latest input availability in a group chooses its split. This may move
    an early item into a later split, but never lets a later reprint leak back
    into training. A row unavailable at the freeze is excluded entirely.
    """

    train_cut = utc(train_end)
    validation_cut = utc(validation_end)
    frozen = utc(freeze_at)
    if not train_cut < validation_cut <= frozen:
        raise ValueError("chronological cutoffs must satisfy train < validation <= freeze")
    rows = tuple(candidates)
    ids = [row.example_id for row in rows]
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate example IDs")
    for row in rows:
        row.validate()
    group_latest: dict[str, datetime] = {}
    for row in rows:
        observed = utc(row.input_available_at)
        group_latest[row.event_group_id] = max(group_latest.get(row.event_group_id, observed), observed)
    output: list[ManifestRow] = []
    for row in rows:
        decision = training_eligibility(row, freeze_at=frozen)
        group_time = group_latest[row.event_group_id]
        split = "EXCLUDED" if not decision.eligible else \
            "TRAIN" if group_time <= train_cut else \
            "VALIDATION" if group_time <= validation_cut else "FUTURE_TEST"
        output.append(ManifestRow(
            example_id=row.example_id,
            event_group_id=row.event_group_id,
            label_kind=row.label_kind,
            split=split,
            eligible=decision.eligible,
            exclusion_reasons=decision.reasons,
            input_hash=row.input_hash,
            label_version=row.label_version,
            input_available_at=utc_text(row.input_available_at),
            label_available_at=None if row.label_available_at is None else utc_text(row.label_available_at),
        ))
    return tuple(output)


@dataclass(frozen=True)
class ModelVersionGate:
    version_id: str
    rollback_to: str | None
    offline_out_of_time_evaluation: bool
    event_disjoint_test: bool
    human_approval: bool
    metric_gate_passed: bool


def promotion_readiness(gate: ModelVersionGate) -> tuple[bool, tuple[str, ...]]:
    """Report readiness only. Promotion and rollback require explicit actions."""

    reasons: list[str] = []
    if not gate.version_id:
        reasons.append("CANDIDATE_VERSION_MISSING")
    if not gate.rollback_to or gate.rollback_to == gate.version_id:
        reasons.append("ROLLBACK_TARGET_MISSING")
    if not gate.offline_out_of_time_evaluation:
        reasons.append("OUT_OF_TIME_EVALUATION_MISSING")
    if not gate.event_disjoint_test:
        reasons.append("EVENT_DISJOINT_TEST_MISSING")
    if not gate.metric_gate_passed:
        reasons.append("METRIC_GATE_NOT_PASSED")
    if not gate.human_approval:
        reasons.append("HUMAN_APPROVAL_MISSING")
    return not reasons, tuple(reasons)
