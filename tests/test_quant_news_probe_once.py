"""probe_once: handshake is not authentication; transport / auth / news / close are reported separately (no network)."""
import io
import json
import unittest
import urllib.error

import websocket

from scripts.quant_news_v1 import probe_once

TOKEN = "bzONCEtoken000111222333"
NEWS = json.dumps({"id": "m1", "api_version": "websocket/v1", "kind": "news",
                   "data": {"action": "Created", "id": 1, "timestamp": "2026-11-19T06:00:00Z",
                            "content": {"id": 1, "title": "t", "created": "Wed, 19 Nov 2026 01:00:00 -0500", "stocks": [{"name": "MU"}]}}})


class Frame:
    def __init__(self, data): self.data = data


class FakeWS:
    """frames: list of ("text", str) | ("close", code, reason) | ("timeout",) | ("lost",)"""
    def __init__(self, frames=(), bad_status=None):
        self.frames, self.bad_status, self.closed, self.sent, self.connects = list(frames), bad_status, False, [], 0
    def connect(self, url, timeout=20):
        self.connects += 1; self.url = url
        if self.bad_status:
            raise websocket.WebSocketBadStatusException("Handshake status %d %s", self.bad_status, "x",
                                                        {"x-request-id": "req-1", "set-cookie": "secret"}, f"denied {TOKEN}".encode())
    def getstatus(self): return 101
    def getheaders(self): return {"x-request-id": "req-ws-9", "server": "nginx", "set-cookie": "s"}
    def settimeout(self, t): pass
    def send(self, t): self.sent.append(t)
    def recv_data_frame(self, control=True):
        if not self.frames:
            raise websocket.WebSocketTimeoutException()
        f = self.frames.pop(0)
        if f[0] == "text":
            return websocket.ABNF.OPCODE_TEXT, Frame(f[1].encode())
        if f[0] == "close":
            return websocket.ABNF.OPCODE_CLOSE, Frame(f[1].to_bytes(2, "big") + f[2].encode())
        if f[0] == "lost":
            raise websocket.WebSocketConnectionClosedException()
        raise websocket.WebSocketTimeoutException()
    def close(self): self.closed = True


def run_ws(ws, seconds=0.3):
    return probe_once.ws_once({"BENZINGA_WS_TOKEN": TOKEN}, seconds, ws_factory=lambda: ws, collector_running=lambda: False)


class WsOnceTests(unittest.TestCase):
    def test_handshake_then_server_close_is_not_auth_success(self):
        ws = FakeWS([("close", 4001, f"invalid token {TOKEN}")])
        r = run_ws(ws)
        self.assertEqual(r["transport"]["handshake"], "OK"); self.assertEqual(r["transport"]["headers"]["x-request-id"], "req-ws-9")
        self.assertNotIn("set-cookie", r["transport"]["headers"])
        self.assertEqual((r["close"]["code"], r["auth"], r["result"]), (4001, "REJECTED_BY_SERVER_CLOSE_4001", "SERVER_CLOSED"))
        self.assertTrue(ws.closed and r["socket_closed"]); self.assertNotIn(TOKEN, json.dumps(r)); self.assertNotIn("replay", ws.sent)

    def test_quiet_window_is_unproven_not_invalid(self):
        ws = FakeWS([])
        r = run_ws(ws)
        self.assertEqual((r["auth"], r["result"]), ("UNPROVEN", "CONNECTED_NO_NEWS_IN_WINDOW"))
        self.assertIn("本窗口未观察到新闻", r["news"]["observed"]); self.assertTrue(ws.closed)

    def test_news_frame_is_auth_evidence(self):
        ws = FakeWS([("text", "pong"), ("text", NEWS)])
        r = run_ws(ws)
        self.assertEqual((r["auth"], r["news"]["news_messages"], r["result"]), ("EVIDENCED_BY_NEWS_FRAME", 1, "NEWS_RECEIVED"))

    def test_lost_connection_without_close_frame(self):
        ws = FakeWS([("lost",)])
        r = run_ws(ws)
        self.assertEqual((r["close"]["reason"], r["auth"]), ("closed without close frame", "UNPROVEN")); self.assertTrue(ws.closed)

    def test_429_stops_once_and_401_is_rejected(self):
        ws = FakeWS(bad_status=429); r = run_ws(ws)
        self.assertEqual((r["result"], ws.connects), ("RATE_LIMITED_STOPPED", 1)); self.assertTrue(ws.closed)
        ws = FakeWS(bad_status=401); r = run_ws(ws)
        self.assertEqual((r["auth"], r["transport"]["http_status"]), ("REJECTED_AT_HANDSHAKE", 401))
        self.assertNotIn(TOKEN, json.dumps(r)); self.assertEqual(r["transport"]["headers"], {"x-request-id": "req-1"})

    def test_refuses_when_collector_holds_stream(self):
        ws = FakeWS([])
        r = probe_once.ws_once({"BENZINGA_WS_TOKEN": TOKEN}, 1, ws_factory=lambda: ws, collector_running=lambda: True)
        self.assertEqual(r["result"], "SKIPPED_COLLECTOR_HOLDS_THE_STREAM"); self.assertEqual(ws.connects, 0)


class Resp:
    def __init__(self, status, body, headers): self.status, self._b, self.headers = status, body, headers
    def read(self): return self._b
    def __enter__(self): return self
    def __exit__(self, *a): return False


class RestOnceTests(unittest.TestCase):
    def call(self, opener):
        return probe_once.rest_once({"BENZINGA_TOKEN": TOKEN}, opener=opener)

    def test_params_and_sample(self):
        seen = []
        def op(req, timeout):
            seen.append(req.full_url)
            return Resp(200, json.dumps([{"id": 9, "title": "x", "created": "c", "updated": "u", "url": "https://www.benzinga.com/9", "stocks": [{"name": "NVDA"}]}]).encode(),
                        {"X-Request-Id": "r-77", "Content-Type": "application/json"})
        r = self.call(op)
        self.assertIn("pageSize=1", seen[0]); self.assertIn("displayOutput=headline", seen[0])
        self.assertNotIn("tickers", seen[0]); self.assertNotIn("channels", seen[0])
        self.assertEqual((r["http_status"], r["result"], r["headers"]["x-request-id"], r["sample"]["stocks"]), (200, "AUTH_OK_NEWS_SAMPLE", "r-77", ["NVDA"]))
        self.assertNotIn(TOKEN, json.dumps(r))

    def test_empty_list_is_no_news_sample(self):
        r = self.call(lambda req, timeout: Resp(200, b"[]", {}))
        self.assertEqual((r["result"], r["note"]), ("AUTH_OK_NO_NEWS_SAMPLE", "没有新闻样本（空列表）"))

    def test_401_and_429_redacted(self):
        def op401(req, timeout):
            raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {"X-Request-Id": "r-1"}, io.BytesIO(f'["Access denied {TOKEN}"]'.encode()))
        r = self.call(op401)
        self.assertEqual((r["result"], r["http_status"], r["headers"]), ("AUTH_FAILED", 401, {"x-request-id": "r-1"}))
        self.assertNotIn(TOKEN, json.dumps(r))
        def op429(req, timeout):
            raise urllib.error.HTTPError(req.full_url, 429, "Too Many", {"Retry-After": "30"}, io.BytesIO(b""))
        r = self.call(op429)
        self.assertEqual((r["result"], r["stopped"]), ("RATE_LIMITED", "HTTP 429: stopped, not retried"))


if __name__ == "__main__":
    unittest.main()
