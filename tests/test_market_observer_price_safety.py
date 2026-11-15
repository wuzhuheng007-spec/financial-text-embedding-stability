"""Synthetic ETF, action and cross-market gates; no live price data."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json

import pytest

from scripts.market_observer_v1.evaluation import (
    FrozenPrediction, MinuteBar, RegularSession, digest, minute_ceil, utc_text,
)
from scripts.market_observer_v1 import research
from scripts.market_observer_v1.price_safety import (
    ActionEvidence, PriceSeriesEvidence, evaluate_with_price_safety,
)
from scripts.market_observer_v1.store import Store


DAY = datetime(2026, 11, 16, tzinfo=timezone.utc)


def at(hour: int, minute: int, second: int = 0) -> datetime:
    return DAY.replace(hour=hour, minute=minute, second=second)


def session() -> RegularSession:
    return RegularSession("US-2026-11-16-synthetic", "US", at(9, 30), at(16, 0),
                          "synthetic-calendar", "synthetic-v1")


def prediction(instrument_id: str = "US.TQQQ") -> FrozenPrediction:
    return FrozenPrediction("synthetic-p1", "synthetic-event", instrument_id,
                            at(10, 0), at(10, 0, 30), at(9, 59, 30),
                            "a" * 64, None, None, "rule-v1", "b" * 64,
                            "UNADJUSTED", "synthetic-v1", "UP")


def series(instrument_id: str, *, form: str = "ETF", market: str = "US",
           currency: str = "USD", actions: tuple[ActionEvidence, ...] = (),
           status: str = "COMPLETE", leverage: float | None = None) -> PriceSeriesEvidence:
    return PriceSeriesEvidence(instrument_id, market, currency, form, "UNADJUSTED",
                               at(9, 30), at(10, 33), at(10, 34), status,
                               "c" * 64, actions, leverage)


def bars(instrument_id: str, *, initial: float, final: float) -> list[MinuteBar]:
    result = []
    for i in range(31):
        start = at(10, 1) + timedelta(minutes=i)
        end = start + timedelta(minutes=1)
        value = initial + (final - initial) * i / 30
        result.append(MinuteBar(f"{instrument_id}-{i}", instrument_id,
                                start, end, value, value, session().session_id,
                                "REGULAR", "UNADJUSTED", end, end + timedelta(seconds=1),
                                "REALTIME"))
    return result


def evaluate(*, instrument_id: str = "US.TQQQ",
             instrument_series: PriceSeriesEvidence | None = None,
             qqq_series: PriceSeriesEvidence | None = None,
             instrument_bars: list[MinuteBar] | None = None):
    return evaluate_with_price_safety(
        prediction(instrument_id), session=session(),
        instrument_bars=bars(instrument_id, initial=100, final=105)
        if instrument_bars is None else instrument_bars,
        qqq_bars=bars("US.QQQ", initial=200, final=204),
        as_of=at(10, 34), price_basis="UNADJUSTED",
        instrument_series=instrument_series or series(instrument_id, leverage=3.0 if instrument_id == "US.TQQQ" else None),
        qqq_series=qqq_series or series("US.QQQ"),
    )


def test_tqqq_uses_its_own_actual_bars_not_three_times_qqq() -> None:
    checked = evaluate()
    assert checked.status == "MATURED"
    assert checked.outcome is not None
    assert checked.outcome.return_value == pytest.approx(0.05)
    assert checked.outcome.qqq_return == pytest.approx(0.02)
    assert checked.outcome.return_value != pytest.approx(3 * checked.outcome.qqq_return)
    assert checked.outcome.excess_return == pytest.approx(0.03)
    manifest = json.loads(checked.outcome.input_manifest_json)
    assert manifest["price_safety_v2"]["instrument"]["instrument_id"] == "US.TQQQ"
    assert manifest["price_safety_v2"]["qqq"]["instrument_id"] == "US.QQQ"
    assert hashlib.sha256(checked.outcome.input_manifest_json.encode()).hexdigest() == checked.outcome.input_hash


def test_qqq_bars_cannot_substitute_for_missing_tqqq_bars() -> None:
    checked = evaluate(instrument_bars=bars("US.QQQ", initial=200, final=204))
    assert checked.status == "DATA_GAP"
    assert checked.outcome is not None and checked.outcome.return_value is None


@pytest.mark.parametrize("kind", ["SPLIT", "DIVIDEND", "ADR_RATIO", "FX_BASIS_CHANGE"])
def test_mechanical_price_changes_block_unadjusted_label(kind: str) -> None:
    identity = "US.SKHY" if kind in {"ADR_RATIO", "FX_BASIS_CHANGE"} else "US.TQQQ"
    action = ActionEvidence(identity, kind, at(10, 15), "d" * 64, False,
                            "https://example.invalid/synthetic-action")
    checked = evaluate(instrument_id=identity,
                       instrument_series=series(identity, form="ADR" if identity == "US.SKHY" else "ETF",
                                                actions=(action,)))
    assert checked.status == "ACTION_ADJUSTMENT_PENDING"
    assert checked.outcome is None


def test_dividend_needs_total_return_basis_even_when_marked_applied() -> None:
    action = ActionEvidence("US.TQQQ", "DIVIDEND", at(10, 15), "d" * 64, True)
    checked = evaluate(instrument_series=series("US.TQQQ", actions=(action,), leverage=3.0))
    assert checked.status == "ACTION_ADJUSTMENT_PENDING"
    assert checked.outcome is None


def test_korean_common_is_not_spliced_into_us_adr_or_qqq_window() -> None:
    checked = evaluate(instrument_id="KR.000660",
                       instrument_series=series("KR.000660", form="COMMON", market="KR", currency="KRW"))
    assert checked.status == "UNSUPPORTED_MARKET_PROTOCOL"
    assert checked.outcome is None


def test_incomplete_action_inventory_blocks_mature_label() -> None:
    checked = evaluate(instrument_series=series("US.TQQQ", status="INCOMPLETE", leverage=3.0))
    assert checked.status == "ACTION_COVERAGE_PENDING"
    assert checked.outcome is None


def test_series_identity_cannot_be_relabelled_as_another_security() -> None:
    with pytest.raises(ValueError, match="identities"):
        evaluate(instrument_series=series("US.SPY"))


def test_store_orchestration_cannot_mature_without_action_inventory(tmp_path, monkeypatch) -> None:
    """Synthetic mature bars must not bypass the stored-outcome gate."""
    with Store(tmp_path / "synthetic.sqlite") as store:
        store.upsert_instrument("US.TQQQ", "synthetic", "TQQQ", market="US",
                                identity_status="VERIFIED")
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(seconds=2)
        with store.connection:
            store.connection.execute(
                "INSERT INTO event_clusters(event_id,cluster_key,created_at,updated_at,status) "
                "VALUES(?,?,?,?,?)",
                ("synthetic-event", "synthetic-key", utc_text(now), utc_text(now), "UNREVIEWED"))
        manifest = {"event_id": "synthetic-event", "instrument_id": "US.TQQQ",
                    "rule_version": "synthetic-v2", "information_cutoff": utc_text(cutoff),
                    "records": []}
        store.insert_prediction({
            "prediction_id": "synthetic-prediction", "event_id": "synthetic-event",
            "instrument_id": "US.TQQQ", "created_at": utc_text(now),
            "information_cutoff": utc_text(cutoff),
            "input_snapshot_hash": digest(manifest), "input_manifest_json": manifest,
            "rule_version": "synthetic-v2", "evaluation_protocol_hash": "d" * 64,
            "price_basis": "UNADJUSTED", "calendar_version": "synthetic-v1",
            "horizon_minutes": 30, "direction": "UP", "rationale": "synthetic",
            "transmission_json": [], "counter_scenario": "synthetic",
            "data_quality_status": "SYNTHETIC",
        })
        persisted = store.get_prediction("synthetic-prediction")["persisted_at"]
        start = minute_ceil(datetime.fromisoformat(persisted.replace("Z", "+00:00")))
        synthetic_session = RegularSession(
            "synthetic-session", "US", start - timedelta(hours=1),
            start + timedelta(hours=2), "synthetic-calendar", "synthetic-v1")

        def stored_bars(_store, instrument_id, _session_id):
            return [MinuteBar(
                f"{instrument_id}-{i}", instrument_id,
                start + timedelta(minutes=i), start + timedelta(minutes=i + 1),
                100 + i, 100 + i, synthetic_session.session_id, "REGULAR",
                "UNADJUSTED", start + timedelta(minutes=i + 1),
                start + timedelta(minutes=i + 1, seconds=1), "REALTIME")
                for i in range(31)]

        monkeypatch.setattr(research, "_stored_bars", stored_bars)
        receipt = research.evaluate_due_prediction(
            store, prediction_id="synthetic-prediction", session=synthetic_session,
            as_of=start + timedelta(minutes=33), qqq_instrument_id="US.QQQ")
        assert receipt.status == "ACTION_COVERAGE_PENDING"
        row = store.connection.execute("SELECT status,return_value,qqq_return FROM outcomes").fetchone()
        assert tuple(row) == ("ACTION_COVERAGE_PENDING", None, None)
