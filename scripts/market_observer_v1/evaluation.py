"""Point-in-time prediction and 30-minute regular-session outcome contract.

The first complete bar begins at the first minute boundary on or after the
prediction's recorded durable-write time. Its close is the entry price. The
close of the 30th *following* complete regular-session bar is the exit price.
All 31 bars, including the same 31 QQQ bars, must be present in one documented
session. A missing or late bar is a data gap, never a zero return.

This is descriptive observation, not a causal alpha or trading simulator.
The module performs no network, model or brokerage operation.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping


MINUTE = timedelta(minutes=1)
HORIZON_MINUTES = 30
MOMENTUM_LOOKBACK_MINUTES = 5
DIRECTIONS = frozenset({"UP", "DOWN", "ABSTAIN"})


def utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must have an explicit timezone")
    return value.astimezone(timezone.utc)


def utc_text(value: datetime) -> str:
    return utc(value).isoformat().replace("+00:00", "Z")


def minute_floor(value: datetime) -> datetime:
    value = utc(value)
    return value.replace(second=0, microsecond=0)


def minute_ceil(value: datetime) -> datetime:
    lower = minute_floor(value)
    return lower if utc(value) == lower else lower + MINUTE


def digest(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False, allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def stored_utc(value: str) -> datetime:
    """Read the private store's explicit-Z timestamps without local-time guesses."""

    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError("stored timestamp must explicitly end in Z")
    return utc(datetime.fromisoformat(value[:-1] + "+00:00"))


@dataclass(frozen=True)
class FrozenPrediction:
    prediction_id: str
    event_id: str
    instrument_id: str
    created_at: datetime
    persisted_at: datetime
    information_cutoff: datetime
    input_snapshot_hash: str
    model_version: str | None
    prompt_version: str | None
    rule_version: str
    evaluation_protocol_hash: str
    price_basis: str
    calendar_version: str
    direction: str
    horizon_minutes: int = HORIZON_MINUTES
    parent_prediction_id: str | None = None
    data_quality_status: str = "UNKNOWN"

    def validate(self) -> None:
        if not all((self.prediction_id, self.event_id, self.instrument_id,
                    self.rule_version, self.price_basis, self.calendar_version)):
            raise ValueError("prediction identity and frozen versions are required")
        if not _is_sha256(self.input_snapshot_hash):
            raise ValueError("input snapshot requires a SHA-256 hex digest")
        if not _is_sha256(self.evaluation_protocol_hash):
            raise ValueError("evaluation protocol requires a SHA-256 hex digest")
        if self.direction not in DIRECTIONS:
            raise ValueError("direction must be UP, DOWN or ABSTAIN")
        if self.horizon_minutes != HORIZON_MINUTES:
            raise ValueError("observer v1 has a fixed 30 regular-minute horizon")
        if not (utc(self.information_cutoff) <= utc(self.created_at) <= utc(self.persisted_at)):
            raise ValueError("information cutoff, creation and durable write are out of order")
        if self.parent_prediction_id == self.prediction_id:
            raise ValueError("a revision must use a new prediction ID")


@dataclass(frozen=True)
class SnapshotEvidence:
    """A locally available, versioned input; publication time is separate."""

    record_id: str
    record_kind: str
    content_hash: str
    received_at: datetime
    persisted_at: datetime
    provider_time: datetime | None
    source_timezone: str
    version: int

    def validate(self) -> None:
        if not self.record_id or not self.record_kind or not self.source_timezone:
            raise ValueError("snapshot record identity and source timezone are required")
        if not _is_sha256(self.content_hash):
            raise ValueError("snapshot input requires a SHA-256 content hash")
        if self.version < 1:
            raise ValueError("snapshot input version must be positive")
        if utc(self.received_at) > utc(self.persisted_at):
            raise ValueError("input cannot be persisted before local receipt")
        if self.provider_time is not None:
            utc(self.provider_time)


@dataclass(frozen=True)
class FrozenInputSnapshot:
    input_hash: str
    manifest_json: str
    evidence_count: int


def freeze_input_snapshot(evidence: Iterable[SnapshotEvidence], *,
                          information_cutoff: datetime, event_id: str,
                          instrument_id: str, rule_version: str) -> FrozenInputSnapshot:
    """Freeze only versions durably stored by the prediction's information cutoff.

    A provider's publication timestamp alone cannot establish when this local
    observer could use an item. Future calendar events may be known in advance,
    so provider_time is retained without treating it as local availability.
    """

    cutoff = utc(information_cutoff)
    if not all((event_id, instrument_id, rule_version)):
        raise ValueError("event, instrument and rule version are required")
    items = tuple(evidence)
    for item in items:
        item.validate()
        if utc(item.persisted_at) > cutoff:
            raise ValueError("snapshot contains an input unavailable at information cutoff")
    identities = [(item.record_kind, item.record_id) for item in items]
    if len(set(identities)) != len(items):
        raise ValueError("duplicate snapshot record identity")
    manifest = {
        "event_id": event_id,
        "instrument_id": instrument_id,
        "rule_version": rule_version,
        "information_cutoff": utc_text(cutoff),
        "records": [
            {
                "record_id": item.record_id,
                "record_kind": item.record_kind,
                "content_hash": item.content_hash,
                "received_at": utc_text(item.received_at),
                "persisted_at": utc_text(item.persisted_at),
                "provider_time": None if item.provider_time is None else utc_text(item.provider_time),
                "source_timezone": item.source_timezone,
                "version": item.version,
            }
            for item in sorted(items, key=lambda value: (value.record_kind, value.record_id))
        ],
    }
    manifest_json = json.dumps(manifest, sort_keys=True, separators=(",", ":"),
                               ensure_ascii=False, allow_nan=False)
    return FrozenInputSnapshot(hashlib.sha256(manifest_json.encode("utf-8")).hexdigest(),
                               manifest_json, len(items))


@dataclass(frozen=True)
class RegularSession:
    session_id: str
    market: str
    opens_at: datetime
    closes_at: datetime
    calendar_source: str
    calendar_version: str

    def validate(self) -> None:
        if not all((self.session_id, self.market, self.calendar_source,
                    self.calendar_version)):
            raise ValueError("documented market-session identity is required")
        if utc(self.opens_at) >= utc(self.closes_at):
            raise ValueError("session close must follow open")
        if minute_floor(self.opens_at) != utc(self.opens_at) or \
                minute_floor(self.closes_at) != utc(self.closes_at):
            raise ValueError("session boundaries must align to whole minutes")


@dataclass(frozen=True)
class MinuteBar:
    bar_id: str | int
    instrument_id: str
    start_at: datetime
    end_at: datetime
    open: float
    close: float
    session_id: str
    market_session: str
    price_basis: str
    received_at: datetime
    persisted_at: datetime
    delay_status: str
    version: int = 1

    def validate(self) -> None:
        if not all((self.bar_id, self.instrument_id, self.session_id, self.price_basis)):
            raise ValueError("bar identity, session and price basis are required")
        if self.market_session != "REGULAR":
            raise ValueError("outcome requires regular-session bars")
        if minute_floor(self.start_at) != utc(self.start_at) or \
                utc(self.end_at) - utc(self.start_at) != MINUTE:
            raise ValueError("bar must cover exactly one aligned minute")
        if not (utc(self.end_at) <= utc(self.received_at) <= utc(self.persisted_at)):
            raise ValueError("a complete bar cannot be received before its end")
        if not all(math.isfinite(v) and v > 0 for v in (self.open, self.close)):
            raise ValueError("bar prices must be finite and positive")
        if self.version < 1:
            raise ValueError("bar version must be positive")


@dataclass(frozen=True)
class MomentumBaseline:
    direction: str  # UP, DOWN, ABSTAIN, or UNAVAILABLE
    bar_ids: tuple[str, ...]
    reason: str


@dataclass(frozen=True)
class OutcomeResult:
    prediction_id: str
    status: str
    reason: str
    start_at: datetime | None
    end_at: datetime | None
    start_bar_id: str | int | None
    end_bar_id: str | int | None
    return_value: float | None
    qqq_return: float | None
    excess_return: float | None
    direction_hit: bool | None
    baseline_direction: str
    baseline_hit: bool | None
    data_quality_status: str
    input_hash: str | None
    input_manifest_json: str | None


def prediction_from_store_row(row: Mapping[str, Any]) -> FrozenPrediction:
    """Adapt an immutable Store prediction row to the evaluation contract."""

    prediction = FrozenPrediction(
        prediction_id=str(row["prediction_id"]),
        event_id=str(row["event_id"]),
        instrument_id=str(row["instrument_id"]),
        created_at=stored_utc(row["created_at"]),
        persisted_at=stored_utc(row["persisted_at"]),
        information_cutoff=stored_utc(row["information_cutoff"]),
        input_snapshot_hash=str(row["input_snapshot_hash"]),
        model_version=row["model_version"],
        prompt_version=row["prompt_version"],
        rule_version=str(row["rule_version"]),
        evaluation_protocol_hash=str(row["evaluation_protocol_hash"]),
        price_basis=str(row["price_basis"]),
        calendar_version=str(row["calendar_version"]),
        direction=str(row["direction"]),
        horizon_minutes=int(row["horizon_minutes"]),
        parent_prediction_id=row["parent_prediction_id"],
        data_quality_status=str(row["data_quality_status"]),
    )
    prediction.validate()
    return prediction


def bar_from_store_row(row: Mapping[str, Any]) -> MinuteBar:
    """Require documented bar boundaries; provider_time has no assumed meaning."""

    if row["kind"] != "BAR_1M":
        raise ValueError("only BAR_1M rows can enter 30-minute evaluation")
    payload = json.loads(row["payload_json"]) if isinstance(row["payload_json"], str) \
        else row["payload_json"]
    if not isinstance(payload, dict):
        raise ValueError("bar payload must be an object")
    bar = MinuteBar(
        bar_id=int(row["id"]),
        instrument_id=str(row["instrument_id"]),
        start_at=stored_utc(payload["start_at"]),
        end_at=stored_utc(payload["end_at"]),
        open=float(payload["open"]),
        close=float(payload["close"]),
        session_id=str(payload["session_id"]),
        market_session=str(row["market_session"]),
        price_basis=str(payload["price_basis"]),
        received_at=stored_utc(row["received_at"]),
        persisted_at=stored_utc(row["persisted_at"]),
        delay_status=str(row["delay_status"]),
        version=int(row["version"]),
    )
    bar.validate()
    return bar


def outcome_to_store_record(outcome: OutcomeResult) -> dict[str, Any]:
    """Make an append-only outcome row; an unchanged result has a stable ID."""

    content_hash = outcome.input_hash or digest({
        "prediction_id": outcome.prediction_id,
        "status": outcome.status,
        "start": None if outcome.start_at is None else utc_text(outcome.start_at),
        "end": None if outcome.end_at is None else utc_text(outcome.end_at),
        "reason": outcome.reason,
    })
    return {
        "outcome_id": digest((outcome.prediction_id, outcome.status, content_hash)),
        "prediction_id": outcome.prediction_id,
        "status": outcome.status,
        "start_bar_id": outcome.start_bar_id,
        "end_bar_id": outcome.end_bar_id,
        "return_value": outcome.return_value,
        "qqq_return": outcome.qqq_return,
        "excess_return": outcome.excess_return,
        "direction_hit": None if outcome.direction_hit is None else int(outcome.direction_hit),
        "baseline_direction": outcome.baseline_direction,
        "baseline_hit": None if outcome.baseline_hit is None else int(outcome.baseline_hit),
        "reason": outcome.reason,
        "input_hash": content_hash,
        "input_manifest_json": outcome.input_manifest_json,
    }


def _selected_bars(bars: Iterable[MinuteBar], *, as_of: datetime) -> dict[datetime, MinuteBar]:
    selected: dict[datetime, MinuteBar] = {}
    for bar in bars:
        bar.validate()
        if utc(bar.persisted_at) > utc(as_of):
            continue
        start = utc(bar.start_at)
        previous = selected.get(start)
        if previous is None or (utc(bar.persisted_at), bar.version) > \
                (utc(previous.persisted_at), previous.version):
            selected[start] = bar
        elif (utc(bar.persisted_at), bar.version) == \
                (utc(previous.persisted_at), previous.version) and bar.bar_id != previous.bar_id:
            raise ValueError("ambiguous same-time bar revisions")
    return selected


def _is_sha256(value: str) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(
        ch in "0123456789abcdef" for ch in value.lower()
    )


def momentum_baseline(bars: Iterable[MinuteBar], *, instrument_id: str,
                      session: RegularSession, information_cutoff: datetime,
                      price_basis: str,
                      lookback_minutes: int = MOMENTUM_LOOKBACK_MINUTES) -> MomentumBaseline:
    """Fixed five-minute price momentum using only data persisted by the cutoff."""

    session.validate()
    if lookback_minutes != MOMENTUM_LOOKBACK_MINUTES:
        raise ValueError("observer v1 baseline lookback is frozen at five minutes")
    cutoff = utc(information_cutoff)
    last_start = minute_floor(cutoff) - MINUTE
    starts = [last_start - MINUTE * n for n in range(lookback_minutes, -1, -1)]
    if starts[0] < utc(session.opens_at) or starts[-1] + MINUTE > utc(session.closes_at):
        return MomentumBaseline("UNAVAILABLE", (), "Five complete pre-cutoff regular bars do not fit the session")
    selected = _selected_bars(
        (bar for bar in bars if bar.instrument_id == instrument_id and
         bar.session_id == session.session_id and bar.market_session == "REGULAR"),
        as_of=cutoff,
    )
    matched = [selected.get(start) for start in starts]
    if any(bar is None for bar in matched):
        return MomentumBaseline("UNAVAILABLE", (), "Pre-cutoff momentum bars are missing or were received later")
    complete = [bar for bar in matched if bar is not None]
    if any(bar.price_basis != price_basis for bar in complete):
        return MomentumBaseline("UNAVAILABLE", (), "Pre-cutoff price basis differs from outcome basis")
    change = complete[-1].close / complete[0].close - 1.0
    direction = "UP" if change > 0 else "DOWN" if change < 0 else "ABSTAIN"
    return MomentumBaseline(direction, tuple(bar.bar_id for bar in complete),
                            "Five-minute pre-cutoff close-to-close momentum")


def evaluate_outcome(prediction: FrozenPrediction, *, session: RegularSession,
                     instrument_bars: Iterable[MinuteBar],
                     qqq_bars: Iterable[MinuteBar], as_of: datetime,
                     price_basis: str, qqq_instrument_id: str = "US.QQQ") -> OutcomeResult:
    """Evaluate an immutable prediction only after a full same-session window.

    Calling again after a source price correction can produce a different input
    hash. The store must insert a new outcome version, never overwrite one.
    """

    prediction.validate()
    session.validate()
    now = utc(as_of)
    if not price_basis:
        raise ValueError("price adjustment basis must be frozen before evaluation")
    if prediction.price_basis != price_basis:
        raise ValueError("outcome price basis differs from frozen prediction protocol")
    if prediction.calendar_version != session.calendar_version:
        raise ValueError("calendar version differs from frozen prediction protocol")
    if prediction.instrument_id == qqq_instrument_id:
        raise ValueError("QQQ benchmark cannot be the evaluated instrument")
    actual = tuple(instrument_bars)
    benchmark = tuple(qqq_bars)
    baseline = momentum_baseline(
        actual, instrument_id=prediction.instrument_id, session=session,
        information_cutoff=prediction.information_cutoff, price_basis=price_basis,
    )

    def result(status: str, reason: str, *, start: datetime | None = None,
               end: datetime | None = None, start_bar: MinuteBar | None = None,
               end_bar: MinuteBar | None = None, ret: float | None = None,
               qqq_ret: float | None = None, quality: str = "UNKNOWN",
               input_hash: str | None = None,
               input_manifest_json: str | None = None) -> OutcomeResult:
        hit = None if ret is None or prediction.direction == "ABSTAIN" else \
            (ret > 0 if prediction.direction == "UP" else ret < 0)
        baseline_hit = None if ret is None or baseline.direction not in {"UP", "DOWN"} else \
            (ret > 0 if baseline.direction == "UP" else ret < 0)
        return OutcomeResult(
            prediction.prediction_id, status, reason, start, end,
            None if start_bar is None else start_bar.bar_id,
            None if end_bar is None else end_bar.bar_id,
            ret, qqq_ret, None if ret is None or qqq_ret is None else ret - qqq_ret,
            hit, baseline.direction, baseline_hit, quality, input_hash,
            input_manifest_json,
        )

    if now < utc(prediction.persisted_at):
        return result("PENDING", "Evaluation time precedes prediction persistence")
    if utc(prediction.persisted_at) >= utc(session.closes_at):
        return result("NO_REGULAR_WINDOW", "Prediction was persisted after this session closed")
    start = max(utc(session.opens_at), minute_ceil(prediction.persisted_at))
    end = start + MINUTE * (prediction.horizon_minutes + 1)
    if end > utc(session.closes_at):
        return result("CROSSES_CLOSE", "Thirty following regular minutes do not fit before close",
                      start=start, end=end)
    if now < end:
        return result("PENDING", "The full 30-minute outcome has not matured",
                      start=start, end=end)
    starts = [start + MINUTE * minute for minute in range(prediction.horizon_minutes + 1)]
    observed = _selected_bars(
        (bar for bar in actual if bar.instrument_id == prediction.instrument_id and
         bar.session_id == session.session_id), as_of=now,
    )
    controls = _selected_bars(
        (bar for bar in benchmark if bar.instrument_id == qqq_instrument_id and
         bar.session_id == session.session_id), as_of=now,
    )
    observed_window = [observed.get(at) for at in starts]
    control_window = [controls.get(at) for at in starts]
    missing_observed = [utc_text(at) for at, bar in zip(starts, observed_window) if bar is None]
    missing_control = [utc_text(at) for at, bar in zip(starts, control_window) if bar is None]
    if missing_observed or missing_control:
        return result("DATA_GAP", "Missing full regular bars: instrument=" +
                      ",".join(missing_observed) + "; QQQ=" + ",".join(missing_control),
                      start=start, end=end)
    full_observed = [bar for bar in observed_window if bar is not None]
    full_control = [bar for bar in control_window if bar is not None]
    all_bars = full_observed + full_control
    if any(bar.price_basis != price_basis for bar in all_bars):
        return result("PRICE_BASIS_MISMATCH", "One or more bars use another adjustment basis",
                      start=start, end=end)
    ret = full_observed[-1].close / full_observed[0].close - 1.0
    qqq_ret = full_control[-1].close / full_control[0].close - 1.0
    quality = "DELAYED" if any(bar.delay_status == "DELAYED" for bar in all_bars) else \
        "UNKNOWN_DELAY" if any(bar.delay_status not in {"REALTIME", "DELAYED"} for bar in all_bars) \
        else "REALTIME"
    input_manifest = {
        "prediction_id": prediction.prediction_id,
        "prediction_snapshot": prediction.input_snapshot_hash,
        "evaluation_protocol_hash": prediction.evaluation_protocol_hash,
        "session_id": session.session_id,
        "calendar_source": session.calendar_source,
        "calendar_version": session.calendar_version,
        "price_basis": price_basis,
        "instrument_bars": [(bar.bar_id, bar.version, bar.close) for bar in full_observed],
        "qqq_bars": [(bar.bar_id, bar.version, bar.close) for bar in full_control],
        "baseline_bars": baseline.bar_ids,
    }
    input_manifest_json = json.dumps(input_manifest, sort_keys=True,
                                     separators=(",", ":"), ensure_ascii=False,
                                     allow_nan=False)
    source_hash = hashlib.sha256(input_manifest_json.encode("utf-8")).hexdigest()
    return result("MATURED", "Descriptive same-window 30-minute outcome",
                  start=start, end=end, start_bar=full_observed[0],
                  end_bar=full_observed[-1], ret=ret, qqq_ret=qqq_ret,
                  quality=quality, input_hash=source_hash,
                  input_manifest_json=input_manifest_json)
