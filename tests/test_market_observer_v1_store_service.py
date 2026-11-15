"""Focused synthetic checks for private observation and durable time boundaries."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from scripts.market_observer_v1.cli import file_lock
from scripts.market_observer_v1.replay import replay_jin10
from scripts.market_observer_v1.moomoo_probe_import import import_private_projection
from scripts.market_observer_v1.service import (
    AuthorizationError, backoff_seconds, interruptible_backoff,
    load_config, private_output_path, run_service,
)
from scripts.market_observer_v1.store import Store, digest


T0 = "2026-11-15T01:00:00Z"
T1 = "2026-11-15T01:01:00Z"
T2 = "2026-11-15T01:02:00Z"


def _quote(close: float = 100.0) -> dict:
    return {"provider": "SYNTHETIC", "instrument_id": "US.NVDA", "kind": "BAR_1M",
            "provider_time": T1, "received_at": T2, "source_timezone": "America/New_York",
            "market_session": "REGULAR_OPEN", "delay_status": "REALTIME",
            "payload": {"start_at": T0, "end_at": T1, "open": 99.0,
                        "close": close, "session_id": "test-session", "price_basis": "UNADJUSTED"}}


def test_quote_duplicate_and_correction_are_distinct_append_only_versions(tmp_path: Path) -> None:
    with Store(tmp_path / "ledger.sqlite") as store:
        store.upsert_instrument("US.NVDA", "SYNTHETIC", "NVDA", provider_code="US.NVDA",
                                market="US", identity_status="VERIFIED")
        first, created = store.insert_quote(**_quote())
        repeated, created_again = store.insert_quote(**_quote())
        corrected, corrected_created = store.insert_quote(**_quote(100.5))
        assert (first, created) == (repeated, True)
        assert not created_again and corrected_created and corrected != first
        rows = store.connection.execute("SELECT version,revision_of FROM quotes_or_bars ORDER BY version").fetchall()
        assert [(r["version"], r["revision_of"]) for r in rows] == [(1, None), (2, first)]
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            store.connection.execute("UPDATE quotes_or_bars SET delay_status='STALE' WHERE id=?", (first,))


def test_news_revisions_keep_first_local_receipt_and_fetches(tmp_path: Path) -> None:
    with Store(tmp_path / "ledger.sqlite") as store:
        base = {"provider": "SYNTHETIC", "identity_key": "url:one",
                "key_kind": "SOURCE_URL", "potential_collision": False,
                "source_url": "https://example.org/one", "received_at": T0,
                "provider_time": None, "content": "first", "payload": {"title": "first"}}
        first, created = store.insert_news(**base)
        repeated, repeated_created = store.insert_news(**{**base, "received_at": T1})
        withdrawn, changed = store.insert_news(**{**base, "received_at": T2,
                                                   "content": "withdrawn", "status": "WITHDRAWN"})
        assert first == repeated and created and not repeated_created
        assert withdrawn != first and changed
        row = store.connection.execute("SELECT version,revision_of,first_received_at,status FROM news_items WHERE id=?",
                                       (withdrawn,)).fetchone()
        assert tuple(row) == (2, first, T0, "WITHDRAWN")
        assert store.connection.execute("SELECT COUNT(*) FROM news_fetches").fetchone()[0] == 3


def test_prediction_and_outcome_are_immutable_and_protocol_bound(tmp_path: Path) -> None:
    with Store(tmp_path / "ledger.sqlite") as store:
        store.upsert_instrument("US.NVDA", "SYNTHETIC", "NVDA")
        store.connection.execute("INSERT INTO event_clusters(event_id,cluster_key,created_at,updated_at,status) VALUES(?,?,?,?,?)",
                                 ("event-1", "synthetic", T0, T0, "UNVERIFIED"))
        store.connection.commit()
        manifest = {"event_id": "event-1", "instrument_id": "US.NVDA",
                    "rule_version": "rule-1", "information_cutoff": T1,
                    "records": [{"record_kind": "news", "record_id": "n1",
                                 "received_at": T0, "persisted_at": T0}]}
        record = {"prediction_id": "p1", "event_id": "event-1", "instrument_id": "US.NVDA",
                  "created_at": T2, "information_cutoff": T1,
                  "input_snapshot_hash": digest(manifest),
                  "input_manifest_json": manifest, "model_version": None,
                  "prompt_version": None, "rule_version": "rule-1",
                  "evaluation_protocol_hash": digest({"horizon": 30}),
                  "price_basis": "UNADJUSTED", "calendar_version": "US-reg-1",
                  "horizon_minutes": 30, "direction": "ABSTAIN", "rationale": "synthetic",
                  "transmission_json": [], "counter_scenario": "synthetic",
                  "data_quality_status": "SYNTHETIC"}
        store.insert_prediction(record)
        saved = store.get_prediction("p1")
        assert saved and saved["persisted_at"] >= saved["created_at"]
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            store.connection.execute("DELETE FROM predictions WHERE prediction_id='p1'")
        store.insert_outcome({"outcome_id": "o1", "prediction_id": "p1",
                              "status": "PENDING", "input_hash": digest({"pending": True})})
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            store.connection.execute("UPDATE outcomes SET status='MATURE' WHERE outcome_id='o1'")
        with pytest.raises(ValueError, match="time order"):
            store.insert_prediction({**record, "prediction_id": "p2",
                                     "created_at": T0, "information_cutoff": T1})


def test_time_and_checkpoint_fail_closed(tmp_path: Path) -> None:
    with Store(tmp_path / "ledger.sqlite") as store:
        store.upsert_instrument("US.NVDA", "SYNTHETIC", "NVDA")
        with pytest.raises(ValueError, match="UTC"):
            store.insert_quote(**{**_quote(), "provider_time": "2026-11-15 01:01:00"})
        with pytest.raises(ValueError, match="missing fields"):
            store.insert_quote(**{**_quote(), "payload": {"close": 100}})
        with pytest.raises(ValueError, match="fully verified"):
            store.set_checkpoint("SYNTHETIC", "quotes", complete_cursor="partial",
                                 coverage_status="INCOMPLETE")
        assert store.checkpoint("SYNTHETIC", "quotes") is None


def test_single_instance_lock_and_no_feed_service_are_explicit(tmp_path: Path) -> None:
    lock = tmp_path / "one.lock"
    with file_lock(lock):
        with pytest.raises(RuntimeError, match="already running"):
            with file_lock(lock):
                pass
    config = {
        "schema": "market_observer_service_config_v1", "db_path": "artifacts/test/ledger.sqlite",
        "dashboard_path": "artifacts/test/dashboard.html", "feed_mode": "NO_FEED",
        "quote_push_verified": False, "news_quota_confirmed": False,
        "news_poll_interval_seconds": None, "max_provider_requests_per_session": 0,
        "heartbeat_seconds": 0.001, "max_disk_mib": 100, "max_events_per_session": 10,
        "max_reconnect_attempts": 2, "reconnect_initial_seconds": 0.001,
        "reconnect_max_seconds": 0.01, "max_runtime_minutes": None,
    }
    assert run_service(config=config, repo_root=tmp_path, session_id="test-id", max_cycles=2) == "STOPPED_TEST_CYCLES"
    with Store(tmp_path / "artifacts/test/ledger.sqlite") as store:
        assert store.latest_session()["ended_at"] is not None
        assert store.connection.execute("SELECT COUNT(*) FROM quotes_or_bars").fetchone()[0] == 0
    assert (tmp_path / "artifacts/test/dashboard.html").is_file()
    with pytest.raises(ValueError, match="ignored artifacts"):
        private_output_path("outside.sqlite", tmp_path)


def test_config_rejects_unverified_news_polling_and_push(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    import json
    base = {"schema": "market_observer_service_config_v1", "db_path": "artifacts/db.sqlite",
            "dashboard_path": "artifacts/page.html", "feed_mode": "NO_FEED",
            "heartbeat_seconds": 5, "max_disk_mib": 100, "max_events_per_session": 10,
            "max_reconnect_attempts": 3, "reconnect_initial_seconds": 1,
            "reconnect_max_seconds": 5, "news_quota_confirmed": False}
    path.write_text(json.dumps({**base, "news_poll_interval_seconds": 60}), encoding="utf-8")
    with pytest.raises(ValueError, match="verified quota"):
        load_config(path)
    path.write_text(json.dumps({**base, "feed_mode": "LIVE_PUSH",
                                "news_poll_interval_seconds": None}), encoding="utf-8")
    with pytest.raises(ValueError, match="verified quote push"):
        load_config(path)


def _service_config() -> dict:
    return {"schema": "market_observer_service_config_v1",
            "db_path": "artifacts/test/ledger.sqlite",
            "dashboard_path": "artifacts/test/dashboard.html",
            "feed_mode": "LIVE_PUSH", "quote_push_verified": True,
            "news_quota_confirmed": False, "news_poll_interval_seconds": None,
            "max_provider_requests_per_session": 0, "heartbeat_seconds": 0.001,
            "max_disk_mib": 100, "max_events_per_session": 10,
            "max_reconnect_attempts": 2, "reconnect_initial_seconds": 0.001,
            "reconnect_max_seconds": 0.01, "max_runtime_minutes": None,
            "observation_pool": ["NVDA"]}


def test_synthetic_push_disconnect_recovery_records_gap_and_checkpoint(tmp_path: Path) -> None:
    class FakePush:
        provider_name = "SYNTHETIC"

        def __init__(self):
            self.connect_count = 0
            self.sent = False

        def connect(self):
            self.connect_count += 1
            if self.connect_count == 1:
                raise ConnectionError("synthetic disconnect")

        def next_message(self, timeout_seconds):
            if not self.sent:
                self.sent = True
                return {"kind": "QUOTE", "quote": _quote(),
                        "complete_marker": {"test_seq": 1}}
            return None

        def close(self):
            pass

    db = tmp_path / "artifacts/test/ledger.sqlite"
    with Store(db) as store:
        store.upsert_instrument("US.NVDA", "SYNTHETIC", "NVDA", identity_status="VERIFIED")
    fake = FakePush()
    state = run_service(config=_service_config(), repo_root=tmp_path, session_id="test-push",
                        source=fake, max_cycles=3)
    assert state == "STOPPED_TEST_CYCLES"
    with Store(db) as store:
        assert store.connection.execute("SELECT COUNT(*) FROM quotes_or_bars").fetchone()[0] == 1
        assert store.checkpoint("SYNTHETIC", "quotes")["coverage_status"] == "COMPLETE"
        assert store.connection.execute("SELECT reason FROM collection_gaps").fetchone()[0] == "DISCONNECT_RECOVERED"
        assert fake.connect_count == 2


def test_synthetic_authorization_failure_stops_without_retry(tmp_path: Path) -> None:
    class Unauthorized:
        provider_name = "SYNTHETIC"
        connect_count = 0

        def connect(self):
            self.connect_count += 1
            raise AuthorizationError("synthetic 401")

        def next_message(self, timeout_seconds):
            raise AssertionError("must not fetch after authorization failure")

        def close(self):
            pass

    fake = Unauthorized()
    state = run_service(config=_service_config(), repo_root=tmp_path, session_id="test-401",
                        source=fake, max_cycles=3)
    assert state == "STOPPED_AUTHORIZATION_OR_QUOTA"
    assert fake.connect_count == 1
    with Store(tmp_path / "artifacts/test/ledger.sqlite") as store:
        assert store.connection.execute("SELECT reason FROM collection_gaps").fetchone()[0] == "AuthorizationError"
        assert store.connection.execute("SELECT COUNT(*) FROM quotes_or_bars").fetchone()[0] == 0


def test_bounded_jin10_offline_replay_is_idempotent_and_keeps_partial_gap(tmp_path: Path) -> None:
    import json
    source = tmp_path / "source.sqlite"
    target = tmp_path / "target.sqlite"
    with sqlite3.connect(source) as db:
        db.executescript("""
        CREATE TABLE logical_items(logical_key TEXT,tool_name TEXT,key_kind TEXT,
          potential_collision INTEGER,source_id TEXT,source_url TEXT,first_received_at_utc TEXT);
        CREATE TABLE item_versions(logical_key TEXT,version_id INTEGER,version_number INTEGER,
          normalized_json TEXT,published_at_utc TEXT,source_timezone TEXT,time_precision TEXT,
          first_seen_at_utc TEXT,source_status TEXT,raw_id INTEGER);
        CREATE TABLE streams(stream_key TEXT,tool_name TEXT,coverage_status TEXT,last_error TEXT);
        """)
        db.execute("INSERT INTO logical_items VALUES(?,?,?,?,?,?,?)",
                   ("url-1", "list_flash", "CHECKED_URL", 0, None,
                    "https://example.org/1", T0))
        db.execute("INSERT INTO item_versions VALUES(?,?,?,?,?,?,?,?,?,?)",
                   ("url-1", 1, 1, json.dumps({"content": "synthetic text"}), T0,
                    "UTC", "SECOND", T0, "SOURCE_REPORT", 1))
        db.execute("INSERT INTO streams VALUES(?,?,?,?)",
                   ("flash", "list_flash", "CAPTURED_PARTIAL", None))
    first = replay_jin10(source, target, limit=10)
    second = replay_jin10(source, target, limit=10)
    assert first["versions_inserted"] == 1 and first["provider_calls"] == 0
    assert second["versions_inserted"] == 0 and second["duplicates"] == 1
    with Store(target) as store:
        assert store.connection.execute("SELECT COUNT(*) FROM news_items").fetchone()[0] == 1
        assert store.connection.execute("SELECT COUNT(*) FROM news_fetches").fetchone()[0] == 1
        assert store.connection.execute("SELECT COUNT(*) FROM collection_gaps").fetchone()[0] == 1
        audit = store.connection.execute("SELECT source_key_kind,inspected_key_kind FROM news_identity_audits").fetchone()
        assert tuple(audit) == ("CHECKED_URL", "SOURCE_URL")


def test_official_identity_resolution_preserves_requested_candidate(tmp_path: Path) -> None:
    with Store(tmp_path / "ledger.sqlite") as store:
        assert store.seed_candidate_instruments(["NVDA"]) == 1
        assert store.seed_candidate_instruments(["NVDA"]) == 0
        with pytest.raises(ValueError, match="verified official"):
            store.insert_quote(**{**_quote(), "instrument_id": "CANDIDATE:NVDA"})
        kwargs = {"requested_symbol": "NVDA", "verified_id": "US.NVDA",
                  "provider_code": "US.NVDA", "market": "US", "verified_at": T2,
                  "evidence_hash": digest({"official_basicinfo": "synthetic"}),
                  "source_tool": "SYNTHETIC_BASICINFO"}
        store.record_instrument_resolution(**kwargs)
        store.record_instrument_resolution(**kwargs)
        rows = store.connection.execute("SELECT identity_status FROM instruments ORDER BY instrument_id").fetchall()
        assert sorted(row[0] for row in rows) == ["PENDING_OFFICIAL_LOOKUP", "VERIFIED"]
        assert store.connection.execute("SELECT COUNT(*) FROM instrument_resolutions").fetchone()[0] == 1
        with pytest.raises(ValueError, match="conflicts"):
            store.record_instrument_resolution(**{**kwargs, "market": "HK"})


def test_synthetic_moomoo_probe_import_quarantines_imprecise_quote(tmp_path: Path) -> None:
    import hashlib
    import json
    raw = tmp_path / "private_raw.jsonl"
    raw.write_text('{"synthetic":true}\n', encoding="utf-8")
    raw_hash = hashlib.sha256(raw.read_bytes()).hexdigest()
    projection = tmp_path / "private_projection.json"
    projection.write_text(json.dumps({
        "schema": "moomoo_private_probe_projection_v1", "raw_jsonl_path": str(raw),
        "raw_jsonl_sha256": raw_hash,
        "session_time_lower_bound_utc": T1, "session_time_upper_bound_utc": T2,
        "per_call_received_at_utc": None,
        "basicinfo": {"tool": "quote_stock_basicinfo", "ret_code": 0,
                      "rows": [{"requested_code": "US.NVDA", "returned_code": "US.NVDA",
                                "exchange": "US", "verification_status": "VERIFIED_EXACT_CODE_EXCHANGE",
                                "stock_type": "STOCK", "state": "NORMAL", "suspension": False}]},
        "quote": {"tool": "quote_stock_quote", "ret_code": 0,
                  "row": {"code": "US.NVDA", "last_price": 100.0},
                  "provider_data_time_utc": T0,
                  "delay_status": "UNKNOWN_NO_EXPLICIT_FIELD",
                  "market_session": "UNKNOWN_NO_MARKET_STATE_CALL"},
    }), encoding="utf-8")
    db = tmp_path / "observer.sqlite"
    first = import_private_projection(projection, db, ["NVDA"])
    second = import_private_projection(projection, db, ["NVDA"])
    assert first["verified_resolutions_inserted"] == 1
    assert first["probe_snapshots_inserted"] == 1
    assert second["verified_resolutions_inserted"] == 0
    assert second["probe_snapshots_inserted"] == 0
    with Store(db) as store:
        assert store.connection.execute("SELECT COUNT(*) FROM quotes_or_bars").fetchone()[0] == 0
        assert store.connection.execute("SELECT COUNT(*) FROM probe_observations").fetchone()[0] == 1
        assert store.connection.execute("SELECT COUNT(*) FROM instrument_resolutions").fetchone()[0] == 1


def test_reconnect_backoff_waits_full_delay_and_honors_stop(tmp_path: Path) -> None:
    import time
    assert [backoff_seconds(n, 1, 30) for n in range(7)] == [1, 2, 4, 8, 16, 30, 30]
    with Store(tmp_path / "ledger.sqlite") as store:
        store.create_session("backoff", mode="LIVE_PUSH", pid=None, config_hash="synthetic")
        began = time.monotonic()
        assert not interruptible_backoff(store, "backoff", delay_seconds=0.03,
                                         heartbeat_seconds=0.002)
        assert time.monotonic() - began >= 0.025
        store.request_stop("backoff")
        began = time.monotonic()
        assert interruptible_backoff(store, "backoff", delay_seconds=0.1,
                                     heartbeat_seconds=0.002)
        assert time.monotonic() - began < 0.05
