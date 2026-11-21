"""Raw-response retention of the personal feed store (2026-11-21 incident).

On the cloud host the old retention statement ranked every session quote with a window function; SQLite spilled the
sort to a 64 MB tmpfs, raised "database or disk is full" right after each reconnect, and the collector reported the
provider as DISCONNECTED in a loop. These tests pin: the same rows are selected as before; a retention failure does
not escape `put_session_quotes`; the helper index is built by the pruner only."""
import hashlib
import random
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from scripts.quant_personal_feed.store import PersonalFeedStore

OLD_SQL = """WITH latest AS (
  SELECT raw_sha256, ROW_NUMBER() OVER (
    PARTITION BY owner_id,symbol,session
    ORDER BY (source_at IS NOT NULL) DESC,source_at DESC,
      parent_source_at_hint DESC,received_at DESC,id DESC
  ) AS rank_in_cell FROM session_quotes
), referenced AS (
  SELECT raw_sha256 FROM session_quotes WHERE received_at>=?
  UNION SELECT raw_sha256 FROM latest WHERE rank_in_cell=1
)
SELECT sha256 FROM session_raw_responses WHERE sha256 NOT IN (
  SELECT raw_sha256 FROM referenced
) ORDER BY received_at DESC,sha256 DESC LIMIT 10000 OFFSET ?"""


def iso(d):
    return d.isoformat().replace("+00:00", "Z")


def fill(store, n=3000, seed=7, hours=72):
    """Made-up rows written straight into the tables: old and recent quotes, shared and unreferenced raw responses,
    quotes without a source time, and a cell whose latest quote is old."""
    rnd = random.Random(seed); now = datetime.now(timezone.utc); raws = []
    for i in range(n):
        at = now - timedelta(hours=hours) + timedelta(seconds=i * hours * 3600 / n)
        sha = hashlib.sha256(f"raw{i}".encode()).hexdigest(); raws.append((sha, at))
        store.db.execute("INSERT INTO session_raw_responses(sha256,received_at,source_channel,raw_json) VALUES(?,?,?,?)", (sha, iso(at), "WS_QUOTE", "{}"))
        if rnd.random() < 0.25:
            continue                                                   # a raw response no quote refers to
        for _ in range(rnd.choice((1, 1, 2))):
            ref_sha, _ref_at = (sha, at) if rnd.random() < 0.9 else rnd.choice(raws)   # sometimes a quote refers to an older raw response
            sym = rnd.choice(("AAA", "BBB", "CCC")); sess = rnd.choice(("PREMARKET", "REGULAR", "AFTERHOURS", "OVERNIGHT"))
            src = None if rnd.random() < 0.1 else iso(at - timedelta(seconds=rnd.randint(0, 5)))
            store.db.execute("""INSERT INTO session_quotes(owner_id,symbol,provider_code,session,price_text,currency,source_at,received_at,
              provider_field_path,source_time_field_path,trade_date,date_basis,parent_date_hint,parent_source_at_hint,delay_status,source_channel,raw_sha256)
              VALUES(1,?,?,?,?,NULL,?,?,'p',NULL,NULL,'NONE',NULL,?,'UNKNOWN','WS_QUOTE',?)""",
                             (sym, "US." + sym, sess, str(rnd.randint(1, 99)), src, iso(at), src, ref_sha))
    old = now - timedelta(hours=hours - 1); sha = hashlib.sha256(b"lonely").hexdigest()        # a cell seen once, long ago: its raw response must stay
    store.db.execute("INSERT INTO session_raw_responses(sha256,received_at,source_channel,raw_json) VALUES(?,?,?,?)", (sha, iso(old), "WS_QUOTE", "{}"))
    store.db.execute("""INSERT INTO session_quotes(owner_id,symbol,provider_code,session,price_text,currency,source_at,received_at,provider_field_path,
      source_time_field_path,trade_date,date_basis,parent_date_hint,parent_source_at_hint,delay_status,source_channel,raw_sha256)
      VALUES(1,'ZZZ','US.ZZZ','REGULAR','5',NULL,?,?,'p',NULL,NULL,'NONE',NULL,NULL,'UNKNOWN','WS_QUOTE',?)""", (iso(old), iso(old), sha))
    return sha


@pytest.mark.parametrize("seed, extra", [(7, 100), (11, 0), (23, 37)])
def test_the_new_selection_returns_exactly_the_rows_of_the_old_window_statement(tmp_path, seed, extra):
    store = PersonalFeedStore(tmp_path / "feed.sqlite3", owner_id=1)
    try:
        store.raw_response_extra_count = extra; lonely = fill(store, seed=seed)
        cutoff = iso(datetime.now(timezone.utc) - timedelta(hours=store.raw_response_recent_hours))
        expected = [r[0] for r in store.db.execute(OLD_SQL, (cutoff, extra))]
        got = store._stale_raw_hashes()
        assert got == expected and len(got) > 100 and lonely not in got                 # same hashes, same order
        before = store.db.execute("SELECT COUNT(*) FROM session_raw_responses").fetchone()[0]
        store._prune_raw_responses()
        assert store.db.execute("SELECT COUNT(*) FROM session_raw_responses").fetchone()[0] == before - len(expected)
        assert store.db.execute("SELECT COUNT(*) FROM session_quotes q LEFT JOIN session_raw_responses r ON r.sha256=q.raw_sha256 WHERE q.received_at>=? AND r.sha256 IS NULL",
                                (cutoff,)).fetchone()[0] == 0                            # every recent quote still has its evidence
        assert store._stale_raw_hashes() == []                                           # nothing left beyond the kept extras
        plan = " ".join(r[3] for r in store.db.execute("EXPLAIN QUERY PLAN SELECT raw_sha256 FROM session_quotes WHERE owner_id=1 AND symbol='AAA' AND session='REGULAR' "
                                                      "ORDER BY (source_at IS NOT NULL) DESC,source_at DESC,parent_source_at_hint DESC,received_at DESC,id DESC LIMIT 1"))
        assert "session_quotes" in plan
    finally:
        store.close()


def row(received):
    return {"symbol": "AAA", "provider_code": "US.AAA", "session": "REGULAR", "price_text": "10", "currency": None, "source_at": received, "received_at": received,
            "provider_field_path": "p", "source_time_field_path": None, "trade_date": None, "date_basis": "NONE", "parent_date_hint": None, "parent_source_at_hint": None,
            "delay_status": "UNKNOWN", "raw_sha256": hashlib.sha256(b"{}").hexdigest()}


def test_a_retention_failure_does_not_escape_the_quote_write_and_is_retried_later(tmp_path, capsys):
    store = PersonalFeedStore(tmp_path / "feed.sqlite3", owner_id=1)
    try:
        calls = []

        def full_disk():
            calls.append(1); exc = sqlite3.OperationalError("database or disk is full"); exc.sqlite_errorcode = 13   # what the engine sets (SQLITE_FULL)
            raise exc
        store._prune_raw_responses = full_disk
        now = iso(datetime.now(timezone.utc))
        out = store.put_session_quotes(1, [row(now)], states={"AAA": {"REGULAR": "HAS_SOURCE_TIME"}}, checked_at=now, raw_json="{}",
                                       raw_sha256=hashlib.sha256(b"{}").hexdigest(), source_channel="WS_QUOTE")
        assert out == {"inserted": 1, "duplicate": 0} and calls == [1]                   # before the fix this raised and the collector marked the provider DISCONNECTED
        # the record names the class and the engine code; the exception text itself is no longer written anywhere
        assert store.raw_retention_last_error == "OperationalError: SQLITE_FULL (code 13)" and not store.db.in_transaction
        assert "raw retention failed" in capsys.readouterr().err
        assert store.db.execute("SELECT COUNT(*) FROM session_quotes").fetchone()[0] == 1
        store.put_session_quotes(1, [row(now)], states={}, checked_at=now, source_channel="WS_QUOTE")
        assert calls == [1]                                                              # not retried on every write: next attempt after the five-minute interval
        store._next_raw_retention_monotonic = 0.0; del store._prune_raw_responses        # the condition clears: the next run succeeds and the error is cleared
        store.put_session_quotes(1, [row(now)], states={}, checked_at=now, source_channel="WS_QUOTE")
        assert store.raw_retention_last_error is None
    finally:
        store.close()


def test_a_non_database_error_in_retention_is_not_swallowed(tmp_path):
    store = PersonalFeedStore(tmp_path / "feed.sqlite3", owner_id=1)
    try:
        def boom():
            raise ValueError("a programming error must stay visible")
        store._prune_raw_responses = boom; now = iso(datetime.now(timezone.utc))
        with pytest.raises(ValueError):
            store.put_session_quotes(1, [], states={}, checked_at=now, source_channel="WS_QUOTE")
    finally:
        store.close()


def test_the_helper_index_is_built_by_the_pruner_not_by_every_opener(tmp_path):
    path = tmp_path / "feed.sqlite3"; has = lambda s: bool(s.db.execute("SELECT 1 FROM sqlite_master WHERE name='session_quotes_raw_ref'").fetchone())
    store = PersonalFeedStore(path, owner_id=1)
    try:
        assert not has(store)                                                            # opening (what web workers do) builds nothing
        temp_before = store.db.execute("PRAGMA temp_store").fetchone()[0]
        assert store._stale_raw_hashes() == [] and has(store)
        assert store.db.execute("PRAGMA temp_store").fetchone()[0] == temp_before        # the in-memory sort setting is restored
    finally:
        store.close()
    again = PersonalFeedStore(path, owner_id=1)
    try:
        assert has(again) and again._stale_raw_hashes() == []
    finally:
        again.close()
