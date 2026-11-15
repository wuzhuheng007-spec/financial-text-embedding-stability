"""Offline orchestration for source-aware observations and due outcomes.

These functions consume *already stored* records. They do not connect to a
provider, call a model, or place an order. A new rule observation requires an
active, authorized live-push session and a freshly published source claim;
saved historical replay cannot be relabeled a contemporaneous prediction.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Callable, Protocol

from .evaluation import (
    HORIZON_MINUTES, MOMENTUM_LOOKBACK_MINUTES, FrozenInputSnapshot,
    FrozenPrediction, MinuteBar, OutcomeResult, RegularSession, SnapshotEvidence,
    bar_from_store_row, digest, evaluate_outcome, freeze_input_snapshot,
    minute_ceil, momentum_baseline, outcome_to_store_record,
    prediction_from_store_row, stored_utc, utc, utc_text,
)
from .events import (
    ClaimRelation, ProvenanceStatus, SourceEvidence, classify_claim,
    source_evidence_from_claim_row,
)
from .learning import propose_reviews, review_to_store_record
from .price_safety import PriceSeriesEvidence, evaluate_with_price_safety
from .store import Store


Clock = Callable[[], datetime]


class FutureModelInterface(Protocol):
    """Reserved interface; this version never invokes it."""

    def predict(self, frozen_input: FrozenInputSnapshot) -> dict: ...


@dataclass(frozen=True)
class ObservationProtocol:
    rule_version: str
    price_basis: str
    calendar_version: str
    max_event_age_seconds: int
    max_heartbeat_age_seconds: int
    horizon_minutes: int = HORIZON_MINUTES
    momentum_lookback_minutes: int = MOMENTUM_LOOKBACK_MINUTES

    def validate(self) -> None:
        if not all((self.rule_version, self.price_basis, self.calendar_version)):
            raise ValueError("rule, price and calendar versions must be frozen")
        if self.horizon_minutes != HORIZON_MINUTES or \
                self.momentum_lookback_minutes != MOMENTUM_LOOKBACK_MINUTES:
            raise ValueError("observer v1 time rules are fixed at 30 and five minutes")
        if self.max_event_age_seconds <= 0 or self.max_heartbeat_age_seconds <= 0:
            raise ValueError("freshness limits must be explicit and positive")

    @property
    def protocol_hash(self) -> str:
        self.validate()
        return digest(asdict(self))


@dataclass(frozen=True)
class EventTrigger:
    event_id: str
    claim_group_id: str
    anchor_claim_id: str
    instrument_id: str
    live_session_id: str
    origin: str  # LIVE_AUTHORIZED or REPLAY
    source_time_precision: str  # MILLISECOND, SECOND, MINUTE, DATE, UNKNOWN
    company_relation_status: str  # VERIFIED or CANDIDATE


@dataclass(frozen=True)
class ObservationReceipt:
    status: str
    prediction_id: str | None
    reason: str
    provenance: str | None
    momentum_direction: str | None
    input_snapshot_hash: str | None


@dataclass(frozen=True)
class OutcomeReceipt:
    status: str
    outcome_id: str
    created: bool
    review_ids: tuple[str, ...]
    reason: str


def _claim_rows(store: Store, *, event_id: str, claim_group_id: str,
                cutoff: datetime) -> list[dict]:
    return [dict(row) for row in store.connection.execute(
        """SELECT claims.*, COALESCE(claims.platform, news.provider) AS observed_platform
           FROM source_claims AS claims
           LEFT JOIN news_items AS news ON news.id=claims.news_id
           WHERE claims.event_id=? AND claims.claim_group_id=?
           ORDER BY claims.claim_id""",
        (event_id, claim_group_id),
    ) if stored_utc(row["persisted_at"]) <= utc(cutoff)]


def _stored_bars(store: Store, instrument_id: str, session_id: str) -> list[MinuteBar]:
    bars: list[MinuteBar] = []
    for row in store.connection.execute(
        "SELECT * FROM quotes_or_bars WHERE instrument_id=? AND kind='BAR_1M' ORDER BY id",
        (instrument_id,),
    ):
        payload = json.loads(row["payload_json"])
        if payload.get("session_id") == session_id:
            bars.append(bar_from_store_row(row))
    return bars


def _source_snapshot_input(row: dict) -> SnapshotEvidence:
    return SnapshotEvidence(
        record_id=str(row["claim_id"]), record_kind="SOURCE_CLAIM",
        content_hash=str(row["evidence_hash"]),
        received_at=stored_utc(row["received_at"]),
        persisted_at=stored_utc(row["persisted_at"]),
        provider_time=None if row["provider_time"] is None
        else stored_utc(row["provider_time"]),
        source_timezone=str(row["source_timezone"]),
        version=1,
    )


def _bar_snapshot_inputs(store: Store, bar_ids: tuple[str | int, ...]) -> list[SnapshotEvidence]:
    inputs: list[SnapshotEvidence] = []
    for bar_id in bar_ids:
        row = store.connection.execute("SELECT * FROM quotes_or_bars WHERE id=?", (bar_id,)).fetchone()
        if row is None:
            raise ValueError("pre-cutoff baseline bar is missing from private store")
        inputs.append(SnapshotEvidence(
            record_id=str(row["id"]), record_kind="BAR_1M",
            content_hash=str(row["input_hash"]),
            received_at=stored_utc(row["received_at"]),
            persisted_at=stored_utc(row["persisted_at"]),
            provider_time=stored_utc(row["provider_time"]),
            source_timezone=str(row["source_timezone"]), version=int(row["version"]),
        ))
    return inputs


def record_rule_observation(store: Store, *, trigger: EventTrigger,
                            protocol: ObservationProtocol, session: RegularSession,
                            clock: Clock = lambda: datetime.now(timezone.utc)) -> ObservationReceipt:
    """Write at most one root rule judgment for one fresh event/instrument.

    The rule is a fixed five-minute *price-momentum comparator*. It does not
    infer a news effect. It abstains when provenance, company relation or quote
    freshness is inadequate. No historical replay is accepted.
    """

    protocol.validate()
    session.validate()
    if protocol.calendar_version != session.calendar_version:
        raise ValueError("live session calendar differs from frozen protocol")
    if trigger.origin != "LIVE_AUTHORIZED":
        return ObservationReceipt("SKIPPED_REPLAY", None, "Historical replay is not a live event",
                                  None, None, None)
    if trigger.source_time_precision not in {"MILLISECOND", "SECOND", "MINUTE"}:
        return ObservationReceipt("SKIPPED_TIME_PRECISION", None,
                                  "Source publication time is not precise enough for live eligibility",
                                  None, None, None)
    if trigger.company_relation_status not in {"VERIFIED", "CANDIDATE"}:
        raise ValueError("company relation must be explicitly verified or candidate")
    now = utc(clock())
    live = store.connection.execute("SELECT * FROM sessions WHERE session_id=?",
                                    (trigger.live_session_id,)).fetchone()
    if live is None or live["mode"] != "LIVE_PUSH" or live["ended_at"] is not None or \
            live["status"] not in {"CONNECTED_WAITING_FOR_DATA", "CONNECTED_DATA_RECEIVED"}:
        return ObservationReceipt("SKIPPED_NO_LIVE_SESSION", None,
                                  "No active authorized push session", None, None, None)
    started = stored_utc(live["started_at"])
    heartbeat = stored_utc(live["heartbeat_at"])
    if heartbeat > now or now - heartbeat > timedelta(seconds=protocol.max_heartbeat_age_seconds):
        return ObservationReceipt("SKIPPED_STALE_SESSION", None,
                                  "Live session heartbeat is stale", None, None, None)
    existing = store.connection.execute(
        """SELECT prediction_id FROM predictions WHERE event_id=? AND instrument_id=?
           AND rule_version=? AND parent_prediction_id IS NULL LIMIT 1""",
        (trigger.event_id, trigger.instrument_id, protocol.rule_version),
    ).fetchone()
    if existing is not None:
        return ObservationReceipt("DUPLICATE", str(existing["prediction_id"]),
                                  "Root prediction already exists for this event/instrument/rule",
                                  None, None, None)
    instrument = store.connection.execute(
        "SELECT identity_status FROM instruments WHERE instrument_id=?",
        (trigger.instrument_id,),
    ).fetchone()
    if instrument is None or instrument["identity_status"] != "VERIFIED":
        return ObservationReceipt("SKIPPED_UNVERIFIED_INSTRUMENT", None,
                                  "Provider instrument identity is not verified",
                                  None, None, None)
    event = store.connection.execute("SELECT event_id FROM event_clusters WHERE event_id=?",
                                     (trigger.event_id,)).fetchone()
    if event is None:
        return ObservationReceipt("SKIPPED_UNKNOWN_EVENT", None,
                                  "Event cluster is not persisted", None, None, None)
    anchor = store.connection.execute(
        """SELECT * FROM source_claims WHERE claim_id=? AND claim_group_id=?
           AND event_id=?""",
        (trigger.anchor_claim_id, trigger.claim_group_id, trigger.event_id),
    ).fetchone()
    if anchor is None or stored_utc(anchor["persisted_at"]) > now or \
            anchor["provider_time"] is None:
        return ObservationReceipt("SKIPPED_SOURCE_TIME_UNKNOWN", None,
                                  "Anchor source publication time is unavailable", None, None, None)
    if anchor["time_precision"] != trigger.source_time_precision:
        return ObservationReceipt("SKIPPED_TIME_PRECISION", None,
                                  "Trigger precision does not match stored source evidence",
                                  None, None, None)
    published = stored_utc(anchor["provider_time"])
    first_received = stored_utc(anchor["received_at"])
    if published > now or first_received > now or first_received < started or \
            now - published > timedelta(seconds=protocol.max_event_age_seconds):
        return ObservationReceipt("SKIPPED_STALE_EVENT", None,
                                  "Source was not a fresh event in this live session",
                                  None, None, None)
    if utc(now) >= utc(session.closes_at) or \
            max(utc(session.opens_at), minute_ceil(now)) + \
            timedelta(minutes=HORIZON_MINUTES + 2) > utc(session.closes_at):
        return ObservationReceipt("SKIPPED_NO_REGULAR_WINDOW", None,
                                  "No conservative full regular-session window remains",
                                  None, None, None)
    rows = _claim_rows(store, event_id=trigger.event_id,
                       claim_group_id=trigger.claim_group_id, cutoff=now)
    if not rows:
        return ObservationReceipt("SKIPPED_NO_CLAIM", None,
                                  "No source claims were available at cutoff", None, None, None)
    evidence: list[SourceEvidence] = [
        source_evidence_from_claim_row(row, platform=row["observed_platform"] or "source_record")
        for row in rows
    ]
    provenance = classify_claim(trigger.claim_group_id, evidence).status
    observed = _stored_bars(store, trigger.instrument_id, session.session_id)
    baseline = momentum_baseline(
        observed, instrument_id=trigger.instrument_id, session=session,
        information_cutoff=now, price_basis=protocol.price_basis,
    )
    used_baseline = [bar for bar in observed if bar.bar_id in baseline.bar_ids]
    delayed_baseline = any(bar.delay_status != "REALTIME" for bar in used_baseline)
    inputs = [_source_snapshot_input(row) for row in rows]
    inputs.extend(_bar_snapshot_inputs(store, baseline.bar_ids))
    frozen = freeze_input_snapshot(inputs, information_cutoff=now,
                                   event_id=trigger.event_id,
                                   instrument_id=trigger.instrument_id,
                                   rule_version=protocol.rule_version)
    source_sufficient = provenance in {
        ProvenanceStatus.PRIMARY_CONFIRMED,
        ProvenanceStatus.INDEPENDENTLY_CORROBORATED,
    }
    direction = baseline.direction if source_sufficient and \
        trigger.company_relation_status == "VERIFIED" and not delayed_baseline and \
        baseline.direction in {"UP", "DOWN"} else "ABSTAIN"
    quality = "SOURCE_UNVERIFIED" if not source_sufficient else \
        "CANDIDATE_COMPANY_RELATION" if trigger.company_relation_status != "VERIFIED" else \
        "BASELINE_UNAVAILABLE" if baseline.direction == "UNAVAILABLE" else \
        "DELAYED_BASELINE" if delayed_baseline else "OBSERVABLE"
    created_at = utc(clock())
    if created_at < now:
        raise ValueError("clock moved backwards while freezing a prediction")
    prediction_id = digest((trigger.event_id, trigger.instrument_id,
                            protocol.rule_version, frozen.input_hash))
    store.insert_prediction({
        "prediction_id": prediction_id,
        "event_id": trigger.event_id,
        "instrument_id": trigger.instrument_id,
        "created_at": utc_text(created_at),
        "information_cutoff": utc_text(now),
        "input_snapshot_hash": frozen.input_hash,
        "input_manifest_json": frozen.manifest_json,
        "model_version": None,
        "prompt_version": None,
        "rule_version": protocol.rule_version,
        "evaluation_protocol_hash": protocol.protocol_hash,
        "price_basis": protocol.price_basis,
        "calendar_version": protocol.calendar_version,
        "horizon_minutes": HORIZON_MINUTES,
        "direction": direction,
        "rationale": "Fixed pre-cutoff five-minute price-momentum comparator; not a news effect or trade signal",
        "transmission_json": [],
        "counter_scenario": "The subsequent market move may differ; source/company relation may be revised",
        "data_quality_status": quality,
    })
    return ObservationReceipt("RECORDED", prediction_id,
                              "Immutable rule observation stored before outcome window",
                              provenance.value, baseline.direction, frozen.input_hash)


def evaluate_due_prediction(store: Store, *, prediction_id: str,
                            session: RegularSession, as_of: datetime,
                            qqq_instrument_id: str,
                            instrument_series: PriceSeriesEvidence | None = None,
                            qqq_series: PriceSeriesEvidence | None = None) -> OutcomeReceipt:
    """Persist a due outcome only when action coverage supports a mature label.

    The optional series evidence keeps historical pending records readable.
    Without *both* inventories, a mature price label is withheld. A live caller
    must provide dated, reviewed inventories for the exact two instruments.
    """

    row = store.get_prediction(prediction_id)
    if row is None:
        raise KeyError(prediction_id)
    prediction: FrozenPrediction = prediction_from_store_row(row)
    observed = _stored_bars(store, prediction.instrument_id, session.session_id)
    benchmark = _stored_bars(store, qqq_instrument_id, session.session_id)
    if (instrument_series is None) != (qqq_series is None):
        raise ValueError("both instrument and QQQ price-series inventories are required")
    if instrument_series is None:
        candidate = evaluate_outcome(prediction, session=session,
                                     instrument_bars=observed, qqq_bars=benchmark,
                                     as_of=as_of, price_basis=prediction.price_basis,
                                     qqq_instrument_id=qqq_instrument_id)
        outcome = candidate if candidate.status != "MATURED" else replace(
            candidate, status="ACTION_COVERAGE_PENDING",
            reason="Dated corporate-action inventories for both price series are missing",
            start_bar_id=None, end_bar_id=None, return_value=None,
            qqq_return=None, excess_return=None, direction_hit=None,
            baseline_hit=None, data_quality_status="UNKNOWN",
            input_hash=None, input_manifest_json=None)
    else:
        guarded = evaluate_with_price_safety(
            prediction, session=session, instrument_bars=observed,
            qqq_bars=benchmark, as_of=as_of, price_basis=prediction.price_basis,
            instrument_series=instrument_series, qqq_series=qqq_series,
            qqq_instrument_id=qqq_instrument_id)
        if guarded.outcome is None:
            gate_manifest = {
                "prediction_id": prediction.prediction_id,
                "gate_status": guarded.status,
                "instrument_source_hash": instrument_series.source_hash,
                "qqq_source_hash": qqq_series.source_hash,
                "instrument_checked_at": utc_text(instrument_series.checked_at),
                "qqq_checked_at": utc_text(qqq_series.checked_at),
            }
            outcome = OutcomeResult(
                prediction.prediction_id, guarded.status, guarded.reason,
                None, None, None, None, None, None, None, None,
                "UNAVAILABLE", None, "UNKNOWN", digest(gate_manifest),
                json.dumps(gate_manifest, sort_keys=True, separators=(",", ":")))
        else:
            outcome = guarded.outcome
    record = outcome_to_store_record(outcome)
    prior = store.connection.execute("SELECT outcome_id FROM outcomes WHERE outcome_id=?",
                                     (record["outcome_id"],)).fetchone()
    created = prior is None
    if created:
        store.insert_outcome(record)
    groups = [r["claim_group_id"] for r in store.connection.execute(
        "SELECT DISTINCT claim_group_id FROM source_claims WHERE event_id=? ORDER BY claim_group_id",
        (prediction.event_id,),
    )]
    statuses: list[ProvenanceStatus] = []
    for group in groups:
        rows = _claim_rows(store, event_id=prediction.event_id,
                           claim_group_id=group, cutoff=prediction.information_cutoff)
        evidence = [source_evidence_from_claim_row(item,
                    platform=item["observed_platform"] or "source_record") for item in rows]
        statuses.append(classify_claim(group, evidence).status)
    provenance = next((status for status in (
        ProvenanceStatus.CONFLICTING, ProvenanceStatus.PRIMARY_CONFIRMED,
        ProvenanceStatus.INDEPENDENTLY_CORROBORATED,
        ProvenanceStatus.SAME_ORIGIN_REPRINT, ProvenanceStatus.SINGLE_SOURCE,
    ) if status in statuses), ProvenanceStatus.UNVERIFIED)
    reviews = propose_reviews(event_id=prediction.event_id,
                              prediction_id=prediction.prediction_id,
                              provenance=provenance, outcome=outcome,
                              evidence_refs=(outcome.input_hash,) if outcome.input_hash else ())
    new_reviews: list[str] = []
    for review in reviews:
        existing = store.connection.execute("SELECT review_id FROM review_queue WHERE review_id=?",
                                            (review.review_id,)).fetchone()
        if existing is None:
            store.enqueue_review(review_to_store_record(review, created_at=as_of))
            new_reviews.append(review.review_id)
    return OutcomeReceipt(outcome.status, str(record["outcome_id"]), created,
                          tuple(new_reviews), outcome.reason)
