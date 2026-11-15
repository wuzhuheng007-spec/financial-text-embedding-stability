"""Separate moomoo WebAPI OAuth for local read-only quote operations.

Codex MCP credentials are deliberately never accessed here. Token material is
DPAPI-encrypted for the current Windows user under the ignored artifacts tree.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen


REGISTER_URL = "https://webapi.moomoo.com/oauth2/register"
AUTHORIZE_URL = "https://webapi.moomoo.com/oauth2/authorize/confirm"
TOKEN_URL = "https://webapi.moomoo.com/oauth2/token"


class OAuthError(RuntimeError):
    """Safe errors contain no authorization code or token body."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _request_json(url: str, data: dict, *, form: bool = False) -> dict:
    payload = (urlencode(data).encode("utf-8") if form else
               json.dumps(data, separators=(",", ":")).encode("utf-8"))
    content_type = "application/x-www-form-urlencoded" if form else "application/json"
    try:
        with urlopen(Request(url, data=payload, method="POST",
                             headers={"Content-Type": content_type}), timeout=15) as response:
            if response.status != 200 and response.status != 201:
                raise OAuthError(f"OAuth endpoint returned HTTP {response.status}")
            answer = json.loads(response.read(65536).decode("utf-8"))
    except HTTPError as exc:
        raise OAuthError(f"OAuth endpoint returned HTTP {exc.code}") from None
    except (URLError, TimeoutError, ValueError) as exc:
        raise OAuthError(f"OAuth endpoint failed: {type(exc).__name__}") from None
    if not isinstance(answer, dict):
        raise OAuthError("OAuth endpoint returned a non-object response")
    return answer


def _protect(data: bytes) -> bytes:
    try:
        import win32crypt  # Windows DPAPI, scoped to the current login.
    except ImportError as exc:
        raise OAuthError("Windows DPAPI is unavailable; refuse plaintext token storage") from exc
    return win32crypt.CryptProtectData(data, "moomoo market observer", None, None, None, 0)


def _unprotect(data: bytes) -> bytes:
    try:
        import win32crypt
        return win32crypt.CryptUnprotectData(data, None, None, None, 0)[1]
    except Exception as exc:
        raise OAuthError(f"cannot unlock local OAuth token: {type(exc).__name__}") from None


def _check_scope(token: dict) -> None:
    scopes = set(str(token.get("scope", "")).split())
    # The user's later approval permitted a broader grant, but a new consent
    # returned only quote:read plus an account selector. Keep the installed
    # credential narrow and reject any other scope, including trade:write.
    permitted = {"quote:read"}
    forbidden = sorted(scope for scope in scopes
                       if scope not in permitted and not scope.startswith("accid:"))
    if "quote:read" not in scopes or forbidden:
        safe = sorted("accid:<redacted>" if scope.startswith("accid:") else scope
                      for scope in scopes)
        raise OAuthError(f"OAuth scope rejected; returned scope classes: {safe}")
    if token.get("token_type", "").lower() != "bearer":
        raise OAuthError("OAuth response token type is not Bearer")
    if not token.get("access_token"):
        raise OAuthError("OAuth response has no access token")


class LocalTokenStore:
    def __init__(self, path: Path):
        self.path = path

    def save(self, token: dict) -> None:
        _check_scope(token)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        raw = json.dumps(token, ensure_ascii=False, sort_keys=True).encode("utf-8")
        temporary = self.path.with_name(self.path.name + ".tmp")
        temporary.write_bytes(_protect(raw))
        temporary.replace(self.path)

    def load(self) -> dict:
        if not self.path.is_file():
            raise OAuthError("separate moomoo WebAPI quote:read authorization is not installed")
        try:
            token = json.loads(_unprotect(self.path.read_bytes()))
        except (ValueError, TypeError) as exc:
            raise OAuthError("local OAuth token record is invalid") from None
        _check_scope(token)
        return token

    def status(self) -> dict:
        if not self.path.is_file():
            return {"credential_state": "NOT_INSTALLED"}
        try:
            token = self.load()
            return {"credential_state": "QUOTE_READ_AUTHORIZED",
                    "granted_scope_classes": sorted(
                        "accid:<redacted>" if scope.startswith("accid:") else scope
                        for scope in str(token.get("scope", "")).split()),
                    "access_expires_at": token.get("access_expires_at"),
                    "token_file_encrypted": True}
        except OAuthError:
            return {"credential_state": "UNREADABLE_OR_SCOPE_REJECTED"}

    def access_token(self) -> str:
        token = self.load()
        if time.time() >= float(token.get("access_expires_epoch", 0)) - 600:
            if not token.get("refresh_token") or not token.get("client_id"):
                raise OAuthError("WebAPI access token expired and refresh is unavailable")
            refreshed = _request_json(TOKEN_URL, {
                "grant_type": "refresh_token", "refresh_token": token["refresh_token"],
                "client_id": token["client_id"]}, form=True)
            _check_scope(refreshed)
            if int(refreshed.get("expires_in", 0)) <= 600:
                raise OAuthError("OAuth refresh lifetime is too short")
            expiry = time.time() + int(refreshed["expires_in"])
            token.update({"access_token": refreshed["access_token"],
                          "access_expires_epoch": expiry,
                          "access_expires_at": datetime.fromtimestamp(
                              expiry, timezone.utc).isoformat().replace("+00:00", "Z")})
            if refreshed.get("refresh_token"):
                token["refresh_token"] = refreshed["refresh_token"]
            self.save(token)
        return str(token["access_token"])


def authorize_quote_read(token_store: LocalTokenStore, *, timeout_seconds: int = 300) -> dict:
    """Run one local browser consent with a loopback-only callback.

    The returned receipt is safe to print; the callback code and tokens are not.
    """
    if timeout_seconds <= 0 or timeout_seconds > 300:
        raise ValueError("OAuth callback timeout must be 1..300 seconds")
    callback: dict[str, str] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - HTTP protocol name
            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)
            if parsed.path == "/callback" and params.get("code") and params.get("state"):
                callback["code"] = params["code"][0]
                callback["state"] = params["state"][0]
                body = b"Authorization received. You may return to the local program."
                self.send_response(200)
            else:
                body = b"Invalid callback."
                self.send_response(400)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            # The default HTTP handler logs the callback path, including code.
            pass

    with HTTPServer(("127.0.0.1", 0), Handler) as server:
        server.timeout = timeout_seconds
        redirect_uri = f"http://127.0.0.1:{server.server_port}/callback"
        registration = _request_json(REGISTER_URL, {
            "redirect_uris": [redirect_uri],
            "token_endpoint_auth_method": "none",
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "client_name": "quant-readonly-market-observer",
            "scope": "quote:read"})
        client_id = registration.get("client_id")
        if not client_id:
            raise OAuthError("OAuth client registration did not return client_id")
        # Registration advertises the client's available scopes; the token
        # response is the actual grant. Enforce the approved grant in _check_scope.
        verifier = secrets.token_urlsafe(48)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        state = secrets.token_urlsafe(24)
        consent = AUTHORIZE_URL + "?" + urlencode({
            "response_type": "code", "client_id": client_id,
            "redirect_uri": redirect_uri, "code_challenge": challenge,
            "code_challenge_method": "S256", "state": state,
            "scope": "quote:read"})
        if not webbrowser.open(consent, new=1):
            raise OAuthError("could not open the browser consent page")
        server.handle_request()
        if not callback.get("code"):
            raise OAuthError("browser consent callback was not received before timeout")
        if not hmac.compare_digest(callback.get("state", ""), state):
            raise OAuthError("OAuth callback state mismatch")
        response = _request_json(TOKEN_URL, {
            "grant_type": "authorization_code", "code": callback["code"],
            "client_id": client_id, "redirect_uri": redirect_uri,
            "code_verifier": verifier}, form=True)
        _check_scope(response)
        if not response.get("refresh_token"):
            raise OAuthError("OAuth response has no refresh token for continuous collection")
        lifetime = int(response.get("expires_in", 0))
        if lifetime <= 600:
            raise OAuthError("OAuth access token lifetime is too short")
        token_store.save({**response, "client_id": client_id,
                          "access_expires_epoch": time.time() + lifetime,
                          "access_expires_at": datetime.fromtimestamp(time.time() + lifetime,
                                                                         timezone.utc).isoformat().replace("+00:00", "Z"),
                          "authorized_at": _utc_now()})
        return {"authorization": "QUOTE_READ_AUTHORIZED",
                "token_file_encrypted": True, "token_printed": False,
                "callback_host": "127.0.0.1"}
