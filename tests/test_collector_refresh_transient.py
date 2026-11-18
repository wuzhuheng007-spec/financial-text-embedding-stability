"""Collector-level checks for token refresh failures (synthetic only, no network, no real token).

* transient refresh failures (network, timeout, HTTP 429, HTTP 5xx) retry a bounded
  number of times, then the moomoo thread ends as DISCONNECTED / ConnectionError;
* real grant rejection (HTTP 400/401/403) and HTTP 439 stop at once as AUTH_EXPIRED;
* the Linux supervisor restarts the first case (bounded) and holds the second;
* the Jin10 thread keeps polling while the moomoo thread is failing.
"""
from __future__ import annotations

import io
import sqlite3
import sys
import threading
from contextlib import closing
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError

import pytest

from scripts.market_observer_v1 import moomoo_webapi_oauth as oauth
from scripts.quant_personal_feed import collector
from tests.test_market_observer_moomoo_ws import _live

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "deploy/linux"))
import supervisor  # noqa: E402


def _http(code: int) -> HTTPError:
    return HTTPError("https://example.invalid", code, "x", {}, io.BytesIO(b""))


class EndpointTokens:
    """Token store whose refresh goes through the real _request_json error mapping."""

    def __init__(self) -> None:
        self.calls = 0

    def access_token(self) -> str:
        self.calls += 1
        oauth._request_json(oauth.TOKEN_URL, {"grant_type": "refresh_token"}, form=True)
        raise AssertionError("urlopen is patched to fail")


def _config(tmp: Path) -> dict:
    return {"cache_db": "feed.sqlite3",
            "moomoo": {"priority_symbols": ["NVDA"], "status_path": "status.json",
                       "max_reconnect_attempts": 3, "reconnect_initial_seconds": 0,
                       "reconnect_max_seconds": 0},
            "jin10": {"tool": "list_flash", "poll_interval_seconds": 60,
                      "max_business_calls_per_utc_day": 1440, "max_reconnect_attempts": 3,
                      "timeout_seconds": 5, "max_response_bytes": 65536},
            "retention": {"quotes_per_symbol": 10, "news_items": 10, "news_versions_per_item": 2}}


def _row(db: Path, provider: str) -> dict:
    with closing(sqlite3.connect(db)) as con:
        con.row_factory = sqlite3.Row
        return dict(con.execute("SELECT * FROM provider_state WHERE provider=?", (provider,)).fetchone())


def _run_moomoo(tmp: Path, failure: Exception) -> tuple[dict, int]:
    tokens = EndpointTokens()
    live = _live()
    live["subscriptions"] = [dict(live["subscriptions"][0], requested_symbol="NVDA")]
    with patch.object(collector, "under_root", lambda rel: tmp / rel), \
         patch.object(collector, "verified_subscriptions", lambda cfg, desired=None: (live, [], {"NVDA": "NVDA"})), \
         patch.object(collector, "token_store", lambda path: tokens), \
         patch.object(oauth, "urlopen", side_effect=failure):
        collector.run_moomoo(_config(tmp), 1, threading.Event())
    return _row(tmp / "feed.sqlite3", "moomoo"), tokens.calls


TRANSIENT = [URLError("dns"), TimeoutError(), ConnectionResetError(), _http(429), _http(500), _http(503)]
TERMINAL = [_http(400), _http(401), _http(403), _http(439)]


@pytest.mark.parametrize("failure", TRANSIENT, ids=lambda e: getattr(e, "code", type(e).__name__))
def test_transient_refresh_retries_bounded_then_disconnected(tmp_path: Path, failure: Exception) -> None:
    row, calls = _run_moomoo(tmp_path, failure)
    assert calls == 3  # max_reconnect_attempts, not 1 and not unbounded
    assert row["state"] == "DISCONNECTED"
    assert row["last_error_class"] == "ConnectionError"
    assert row["worker_alive"] == 0
    verdict, reason = supervisor.classify_feed(tmp_path / "feed.sqlite3", {"moomoo": True}, True)
    assert verdict == supervisor.RESTART, reason  # bounded by grace, backoff and per-hour cap


@pytest.mark.parametrize("failure", TERMINAL, ids=lambda e: str(e.code))
def test_rejection_and_439_stop_immediately(tmp_path: Path, failure: Exception) -> None:
    row, calls = _run_moomoo(tmp_path, failure)
    assert calls == 1
    assert row["state"] == "AUTH_EXPIRED"
    assert row["last_error_class"] == "AUTHORIZATION"
    assert row["worker_alive"] == 0
    verdict, _ = supervisor.classify_feed(tmp_path / "feed.sqlite3", {"moomoo": True}, True)
    assert verdict == supervisor.HOLD


def test_supervisor_reads_real_store_error_column_for_quota(tmp_path: Path) -> None:
    db = tmp_path / "feed.sqlite3"
    store = collector.PersonalFeedStore(db, 1)
    store.state("moomoo", "RATE_LIMITED", error_class="SUBSCRIPTION_QUOTA")
    store.provider_metric("moomoo", worker_alive=0)
    store.close()
    assert supervisor.classify_feed(db, {"moomoo": True}, True)[0] == supervisor.HOLD


class FakeReply:
    payload = {"result": {"structuredContent": {"status": 200, "data": {"items": []}}}}


class FakeSession:
    requests_used = 0

    def call_tool(self, name: str, args: dict) -> FakeReply:
        return FakeReply()


def test_jin10_keeps_polling_while_moomoo_fails(tmp_path: Path) -> None:
    stop = threading.Event()
    config = _config(tmp_path)
    # Production opens an already-migrated cache. Two threads migrating a brand
    # new file at once race on ALTER TABLE (separate pre-existing issue), so
    # migrate once here and test only the provider isolation.
    collector.PersonalFeedStore(tmp_path / "feed.sqlite3", 1).close()
    jin10_ok = threading.Event()
    real_state = collector.PersonalFeedStore.state

    def watch_state(self, provider, state, **kw):
        real_state(self, provider, state, **kw)
        if provider == "jin10" and kw.get("success"):
            jin10_ok.set()

    with patch.object(collector, "under_root", lambda rel: tmp_path / rel), \
         patch.object(collector, "_jin10_session", lambda cfg, store: (FakeSession(), {})), \
         patch.object(collector.PersonalFeedStore, "state", watch_state):
        jin10 = threading.Thread(target=collector.run_jin10, args=(config, 1, stop), daemon=True)
        jin10.start()
        row, calls = _run_moomoo(tmp_path, URLError("network down"))
        assert jin10_ok.wait(20)
        assert jin10.is_alive()
        stop.set()
        jin10.join(20)
    assert calls == 3 and row["state"] == "DISCONNECTED"
    j = _row(tmp_path / "feed.sqlite3", "jin10")
    assert j["business_success_total"] >= 1
    assert j["state"] == "DISCONNECTED" and j["last_error_class"] == "USER_STOP"
