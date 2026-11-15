"""Focused synthetic contract checks; no provider or model call is made."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
import sqlite3

import pytest

from scripts.market_observer_v1.evaluation import (
    FrozenPrediction, MinuteBar, RegularSession, bar_from_store_row,
    SnapshotEvidence, evaluate_outcome, freeze_input_snapshot,
    momentum_baseline, outcome_to_store_record,
    prediction_from_store_row, utc_text,
)
from scripts.market_observer_v1.events import (
    ClaimRelation, ProvenanceStatus, SourceEvidence, classify_claim,
    source_evidence_from_claim_row,
)
from scripts.market_observer_v1.learning import (
    ModelVersionGate, TrainingCandidate, build_temporal_manifest,
    promotion_readiness, propose_reviews, review_to_store_record,
    training_eligibility,
)
from scripts.market_observer_v1.research import (
    EventTrigger, ObservationProtocol, evaluate_due_prediction,
    record_rule_observation,
)
from scripts.market_observer_v1.store import Store
from scripts.market_observer_v1.store import utc_now as store_utc_now


UTC = timezone.utc
DAY = datetime(2026, 11, 16, tzinfo=UTC)


def at(hour: int, minute: int, second: int = 0) -> datetime:
    return DAY.replace(hour=hour, minute=minute, second=second)


def session(close_hour: int = 16) -> RegularSession:
    return RegularSession("US-2026-11-16", "US", at(9, 30), at(close_hour, 0),
                          "verified-calendar-fixture", "v1")


def prediction(*, persisted: datetime = at(10, 0, 30),
               cutoff: datetime = at(9, 59, 30), direction: str = "UP") -> FrozenPrediction:
    return FrozenPrediction("p-1", "event-1", "US.NVDA", at(10, 0, 0), persisted,
                            cutoff, "a" * 64, "rule-v1", "none", "v1",
                            "b" * 64, "UNADJUSTED", "v1", direction)


def bar(instrument: str, start: datetime, index: int, *, close: float | None = None,
        version: int = 1, delay: str = "REALTIME") -> MinuteBar:
    price = float(100 + index) if close is None else close
    end = start + timedelta(minutes=1)
    return MinuteBar(f"{instrument}-{start.isoformat()}-v{version}", instrument,
                     start, end, price, price, session().session_id,
                     "REGULAR", "UNADJUSTED", end, end + timedelta(seconds=1),
                     delay, version)


def bars(instrument: str, start: datetime, count: int, *, first_price: float = 100) -> list[MinuteBar]:
    return [bar(instrument, start + timedelta(minutes=i), i, close=first_price + i)
            for i in range(count)]


def test_first_full_bar_and_thirtieth_following_bar_are_used() -> None:
    observed = bars("US.NVDA", at(9, 53), 40)
    qqq = bars("US.QQQ", at(10, 0), 33, first_price=300)
    outcome = evaluate_outcome(prediction(), session=session(), instrument_bars=observed,
                               qqq_bars=qqq, as_of=at(10, 33), price_basis="UNADJUSTED")
    assert outcome.status == "MATURED"
    assert outcome.start_at == at(10, 1)
    assert outcome.end_at == at(10, 32)
    assert outcome.start_bar_id == observed[8].bar_id  # 10:01, not 10:00
    assert outcome.end_bar_id == observed[38].bar_id  # 10:31 bar, 10:32 close
    assert outcome.return_value == pytest.approx(observed[38].close / observed[8].close - 1)
    assert outcome.qqq_return == pytest.approx(qqq[31].close / qqq[1].close - 1)
    assert outcome.excess_return == pytest.approx(outcome.return_value - outcome.qqq_return)
    assert outcome.direction_hit is True
    assert outcome.baseline_direction == "UP"
    assert outcome.baseline_hit is True
    manifest = json.loads(outcome.input_manifest_json)
    assert len(manifest["instrument_bars"]) == len(manifest["qqq_bars"]) == 31
    assert hashlib.sha256(outcome.input_manifest_json.encode()).hexdigest() == outcome.input_hash


def test_window_is_pending_before_thirtieth_following_bar_closes() -> None:
    outcome = evaluate_outcome(prediction(), session=session(),
                               instrument_bars=[], qqq_bars=[], as_of=at(10, 31, 59),
                               price_basis="UNADJUSTED")
    assert outcome.status == "PENDING"
    assert outcome.return_value is None


def test_missing_qqq_bar_is_gap_not_zero_return() -> None:
    observed = bars("US.NVDA", at(10, 1), 31)
    qqq = bars("US.QQQ", at(10, 1), 31)
    del qqq[14]
    outcome = evaluate_outcome(prediction(), session=session(),
                               instrument_bars=observed, qqq_bars=qqq,
                               as_of=at(10, 33), price_basis="UNADJUSTED")
    assert outcome.status == "DATA_GAP"
    assert outcome.return_value is None
    assert outcome.qqq_return is None
    assert "QQQ=" in outcome.reason


def test_post_cutoff_bar_cannot_enter_momentum_baseline() -> None:
    prior = bars("US.NVDA", at(9, 53), 6)
    late = replace(prior[-1], received_at=at(10, 0), persisted_at=at(10, 0, 1))
    baseline = momentum_baseline(prior[:-1] + [late], instrument_id="US.NVDA",
                                 session=session(), information_cutoff=at(9, 59, 30),
                                 price_basis="UNADJUSTED")
    assert baseline.direction == "UNAVAILABLE"


def test_cross_close_and_no_regular_window_are_not_spliced() -> None:
    late = prediction(persisted=at(15, 29, 1), cutoff=at(15, 28))
    late = replace(late, created_at=at(15, 29))
    result = evaluate_outcome(late, session=session(), instrument_bars=[], qqq_bars=[],
                              as_of=at(16, 10), price_basis="UNADJUSTED")
    assert result.status == "CROSSES_CLOSE"
    assert result.return_value is None
    closed = replace(late, created_at=at(16, 0), persisted_at=at(16, 0))
    result2 = evaluate_outcome(closed, session=session(), instrument_bars=[], qqq_bars=[],
                               as_of=at(16, 10), price_basis="UNADJUSTED")
    assert result2.status == "NO_REGULAR_WINDOW"


def test_corrections_change_input_hash_without_mutating_old_result() -> None:
    observed = bars("US.NVDA", at(10, 1), 31)
    qqq = bars("US.QQQ", at(10, 1), 31)
    first = evaluate_outcome(prediction(), session=session(), instrument_bars=observed,
                             qqq_bars=qqq, as_of=at(10, 33), price_basis="UNADJUSTED")
    revised = replace(observed[-1], bar_id="corrected-end", close=155.0,
                      version=2, persisted_at=at(10, 34))
    second = evaluate_outcome(prediction(), session=session(),
                              instrument_bars=observed + [revised], qqq_bars=qqq,
                              as_of=at(10, 35), price_basis="UNADJUSTED")
    assert first.status == second.status == "MATURED"
    assert first.input_hash != second.input_hash
    assert first.end_bar_id != second.end_bar_id
    assert first.return_value != second.return_value


def test_delayed_bar_is_flagged_and_unknown_basis_is_rejected() -> None:
    observed = bars("US.NVDA", at(10, 1), 31)
    qqq = bars("US.QQQ", at(10, 1), 31)
    qqq[0] = replace(qqq[0], delay_status="DELAYED")
    outcome = evaluate_outcome(prediction(), session=session(), instrument_bars=observed,
                               qqq_bars=qqq, as_of=at(10, 33), price_basis="UNADJUSTED")
    assert outcome.status == "MATURED"
    assert outcome.data_quality_status == "DELAYED"
    with pytest.raises(ValueError, match="basis"):
        evaluate_outcome(prediction(), session=session(), instrument_bars=observed,
                         qqq_bars=qqq, as_of=at(10, 33), price_basis="")


def test_prediction_and_bar_time_contracts_reject_future_leakage() -> None:
    with pytest.raises(ValueError, match="out of order"):
        replace(prediction(), information_cutoff=at(10, 1)).validate()
    with pytest.raises(ValueError, match="complete bar"):
        replace(bar("US.NVDA", at(10, 1), 0), received_at=at(10, 1, 30)).validate()


def test_store_bar_adapter_requires_boundaries_and_ignores_ambiguous_provider_time() -> None:
    row = {
        "id": 17, "kind": "BAR_1M", "instrument_id": "US.NVDA",
        "provider_time": "2026-11-16T10:05:00Z",  # not interpreted as open or close
        "payload_json": {
            "start_at": "2026-11-16T10:01:00Z", "end_at": "2026-11-16T10:02:00Z",
            "open": 100, "close": 101, "session_id": "US-2026-11-16",
            "price_basis": "UNADJUSTED",
        },
        "market_session": "REGULAR", "received_at": "2026-11-16T10:02:01Z",
        "persisted_at": "2026-11-16T10:02:02Z", "delay_status": "REALTIME",
        "version": 1,
    }
    adapted = bar_from_store_row(row)
    assert adapted.start_at == at(10, 1)
    assert adapted.end_at == at(10, 2)
    assert adapted.bar_id == 17
    with pytest.raises(KeyError):
        bar_from_store_row({**row, "payload_json": {"close": 101}})


def test_prediction_store_adapter_and_frozen_protocol_match() -> None:
    row = {
        "prediction_id": "p-1", "event_id": "event-1", "instrument_id": "US.NVDA",
        "created_at": "2026-11-16T10:00:00Z", "persisted_at": "2026-11-16T10:00:30Z",
        "information_cutoff": "2026-11-16T09:59:30Z", "input_snapshot_hash": "a" * 64,
        "model_version": None, "prompt_version": None, "rule_version": "v1",
        "evaluation_protocol_hash": "b" * 64, "price_basis": "UNADJUSTED",
        "calendar_version": "v1", "direction": "ABSTAIN", "horizon_minutes": 30,
        "parent_prediction_id": None, "data_quality_status": "UNKNOWN",
    }
    adapted = prediction_from_store_row(row)
    assert adapted.direction == "ABSTAIN"
    with pytest.raises(ValueError, match="calendar version"):
        evaluate_outcome(adapted, session=replace(session(), calendar_version="v2"),
                         instrument_bars=[], qqq_bars=[], as_of=at(10, 33),
                         price_basis="UNADJUSTED")
    with pytest.raises(ValueError, match="price basis"):
        evaluate_outcome(adapted, session=session(), instrument_bars=[], qqq_bars=[],
                         as_of=at(10, 33), price_basis="SPLIT_ADJUSTED")


def test_pending_record_is_not_a_fabricated_market_label() -> None:
    outcome = evaluate_outcome(prediction(), session=session(), instrument_bars=[],
                               qqq_bars=[], as_of=at(10, 20), price_basis="UNADJUSTED")
    record = outcome_to_store_record(outcome)
    assert record["status"] == "PENDING"
    assert record["return_value"] is None
    assert record["qqq_return"] is None
    assert record == outcome_to_store_record(outcome)


def test_input_snapshot_uses_local_persistence_not_source_timestamp() -> None:
    known_schedule = SnapshotEvidence(
        "calendar-1", "CALENDAR", "f" * 64, at(9, 0), at(9, 0, 1),
        at(14, 0), "America/New_York", 1,
    )
    snapshot = freeze_input_snapshot([known_schedule], information_cutoff=at(9, 30),
                                     event_id="event-1", instrument_id="US.NVDA",
                                     rule_version="v1")
    assert snapshot.evidence_count == 1
    assert hashlib.sha256(snapshot.manifest_json.encode()).hexdigest() == snapshot.input_hash
    later_received = replace(known_schedule, record_id="later", persisted_at=at(9, 31))
    with pytest.raises(ValueError, match="unavailable"):
        freeze_input_snapshot([later_received], information_cutoff=at(9, 30),
                              event_id="event-1", instrument_id="US.NVDA",
                              rule_version="v1")


def test_private_store_prediction_and_pending_outcome_are_append_only(tmp_path) -> None:
    now = datetime.now(UTC)
    cutoff = now - timedelta(seconds=5)
    created = now - timedelta(seconds=1)
    snapshot = freeze_input_snapshot(
        [SnapshotEvidence("fixture-1", "SYNTHETIC", "a" * 64,
                          now - timedelta(seconds=30), now - timedelta(seconds=29),
                          None, "UTC", 1)],
        information_cutoff=cutoff, event_id="fixture-event",
        instrument_id="US.NVDA", rule_version="fixture-v1",
    )
    with Store(tmp_path / "observer.sqlite") as store:
        store.upsert_instrument("US.NVDA", "synthetic-fixture", "NVDA",
                                provider_code="US.NVDA", market="US",
                                identity_status="SYNTHETIC")
        with store.connection:
            store.connection.execute(
                "INSERT INTO event_clusters(event_id,cluster_key,created_at,updated_at,status) "
                "VALUES(?,?,?,?,?)",
                ("fixture-event", "fixture", utc_text(now), utc_text(now), "UNVERIFIED"),
            )
        store.insert_prediction({
            "prediction_id": "fixture-prediction", "event_id": "fixture-event",
            "instrument_id": "US.NVDA", "created_at": utc_text(created),
            "information_cutoff": utc_text(cutoff),
            "input_snapshot_hash": snapshot.input_hash,
            "input_manifest_json": snapshot.manifest_json,
            "model_version": None, "prompt_version": None,
            "rule_version": "fixture-v1", "evaluation_protocol_hash": "b" * 64,
            "price_basis": "UNADJUSTED", "calendar_version": "fixture-v1",
            "horizon_minutes": 30, "direction": "ABSTAIN", "rationale": "synthetic fixture",
            "transmission_json": [], "counter_scenario": "none",
            "data_quality_status": "SYNTHETIC",
        })
        frozen = prediction_from_store_row(store.get_prediction("fixture-prediction"))
        assert frozen.input_snapshot_hash == snapshot.input_hash
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            with store.connection:
                store.connection.execute(
                    "UPDATE predictions SET direction='UP' WHERE prediction_id='fixture-prediction'"
                )
        opened = now.replace(second=0, microsecond=0) - timedelta(minutes=1)
        fixture_session = RegularSession("fixture-session", "US", opened,
                                         opened + timedelta(hours=3),
                                         "synthetic-calendar", "fixture-v1")
        outcome = evaluate_outcome(frozen, session=fixture_session,
                                   instrument_bars=[], qqq_bars=[], as_of=now,
                                   price_basis="UNADJUSTED")
        assert outcome.status == "PENDING"
        store.insert_outcome(outcome_to_store_record(outcome))
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            with store.connection:
                store.connection.execute(
                    "UPDATE outcomes SET return_value=1 WHERE prediction_id='fixture-prediction'"
                )


def test_research_orchestration_blocks_replay_and_records_one_live_abstention(tmp_path) -> None:
    with Store(tmp_path / "observer.sqlite") as store:
        store.upsert_instrument("US.NVDA", "synthetic-fixture", "NVDA",
                                provider_code="US.NVDA", market="US",
                                identity_status="VERIFIED")
        now_text = store_utc_now()
        with store.connection:
            store.connection.execute(
                "INSERT INTO event_clusters(event_id,cluster_key,created_at,updated_at,status) "
                "VALUES(?,?,?,?,?)",
                ("fixture-live-event", "fixture-live", now_text, now_text, "UNREVIEWED"),
            )
        store.create_session("fixture-live-session", mode="LIVE_PUSH", pid=None,
                             config_hash="c" * 64, status="CONNECTED_DATA_RECEIVED")
        store.insert_source_claim({
            "claim_id": "fixture-claim-1", "claim_group_id": "fixture-group-1",
            "origin_id": "fixture-official-origin", "platform": "synthetic-fixture",
            "relation": "SUPPORTS",
            "primary_document_verified": True, "event_id": "fixture-live-event",
            "claim_text": "synthetic fixture fact", "status": "PRIMARY_CONFIRMED",
            "source_url": "https://example.org/fixture", "source_type": "OFFICIAL_PRIMARY",
            "provider_time": store_utc_now(), "received_at": store_utc_now(),
            "source_timezone": "UTC", "time_precision": "SECOND",
            "evidence_hash": "a" * 64,
        })
        current = datetime.now(UTC)
        opened = current.replace(second=0, microsecond=0) - timedelta(hours=1)
        fixture_session = RegularSession("fixture-live-calendar", "US", opened,
                                         opened + timedelta(hours=4),
                                         "synthetic-calendar", "fixture-calendar-v1")
        protocol = ObservationProtocol("fixture-momentum-v1", "UNADJUSTED",
                                       "fixture-calendar-v1", 600, 60)
        trigger = EventTrigger("fixture-live-event", "fixture-group-1",
                               "fixture-claim-1", "US.NVDA", "fixture-live-session",
                               "REPLAY", "SECOND", "VERIFIED")
        replay = record_rule_observation(store, trigger=trigger, protocol=protocol,
                                         session=fixture_session)
        assert replay.status == "SKIPPED_REPLAY"
        wrong_precision = record_rule_observation(
            store, trigger=replace(trigger, origin="LIVE_AUTHORIZED",
                                   source_time_precision="MINUTE"),
            protocol=protocol, session=fixture_session)
        assert wrong_precision.status == "SKIPPED_TIME_PRECISION"
        live = record_rule_observation(store, trigger=replace(trigger, origin="LIVE_AUTHORIZED"),
                                       protocol=protocol, session=fixture_session)
        assert live.status == "RECORDED"
        assert live.provenance == "PRIMARY_CONFIRMED"
        assert live.momentum_direction == "UNAVAILABLE"
        frozen = prediction_from_store_row(store.get_prediction(live.prediction_id))
        assert frozen.direction == "ABSTAIN"
        duplicate = record_rule_observation(store,
            trigger=replace(trigger, origin="LIVE_AUTHORIZED"),
            protocol=protocol, session=fixture_session)
        assert duplicate.status == "DUPLICATE"
        assert duplicate.prediction_id == live.prediction_id
        assert store.connection.execute("SELECT COUNT(*) FROM predictions").fetchone()[0] == 1
        pending = evaluate_due_prediction(store, prediction_id=live.prediction_id,
                                          session=fixture_session, as_of=datetime.now(UTC),
                                          qqq_instrument_id="US.QQQ")
        assert pending.status == "PENDING"
        assert pending.created
        assert store.connection.execute("SELECT COUNT(*) FROM outcomes").fetchone()[0] == 1
        assert store.connection.execute("SELECT category FROM review_queue").fetchone()[0] == "LABEL_PENDING"
        again = evaluate_due_prediction(store, prediction_id=live.prediction_id,
                                        session=fixture_session, as_of=datetime.now(UTC),
                                        qqq_instrument_id="US.QQQ")
        assert not again.created
        assert store.connection.execute("SELECT COUNT(*) FROM outcomes").fetchone()[0] == 1


def test_live_gate_does_not_treat_new_receipt_of_old_story_as_new_event(tmp_path) -> None:
    with Store(tmp_path / "observer.sqlite") as store:
        store.upsert_instrument("US.NVDA", "synthetic-fixture", "NVDA",
                                provider_code="US.NVDA", market="US",
                                identity_status="VERIFIED")
        now_text = store_utc_now()
        with store.connection:
            store.connection.execute(
                "INSERT INTO event_clusters(event_id,cluster_key,created_at,updated_at,status) "
                "VALUES(?,?,?,?,?)",
                ("old-event", "old", now_text, now_text, "UNREVIEWED"),
            )
        store.create_session("live-session", mode="LIVE_PUSH", pid=None,
                             config_hash="c" * 64, status="CONNECTED_DATA_RECEIVED")
        old_published = utc_text(datetime.now(UTC) - timedelta(days=1))
        store.insert_source_claim({
            "claim_id": "old-claim", "claim_group_id": "old-group",
            "origin_id": "old-origin", "platform": "synthetic-fixture",
            "relation": "SUPPORTS",
            "primary_document_verified": True, "event_id": "old-event",
            "claim_text": "old synthetic fixture", "status": "PRIMARY_CONFIRMED",
            "source_url": "https://example.org/old", "source_type": "OFFICIAL_PRIMARY",
            "provider_time": old_published, "received_at": store_utc_now(),
            "source_timezone": "UTC", "time_precision": "SECOND",
            "evidence_hash": "a" * 64,
        })
        current = datetime.now(UTC)
        opened = current.replace(second=0, microsecond=0) - timedelta(hours=1)
        fixture_session = RegularSession("fixture", "US", opened,
                                         opened + timedelta(hours=4),
                                         "synthetic-calendar", "v1")
        receipt = record_rule_observation(
            store, trigger=EventTrigger("old-event", "old-group", "old-claim",
                                        "US.NVDA", "live-session", "LIVE_AUTHORIZED",
                                        "SECOND", "VERIFIED"),
            protocol=ObservationProtocol("momentum-v1", "UNADJUSTED", "v1", 600, 60),
            session=fixture_session,
        )
        assert receipt.status == "SKIPPED_STALE_EVENT"
        assert store.connection.execute("SELECT COUNT(*) FROM predictions").fetchone()[0] == 0


def source(item: str, origin: str | None, *, kind: str = "INDEPENDENT_REPORTING",
           relation: ClaimRelation = ClaimRelation.SUPPORTS,
           verified: bool = False) -> SourceEvidence:
    return SourceEvidence(item, "claim-1", "platform", origin, kind, relation,
                          f"https://example.org/{item}", at(9, 0), at(9, 1),
                          primary_document_verified=verified)


def test_provenance_distinguishes_primary_independent_reprint_and_conflict() -> None:
    primary = source("official", "official-1", kind="OFFICIAL_PRIMARY", verified=True)
    assert classify_claim("claim-1", [primary]).status == ProvenanceStatus.PRIMARY_CONFIRMED
    assert classify_claim("claim-1", [source("a", "origin-1"), source("b", "origin-2")]
                          ).status == ProvenanceStatus.INDEPENDENTLY_CORROBORATED
    assert classify_claim("claim-1", [source("a", "origin-1"), source("b", "origin-1")]
                          ).status == ProvenanceStatus.SAME_ORIGIN_REPRINT
    assert classify_claim("claim-1", [source("a", "origin-1"),
                                      source("b", "origin-2", relation=ClaimRelation.CONTRADICTS)]
                          ).status == ProvenanceStatus.CONFLICTING
    assert classify_claim("claim-1", [source("a", None, relation=ClaimRelation.UNREVIEWED)]
                          ).status == ProvenanceStatus.UNVERIFIED


def test_source_claim_adapter_keeps_group_and_origin_separate() -> None:
    row = {
        "claim_id": "evidence-1", "claim_group_id": "logical-claim-1",
        "origin_id": "publisher-1", "source_type": "INDEPENDENT_REPORTING",
        "relation": "SUPPORTS", "source_url": "https://example.org/one",
        "provider_time": "2026-11-16T09:00:00Z",
        "received_at": "2026-11-16T09:01:00Z",
        "primary_document_verified": 0,
    }
    item = source_evidence_from_claim_row(row, platform="jin10")
    assert item.item_id == "evidence-1"
    assert item.claim_id == "logical-claim-1"
    assert item.origin_id == "publisher-1"


def candidate(example_id: str, group: str, when: datetime, *, reused: bool = False,
              rights: str = "TRAINING_ALLOWED", label_kind: str = "EXTRACTION") -> TrainingCandidate:
    return TrainingCandidate(example_id, group, "a" * 64, when, when + timedelta(minutes=40),
                             rights, "rights-record-v1" if rights == "TRAINING_ALLOWED" else None,
                             label_kind,
                             "HUMAN_ADJUDICATION" if label_kind == "EXTRACTION" else "MARKET_OBSERVATION",
                             "label-v1", "ACCEPTED", reused, True, "UP")


def test_learning_rights_and_development_reuse_fail_closed() -> None:
    freeze = at(15, 0)
    assert "REUSED_DEVELOPMENT_SAMPLE" in training_eligibility(
        candidate("old-six", "event-old", at(10, 0), reused=True), freeze_at=freeze).reasons
    assert "SOURCE_TRAINING_RIGHTS_UNCONFIRMED" in training_eligibility(
        candidate("unknown-rights", "event-new", at(10, 0), rights="UNKNOWN"),
        freeze_at=freeze).reasons
    assert "LABEL_NOT_AVAILABLE_AT_FREEZE" in training_eligibility(
        candidate("unmatured", "event-new", at(14, 30)), freeze_at=freeze).reasons
    with pytest.raises(ValueError, match="human extraction"):
        replace(candidate("x", "e", at(10, 0)), label_source="MARKET_OBSERVATION").validate()


def test_reprints_remain_in_one_temporal_split() -> None:
    entries = [candidate("early", "same-event", at(10, 0)),
               candidate("late-reprint", "same-event", at(13, 0)),
               candidate("other", "other-event", at(9, 0))]
    manifest = build_temporal_manifest(entries, train_end=at(11, 0),
                                       validation_end=at(14, 0), freeze_at=at(15, 0))
    assert {row.split for row in manifest if row.event_group_id == "same-event"} == {"VALIDATION"}
    assert next(row.split for row in manifest if row.example_id == "other") == "TRAIN"


def test_review_queue_keeps_human_ruling_blank_and_model_separate_from_market() -> None:
    reviews = propose_reviews(event_id="event-1", prediction_id="p-1",
                              provenance=ProvenanceStatus.CONFLICTING,
                              extraction_flags=["FIELD_MISMATCH", "INSUFFICIENT_QUOTE"],
                              evidence_refs=["source-a", "source-b"])
    assert {item.category for item in reviews} == {
        "SOURCE_CONFLICT", "FIELD_MISMATCH", "INSUFFICIENT_QUOTE"}
    assert all(item.human_ruling is None and item.status == "OPEN" for item in reviews)
    assert len({item.review_id for item in reviews}) == len(reviews)
    stored = review_to_store_record(reviews[0], created_at=at(10, 33))
    assert stored["human_ruling_json"] is None
    assert stored["evidence_json"] == {"refs": ["source-a", "source-b"]}
    quality_review = propose_reviews(event_id="event-1", prediction_id="p-1",
                                     provenance=ProvenanceStatus.SINGLE_SOURCE,
                                     quality_flags=["PRICE_ANOMALY"])
    assert [item.category for item in quality_review] == ["PRICE_ANOMALY"]


def test_promotion_requires_out_of_time_event_disjoint_gate_and_rollback() -> None:
    gate = ModelVersionGate("new", None, False, False, False, False)
    ready, reasons = promotion_readiness(gate)
    assert not ready and "ROLLBACK_TARGET_MISSING" in reasons
    assert promotion_readiness(ModelVersionGate("new", "old", True, True, True, True)) == (True, ())
