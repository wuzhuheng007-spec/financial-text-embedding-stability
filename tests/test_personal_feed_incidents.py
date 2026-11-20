"""Sanitized incident records of the collector (2026-11-21). Synthetic: no vendor, no account, no network.

A failed provider loop used to leave only an exception class name. These tests pin that a disconnect, a failed
authorization renewal and a recovery are each traceable (time, provider, phase, reason code, code location), and that
no credential, parameterised URL or supplier payload can reach the record."""
import json
import sqlite3
import threading
from unittest.mock import Mock, patch

import pytest

from scripts.market_observer_v1.service import AuthorizationError, QuotaError
from scripts.quant_personal_feed import collector, incidents
from scripts.quant_personal_feed.store import PersonalFeedStore

SECRET = "FAKE0TEST0TOKEN0NOT0A0REAL0KEY0000000001"  # made up for this test; matches no vendor format
PAYLOAD = '{"s2c":{"basicQotList":[{"security":{"code":"NVDA"},"curPrice":142.9,"preMarket":{"price":140.1}}]}}'


def test_text_sanitizer_removes_credentials_and_url_parameters():
    cases = {
        f"GET https://user:pw@api.example.com/v2/quote?token={SECRET}&symbol=NVDA failed": "GET https://api.example.com/v2/quote?<redacted> failed",
        f"Authorization: Bearer {SECRET}": "Authorization: <redacted> <redacted>",
        f'{{"access_token": "{SECRET}", "expires_in": 3600}}': '{"access_token": "<redacted>", "expires_in": 3600}',
        f"refresh_token={SECRET}; path=/": "refresh_token=<redacted>; path=/",
        f"wss://push.example.com/ws?sign={SECRET}": "wss://push.example.com/ws?<redacted>",
        "database or disk is full": "database or disk is full",
    }
    for raw, expected in cases.items():
        got = incidents.sanitize_text(raw)
        assert SECRET not in got and "user:pw" not in got and got == expected
    assert len(incidents.sanitize_text("x " * 500)) <= incidents.MAX_MESSAGE + 1


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


def test_a_third_party_message_is_dropped_and_the_traceback_holds_locations_only():
    class VendorError(Exception):
        pass
    exc = raise_from(2, VendorError(f"push rejected: {PAYLOAD} token={SECRET}"))
    r = incidents.describe(exc, provider="moomoo", phase="RECEIVE", recorded_state="DISCONNECTED", attempt=1)
    blob = json.dumps(r)
    assert SECRET not in blob and "basicQotList" not in blob and "142.9" not in blob          # no credential, no supplier payload
    assert r["message"].startswith("<omitted: ") and r["error_class"] == "VendorError" and r["reason_code"] == "UNCLASSIFIED_VENDORERROR"
    assert r["phase"] == "RECEIVE" and r["at_utc"].endswith("Z") and r["provider"] == "moomoo"
    assert len(r["traceback"]) == 4 and all(": " not in t.split(" in ")[0].split(":", 1)[1] for t in r["traceback"])
    assert r["traceback"][-1].startswith("tests/test_personal_feed_incidents.py:") and r["traceback"][-1].endswith(" in inner")
    assert "local_secret" not in blob and "raise exc" not in blob                             # no source line, no local variable


def test_reason_codes_separate_store_authorization_quota_network_and_timeout():
    code = lambda e: incidents.reason_code(e)
    assert code(sqlite3.OperationalError("database or disk is full")) == "STORE_SQLITE_DISK_OR_TEMP_FULL"       # the 2026-11-20 incident
    assert code(sqlite3.OperationalError("database is locked")) == "STORE_SQLITE_LOCKED"
    assert code(AuthorizationError("refresh failed")) == "AUTHORIZATION" and code(QuotaError("x")) == "SUBSCRIPTION_QUOTA"
    assert code(TimeoutError()) == "TIMEOUT" and code(ConnectionResetError()) == "NETWORK_CONNECTION_RESET_ERROR"
    assert code(OSError(28, "No space left on device")) == "OS_ENOSPC" and code(KeyError("k")) == "UNCLASSIFIED_KEYERROR"
    own = incidents.describe(raise_from(0, sqlite3.OperationalError("database or disk is full")), provider="moomoo", phase="STORE_SESSION_QUOTES", recorded_state="DISCONNECTED")
    assert own["message"] == "database or disk is full"                                       # SQLite's own text is safe and is the useful part


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


def test_a_disconnect_and_the_recovery_after_it_are_both_traceable(tmp_path, capsys):
    stop_holder = {}
    drop = ConnectionResetError(f"peer reset; last frame {PAYLOAD}")                           # an OS error class whose text we still sanitize and cut
    broken_connect = adapter(connect=TimeoutError("handshake timed out"))
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
    assert rows[2]["at_utc"] >= rows[1]["at_utc"]
    blob = json.dumps(rows) + capsys.readouterr().err
    assert "personal_feed {" in blob and blob.count('"kind": "INCIDENT"') >= 4                # stderr line and stored row, for each incident
    assert SECRET not in blob and "curPrice" not in blob and "basicQotList" not in blob       # the frame text inside the OS error is not kept
    assert rows[2]["message"].startswith("<omitted: ") and rows[0]["message"].startswith("<omitted: ")
    full_disk = incidents.describe(OSError(28, "No space left on device"), provider="moomoo", phase="STORE_QUOTE", recorded_state="DISCONNECTED")
    assert full_disk["message"] == "No space left on device" and full_disk["reason_code"] == "OS_ENOSPC"   # the errno text is kept
    structured = incidents.describe(AuthorizationError(f"refresh failed: {PAYLOAD}"), provider="moomoo", phase="RECEIVE", recorded_state="AUTH_EXPIRED")
    assert structured["message"].startswith("<omitted: ")                                     # even our own class, if it wraps a response body
    states = [r[0] for r in store.db.execute("SELECT state FROM provider_state_events WHERE provider='moomoo' ORDER BY id")]
    assert states == ["CONNECTING", "DISCONNECTED", "CONNECTING", "CONNECTED_DELAY_UNKNOWN", "DISCONNECTED", "CONNECTING", "CONNECTED_DELAY_UNKNOWN",
                      "DISCONNECTED"]                                                    # two failures, two recoveries, then the test's own stop
    store.db.close()


def test_a_failed_authorization_renewal_is_recorded_with_its_phase_and_stops_the_loop(tmp_path):
    renewal = AuthorizationError("token refresh rejected")
    a = adapter(messages=[None, renewal])                                                     # connected, then the renewal fails while receiving
    store = harness(tmp_path, [a], lambda stop: None)
    rows = store.incidents("moomoo")
    assert len(rows) == 1 and (rows[0]["phase"], rows[0]["reason_code"], rows[0]["recorded_state"]) == ("RECEIVE", "AUTHORIZATION", "AUTH_EXPIRED")
    assert rows[0]["message"] == "token refresh rejected"                                     # our own exception class: text kept (sanitized)
    assert store.db.execute("SELECT state,last_error_class FROM provider_state WHERE provider='moomoo'").fetchone()[:] == ("AUTH_EXPIRED", "AUTHORIZATION")
    at_connect = harness(tmp_path / "b" if (tmp_path / "b").mkdir() is None else tmp_path, [adapter(connect=AuthorizationError("no valid token"))], lambda stop: None)
    assert at_connect.incidents("moomoo")[0]["phase"] == "AUTHORIZE_AND_CONNECT"
    store.db.close(); at_connect.db.close()


def test_a_store_failure_is_attributed_to_the_store_phase_and_the_table_is_capped(tmp_path):
    quote = {"kind": "QUOTE", "quote": {"instrument_id": "US.TQQQ", "provider_time": "2026-11-17T20:00:00Z", "received_at": "2026-11-17T20:00:01Z",
                                         "payload": {"price": "10.00", "provider_data": {"pre_market": {"price": "10.00"}}}}}
    stop_holder = {}
    a = adapter(messages=[quote]); b = adapter(messages=[lambda: stop_holder["stop"].set()])
    with patch.object(collector, "parse_push_children", side_effect=sqlite3.OperationalError("database or disk is full")):
        store = harness(tmp_path, [a, b], lambda stop: stop_holder.setdefault("stop", stop))
    first = store.incidents("moomoo")[-1]
    assert (first["phase"], first["reason_code"], first["message"]) == ("STORE_SESSION_QUOTES", "STORE_SQLITE_DISK_OR_TEMP_FULL", "database or disk is full")
    for i in range(store.INCIDENT_ROWS_KEPT + 40):                                            # a failure loop cannot grow the table without bound
        store.record_incident({**first, "at_utc": f"2026-11-21T00:00:{i % 60:02d}Z"})
    assert store.db.execute("SELECT COUNT(*) FROM provider_incidents").fetchone()[0] == store.INCIDENT_ROWS_KEPT
    store.db.close()


def test_recording_can_never_become_a_failure_of_its_own():
    broken = Mock(); broken.record_incident.side_effect = sqlite3.OperationalError("database or disk is full")
    incidents.emit(incidents.describe(raise_from(0, ValueError("x")), provider="moomoo", phase="NOT_A_PHASE", recorded_state="DISCONNECTED"), broken)
    assert broken.record_incident.call_count == 1                                             # tried, failed, swallowed
