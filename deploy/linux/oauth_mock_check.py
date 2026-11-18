"""MOCK OAuth provider check for the moomoo authorization callback path. TEST ONLY.

Runs inside the auth container (or locally in unit tests). A fake provider is
started on the container's own loopback and the client's REGISTER/AUTHORIZE/
TOKEN URLs are patched to it; the script refuses to continue unless all three
point at the mock, so no real moomoo endpoint is ever contacted. Passing this
check proves the callback plumbing (bind address, port publishing, state,
error callback, exact redirect_uri, PKCE), NOT that moomoo authorization works.

    python oauth_mock_check.py serve --case ok|bad_state|provider_error|redirect_mismatch --port 8790 --bind 0.0.0.0

It prints one JSON line {"event":"consent", ...} for the driver acting as the
browser, then one JSON line {"event":"result", ...} and exits 0 when the client
behaved as the case expects, else 1.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import os
import secrets
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

_repo = Path(__file__).resolve().parents[2]
# Prefer the repository this file belongs to; inside a container the script is
# mounted at /x and the current code is at /app (compose.devmount.yaml).
sys.path.insert(0, str(_repo) if (_repo / "scripts/market_observer_v1").is_dir() else "/app")
from scripts.market_observer_v1 import moomoo_webapi_oauth as oauth  # noqa: E402


class MockProvider:
    def __init__(self, case: str):
        self.case = case
        self.registered: list[str] = []
        self.challenge: str | None = None
        self.issued_code = secrets.token_urlsafe(16)
        self.token_requests: list[dict] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_a):
                pass

            def _json(self, code, body):
                data = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_POST(self):  # noqa: N802
                raw = self.rfile.read(int(self.headers.get("Content-Length", 0))).decode()
                if self.path == "/oauth2/register":
                    body = json.loads(raw)
                    outer.registered = list(body["redirect_uris"])
                    return self._json(201, {"client_id": "mock-client"})
                if self.path == "/oauth2/token":
                    form = {k: v[0] for k, v in parse_qs(raw).items()}
                    outer.token_requests.append({k: form.get(k) for k in ("grant_type", "redirect_uri")})
                    verifier_ok = outer.challenge == base64.urlsafe_b64encode(
                        hashlib.sha256(form.get("code_verifier", "").encode()).digest()).decode().rstrip("=")
                    if (form.get("code") != outer.issued_code or form.get("redirect_uri") not in outer.registered
                            or not verifier_ok):
                        return self._json(400, {"error": "invalid_grant"})
                    return self._json(200, {"access_token": "MOCK-ACCESS-NOT-A-CREDENTIAL", "token_type": "Bearer",
                                            "expires_in": 3600, "refresh_token": "MOCK-REFRESH",
                                            "scope": "quote:read accid:000"})
                return self._json(404, {})

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()


class ConsentTap(io.TextIOBase):
    """Capture the consent URL the client prints for the operator."""

    def __init__(self, provider: MockProvider):
        self.provider = provider
        self.consent = None

    def write(self, text):
        for token in text.split():
            if token.startswith(self.provider.base + "/oauth2/authorize"):
                self.consent = token
        return len(text)


def serve(case: str, port: int, bind: str) -> int:
    provider = MockProvider(case)
    oauth.REGISTER_URL = provider.base + "/oauth2/register"
    oauth.AUTHORIZE_URL = provider.base + "/oauth2/authorize/confirm"
    oauth.TOKEN_URL = provider.base + "/oauth2/token"
    if not all(u.startswith(provider.base) for u in (oauth.REGISTER_URL, oauth.AUTHORIZE_URL, oauth.TOKEN_URL)):
        print(json.dumps({"event": "result", "ok": False, "reason": "URL_PATCH_FAILED"}), flush=True)
        return 1
    tmp = Path(tempfile.mkdtemp())
    key = tmp / "key"
    key.write_text(base64.b64encode(secrets.token_bytes(32)).decode())
    os.chmod(key, 0o600)
    store = oauth.ServerFileTokenStore(tmp / "token.sealed", key)
    tap = ConsentTap(provider)
    outcome: dict = {}

    def client():
        real = sys.stderr
        sys.stderr = tap
        try:
            outcome["receipt"] = oauth.authorize_quote_read(store, timeout_seconds=60, open_browser=False,
                                                            callback_port=port, bind_host=bind)
        except Exception as exc:  # noqa: BLE001
            outcome["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            sys.stderr = real

    thread = threading.Thread(target=client)
    thread.start()
    deadline = time.monotonic() + 20
    while tap.consent is None and time.monotonic() < deadline and thread.is_alive():
        time.sleep(0.05)
    if tap.consent is None:
        thread.join(5)
        print(json.dumps({"event": "result", "ok": False, "reason": "NO_CONSENT_URL", "client": outcome}), flush=True)
        return 1
    params = {k: v[0] for k, v in parse_qs(urlparse(tap.consent).query).items()}
    provider.challenge = params["code_challenge"]
    redirect = params["redirect_uri"]
    if case == "redirect_mismatch":
        provider.registered = ["http://127.0.0.1:1/other"]
    query = {"ok": f"code={provider.issued_code}&state={params['state']}",
             "redirect_mismatch": f"code={provider.issued_code}&state={params['state']}",
             "bad_state": f"code={provider.issued_code}&state=WRONG",
             "provider_error": "error=access_denied&state=" + params["state"]}[case]
    print(json.dumps({"event": "consent", "redirect_uri": redirect, "callback_query": query,
                      "registered_matches": redirect in provider.registered or case == "redirect_mismatch",
                      "scope": params.get("scope"), "pkce_method": params.get("code_challenge_method")}), flush=True)
    thread.join(70)
    stored = store.status() if "receipt" in outcome else {"credential_state": "NOT_INSTALLED"}
    expect = {"ok": lambda: "receipt" in outcome and stored["credential_state"] == "QUOTE_READ_AUTHORIZED",
              "bad_state": lambda: "state mismatch" in outcome.get("error", ""),
              "provider_error": lambda: "refused by provider" in outcome.get("error", ""),
              "redirect_mismatch": lambda: "HTTP 400" in outcome.get("error", "")}[case]
    ok = bool(expect()) and (case == "ok" or stored["credential_state"] == "NOT_INSTALLED")
    print(json.dumps({"event": "result", "case": case, "ok": ok, "client_error": outcome.get("error"),
                      "stored_state": stored.get("credential_state"), "storage": stored.get("storage"),
                      "token_requests": provider.token_requests, "mock_only": True}), flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["serve"])
    parser.add_argument("--case", default="ok", choices=["ok", "bad_state", "provider_error", "redirect_mismatch"])
    parser.add_argument("--port", type=int, default=8790)
    parser.add_argument("--bind", default="0.0.0.0")
    args = parser.parse_args()
    raise SystemExit(serve(args.case, args.port, args.bind))
