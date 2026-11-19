"""PersonalFeedStore first-open migration under real concurrency (synthetic data only).

On a cloud first start the web container (gunicorn, several workers and threads),
the feed process and one-off ops commands all open the same personal_feeds.sqlite3
on a shared volume. Each opener runs _initialize(). These tests start several
processes and threads on one barrier against a database that does not exist yet
(no pre-built fixture), then check the final schema, data and business threads.
"""
from __future__ import annotations

import multiprocessing as mp
import sqlite3
import threading
from contextlib import closing
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError

import pytest

from scripts.quant_personal_feed.store import PersonalFeedStore

PROCESSES, THREADS_PER_PROCESS, TRIALS = 4, 2, 12


def _open_many(path: str, barrier, results, threads: int) -> None:
    local = threading.Barrier(threads)

    def one() -> None:
        local.wait()
        try:
            PersonalFeedStore(Path(path), 1).close()
            results.put("ok")
        except Exception as exc:  # report, do not hide
            results.put(f"{type(exc).__name__}: {exc}")

    barrier.wait()
    workers = [threading.Thread(target=one) for _ in range(threads)]
    for t in workers:
        t.start()
    for t in workers:
        t.join()


def _concurrent_open(path: Path) -> list[str]:
    ctx = mp.get_context("spawn")
    barrier, results = ctx.Barrier(PROCESSES), ctx.Queue()
    procs = [ctx.Process(target=_open_many, args=(str(path), barrier, results, THREADS_PER_PROCESS))
             for _ in range(PROCESSES)]
    for p in procs:
        p.start()
    for p in procs:
        p.join(60)
        assert p.exitcode == 0
    return [results.get(timeout=5) for _ in range(PROCESSES * THREADS_PER_PROCESS)]


def _schema(path: Path) -> dict:
    with closing(sqlite3.connect(path)) as con:
        objects = sorted(con.execute("SELECT type,name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"))
        columns = {t: [r[1] for r in con.execute(f"PRAGMA table_info({t})")]
                   for (kind, t, _sql) in objects if kind == "table"}
    return {"objects": [(k, n) for k, n, _ in objects], "columns": columns}


@pytest.fixture(scope="module")
def reference(tmp_path_factory) -> dict:
    path = tmp_path_factory.mktemp("ref") / "feed.sqlite3"
    PersonalFeedStore(path, 1).close()
    return _schema(path)


def test_empty_database_concurrent_first_open(tmp_path: Path, reference: dict) -> None:
    failures = []
    for trial in range(TRIALS):
        path = tmp_path / f"trial{trial}" / "feed.sqlite3"
        path.parent.mkdir()
        assert not path.exists()
        outcome = _concurrent_open(path)
        failures += [o for o in outcome if o != "ok"]
        assert _schema(path) == reference
        with closing(sqlite3.connect(path)) as con:
            assert con.execute("SELECT count(*), max(owner_id) FROM owner_binding").fetchone() == (1, 1)
    assert failures == []


LEGACY = """
CREATE TABLE provider_state (
  provider TEXT PRIMARY KEY, state TEXT NOT NULL, heartbeat_at TEXT NOT NULL,
  last_success_at TEXT, last_received_at TEXT, last_source_at TEXT,
  last_error_class TEXT, requests_total INTEGER NOT NULL DEFAULT 0,
  business_calls_today INTEGER NOT NULL DEFAULT 0, business_day_utc TEXT);
CREATE TABLE session_quotes (
  id INTEGER PRIMARY KEY, owner_id INTEGER NOT NULL, symbol TEXT NOT NULL,
  provider_code TEXT NOT NULL, session TEXT NOT NULL, price_text TEXT NOT NULL,
  currency TEXT, source_at TEXT, received_at TEXT NOT NULL,
  provider_field_path TEXT NOT NULL, trade_date TEXT, date_basis TEXT NOT NULL,
  parent_date_hint TEXT, delay_status TEXT NOT NULL,
  source_channel TEXT NOT NULL, raw_sha256 TEXT NOT NULL);
CREATE TABLE owner_binding (singleton INTEGER PRIMARY KEY CHECK(singleton=1), owner_id INTEGER NOT NULL);
INSERT INTO owner_binding VALUES (1, 1);
INSERT INTO provider_state(provider,state,heartbeat_at,last_error_class,requests_total,business_calls_today,business_day_utc)
  VALUES ('jin10','CONNECTED','2026-11-18T00:00:00Z',NULL,7,3,'2099-01-01');
INSERT INTO session_quotes(id,owner_id,symbol,provider_code,session,price_text,source_at,received_at,
  provider_field_path,date_basis,delay_status,source_channel,raw_sha256)
  VALUES (1,1,'SYN','US.SYN','OVERNIGHT','1.00','2026-11-18T00:00:00Z','2026-11-18T00:00:01Z','p','SYNTHETIC','UNKNOWN','WS','raw1');
"""


def test_existing_database_concurrent_upgrade(tmp_path: Path, reference: dict) -> None:
    failures = []
    for trial in range(TRIALS):
        path = tmp_path / f"legacy{trial}.sqlite3"
        with closing(sqlite3.connect(path)) as con:
            con.executescript(LEGACY)
        failures += [o for o in _concurrent_open(path) if o != "ok"]
        schema = _schema(path)
        for table in ("provider_state", "session_quotes"):
            assert set(schema["columns"][table]) == set(reference["columns"][table])
        assert set(schema["objects"]) == set(reference["objects"])
        with closing(sqlite3.connect(path)) as con:
            assert con.execute("SELECT state,requests_total,business_calls_today FROM provider_state").fetchall() == [("CONNECTED", 7, 3)]
            assert con.execute("SELECT id,symbol,price_text,raw_sha256,parent_source_at_hint FROM session_quotes").fetchall() == [(1, "SYN", "1.00", "raw1", None)]
    assert failures == []


def test_restart_after_initialisation_changes_nothing(tmp_path: Path, reference: dict) -> None:
    path = tmp_path / "feed.sqlite3"
    assert _concurrent_open(path) == ["ok"] * PROCESSES * THREADS_PER_PROCESS
    store = PersonalFeedStore(path, 1)
    store.state("jin10", "CONNECTED", success=True)
    store.close()
    with closing(sqlite3.connect(path)) as con:
        before = con.execute("SELECT * FROM provider_state").fetchall()
    assert _concurrent_open(path) == ["ok"] * PROCESSES * THREADS_PER_PROCESS
    assert _schema(path) == reference
    with closing(sqlite3.connect(path)) as con:
        assert con.execute("SELECT * FROM provider_state").fetchall() == before


def test_real_initialisation_errors_still_raise(tmp_path: Path) -> None:
    path = tmp_path / "feed.sqlite3"
    PersonalFeedStore(path, 1).close()
    with pytest.raises(ValueError, match="different owner"):
        PersonalFeedStore(path, 2)
    junk = tmp_path / "junk.sqlite3"
    junk.write_bytes(b"not a database" * 100)
    with pytest.raises(sqlite3.DatabaseError):
        PersonalFeedStore(junk, 1)


def test_a_lock_that_never_clears_is_still_reported(tmp_path: Path) -> None:
    import time
    path = tmp_path / "held.sqlite3"
    holder = sqlite3.connect(path, isolation_level=None)
    holder.execute("BEGIN EXCLUSIVE")  # rollback-journal file, never released during the open
    try:
        started = time.monotonic()
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            PersonalFeedStore(path, 1)
        assert 9 <= time.monotonic() - started < 40  # bounded wait, then the error surfaces
    finally:
        holder.execute("ROLLBACK")
        holder.close()


def test_both_collector_threads_on_a_brand_new_cache(tmp_path: Path) -> None:
    """The real thread entry points race on a cache that does not exist yet."""
    from scripts.quant_personal_feed import collector
    from tests.test_collector_refresh_transient import FakeSession, _config, _row, _run_moomoo

    assert not (tmp_path / "feed.sqlite3").exists()
    stop, jin10_ok = threading.Event(), threading.Event()
    real_state = collector.PersonalFeedStore.state

    def watch_state(self, provider, state, **kw):
        real_state(self, provider, state, **kw)
        if provider == "jin10" and kw.get("success"):
            jin10_ok.set()

    jin10_error: list[BaseException] = []

    def jin10() -> None:
        try:
            collector.run_jin10(_config(tmp_path), 1, stop)
        except BaseException as exc:  # surfaced below
            jin10_error.append(exc)

    with patch.object(collector, "under_root", lambda rel: tmp_path / rel), \
         patch.object(collector, "_jin10_session", lambda cfg, store: (FakeSession(), {})), \
         patch.object(collector.PersonalFeedStore, "state", watch_state):
        thread = threading.Thread(target=jin10, daemon=True)
        thread.start()
        row, calls = _run_moomoo(tmp_path, URLError("network down"))
        ok = jin10_ok.wait(20)
        stop.set()
        thread.join(20)
    assert jin10_error == [] and ok
    assert calls == 3 and row["state"] == "DISCONNECTED" and row["last_error_class"] == "ConnectionError"
    j = _row(tmp_path / "feed.sqlite3", "jin10")
    assert j["business_success_total"] >= 1 and j["last_error_class"] == "USER_STOP"
