"""Separate moomoo WebAPI OAuth for local read-only quote operations.

Codex MCP credentials are deliberately never accessed here. On Windows, token
material is DPAPI-encrypted for the current Windows user under the ignored
artifacts tree. A Linux server cannot decrypt those files, so it uses a
separate quote:read authorization stored by ``ServerFileTokenStore``: AES-256-GCM
ciphertext on the private persistent volume, keyed by a secret file that is
mounted separately (for example a Docker secret). Neither store prints tokens.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import sys
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
    storage = "WINDOWS_DPAPI"

    def __init__(self, path: Path):
        self.path = path

    def _seal(self, raw: bytes) -> bytes:
        return _protect(raw)

    def _open(self, data: bytes) -> bytes:
        return _unprotect(data)

    def save(self, token: dict) -> None:
        _check_scope(token)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        raw = json.dumps(token, ensure_ascii=False, sort_keys=True).encode("utf-8")
        temporary = self.path.with_name(self.path.name + ".tmp")
        sealed = self._seal(raw)
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0)
        descriptor = os.open(temporary, flags, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(sealed)
        temporary.replace(self.path)

    def load(self) -> dict:
        if not self.path.is_file():
            raise OAuthError("separate moomoo WebAPI quote:read authorization is not installed")
        try:
            token = json.loads(self._open(self.path.read_bytes()))
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
                    "token_file_encrypted": True, "storage": self.storage}
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


_SERVER_MAGIC = b"QTS1"
_SERVER_AAD = b"moomoo-webapi-quote-read-token-v1"


class ServerFileTokenStore(LocalTokenStore):
    """Linux/server token store: AES-256-GCM sealed file plus a separate key file.

    The key file holds 32 random bytes encoded as base64 (or 64 hex digits). It
    must not be group/world readable and must not live beside the ciphertext in
    the image. A missing key or token is reported as not installed; there is no
    plaintext fallback and Windows DPAPI files are not accepted.
    """

    storage = "SERVER_AES256GCM_KEYFILE"

    def __init__(self, path: Path, key_path: Path):
        super().__init__(path)
        self.key_path = key_path

    def _key(self) -> bytes:
        if not self.key_path.is_file():
            raise OAuthError("server token key file is not mounted")
        if os.name != "nt" and self.key_path.stat().st_mode & 0o077:
            raise OAuthError("server token key file must not be group/world readable")
        text = self.key_path.read_bytes().strip()
        try:
            key = (bytes.fromhex(text.decode("ascii")) if len(text) == 64
                   else base64.b64decode(text, validate=True))
        except (ValueError, UnicodeDecodeError):
            raise OAuthError("server token key file is not valid base64 or hex") from None
        if len(key) != 32:
            raise OAuthError("server token key must decode to 32 bytes")
        return key

    def _seal(self, raw: bytes) -> bytes:
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        except ImportError as exc:
            raise OAuthError("cryptography is unavailable; refuse plaintext token storage") from exc
        nonce = secrets.token_bytes(12)
        return _SERVER_MAGIC + nonce + AESGCM(self._key()).encrypt(nonce, raw, _SERVER_AAD)

    def _open(self, data: bytes) -> bytes:
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        except ImportError as exc:
            raise OAuthError("cryptography is unavailable") from exc
        if not data.startswith(_SERVER_MAGIC) or len(data) < len(_SERVER_MAGIC) + 12 + 16:
            # Includes copied Windows DPAPI blobs: they are not migrated.
            raise OAuthError("token file is not a server-sealed record")
        nonce = data[len(_SERVER_MAGIC):len(_SERVER_MAGIC) + 12]
        key = self._key()
        try:
            return AESGCM(key).decrypt(nonce, data[len(_SERVER_MAGIC) + 12:], _SERVER_AAD)
        except Exception as exc:
            raise OAuthError(f"cannot unlock server OAuth token: {type(exc).__name__}") from None

    def status(self) -> dict:
        result = super().status()
        result["key_file_present"] = self.key_path.is_file()
        result["storage"] = self.storage
        return result


def token_store(dpapi_path: Path) -> LocalTokenStore:
    """Choose the platform token store without migrating credentials.

    ``MOOMOO_TOKEN_STORE=server`` (the default off Windows) selects the server
    store; its paths come from ``MOOMOO_TOKEN_FILE`` and
    ``MOOMOO_TOKEN_KEY_FILE``. Otherwise the Windows DPAPI file is used.
    """
    mode = os.environ.get("MOOMOO_TOKEN_STORE", "dpapi" if os.name == "nt" else "server")
    if mode == "dpapi":
        return LocalTokenStore(dpapi_path)
    if mode != "server":
        raise OAuthError("MOOMOO_TOKEN_STORE must be dpapi or server")
    token_path = os.environ.get("MOOMOO_TOKEN_FILE")
    key_path = os.environ.get("MOOMOO_TOKEN_KEY_FILE")
    if not token_path or not key_path:
        raise OAuthError("server token store needs MOOMOO_TOKEN_FILE and MOOMOO_TOKEN_KEY_FILE")
    return ServerFileTokenStore(Path(token_path), Path(key_path))


def authorize_quote_read(token_store: LocalTokenStore, *, timeout_seconds: int = 300,
                         open_browser: bool = True, callback_port: int = 0,
                         bind_host: str = "127.0.0.1") -> dict:
    """Run one browser consent with a loopback-only callback.

    On a headless server use ``open_browser=False`` and a fixed
    ``callback_port``: the consent URL is written to stderr and the owner
    completes it in their own browser through an SSH local port forward to the
    same loopback port. The returned receipt is safe to print; the callback
    code and tokens are not.
    """
    if timeout_seconds <= 0 or timeout_seconds > 300:
        raise ValueError("OAuth callback timeout must be 1..300 seconds")
    if callback_port and not 1024 <= callback_port <= 65535:
        raise ValueError("OAuth callback port must be 1024..65535")
    # The redirect URI always names the loopback address the owner's browser
    # reaches (directly or through an SSH local forward). In a container the
    # listener must bind the container interface so a host port published only
    # on 127.0.0.1 can reach it; that needs a fixed port.
    if bind_host not in ("127.0.0.1", "0.0.0.0"):
        raise ValueError("bind_host must be 127.0.0.1 or 0.0.0.0")
    if bind_host == "0.0.0.0" and not callback_port:
        raise ValueError("binding all container interfaces requires a fixed callback port")
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
            elif parsed.path == "/callback" and params.get("error"):
                # Provider-reported refusal; keep only the short error code.
                callback["error"] = str(params["error"][0])[:64]
                body = b"Authorization was not granted."
                self.send_response(400)
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

    with HTTPServer((bind_host, callback_port), Handler) as server:
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
        if open_browser:
            if not webbrowser.open(consent, new=1):
                raise OAuthError("could not open the browser consent page")
        else:
            # The consent URL carries a public client id, PKCE challenge and a
            # one-time state value, not a token. It is shown only on the
            # operator's interactive terminal, never in service logs.
            print("Open this moomoo consent URL in your own browser after SSH-forwarding "
                  f"127.0.0.1:{server.server_port}:\n{consent}", file=sys.stderr, flush=True)
        server.handle_request()
        if callback.get("error"):
            raise OAuthError(f"authorization refused by provider: {callback['error']}")
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
        return {"authorization": "QUOTE_READ_AUTHORIZED", "storage": token_store.storage,
                "token_file_encrypted": True, "token_printed": False,
                "callback_host": "127.0.0.1"}
