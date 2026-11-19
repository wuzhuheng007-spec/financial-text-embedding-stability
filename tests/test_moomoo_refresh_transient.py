"""Refresh failures: transient network errors must retry, real grant rejection must stop."""
from __future__ import annotations

import io
import time
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError

import pytest

from scripts.market_observer_v1 import moomoo_webapi_oauth as oauth
from scripts.market_observer_v1.moomoo_ws import MoomooWebSocketPush
from scripts.market_observer_v1.service import AuthorizationError
from tests.test_market_observer_moomoo_ws import FakeWebSocket, _live


def _http(code: int) -> HTTPError:
    return HTTPError("https://example.invalid", code, "x", {}, io.BytesIO(b""))


@pytest.mark.parametrize("exc", [URLError("dns"), TimeoutError(), ConnectionResetError(), _http(429), _http(500), _http(502), _http(503)])
def test_network_and_server_errors_are_transient(exc: Exception) -> None:
    with patch.object(oauth, "urlopen", side_effect=exc):
        with pytest.raises(oauth.OAuthTransientError):
            oauth._request_json(oauth.TOKEN_URL, {"grant_type": "refresh_token"}, form=True)


@pytest.mark.parametrize("code", [400, 401, 403, 439])
def test_grant_rejection_is_terminal(code: int) -> None:
    with patch.object(oauth, "urlopen", side_effect=_http(code)):
        with pytest.raises(oauth.OAuthError) as info:
            oauth._request_json(oauth.TOKEN_URL, {"grant_type": "refresh_token"}, form=True)
    assert not isinstance(info.value, oauth.OAuthTransientError)


class RaisingTokens:
    def __init__(self, exc: Exception):
        self.exc = exc

    def access_token(self) -> str:
        raise self.exc


def test_connect_maps_transient_refresh_to_connection_error(tmp_path: Path) -> None:
    ws = FakeWebSocket()
    source = MoomooWebSocketPush(live=_live(), token_store=RaisingTokens(oauth.OAuthTransientError("URLError")),
                                 status_path=tmp_path / "s.json", ws_factory=lambda *a, **k: ws)
    with pytest.raises(ConnectionError):
        source.connect()
    assert ws.sent == []  # no auth frame sent without a token


def test_connect_keeps_terminal_refresh_as_authorization_error(tmp_path: Path) -> None:
    source = MoomooWebSocketPush(live=_live(), token_store=RaisingTokens(oauth.OAuthError("HTTP 400")),
                                 status_path=tmp_path / "s.json", ws_factory=lambda *a, **k: FakeWebSocket())
    with pytest.raises(AuthorizationError):
        source.connect()


def test_periodic_refresh_maps_transient_to_connection_error(tmp_path: Path) -> None:
    from tests.test_market_observer_moomoo_ws import FakeTokens
    ws = FakeWebSocket()
    tokens = FakeTokens()
    source = MoomooWebSocketPush(live=_live(), token_store=tokens,
                                 status_path=tmp_path / "s.json", ws_factory=lambda *a, **k: ws)
    source.connect()
    source.token_store = RaisingTokens(oauth.OAuthTransientError("TimeoutError"))
    source._last_refresh = time.monotonic() - 301
    with pytest.raises(ConnectionError):
        source._refresh_if_due()
    source.token_store = RaisingTokens(oauth.OAuthError("HTTP 401"))
    with pytest.raises(AuthorizationError):
        source._refresh_if_due()
    source.close()
