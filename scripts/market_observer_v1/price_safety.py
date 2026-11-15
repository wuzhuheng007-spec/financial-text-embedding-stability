"""Action and currency gate for future ETF/ADR outcome labels.

The v1 minute-bar evaluator computes an instrument's own return. This v2 gate
requires a documented action window and compatible price series before that
evaluator can be used for a new watchlist outcome. It makes no provider call.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
import json
from typing import Iterable

from .evaluation import (FrozenPrediction, MinuteBar, OutcomeResult,
                         RegularSession, digest, evaluate_outcome, minute_ceil,
                         utc, utc_text)


ACTION_KINDS = frozenset({"SPLIT", "DIVIDEND", "ADR_RATIO", "FX_BASIS_CHANGE"})
REQUIRED_BASIS = {
    "SPLIT": "SPLIT_ADJUSTED",
    "DIVIDEND": "TOTAL_RETURN_ADJUSTED",
    "ADR_RATIO": "ADR_RATIO_ADJUSTED",
    "FX_BASIS_CHANGE": "FX_ADJUSTED",
}


@dataclass(frozen=True)
class ActionEvidence:
    instrument_id: str
    kind: str
    effective_at: datetime
    source_hash: str
    applied_to_price_basis: bool
    source_url: str | None = None

    def validate(self) -> None:
        if self.kind not in ACTION_KINDS or not self.instrument_id:
            raise ValueError("action kind and exact instrument identity are required")
        utc(self.effective_at)
        if len(self.source_hash) != 64 or not all(c in "0123456789abcdef" for c in self.source_hash.lower()):
            raise ValueError("action source requires a SHA-256 digest")


@dataclass(frozen=True)
class PriceSeriesEvidence:
    instrument_id: str
    market: str
    currency: str
    security_form: str  # COMMON, ADR, ETF
    price_basis: str
    coverage_start: datetime
    coverage_end: datetime
    checked_at: datetime
    coverage_status: str  # COMPLETE or INCOMPLETE
    source_hash: str
    actions: tuple[ActionEvidence, ...] = ()
    daily_target_leverage: float | None = None

    def validate(self) -> None:
        if not all((self.instrument_id, self.market, self.currency, self.price_basis)):
            raise ValueError("price series needs identity, market, currency and basis")
        if self.security_form not in {"COMMON", "ADR", "ETF"}:
            raise ValueError("unsupported security form")
        if self.coverage_status not in {"COMPLETE", "INCOMPLETE"}:
            raise ValueError("action coverage status must be explicit")
        if utc(self.coverage_start) >= utc(self.coverage_end):
            raise ValueError("action coverage interval is reversed")
        utc(self.checked_at)
        if len(self.source_hash) != 64 or not all(c in "0123456789abcdef" for c in self.source_hash.lower()):
            raise ValueError("action coverage source requires a SHA-256 digest")
        if self.daily_target_leverage is not None and self.security_form != "ETF":
            raise ValueError("daily target leverage only applies to an ETF")
        if self.daily_target_leverage is not None and self.daily_target_leverage <= 0:
            raise ValueError("daily target leverage must be positive")
        for action in self.actions:
            action.validate()
            if action.instrument_id != self.instrument_id:
                raise ValueError("action belongs to another price series")


@dataclass(frozen=True)
class GuardedOutcome:
    status: str
    reason: str
    outcome: OutcomeResult | None


def evaluate_with_price_safety(
    prediction: FrozenPrediction, *, session: RegularSession,
    instrument_bars: Iterable[MinuteBar], qqq_bars: Iterable[MinuteBar],
    as_of: datetime, price_basis: str, instrument_series: PriceSeriesEvidence,
    qqq_series: PriceSeriesEvidence, qqq_instrument_id: str = "US.QQQ",
) -> GuardedOutcome:
    """Evaluate only exact US/USD series with complete action coverage.

    TQQQ uses its own supplied bars; its daily 3x target is descriptive ETF
    metadata and never multiplies QQQ returns. Korean common and US ADR series
    remain distinct. An action inside the evaluation window requires a
    reviewed adjustment on the matching price basis; otherwise return no label.
    """

    prediction.validate()
    session.validate()
    instrument_series.validate()
    qqq_series.validate()
    if instrument_series.instrument_id != prediction.instrument_id or \
            qqq_series.instrument_id != qqq_instrument_id:
        raise ValueError("price-series identities do not match evaluation instruments")
    if instrument_series.price_basis != price_basis or qqq_series.price_basis != price_basis:
        return GuardedOutcome("PRICE_BASIS_MISMATCH", "Series price basis differs from frozen evaluation basis", None)
    if session.market != "US" or instrument_series.market != "US" or qqq_series.market != "US":
        return GuardedOutcome("UNSUPPORTED_MARKET_PROTOCOL", "US regular-minute protocol cannot evaluate another market", None)
    if instrument_series.currency != "USD" or qqq_series.currency != "USD":
        return GuardedOutcome("FX_CONVERSION_PENDING", "Cross-currency QQQ return difference needs a frozen FX rule", None)

    window_start = max(utc(session.opens_at), minute_ceil(prediction.persisted_at))
    window_end = window_start + timedelta(minutes=prediction.horizon_minutes + 1)
    for series in (instrument_series, qqq_series):
        if series.coverage_status != "COMPLETE" or \
                utc(series.coverage_start) > window_start or \
                utc(series.coverage_end) < window_end or \
                utc(series.checked_at) < window_end:
            return GuardedOutcome("ACTION_COVERAGE_PENDING", "Action inventory does not cover the complete outcome window", None)
        for action in series.actions:
            if window_start <= utc(action.effective_at) < window_end and \
                    (not action.applied_to_price_basis or
                     price_basis != REQUIRED_BASIS[action.kind]):
                return GuardedOutcome("ACTION_ADJUSTMENT_PENDING",
                                      f"{action.kind} requires a verified matching price adjustment", None)

    result = evaluate_outcome(prediction, session=session,
                              instrument_bars=instrument_bars, qqq_bars=qqq_bars,
                              as_of=as_of, price_basis=price_basis,
                              qqq_instrument_id=qqq_instrument_id)
    if result.status == "MATURED":
        def inventory(series: PriceSeriesEvidence) -> dict:
            return {
                "instrument_id": series.instrument_id,
                "market": series.market,
                "currency": series.currency,
                "security_form": series.security_form,
                "price_basis": series.price_basis,
                "coverage_start": utc_text(series.coverage_start),
                "coverage_end": utc_text(series.coverage_end),
                "checked_at": utc_text(series.checked_at),
                "coverage_status": series.coverage_status,
                "source_hash": series.source_hash,
                "actions": [
                    {"kind": action.kind, "effective_at": utc_text(action.effective_at),
                     "source_hash": action.source_hash,
                     "applied_to_price_basis": action.applied_to_price_basis}
                    for action in series.actions
                ],
            }
        manifest = json.loads(result.input_manifest_json)
        manifest["price_safety_v2"] = {
            "instrument": inventory(instrument_series), "qqq": inventory(qqq_series)}
        result = replace(
            result, input_hash=digest(manifest),
            input_manifest_json=json.dumps(manifest, sort_keys=True,
                                           separators=(",", ":"), ensure_ascii=False,
                                           allow_nan=False))
    return GuardedOutcome(result.status, result.reason, result)
