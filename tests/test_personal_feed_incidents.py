"""Sanitized incident records of the collector (2026-11-21). Synthetic: no vendor, no account, no network.

A failed provider loop used to leave only an exception class name. These tests pin that a disconnect, a failed
authorization renewal and a recovery are each traceable (time, provider, phase, reason code, code location), and that
no exception TEXT reaches any of the three sinks (stderr line, stored detail_json, traceback): an exception class
does not make its message safe - OSError.strerror is caller-controlled, and AssertionError, application errors and
hand-built SQLite errors can all wrap supplier content."""
import errno
import json
import os
import sqlite3
import sys
import threading
from unittest.mock import Mock, patch

import pytest

from scripts.market_observer_v1.service import AuthorizationError, QuotaError
from scripts.quant_personal_feed import collector, incidents
from scripts.quant_personal_feed.store import PersonalFeedStore

SECRET = "FAKE0TEST0TOKEN0NOT0A0REAL0KEY0000000001"  # made up for this test; matches no vendor format
PAYLOAD = '{"s2c":{"basicQotList":[{"security":{"code":"NVDA"},"curPrice":142.9,"preMarket":{"price":140.1}}]}}'
HEADLINE = "SYNTHETIC supplier headline and body"
LEAKS = (SECRET, "basicQotList", "curPrice", "142.9", HEADLINE, "user:pw")


def clean(blob: str) -> bool:
    return not any(x in blob for x in LEAKS)


def engine_full_error() -> sqlite3.OperationalError:
    """A real SQLITE_FULL raised by the engine itself (not constructed by hand)."""
    db = sqlite3.connect(":memory:"); db.execute("PRAGMA page_size=512"); db.execute("CREATE TABLE t(x)"); db.execute("PRAGMA max_page_count=4")
    try:
        db.execute("INSERT INTO t VALUES(zeroblob(200000))")
    except sqlite3.OperationalError as e:
        return e
    finally:
        db.close()
    raise AssertionError("the engine did not report a full database")


def raise_from(depth, exc):
    def inner(n):
        local_secret = SECRET            # a local variable must never show up in the record
        if n == 0:
            raise exc
        return inner(n - 1) or local_secret
    try:
        inner(depth)
    except BaseException as e:
        return e


def coded(exc, code):
    exc.sqlite_errorcode = code
    return exc


# ---- the message boundary -------------------------------------------------------------------------------------------

def test_text_sanitizer_removes_credentials_and_url_parameters():
    cases = {
        f"GET https://user:pw@api.example.com/v2/quote?token={SECRET}&symbol=NVDA failed": "GET https://api.example.com/v2/quote?<redacted> failed",
        f"Authorization: Bearer {SECRET}": "Authorization: <redacted> <redacted>",
        f'{{"access_token": "{SECRET}", "expires_in": 3600}}': '{"access_token": "<redacted>", "expires_in": 3600}',
        f"refresh_token={SECRET}; path=/": "refresh_token=<redacted>; path=/",
        f"wss://push.example.com/ws?sign={SECRET}": "wss://push.example.com/ws?<redacted>",
    }
    for raw, expected in cases.items():
        got = incidents.sanitize_text(raw)
        assert SECRET not in got and "user:pw" not in got and got == expected
    assert len(incidents.sanitize_text("x " * 500)) <= incidents.MAX_MESSAGE + 1


@pytest.mark.parametrize("make", [
    lambda: OSError(errno.ENOENT, HEADLINE),                                              # strerror is whatever the caller passed
    lambda: ConnectionResetError(errno.ECONNRESET, f"{HEADLINE} {PAYLOAD}"),
    lambda: ConnectionResetError(f"peer reset; last frame {PAYLOAD}"),                    # no errno at all
    lambda: TimeoutError(HEADLINE),
    lambda: AssertionError(f"{HEADLINE} token={SECRET}"),
    lambda: AuthorizationError(f"refresh failed: {PAYLOAD}"),                              # this program's own class is not a guarantee either
    lambda: AuthorizationError(HEADLINE),
    lambda: type("SyntheticAppError", (Exception,), {"__module__": "scripts.quant_personal_feed.synthetic"})(HEADLINE),
    lambda: sqlite3.OperationalError(HEADLINE),                                           # hand-built, no engine code
    lambda: coded(sqlite3.OperationalError(HEADLINE), sqlite3.SQLITE_FULL),               # a code does not authorize the text
    lambda: RuntimeError(f"{HEADLINE} {PAYLOAD} {SECRET}"),
    lambda: ValueError(HEADLINE),
])
def test_no_exception_text_reaches_the_record_whatever_the_class(make):
    rec = incidents.describe(raise_from(1, make()), provider="moomoo", phase="RECEIVE", recorded_state="DISCONNECTED", attempt=1)
    assert clean(json.dumps(rec)), rec["message"]
    assert rec["error_class"] and rec["reason_code"] and rec["phase"] == "RECEIVE" and rec["traceback"]      # still diagnosable


def test_os_and_sqlite_errors_keep_canonical_structured_information():
    a, b = incidents.safe_message(OSError(errno.ENOENT, HEADLINE)), incidents.safe_message(OSError(errno.ENOENT, "other synthetic text"))
    assert a == b == f"ENOENT: {os.strerror(errno.ENOENT)}"                                # independent of the caller's strerror
    assert incidents.safe_message(OSError(errno.ENOSPC, "x")) == f"ENOSPC: {os.strerror(errno.ENOSPC)}"
    assert incidents.safe_message(ConnectionResetError(errno.ECONNRESET, HEADLINE)).endswith(os.strerror(errno.ECONNRESET))
    assert incidents.safe_message(OSError(HEADLINE)) == "OS_ERROR (no errno; message omitted)" and incidents.safe_message(OSError(10**9, HEADLINE)).startswith("OS_ERROR")
    real = engine_full_error()                                                             # the 2026-11-20 incident, as the engine reports it
    assert real.sqlite_errorcode == sqlite3.SQLITE_FULL and incidents.safe_message(real) == "SQLITE_FULL (code 13)"
    rec = incidents.describe(real, provider="moomoo", phase="STORE_SESSION_QUOTES", recorded_state="DISCONNECTED")
    assert (rec["reason_code"], rec["sqlite_code"], rec["message"]) == ("STORE_SQLITE_DISK_OR_TEMP_FULL", 13, "SQLITE_FULL (code 13)")
    assert incidents.safe_message(sqlite3.OperationalError(HEADLINE)) == "SQLITE_ERROR (no engine code; message omitted)"
    assert incidents.reason_code(sqlite3.OperationalError("database is locked")) == "STORE_SQLITE_UNCODED"   # the text is not consulted, even to classify
    locked = coded(sqlite3.OperationalError("x"), sqlite3.SQLITE_BUSY)
    assert incidents.reason_code(locked) == "STORE_SQLITE_LOCKED" and incidents.safe_message(locked) == "SQLITE_BUSY (code 5)"
    assert incidents.safe_message(coded(sqlite3.OperationalError("x"), 13 | (1 << 8))) == "SQLITE_FULL (code 269)"


def test_reason_codes_separate_store_authorization_quota_network_and_timeout():
    code = incidents.reason_code
    assert code(AuthorizationError("refresh failed")) == "AUTHORIZATION" and code(QuotaError("x")) == "SUBSCRIPTION_QUOTA"
    assert code(TimeoutError()) == "TIMEOUT" and code(ConnectionResetError()) == "NETWORK_CONNECTION_RESET_ERROR"
    assert code(OSError(errno.ENOSPC, "No space left on device")) == "OS_ENOSPC" and code(OSError("x")) == "OS_ERROR" and code(KeyError("k")) == "UNCLASSIFIED_KEYERROR"


def test_the_traceback_holds_locations_only():
    class VendorError(Exception):
        pass
    rec = incidents.describe(raise_from(2, VendorError(f"push rejected: {PAYLOAD} token={SECRET}")), provider="moomoo", phase="RECEIVE", recorded_state="DISCONNECTED")
    blob = json.dumps(rec)
    assert clean(blob) and rec["message"] == incidents.OMITTED and rec["error_class"] == "VendorError" and rec["reason_code"] == "UNCLASSIFIED_VENDORERROR"
    assert len(rec["traceback"]) == 4 and rec["traceback"][-1].startswith("tests/test_personal_feed_incidents.py:") and rec["traceback"][-1].endswith(" in inner")
    assert "local_secret" not in blob and "raise exc" not in blob                           # no source line, no local variable


# ---- recording can never become a failure of its own ---------------------------------------------------------------

class BadString(Exception):
    def __str__(self):
        raise RuntimeError("SYNTHETIC stringification failed")

    __repr__ = __str__


def test_a_broken_exception_text_is_never_invoked_and_cannot_break_recording(capsys):
    assert isinstance(incidents.safe_message(BadString()), str)
    rec = incidents.record(raise_from(0, BadString()), None, provider="moomoo", phase="RECEIVE", recorded_state="DISCONNECTED")
    assert rec["error_class"] == "BadString" and rec["message"] == incidents.OMITTED and "personal_feed {" in capsys.readouterr().err


def test_a_failing_log_stream_or_store_or_describe_does_not_reach_the_caller(monkeypatch, capsys):
    exc = raise_from(0, RuntimeError(HEADLINE))
    broken_store = Mock(); broken_store.record_incident.side_effect = sqlite3.OperationalError("database or disk is full")
    assert incidents.record(exc, broken_store, provider="moomoo", phase="NOT_A_PHASE", recorded_state="DISCONNECTED")["phase"] == "THREAD"
    assert broken_store.record_incident.call_count == 1                                     # tried, failed, swallowed

    class Closed:
        def write(self, *_):
            raise ValueError("I/O operation on closed file")

        def flush(self):
            raise ValueError("closed")
    ok_store = Mock()
    monkeypatch.setattr(sys, "stderr", Closed())
    rec = incidents.record(exc, ok_store, provider="moomoo", phase="RECEIVE", recorded_state="DISCONNECTED")   # the log line fails ...
    monkeypatch.undo()
    assert rec and ok_store.record_incident.call_count == 1                                 # ... the stored row is still written
    monkeypatch.setattr(incidents.json, "dumps", Mock(side_effect=TypeError("not serializable")))
    assert incidents.record(exc, ok_store, provider="moomoo", phase="RECEIVE", recorded_state="DISCONNECTED")  # formatting fails, the row still goes in
    monkeypatch.undo()
    assert ok_store.record_incident.call_count == 2
    monkeypatch.setattr(incidents, "describe", Mock(side_effect=RuntimeError("describe broke")))
    assert incidents.record(exc, ok_store, provider="moomoo", phase="RECEIVE", recorded_state="DISCONNECTED") is None
    monkeypatch.undo()
    err = capsys.readouterr().err
    assert "RECORDING_FAILED" in err and clean(err)


# ---- the collector loop ---------------------------------------------------------------------------------------------

def harness(tmp_path, adapters, stop_after):
    """run_moomoo against a real store file and scripted adapters; returns the store for inspection."""
    stop = threading.Event(); store = PersonalFeedStore(tmp_path / "feed.sqlite3", owner_id=1)
    live = {"subscriptions": [{"requested_symbol": "TQQQ", "provider_code": "US.TQQQ"}]}
    config = {"cache_db": "ignored", "moomoo": {"max_reconnect_attempts": 5, "priority_symbols": ["TQQQ"], "status_path": "ignored",
                                               "reconnect_initial_seconds": 0, "reconnect_max_seconds": 0}, "retention": {"quotes_per_symbol": 10}}
    made = iter(adapters)
    store.close = Mock()                                                                       # keep it open for the assertions
    with patch.object(collector, "PersonalFeedStore", return_value=store), patch.object(collector, "verified_subscriptions", return_value=(live, [], {})), \
         patch.object(collector, "MoomooWebSocketPush", side_effect=lambda **k: next(made)), patch.object(collector, "token_store", return_value=Mock()), \
         patch.object(collector, "under_root", side_effect=lambda p: tmp_path / "x"):
        stop_after(stop)
        collector.run_moomoo(config, 1, stop)
    return store


def adapter(connect=None, messages=()):
    a = Mock(); a._ever_acknowledged = [{"provider_code": "US.TQQQ"}]; a._skipped = []; a._market_state = {"US": 2}; a.connection_state = "CONNECTED_DELAY_UNKNOWN"
    if connect is not None:
        a.connect.side_effect = connect
    seq = list(messages)

    def next_message(**_):
        item = seq.pop(0) if seq else None
        if isinstance(item, BaseException):
            raise item
        if callable(item):
            return item()
        return item
    a.next_message.side_effect = next_message
    return a


def all_sinks(store, capsys) -> str:
    """Everything the run wrote: stderr lines, stored detail_json, and the state table's error column."""
    stored = " ".join(r[0] for r in store.db.execute("SELECT detail_json FROM provider_incidents"))
    states = " ".join(str(r[0]) for r in store.db.execute("SELECT last_error_class FROM provider_state"))
    return capsys.readouterr().err + stored + states


def test_a_disconnect_and_the_recovery_after_it_are_both_traceable_and_carry_no_text(tmp_path, capsys):
    stop_holder = {}
    drop = ConnectionResetError(errno.ECONNRESET, f"peer reset; last frame {PAYLOAD} {HEADLINE}")
    broken_connect = adapter(connect=TimeoutError(f"handshake timed out {HEADLINE}"))
    drops_while_receiving = adapter(messages=[None, drop])
    healthy = adapter(messages=[lambda: stop_holder["stop"].set()])
    store = harness(tmp_path, [broken_connect, drops_while_receiving, healthy], lambda stop: stop_holder.setdefault("stop", stop))
    rows = list(reversed(store.incidents("moomoo")))
    assert [(r["kind"], r["phase"], r["reason_code"]) for r in rows] == [
        ("INCIDENT", "AUTHORIZE_AND_CONNECT", "TIMEOUT"),                                      # could not connect
        ("RECOVERED", "AUTHORIZE_AND_CONNECT", "RECOVERED"),                                   # connected on the next attempt
        ("INCIDENT", "RECEIVE", "NETWORK_CONNECTION_RESET_ERROR"),                             # dropped while receiving
        ("RECOVERED", "AUTHORIZE_AND_CONNECT", "RECOVERED")]
    assert rows[0]["recorded_state"] == "DISCONNECTED" and rows[0]["traceback"] and rows[0]["traceback"][-1].split(":")[0].endswith(".py")
    assert rows[1]["first_failure_at_utc"] == rows[0]["at_utc"] and rows[1]["outage_seconds"] >= 0 and rows[1]["attempt"] == 1
    assert rows[2]["at_utc"] >= rows[1]["at_utc"] and rows[2]["os_errno"] == errno.ECONNRESET
    assert rows[2]["message"] == f"{errno.errorcode[errno.ECONNRESET]}: {os.strerror(errno.ECONNRESET)}"   # the platform's own name and text for that number
    blob = all_sinks(store, capsys)
    assert blob.count('"kind": "INCIDENT"') >= 4 and "personal_feed {" in blob and clean(blob) # stderr line and stored row for each incident, no text in either
    states = [r[0] for r in store.db.execute("SELECT state FROM provider_state_events WHERE provider='moomoo' ORDER BY id")]
    assert states == ["CONNECTING", "DISCONNECTED", "CONNECTING", "CONNECTED_DELAY_UNKNOWN", "DISCONNECTED", "CONNECTING", "CONNECTED_DELAY_UNKNOWN",
                      "DISCONNECTED"]                                                    # two failures, two recoveries, then the test's own stop
    store.db.close()


def test_a_failed_authorization_renewal_is_recorded_with_its_phase_and_stops_the_loop(tmp_path, capsys):
    renewal = AuthorizationError(f"token refresh rejected: {PAYLOAD}")
    a = adapter(messages=[None, renewal])                                                     # connected, then the renewal fails while receiving
    store = harness(tmp_path, [a], lambda stop: None)
    rows = store.incidents("moomoo")
    assert len(rows) == 1 and (rows[0]["phase"], rows[0]["reason_code"], rows[0]["recorded_state"], rows[0]["error_class"]) == ("RECEIVE", "AUTHORIZATION", "AUTH_EXPIRED", "AuthorizationError")
    assert rows[0]["message"] == incidents.OMITTED and rows[0]["traceback"]                    # located by phase, code and traceback, not by free text
    assert store.db.execute("SELECT state,last_error_class FROM provider_state WHERE provider='moomoo'").fetchone()[:] == ("AUTH_EXPIRED", "AUTHORIZATION")
    assert clean(all_sinks(store, capsys))
    (tmp_path / "b").mkdir()
    at_connect = harness(tmp_path / "b", [adapter(connect=AuthorizationError("no valid token"))], lambda stop: None)
    assert at_connect.incidents("moomoo")[0]["phase"] == "AUTHORIZE_AND_CONNECT"
    store.db.close(); at_connect.db.close()


def test_a_store_failure_is_attributed_to_the_store_phase_and_the_table_is_capped(tmp_path, capsys):
    quote = {"kind": "QUOTE", "quote": {"instrument_id": "US.TQQQ", "provider_time": "2026-11-17T20:00:00Z", "received_at": "2026-11-17T20:00:01Z",
                                         "payload": {"price": "10.00", "provider_data": {"pre_market": {"price": "10.00"}}}}}
    stop_holder = {}
    a = adapter(messages=[quote]); b = adapter(messages=[lambda: stop_holder["stop"].set()])
    with patch.object(collector, "parse_push_children", side_effect=engine_full_error()):
        store = harness(tmp_path, [a, b], lambda stop: stop_holder.setdefault("stop", stop))
    first = store.incidents("moomoo")[-1]
    assert (first["phase"], first["reason_code"], first["message"], first["sqlite_code"]) == ("STORE_SESSION_QUOTES", "STORE_SQLITE_DISK_OR_TEMP_FULL", "SQLITE_FULL (code 13)", 13)
    for i in range(store.INCIDENT_ROWS_KEPT + 40):                                            # a failure loop cannot grow the table without bound
        store.record_incident({**first, "at_utc": f"2026-11-21T00:00:{i % 60:02d}Z"})
    assert store.db.execute("SELECT COUNT(*) FROM provider_incidents").fetchone()[0] == store.INCIDENT_ROWS_KEPT
    store.db.close()


def test_a_recorder_that_cannot_write_does_not_disconnect_the_feed(tmp_path, capsys):
    """The chain end to end: the incident table is unusable AND the log line cannot be formatted; the loop must still
    reconnect and go on to receive, and the provider state must end as connected, not as a second failure."""
    stop_holder = {}
    broken_connect = adapter(connect=TimeoutError(HEADLINE)); healthy = adapter(messages=[None, lambda: stop_holder["stop"].set()])
    with patch.object(PersonalFeedStore, "record_incident", side_effect=sqlite3.OperationalError("database or disk is full")), \
         patch.object(incidents.json, "dumps", side_effect=TypeError("cannot serialize")):
        store = harness(tmp_path, [broken_connect, healthy], lambda stop: stop_holder.setdefault("stop", stop))
    states = [r[0] for r in store.db.execute("SELECT state FROM provider_state_events WHERE provider='moomoo' ORDER BY id")]
    assert states[:4] == ["CONNECTING", "DISCONNECTED", "CONNECTING", "CONNECTED_DELAY_UNKNOWN"] and healthy.next_message.call_count == 2
    assert store.db.execute("SELECT COUNT(*) FROM provider_incidents").fetchone()[0] == 0     # nothing could be stored, and nothing broke
    assert clean(capsys.readouterr().err)
    store.db.close()
