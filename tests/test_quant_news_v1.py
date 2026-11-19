"""News adapters/store/collector with fixtures shaped like the documented provider messages (no network)."""
import json
import os
import tempfile
import unittest
from pathlib import Path

from scripts.quant_news_v1 import adapters, store

BZ_CREATED = {"id": "msg-1", "api_version": "websocket/v1", "kind": "news",
              "data": {"action": "Created", "id": 38945001, "timestamp": "2026-11-19T13:35:14Z",
                       "content": {"id": 38945001, "author": "Benzinga Newsdesk", "created": "Wed, 19 Nov 2026 08:35:10 -0500",
                                   "updated": "Wed, 19 Nov 2026 08:35:14 -0500", "title": "NVIDIA Shares Rise",
                                   "teaser": "t", "body": "<p>b</p>", "url": "https://www.benzinga.com/x",
                                   "channels": [{"name": "News"}], "stocks": [{"name": "NVDA"}, {"name": "QQQ"}], "tags": []}}}


def bz(action, msg_id, title="NVIDIA Shares Rise"):
    m = json.loads(json.dumps(BZ_CREATED)); m["id"] = msg_id; m["data"]["action"] = action; m["data"]["content"]["title"] = title
    return m


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); os.environ["QUANT_NEWS_DB"] = str(Path(self.tmp.name) / "n.sqlite3")
        self.db = store.connect()

    def tearDown(self):
        self.db.close(); os.environ.pop("QUANT_NEWS_DB", None); self.tmp.cleanup()


class NormalizeTests(unittest.TestCase):
    def test_benzinga_fields_and_times(self):
        n = adapters.normalize_benzinga(BZ_CREATED)
        self.assertEqual((n["upstream"], n["upstream_id"], n["action"], n["symbols"]), ("benzinga", "38945001", "created", ["NVDA", "QQQ"]))
        self.assertEqual((n["provider_created_at"], n["provider_updated_at"]), ("2026-11-19T13:35:10Z", "2026-11-19T13:35:14Z"))

    def test_massive_and_alpaca(self):
        m = adapters.normalize_massive({"benzinga_id": 38945001, "title": "x", "tickers": ["NVDA"], "published": "2026-11-19T13:35:10Z",
                                        "last_updated": "2026-11-19T13:40:00Z", "url": "https://www.benzinga.com/x"})
        self.assertEqual((m["upstream"], m["upstream_id"], m["body"]), ("benzinga", "38945001", None))  # same upstream article id space
        a = adapters.AlpacaStream.parse(json.dumps([{"T": "n", "id": 77, "headline": "h", "summary": "", "author": "", "created_at": "2026-11-19T13:35:10Z",
                                                     "updated_at": "2026-11-19T13:35:10Z", "url": "u", "content": "", "symbols": ["MU"], "source": "upstreamX"}]))
        self.assertEqual((a[0]["upstream"], a[0]["source"], a[0]["teaser"]), ("alpaca", "upstreamX", None))

    def test_alpaca_error_codes_classified(self):
        with self.assertRaises(adapters.ProviderError) as c:
            adapters.AlpacaStream.parse(json.dumps([{"T": "error", "code": 402, "msg": "auth failed"}]))
        self.assertEqual(c.exception.error_class, "AUTH_FAILED")
        with self.assertRaises(adapters.ProviderError) as c:
            adapters.AlpacaStream.parse(json.dumps([{"T": "error", "code": 406, "msg": "connection limit exceeded"}]))
        self.assertEqual(c.exception.error_class, "CONNECTION_LIMIT")

    def test_http_and_network_classification(self):
        import socket, ssl
        E = lambda code: type("E", (Exception,), {"code": code, "headers": {"Retry-After": "7"}})()
        self.assertEqual(adapters.classify_exception(E(401)).error_class, "AUTH_FAILED")
        self.assertEqual(adapters.classify_exception(E(403)).error_class, "FORBIDDEN")
        r = adapters.classify_exception(E(429)); self.assertEqual((r.error_class, r.retry_after), ("RATE_LIMITED", 7.0))
        self.assertEqual(adapters.classify_exception(socket.gaierror()).error_class, "NETWORK_DNS")
        self.assertEqual(adapters.classify_exception(ssl.SSLError()).error_class, "NETWORK_TLS")
        self.assertEqual(adapters.classify_exception(TimeoutError()).error_class, "NETWORK_TIMEOUT")

    def test_ping_pong_is_not_news(self):
        self.assertEqual(adapters.BenzingaStream.parse("pong"), [])


class StoreTests(Base):
    def test_dedupe_versions_tombstone_and_cross_channel(self):
        n = adapters.normalize_benzinga(BZ_CREATED)
        self.assertEqual(store.apply(self.db, n, provider="benzinga_stream", via="stream", received_at="2026-11-19T13:35:15Z"), "NEW")
        self.assertEqual(store.apply(self.db, n, provider="benzinga_stream", via="stream", received_at="2026-11-19T13:35:16Z"), "DUPLICATE")
        upd = adapters.normalize_benzinga(bz("Updated", "msg-2", "NVIDIA Shares Rise After Results"))
        self.assertEqual(store.apply(self.db, upd, provider="benzinga_stream", via="stream", received_at="2026-11-19T13:36:00Z"), "NEW_VERSION")
        # the same article through Massive's Benzinga REST is the same item, not a new one
        m = adapters.normalize_massive({"benzinga_id": 38945001, "title": "NVIDIA Shares Rise After Results", "tickers": ["NVDA", "QQQ"]})
        self.assertIn(store.apply(self.db, m, provider="massive_benzinga", via="rest_poll", received_at="2026-11-19T13:37:00Z"), ("DUPLICATE", "NEW_VERSION"))
        self.assertEqual(self.db.execute("select count(*) from items").fetchone()[0], 1)
        d = adapters.normalize_benzinga(bz("Deleted", "msg-3"))
        self.assertEqual(store.apply(self.db, d, provider="benzinga_stream", via="stream", received_at="2026-11-19T13:40:00Z"), "TOMBSTONE")
        row = self.db.execute("select deleted, latest_version from items").fetchone()
        self.assertEqual(row[0], 1); self.assertGreaterEqual(row[1], 2)
        self.assertGreaterEqual(self.db.execute("select count(*) from versions").fetchone()[0], 2)  # versions kept
        view = store.read_for_portal()
        self.assertEqual(view["items"][0]["deleted"], 1)

    def test_backfill_is_not_first_online_observation(self):
        n = adapters.normalize_benzinga(BZ_CREATED)
        store.apply(self.db, n, provider="massive_benzinga", via="backfill", received_at="2026-11-19T14:00:00Z", online=False)
        self.assertIsNone(self.db.execute("select first_online_seen_at from items").fetchone()[0])
        store.apply(self.db, n, provider="massive_benzinga", via="rest_poll", received_at="2026-11-19T14:01:00Z", online=True)
        self.assertEqual(self.db.execute("select first_online_seen_at, first_seen_via from items").fetchone()[:], ("2026-11-19T14:01:00Z", "backfill"))

    def test_stream_message_id_dedupe(self):
        self.assertFalse(store.seen_message(self.db, "benzinga_stream", "msg-1"))
        self.assertTrue(store.seen_message(self.db, "benzinga_stream", "msg-1"))


class CollectorTests(Base):
    def test_fake_stream_end_to_end_states(self):
        from scripts.quant_news_v1 import collector

        class FakeWS:
            def __init__(self, frames): self.frames = list(frames); self.sent = []
            def settimeout(self, t): pass
            def send(self, t): self.sent.append(t)
            def recv(self):
                import websocket
                if self.frames:
                    return self.frames.pop(0)
                raise websocket.WebSocketConnectionClosedException()
            def close(self): pass

        class FakeProvider:
            kind, ping_text, replay_text = "WEBSOCKET_PUSH", "ping", "replay"
            def __init__(self): self.ws = FakeWS([json.dumps(BZ_CREATED), json.dumps(BZ_CREATED), "pong", json.dumps(bz("Updated", "m2", "new title"))])
            def connect(self): return self.ws
            parse = staticmethod(adapters.BenzingaStream.parse)

        fp = FakeProvider()
        with self.assertRaises(adapters.ProviderError):  # server closes after the frames
            collector._stream_loop(self.db, fp, "benzinga_stream", {}, None, reconnect=True)
        self.assertEqual(fp.ws.sent[0], "replay")  # gap fill after a reconnect
        st = dict(self.db.execute("select state, counters_json from provider_state").fetchone())
        self.assertEqual(st["state"], "RECEIVING")
        c = json.loads(st["counters_json"]); self.assertEqual((c["news_new"], c["news_versions"], c["duplicates"]), (1, 1, 1))

    def test_not_configured_is_recorded_not_crashed(self):
        from scripts.quant_news_v1 import collector
        for k in ("BENZINGA_TOKEN", "MASSIVE_API_KEY"):
            os.environ.pop(k, None)
        self.assertEqual(collector.run("benzinga_stream", max_seconds=1), 2)
        self.assertEqual(self.db.execute("select state from provider_state where provider='benzinga_stream'").fetchone()[0], "NOT_CONFIGURED")

    def test_probe_without_credentials(self):
        from scripts.quant_news_v1 import probe
        os.environ.pop("MASSIVE_API_KEY", None)
        r = probe.probe("massive_benzinga", 1)
        self.assertEqual((r["ok"], r["result"]), (False, "NOT_CONFIGURED"))
        self.assertNotIn("token", json.dumps(r).lower())


class BenzingaRestAndRedactionTests(Base):
    TOKEN = "bzTESTtoken1234567890abcdef"

    def test_rest_item_offsets_parsed_to_utc_and_same_id_space(self):
        r = {"id": 38945001, "author": "A", "created": "Wed, 19 Nov 2026 08:35:10 -0500", "updated": "Wed, 19 Nov 2026 09:00:00 -0500",
             "title": "x", "teaser": "", "body": "<p>b</p>", "url": "https://www.benzinga.com/x", "stocks": [{"name": "MU"}], "channels": [{"name": "News"}]}
        n = adapters.normalize_benzinga_rest(r)
        self.assertEqual((n["upstream"], n["upstream_id"], n["provider_created_at"], n["provider_updated_at"]),
                         ("benzinga", "38945001", "2026-11-19T13:35:10Z", "2026-11-19T14:00:00Z"))
        self.assertEqual(store.apply(self.db, n, provider="benzinga_rest", via="backfill", received_at="2026-11-19T14:01:00Z", online=False), "NEW")
        s = adapters.normalize_benzinga(BZ_CREATED)  # the stream copy of the same article
        self.assertIn(store.apply(self.db, s, provider="benzinga_stream", via="stream", received_at="2026-11-19T14:02:00Z"), ("DUPLICATE", "NEW_VERSION"))
        self.assertEqual(self.db.execute("select count(*) from items").fetchone()[0], 1)

    def test_http_error_with_token_url_is_redacted(self):
        import io, urllib.error
        from unittest import mock
        rest = adapters.BenzingaRest(self.TOKEN)
        url = f"https://api.benzinga.com/api/v2/news?token={self.TOKEN}&pageSize=1"
        err = urllib.error.HTTPError(url, 401, "Unauthorized", {}, io.BytesIO(f'{{"error":"bad token {self.TOKEN}"}}'.encode()))
        with mock.patch("urllib.request.urlopen", side_effect=err):
            with self.assertRaises(adapters.ProviderError) as c:
                rest.fetch(limit=1)
        text = str(c.exception) + c.exception.detail + repr(c.exception.__cause__) + repr(c.exception.__context__)
        self.assertEqual(c.exception.error_class, "AUTH_FAILED")
        self.assertNotIn(self.TOKEN, text); self.assertIn("body=", c.exception.detail)
        self.assertNotIn(self.TOKEN, adapters.redact(f"GET {url} failed"))
        self.assertIn("token=***", adapters.redact("wss://api.benzinga.com/api/v1/news/stream?token=other-unregistered-value"))

    def test_404_no_data_is_empty_not_error(self):
        import io, urllib.error
        from unittest import mock
        err = urllib.error.HTTPError("u", 404, "Not Found", {}, io.BytesIO(b'{"ok":false}'))
        with mock.patch("urllib.request.urlopen", side_effect=err):
            self.assertEqual(adapters.BenzingaRest(self.TOKEN).fetch(tickers=["SOXX"]), [])

    def test_auth_failure_is_recorded_once_not_retried(self):
        from unittest import mock
        from scripts.quant_news_v1 import collector
        calls = []
        class P:
            kind = "REST_POLL"
            def fetch(self, **kw):
                calls.append(1); raise adapters.ProviderError("AUTH_FAILED", "HTTP 401")
        with mock.patch.object(collector, "build", return_value=P()):
            self.assertEqual(collector.run("benzinga_rest", max_seconds=3), 0)
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.db.execute("select state from provider_state where provider='benzinga_rest'").fetchone()[0], "AUTH_FAILED")


if __name__ == "__main__":
    unittest.main()
