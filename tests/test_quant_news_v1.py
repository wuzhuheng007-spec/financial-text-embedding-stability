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


def bz(action, msg_id, title="NVIDIA Shares Rise", updated="Wed, 19 Nov 2026 08:40:00 -0500", ts="2026-11-19T13:40:00Z"):
    m = json.loads(json.dumps(BZ_CREATED)); m["id"] = msg_id; m["data"]["action"] = action; m["data"]["content"]["title"] = title
    m["data"]["content"]["updated"] = updated; m["data"]["timestamp"] = ts
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
        # no update time while the current version has one -> history only (R4)
        self.assertEqual(store.apply(self.db, m, provider="massive_benzinga", via="rest_poll", received_at="2026-11-19T13:37:00Z"), "HISTORY_VERSION")
        self.assertEqual(self.db.execute("select count(*) from items").fetchone()[0], 1)
        d = adapters.normalize_benzinga(bz("Deleted", "msg-3", ts="2026-11-19T13:50:00Z"))
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
    def _exercise_fake_stream(self, connected_at_utc, *, reconnect):
        from scripts.quant_news_v1 import collector
        from datetime import datetime
        from unittest.mock import patch

        class ConnectionClock(datetime):
            @classmethod
            def now(cls, tz=None):
                return connected_at_utc.astimezone(tz) if tz is not None else connected_at_utc.replace(tzinfo=None)

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
        # Freeze only the connection clock: wall-clock date cannot change live/replay expectations.
        with patch("datetime.datetime", ConnectionClock), self.assertRaises(adapters.ProviderError):
            collector._stream_loop(self.db, fp, "benzinga_stream", {}, None, reconnect=reconnect)
        return fp

    def test_fake_stream_end_to_end_states(self):
        from datetime import datetime, timezone
        fp = self._exercise_fake_stream(datetime(2026, 11, 19, 13, 35, 13, tzinfo=timezone.utc), reconnect=True)
        self.assertEqual(fp.ws.sent[0], "replay")  # gap fill after a reconnect
        st = dict(self.db.execute("select state, counters_json from provider_state").fetchone())
        self.assertEqual(st["state"], "RECEIVING")
        c = json.loads(st["counters_json"]); self.assertEqual((c["news_new"], c["news_versions"], c["duplicates"]), (1, 1, 1))

    def test_historical_replay_is_cached_without_claiming_live_news(self):
        from datetime import datetime, timezone
        fp = self._exercise_fake_stream(datetime(2026, 11, 20, tzinfo=timezone.utc), reconnect=True)
        self.assertEqual(fp.ws.sent[0], "replay")
        self.assertEqual(self.db.execute("select state from provider_state").fetchone()[0], "CONNECTED_NO_MESSAGES_YET")
        c = json.loads(self.db.execute("select counters_json from provider_state").fetchone()[0])
        self.assertEqual((c["news_new"], c["news_versions"], c["duplicates"]), (1, 1, 1))
        self.assertEqual(self.db.execute("select count(*) from items").fetchone()[0], 1)
        self.assertEqual(self.db.execute("select count(*) from versions").fetchone()[0], 2)
        self.assertIsNone(self.db.execute("select first_online_seen_at from items").fetchone()[0])
        self.assertEqual({r[0] for r in self.db.execute("select distinct via from versions")}, {"replay"})

    def test_old_nonreplay_stream_is_cached_without_claiming_live_news(self):
        from datetime import datetime, timezone
        fp = self._exercise_fake_stream(datetime(2026, 11, 20, tzinfo=timezone.utc), reconnect=False)
        self.assertNotIn("replay", fp.ws.sent)
        self.assertEqual(self.db.execute("select state from provider_state").fetchone()[0], "CONNECTED_NO_MESSAGES_YET")
        c = json.loads(self.db.execute("select counters_json from provider_state").fetchone()[0])
        self.assertEqual((c["news_new"], c["news_versions"], c["duplicates"]), (1, 1, 1))
        self.assertEqual(self.db.execute("select count(*) from versions").fetchone()[0], 2)
        self.assertIsNone(self.db.execute("select first_online_seen_at from items").fetchone()[0])
        self.assertEqual({r[0] for r in self.db.execute("select distinct via from versions")}, {"stream_late"})

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
        # the stream copy is older (updated 13:35:14Z < 14:00Z): stored as history, current stays the REST one
        self.assertEqual(store.apply(self.db, s, provider="benzinga_stream", via="stream", received_at="2026-11-19T14:02:00Z"), "HISTORY_VERSION")
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
            def updated_since(self, *a, **kw):
                calls.append(1); raise adapters.ProviderError("AUTH_FAILED", "HTTP 401")
            fetch = removed_since = fetch_page = updated_since
        with mock.patch.object(collector, "build", return_value=P()):
            self.assertEqual(collector.run("benzinga_rest", max_seconds=3), 0)
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.db.execute("select state from provider_state where provider='benzinga_rest'").fetchone()[0], "AUTH_FAILED")


class OrderingRegressionTests(Base):
    """Arrival order must not decide the current content; old backfill must not resurrect deleted articles."""

    def art(self, updated, title, action="created", body="b"):
        return {"upstream": "benzinga", "upstream_id": "77", "message_id": None, "action": action, "title": title, "teaser": None,
                "body": body, "url": "https://www.benzinga.com/77", "source": "Benzinga", "authors": None, "symbols": ["MU"],
                "channels": [], "provider_created_at": "2026-11-19T13:00:00Z", "provider_updated_at": updated, "raw": {}}

    def current(self):
        r = store.read_for_portal()["items"][0]
        return r["title"], r["deleted"]

    def test_newer_then_older_backfill_keeps_newer_current(self):
        self.assertEqual(store.apply(self.db, self.art("2026-11-19T14:00:00Z", "v2"), provider="benzinga_stream", via="stream", received_at="2026-11-19T14:00:01Z"), "NEW")
        self.assertEqual(store.apply(self.db, self.art("2026-11-19T13:30:00Z", "v1"), provider="benzinga_rest", via="gap_fill_rest", received_at="2026-11-19T14:05:00Z", online=False), "HISTORY_VERSION")
        self.assertEqual(self.current(), ("v2", 0))
        self.assertEqual(self.db.execute("select count(*) from versions").fetchone()[0], 2)  # arrival history kept
        self.assertEqual(store.apply(self.db, self.art("2026-11-19T14:10:00Z", "v3"), provider="benzinga_stream", via="stream", received_at="2026-11-19T14:10:01Z"), "NEW_VERSION")
        self.assertEqual(self.current(), ("v3", 0))
        # same exact older content again -> duplicate, not a flip back
        self.assertEqual(store.apply(self.db, self.art("2026-11-19T13:30:00Z", "v1"), provider="benzinga_rest", via="rest_poll", received_at="2026-11-19T14:11:00Z"), "DUPLICATE")
        self.assertEqual(self.current(), ("v3", 0))

    def test_same_time_conflict_and_missing_time_rules(self):
        store.apply(self.db, self.art("2026-11-19T14:00:00Z", "first"), provider="benzinga_stream", via="stream", received_at="2026-11-19T14:00:01Z")
        self.assertEqual(store.apply(self.db, self.art("2026-11-19T14:00:00Z", "second"), provider="benzinga_rest", via="rest_poll", received_at="2026-11-19T14:01:00Z"), "CONFLICT_KEPT")
        self.assertEqual(store.apply(self.db, self.art(None, "untimed"), provider="benzinga_rest", via="rest_poll", received_at="2026-11-19T14:02:00Z"), "HISTORY_VERSION")
        self.assertEqual(self.current(), ("first", 0))
        self.assertEqual(self.db.execute("select conflicts from items").fetchone()[0], 1)

    def test_delete_then_old_backfill_stays_deleted(self):
        self.assertEqual(store.apply(self.db, self.art("2026-11-19T15:00:00Z", None, action="deleted"), provider="benzinga_rest", via="removed_poll", received_at="2026-11-19T15:00:05Z"), "TOMBSTONE")
        self.assertEqual(store.apply(self.db, self.art("2026-11-19T14:00:00Z", "old"), provider="benzinga_rest", via="backfill", received_at="2026-11-19T15:01:00Z", online=False), "STALE_AFTER_DELETE")
        self.assertEqual(store.apply(self.db, self.art(None, "old-untimed"), provider="benzinga_rest", via="backfill", received_at="2026-11-19T15:02:00Z", online=False), "STALE_AFTER_DELETE")
        self.assertEqual(store.read_for_portal()["items"], [])  # never published to the page: no current version
        self.assertEqual(self.db.execute("select deleted, latest_version, versions_n from items").fetchone()[:], (1, 0, 2))
        self.assertEqual(self.db.execute("select count(*) from tombstones").fetchone()[0], 1)

    def test_republish_after_removal_is_restored_by_rule(self):
        store.apply(self.db, self.art("2026-11-19T14:00:00Z", "v1"), provider="benzinga_stream", via="stream", received_at="2026-11-19T14:00:01Z")
        store.apply(self.db, self.art("2026-11-19T15:00:00Z", None, action="deleted"), provider="benzinga_stream", via="stream", received_at="2026-11-19T15:00:01Z")
        self.assertEqual(self.current(), ("v1", 1))
        self.assertEqual(store.apply(self.db, self.art("2026-11-19T16:00:00Z", "republished"), provider="benzinga_stream", via="stream", received_at="2026-11-19T16:00:01Z"), "RESTORED")
        self.assertEqual(self.current(), ("republished", 0))
        # a later deletion keeps the latest removal time; an earlier duplicate removal does not move it back
        store.apply(self.db, self.art("2026-11-19T17:00:00Z", None, action="deleted"), provider="benzinga_rest", via="removed_poll", received_at="2026-11-19T17:00:01Z")
        store.apply(self.db, self.art("2026-11-19T15:00:00Z", None, action="deleted"), provider="benzinga_rest", via="removed_poll", received_at="2026-11-19T17:01:00Z")
        self.assertEqual(self.db.execute("select deleted, deleted_provider_at from items").fetchone()[:], (1, "2026-11-19T17:00:00Z"))

    def test_stream_deletion_uses_event_time(self):
        d = adapters.normalize_benzinga(bz("Deleted", "m9", updated="Wed, 19 Nov 2026 08:00:00 -0500", ts="2026-11-19T15:30:00Z"))
        self.assertEqual((d["action"], d["provider_updated_at"]), ("deleted", "2026-11-19T15:30:00Z"))


class RestSyncTests(Base):
    """updatedSince paging, removals and the cursor (fake HTTP, no network)."""

    def test_sync_pages_updates_applies_removals_and_moves_cursor(self):
        from unittest import mock
        from urllib.parse import urlparse, parse_qs
        from scripts.quant_news_v1 import collector
        seen = []
        def fake(url, headers, timeout=20.0):
            q = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}; path = urlparse(url).path
            seen.append((path, q.get("updatedSince"), q.get("page"), q.get("sort")))
            if path.endswith("news-removed"):
                return {"removed": [{"id": 2, "updated": "Wed, 19 Nov 2026 10:00:00 -0500"}]}
            page = int(q["page"]); size = int(q["pageSize"])
            ids = list(range(1, 151))[page * size:(page + 1) * size]
            return [{"id": i, "title": f"t{i}", "created": "Wed, 19 Nov 2026 08:00:00 -0500", "updated": "Wed, 19 Nov 2026 09:00:00 -0500",
                     "url": f"https://www.benzinga.com/{i}", "stocks": [{"name": "MU"}]} for i in ids]
        rest = adapters.BenzingaRest("bzTESTtoken1234567890abcdef")
        with mock.patch.object(adapters, "http_json", side_effect=fake):
            c = collector.benzinga_rest_sync(self.db, "benzinga_rest", rest, {}, via="rest_poll")
        news = [x for x in seen if x[0] == "/api/v2/news"]
        self.assertEqual([x[2] for x in news], ["0", "1"])           # paged until a short page
        self.assertTrue(all(x[3] == "updated:asc" and x[1] for x in news))  # by UPDATE time, not creation
        self.assertEqual((c["news_new"], c["tombstones"], c["rest_pages_truncated"]), (150, 1, 0))
        self.assertEqual(self.db.execute("select deleted from items where upstream_id='2'").fetchone()[0], 1)
        cur1 = float(collector._cursor(self.db, "benzinga_rest:articles:done"))
        seen.clear()
        with mock.patch.object(adapters, "http_json", side_effect=fake):
            c = collector.benzinga_rest_sync(self.db, "benzinga_rest", rest, {"NEWS_OVERLAP_SECONDS": "300"}, via="rest_poll")
        self.assertEqual(int(seen[0][1]), int(cur1) - 300)               # next sync = cursor minus overlap
        self.assertEqual((c["news_new"], c["news_versions"], c["duplicates"]), (0, 0, 150))  # overlap re-reads are absorbed
        self.assertEqual(self.db.execute("select deleted from items where upstream_id='2'").fetchone()[0], 1)  # still deleted
        self.assertEqual([i["upstream_id"] for i in store.read_for_portal(500)["items"] if not i["deleted"] and i["upstream_id"] == "2"], [])

    def test_truncation_is_reported(self):
        from unittest import mock
        rest = adapters.BenzingaRest("bzTESTtoken1234567890abcdef")
        with mock.patch.object(adapters, "http_json", return_value=[{"id": 1, "updated": "Wed, 19 Nov 2026 09:00:00 -0500"}]):
            items, trunc = rest.updated_since(0, max_pages=2, page_size=1)
        self.assertTrue(trunc); self.assertEqual(len(items), 2)


class ProbeTests(Base):
    def test_stream_probe_no_replay_by_default_closes_and_redacts(self):
        from unittest import mock
        from scripts.quant_news_v1 import probe
        token = "bzWStoken0987654321zyxw"
        class WS:
            def __init__(self): self.sent, self.closed, self.n = [], False, 0
            def settimeout(self, t): pass
            def send(self, t): self.sent.append(t)
            def recv(self):
                self.n += 1
                if self.n == 1:  # a past article (event time before connect) and garbage containing the token
                    return json.dumps(BZ_CREATED)
                if self.n == 2:
                    return f"error for wss://api.benzinga.com/api/v1/news/stream?token={token}"
                raise RuntimeError(f"socket died url=wss://x?token={token}")
            def close(self): self.closed = True
        ws = WS()
        with mock.patch.dict(os.environ, {"BENZINGA_TOKEN": token}), mock.patch.object(adapters.BenzingaStream, "connect", return_value=ws):
            r = probe.probe("benzinga_stream", 2)
        text = json.dumps(r)
        self.assertNotIn(token, text); self.assertIn("token=***", text)
        self.assertNotIn("replay", ws.sent)                 # default: no replay
        self.assertTrue(ws.closed and r["connection_closed"])  # closed on the error path too
        self.assertEqual(r["result"], "UNEXPECTED")

    def test_replay_is_reported_separately(self):
        from unittest import mock
        from scripts.quant_news_v1 import probe
        class WS:
            sent, closed = [], False
            def send(self, t): self.sent.append(t)
            def close(self): self.closed = True
        ws = WS(); past = adapters.normalize_benzinga(BZ_CREATED)
        phases = iter([([], [], [], 0), ([], [past], [], 1)])  # phase 1: nothing new; phase 2 (after replay): one cached message
        with mock.patch.dict(os.environ, {"BENZINGA_TOKEN": "bzTESTtoken1234567890abcdef"}),                 mock.patch.object(adapters.BenzingaStream, "connect", return_value=ws),                 mock.patch.object(probe, "_listen", side_effect=lambda *a: next(phases)):
            r = probe.probe("benzinga_stream", 1, replay=True)
        self.assertEqual((r["new_pushes"]["sample_count"], r["replay"]["cached_messages"], ws.sent), (0, 1, ["replay"]))
        self.assertTrue(r["observed"].startswith("CONNECTED_NO_NEW_PUSH")); self.assertTrue(ws.closed)


class LiveVsLateTests(Base):
    def test_only_fresh_pushes_make_receiving(self):
        from datetime import datetime, timedelta, timezone
        from scripts.quant_news_v1 import collector
        iso = lambda d: d.strftime("%Y-%m-%dT%H:%M:%SZ")
        now = datetime.now(timezone.utc)
        old = bz("Created", "old-1"); old["data"]["timestamp"] = iso(now - timedelta(hours=2)); old["data"]["id"] = 501; old["data"]["content"]["id"] = 501
        fresh = bz("Created", "new-1"); fresh["data"]["timestamp"] = iso(now + timedelta(seconds=1)); fresh["data"]["id"] = 502; fresh["data"]["content"]["id"] = 502
        class WS:
            def __init__(self, frames): self.frames, self.sent = list(frames), []
            def settimeout(self, t): pass
            def send(self, t): self.sent.append(t)
            def recv(self):
                import websocket
                if self.frames:
                    return self.frames.pop(0)
                raise websocket.WebSocketConnectionClosedException()
            def close(self): pass
        class P:
            kind, ping_text, replay_text = "WEBSOCKET_PUSH", "ping", "replay"
            parse = staticmethod(adapters.BenzingaStream.parse)
            def __init__(self, frames): self.ws = WS(frames)
            def connect(self): return self.ws
        with self.assertRaises(adapters.ProviderError):
            collector._stream_loop(self.db, P([json.dumps(old)]), "benzinga_stream", {}, None, reconnect=False)
        st = self.db.execute("select state from provider_state").fetchone()[0]
        self.assertEqual(st, "CONNECTED_NO_MESSAGES_YET")  # a late/past event is stored but is not a live push
        self.assertEqual(self.db.execute("select via from versions where upstream_id='501'").fetchone()[0], "stream_late")
        self.assertIsNone(self.db.execute("select first_online_seen_at from items where upstream_id='501'").fetchone()[0])
        with self.assertRaises(adapters.ProviderError):
            collector._stream_loop(self.db, P([json.dumps(fresh)]), "benzinga_stream", {}, None, reconnect=False)
        self.assertEqual(self.db.execute("select state from provider_state").fetchone()[0], "RECEIVING")
        self.assertEqual(self.db.execute("select via from versions where upstream_id='502'").fetchone()[0], "stream")


class FrameShapeTests(unittest.TestCase):
    def test_string_lists_and_bad_shapes(self):
        m = json.loads(json.dumps(BZ_CREATED)); m["data"]["content"]["stocks"] = ["NVDA", "QQQ"]; m["data"]["content"]["channels"] = ["News"]
        n = adapters.BenzingaStream.parse(json.dumps(m))[0]
        self.assertEqual((n["symbols"], n["channels"]), (["NVDA", "QQQ"], ["News"]))
        self.assertEqual(len(adapters.BenzingaStream.parse(json.dumps([BZ_CREATED, BZ_CREATED]))), 2)
        bad = {"id": "x", "data": {"action": "created", "id": 1, "content": "a string body"}}
        with self.assertRaises(ValueError) as c:
            adapters.BenzingaStream.parse(json.dumps(bad))
        self.assertNotIn("a string body", str(c.exception))  # structure only, never content


class HeartbeatTests(Base):
    def test_heartbeat_runs_on_clock_while_server_pings(self):
        import websocket
        from unittest import mock
        from scripts.quant_news_v1 import collector
        class F:
            def __init__(self, d): self.data = d
        class WS:
            def __init__(self): self.sent, self.n = [], 0
            def settimeout(self, t): pass
            def send(self, t): self.sent.append(t)
            def recv_data_frame(self, c=True):
                self.n += 1
                if self.n < 5:
                    return websocket.ABNF.OPCODE_PING, F(b"")  # server keeps pinging: reads never time out
                return websocket.ABNF.OPCODE_CLOSE, F((4003).to_bytes(2, "big") + b"bye")
            def close(self): pass
        class P:
            kind, ping_text, replay_text = "WEBSOCKET_PUSH", "ping", "replay"
            parse = staticmethod(adapters.BenzingaStream.parse)
            def __init__(self): self.ws = WS()
            def connect(self): return self.ws
        p = P(); clock = iter([0, 0, 40, 40, 80, 80, 120, 120, 160, 160, 200, 200, 240, 240])
        with mock.patch.object(collector.time, "monotonic", side_effect=lambda: next(clock)):
            with self.assertRaises(adapters.ProviderError) as c:
                collector._stream_loop(self.db, p, "benzinga_stream", {}, None, reconnect=False)
        self.assertIn("code 4003", c.exception.detail)          # server close code recorded
        self.assertGreaterEqual(p.ws.sent.count("ping"), 2)     # heartbeat ran although no read timed out


# Shape observed on the live stream 2026-11-19 (values synthetic): event id in data.id, article id in content.id.
def live(action, event_id, rev, updated_at, title="t"):
    return {"api_version": "websocket/v1", "kind": "News/v1",
            "data": {"action": action, "id": event_id, "timestamp": updated_at,
                     "content": {"id": 63418209, "revision_id": rev, "type": "story", "title": title, "body": "b", "authors": ["A", "B"],
                                 "teaser": "", "url": "https://www.benzinga.com/content/63418209/x", "tags": ["T"],
                                 "securities": [{"symbol": "NVDA", "exchange": "NASDAQ", "primary": False}, {"symbol": "MSFT", "exchange": "NASDAQ", "primary": False}],
                                 "channels": ["News"], "image": [], "created_at": "2026-11-19T06:40:12.000Z", "updated_at": updated_at}}}


class LiveShapeTests(Base):
    def test_one_article_three_events_is_one_item_three_versions(self):
        from scripts.quant_news_v1 import collector
        evs = [live("Created", 111471500, 73600320, "2026-11-19T06:40:22.043Z", "v1"),
               live("Updated", 111471501, 73600321, "2026-11-19T06:40:41.680Z", "v2"),
               live("Updated", 111471502, 73600322, "2026-11-19T06:41:06.214Z", "v3")]
        n = adapters.normalize_benzinga(evs[0])
        self.assertEqual((n["upstream_id"], n["message_id"], n["revision_id"], n["symbols"], n["authors"], n["provider_created_at"], n["provider_updated_at"]),
                         ("63418209", 111471500, 73600320, ["MSFT", "NVDA"], "A, B", "2026-11-19T06:40:12Z", "2026-11-19T06:40:22.043Z"))
        c = collector._apply_all(self.db, "benzinga_stream", [adapters.normalize_benzinga(e) for e in evs], via="stream")
        self.assertEqual((c["news_new"], c["news_versions"]), (1, 2))
        self.assertEqual(self.db.execute("select count(*), max(versions_n) from items").fetchone()[:], (1, 3))
        self.assertEqual(store.read_for_portal()["items"][0]["title"], "v3")
        # replayed event (same event id) is a message-level duplicate
        c = collector._apply_all(self.db, "benzinga_stream", [adapters.normalize_benzinga(evs[1])], via="replay")
        self.assertEqual(c["duplicates"], 1)
        # the same article via REST (article id) joins the same item; no message id, so content decides
        r = adapters.normalize_benzinga_rest({"id": 63418209, "title": "v3", "created": "Thu, 19 Nov 2026 02:40:12 -0400",
                                              "updated": "Thu, 19 Nov 2026 02:41:06 -0400", "stocks": [{"name": "NVDA"}]})
        self.assertIsNone(r["message_id"]); self.assertEqual(r["upstream_id"], "63418209")
        self.assertIn(store.apply(self.db, r, provider="benzinga_rest", via="gap_fill_rest", received_at="2026-11-19T06:50:00Z", online=False),
                      ("HISTORY_VERSION", "CONFLICT_KEPT"))  # REST time has whole seconds: not newer than 06:41:06.214
        self.assertEqual(store.read_for_portal()["items"][0]["title"], "v3")


class RebuildTests(Base):
    def test_rebuild_merges_events_by_article_id_and_keeps_original(self):
        import sqlite3
        from pathlib import Path
        from scripts.quant_news_v1 import rebuild
        # simulate the pre-fix cache: three live events stored as three items keyed by event id
        for ev in (live("Created", 111471500, 1, "2026-11-19T06:40:22.043Z", "v1"), live("Updated", 111471501, 2, "2026-11-19T06:40:41.680Z", "v2")):
            n = adapters.normalize_benzinga(ev); n["upstream_id"] = str(ev["data"]["id"])
            store.apply(self.db, n, provider="benzinga_stream", via="stream", received_at=ev["data"]["timestamp"])
        store.apply(self.db, adapters.normalize_benzinga_rest({"id": 5, "title": "r", "updated": "Thu, 19 Nov 2026 02:10:52 -0400"}),
                    provider="benzinga_stream", via="backfill", received_at="2026-11-19T06:36:15Z", online=False)
        store.set_state(self.db, "benzinga_stream", "RECEIVING", detail="x")
        self.assertEqual(self.db.execute("select count(*) from items").fetchone()[0], 3)
        self.db.close()
        src = Path(os.environ["QUANT_NEWS_DB"])
        out = rebuild.rebuild(src, "pre_idfix_test")
        self.assertTrue((src.parent / "n.pre_idfix_test.sqlite3").exists())
        self.db = store.connect()
        self.assertEqual((out["items"], out["versions"]), (2, 3))
        rows = dict(self.db.execute("select upstream_id, versions_n from items").fetchall())
        self.assertEqual(rows, {"63418209": 2, "5": 1})
        self.assertEqual(self.db.execute("select first_online_seen_at from items where upstream_id='63418209'").fetchone()[0], "2026-11-19T06:40:22.043Z")
        self.assertIsNone(self.db.execute("select first_online_seen_at from items where upstream_id='5'").fetchone()[0])
        self.assertEqual(self.db.execute("select state from provider_state").fetchone()[0], "RECEIVING")
        old = sqlite3.connect(src.parent / "n.pre_idfix_test.sqlite3"); self.assertEqual(old.execute("select count(*) from items").fetchone()[0], 3); old.close()


class PagingWatermarkTests(Base):
    """Truncated / failed windows must not advance the completion watermark; the next sync resumes the tail."""
    T0500, T0630 = 1795064400, 1795069800  # 2026-11-19T05:00:00Z, 06:30:00Z (epoch)

    class Server:
        def __init__(self, articles=0, removed=0, same_time=False, fail_page=None):
            self.articles = [{"id": 1000 + i, "title": f"t{i}", "created": "Thu, 19 Nov 2026 00:00:00 -0500",
                              "updated": "Thu, 19 Nov 2026 00:10:00 -0500" if same_time else f"Thu, 19 Nov 2026 00:{10 + i // 60:02d}:{i % 60:02d} -0500",
                              "url": f"https://www.benzinga.com/{1000 + i}"} for i in range(articles)]
            self.removed = [{"id": 5000 + i, "updated": "Thu, 19 Nov 2026 00:20:00 -0500"} for i in range(removed)]
            self.calls = []; self.fail_page = fail_page

        def fetch_page(self, ch, since, page, size):
            self.calls.append((ch, since, page, size))
            if self.fail_page == (ch, page):
                self.fail_page = None
                raise adapters.ProviderError("NETWORK_TIMEOUT", "timeout")
            rows = (self.articles if ch == "articles" else self.removed)[page * size:(page + 1) * size]
            if ch == "articles":
                return [adapters.normalize_benzinga_rest(r) for r in rows], len(rows)
            return [{"upstream": "benzinga", "upstream_id": str(r["id"]), "message_id": None, "action": "deleted",
                     "provider_updated_at": adapters.to_utc(r["updated"]), "provider_created_at": None, "symbols": [], "channels": [], "raw": r}
                    for r in rows], len(rows)

    def sync(self, srv, clock, env=None):
        from unittest import mock
        from scripts.quant_news_v1 import collector
        e = {"NEWS_MAX_PAGES": "1", "NEWS_OVERLAP_SECONDS": "300", **(env or {})}
        with mock.patch.object(collector.time, "time", return_value=clock):
            return collector.benzinga_rest_sync(self.db, "benzinga_rest", srv, e, via="rest_poll")

    def state(self):
        from scripts.quant_news_v1 import collector
        return collector.sync_state(self.db, "benzinga_rest")

    def seed_done(self, epoch):
        from scripts.quant_news_v1 import collector
        for ch in ("articles", "removed"):
            collector._cursor(self.db, f"benzinga_rest:{ch}:done", epoch)

    def test_101_items_page_100_max_pages_1(self):
        srv = self.Server(articles=101); self.seed_done(self.T0500)
        c = self.sync(srv, self.T0630)
        st = self.state()["articles"]
        self.assertEqual(c["rest_pages_truncated"], 1)
        self.assertEqual(st["done"], self.T0500)  # NOT moved to 06:30
        self.assertEqual(st["pending"], {"since": self.T0500 - 300, "target": self.T0630, "next_page": 1, "reason": "max_pages"})
        self.assertEqual(self.db.execute("select count(*) from items").fetchone()[0], 100)
        self.sync(srv, self.T0630 + 600)  # resumes page 1 of the SAME window, not page 0
        self.assertEqual([x[:3] for x in srv.calls if x[0] == "articles"][-1], ("articles", self.T0500 - 300, 1))
        self.assertEqual(self.db.execute("select count(*) from items").fetchone()[0], 101)
        st = self.state()["articles"]
        self.assertEqual((st["done"], st["pending"]), (self.T0630, None))  # watermark = target of the finished window
        self.sync(srv, self.T0630 + 1200)
        self.assertEqual([x[:3] for x in srv.calls if x[0] == "articles"][-1], ("articles", self.T0630 - 300, 0))

    def test_removed_channel_truncation(self):
        srv = self.Server(articles=0, removed=3); self.seed_done(self.T0500)
        self.sync(srv, self.T0630, {"NEWS_PAGE_SIZE_REMOVED": "2"})
        st = self.state()["removed"]
        self.assertEqual((st["done"], st["pending"]["next_page"]), (self.T0500, 1))
        self.sync(srv, self.T0630 + 60, {"NEWS_PAGE_SIZE_REMOVED": "2"})
        self.assertEqual(self.db.execute("select count(*) from tombstones").fetchone()[0], 3)
        self.assertEqual(self.state()["removed"]["done"], self.T0630)

    def test_failure_mid_window_keeps_watermark_and_resumes(self):
        srv = self.Server(articles=150, fail_page=("articles", 1)); self.seed_done(self.T0500)
        with self.assertRaises(adapters.ProviderError):
            self.sync(srv, self.T0630, {"NEWS_MAX_PAGES": "5"})
        st = self.state()["articles"]
        self.assertEqual((st["done"], st["pending"]["next_page"], st["pending"]["reason"]), (self.T0500, 1, "error"))
        self.assertEqual(self.db.execute("select count(*) from items").fetchone()[0], 100)  # page 0 kept
        self.sync(srv, self.T0630 + 60, {"NEWS_MAX_PAGES": "5"})
        self.assertEqual(self.db.execute("select count(*) from items").fetchone()[0], 150)
        self.assertEqual(self.state()["articles"]["done"], self.T0630)

    def test_same_update_time_across_page_boundary(self):
        srv = self.Server(articles=101, same_time=True); self.seed_done(self.T0500)
        self.sync(srv, self.T0630); self.sync(srv, self.T0630 + 60)
        self.assertEqual(self.db.execute("select count(*) from items").fetchone()[0], 101)


class DeleteReorderTests(Base):
    def art(self, updated, title, action="created"):
        return {"upstream": "benzinga", "upstream_id": "88", "message_id": None, "action": action, "title": title, "teaser": None, "body": "b",
                "url": "https://www.benzinga.com/88", "source": "Benzinga", "authors": None, "symbols": ["MU"], "channels": [],
                "provider_created_at": "2026-11-19T05:00:00Z", "provider_updated_at": updated, "raw": {}}

    def visible(self):
        r = [i for i in store.read_for_portal()["items"] if i["upstream_id"] == "88"]
        return (r[0]["title"], r[0]["deleted"]) if r else None

    def test_old_delete_replayed_after_republish_does_not_hide(self):
        ap = lambda n, t: store.apply(self.db, n, provider="benzinga_stream", via="stream", received_at=t)
        self.assertEqual(ap(self.art("2026-11-19T05:00:00Z", "v1"), "2026-11-19T05:00:01Z"), "NEW")
        self.assertEqual(ap(self.art("2026-11-19T05:10:00Z", None, "deleted"), "2026-11-19T05:10:01Z"), "TOMBSTONE")
        self.assertEqual(self.visible(), ("v1", 1))
        self.assertEqual(ap(self.art("2026-11-19T05:20:00Z", "v2 republished"), "2026-11-19T05:20:01Z"), "RESTORED")
        self.assertEqual(self.visible(), ("v2 republished", 0))
        self.assertEqual(ap(self.art("2026-11-19T05:10:00Z", None, "deleted"), "2026-11-19T05:25:00Z"), "STALE_TOMBSTONE")  # replay
        self.assertEqual(self.visible(), ("v2 republished", 0))
        self.assertEqual(self.db.execute("select count(*) from tombstones").fetchone()[0], 2)  # replay kept as history
        self.assertEqual(ap(self.art("2026-11-19T05:30:00Z", None, "deleted"), "2026-11-19T05:30:01Z"), "TOMBSTONE")  # genuinely newer
        self.assertEqual(self.visible(), ("v2 republished", 1))

    def test_delete_then_old_backfill_still_cannot_resurrect(self):
        ap = lambda n, t, v="stream": store.apply(self.db, n, provider="benzinga_rest", via=v, received_at=t, online=False)
        ap(self.art("2026-11-19T05:00:00Z", "v1"), "2026-11-19T05:00:01Z")
        ap(self.art("2026-11-19T05:10:00Z", None, "deleted"), "2026-11-19T05:10:01Z", "removed_poll")
        self.assertEqual(ap(self.art("2026-11-19T05:00:00Z", "v1 old copy"), "2026-11-19T05:11:00Z", "backfill"), "STALE_AFTER_DELETE")
        self.assertEqual(self.visible(), ("v1", 1))


class BackupRestoreCheckTests(Base):
    def test_fixed_copy_restore_reconciles_and_detects_tampering(self):
        import sqlite3
        from pathlib import Path
        from scripts.quant_news_v1 import backup_check
        store.apply(self.db, adapters.normalize_benzinga(BZ_CREATED), provider="benzinga_stream", via="stream", received_at="2026-11-19T13:35:15Z")
        store.set_state(self.db, "benzinga_stream", "RECEIVING", detail="x")
        src = Path(os.environ["QUANT_NEWS_DB"]); fixed = src.with_name("fixed.sqlite3")
        d = sqlite3.connect(fixed); self.db.backup(d); d.close()
        r = backup_check.restore_check(fixed, src.parent / "restore")
        self.assertTrue(r["ok"], r["problems"]); self.assertEqual(r["integrity_check"], ["ok"])
        self.assertTrue(all(v["restored_equal"] for v in r["tables"].values()))
        self.assertEqual((r["semantic"]["items"], r["semantic"]["versions"]), (1, 1))
        with self.assertRaises(FileExistsError):  # never overwrites an existing restore directory
            backup_check.restore_check(fixed, src.parent / "restore")
        con = sqlite3.connect(src.parent / "restore" / "fixed.sqlite3"); con.execute("UPDATE versions SET title='tampered'"); con.commit(); con.close()
        self.assertNotEqual(backup_check.digest(src.parent / "restore" / "fixed.sqlite3")["tables"]["versions"]["sha256"], r["tables"]["versions"]["sha256"])


if __name__ == "__main__":
    unittest.main()
