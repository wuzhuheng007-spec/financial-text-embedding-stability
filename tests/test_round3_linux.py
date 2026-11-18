"""Round-3 checks: retention, supervisor policy, OAuth callback plumbing (mock only)."""
import json
import socket
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "deploy/linux"))
from scripts.quant_personal_feed import retention  # noqa: E402
import supervisor  # noqa: E402

DDL_QUOTES = """CREATE TABLE session_quotes (
  id INTEGER PRIMARY KEY, owner_id INTEGER NOT NULL, symbol TEXT NOT NULL,
  provider_code TEXT NOT NULL, session TEXT NOT NULL, price_text TEXT NOT NULL,
  currency TEXT, source_at TEXT, received_at TEXT NOT NULL,
  provider_field_path TEXT NOT NULL, source_time_field_path TEXT,
  trade_date TEXT, date_basis TEXT NOT NULL,
  parent_date_hint TEXT, parent_source_at_hint TEXT, delay_status TEXT NOT NULL,
  source_channel TEXT NOT NULL, raw_sha256 TEXT NOT NULL)"""


def build_feed(path: Path, now: datetime) -> None:
    with closing(sqlite3.connect(path)) as con, con:
        con.execute("PRAGMA journal_mode=WAL")
        con.execute(DDL_QUOTES)
        con.execute("CREATE INDEX q1 ON session_quotes(owner_id,symbol,session,source_at DESC,received_at DESC,id DESC)")
        con.execute("CREATE TABLE session_raw_responses (sha256 TEXT PRIMARY KEY, received_at TEXT NOT NULL, source_channel TEXT NOT NULL, raw_json TEXT NOT NULL)")
        rows = []
        for i in range(3000):
            age_days = 40 if i < 2000 else 1
            stamp = (now - timedelta(days=age_days, seconds=i)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
            rows.append((i + 1, 1, "SYN" + str(i % 3), "US.SYN", "OVERNIGHT", f"{100 + i % 7}.00", None, stamp, stamp,
                         "p", None, None, "SYNTHETIC", None, None, "UNKNOWN", "WS", f"raw{i}"))
        con.executemany("INSERT INTO session_quotes VALUES(" + ",".join("?" * 18) + ")", rows)
        # Raw evidence still present for 50 old rows -> protected
        con.executemany("INSERT INTO session_raw_responses VALUES(?,?,?,?)",
                        [(f"raw{i}", "x", "WS", "{}") for i in range(100, 150)])


class RetentionTests(unittest.TestCase):
    def test_dry_run_apply_archive_and_protection(self):
        now = datetime(2026, 11, 18, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmp:
            db, archive = Path(tmp) / "feed.sqlite3", Path(tmp) / "archive"
            build_feed(db, now)
            dry = retention.run(db, archive, 30, apply=False, now=now, root=ROOT)
            self.assertEqual(dry["mode"], "DRY_RUN")
            self.assertEqual(dry["protected"]["recent_window"], 1000)
            self.assertEqual(dry["protected"]["raw_evidence_present"], 50)
            # latest row per cell: cells are within the recent window here, so 0 old latest rows
            self.assertEqual(dry["selected_rows"], 1950)
            self.assertFalse(archive.exists())
            with closing(sqlite3.connect(db)) as con:
                self.assertEqual(con.execute("SELECT count(*) FROM session_quotes").fetchone()[0], 3000)
            size_before = db.stat().st_size
            applied = retention.run(db, archive, 30, apply=True, batch=500, pause=0, now=now, root=ROOT)
            self.assertTrue(applied["archive_verification"]["ok"])
            self.assertEqual(applied["deleted_rows"], 1950)
            self.assertEqual(applied["delete"]["batches"], 4)
            with closing(sqlite3.connect(db)) as con:
                self.assertEqual(con.execute("SELECT count(*) FROM session_quotes").fetchone()[0], 1050)
                self.assertEqual(con.execute("SELECT count(*) FROM session_quotes WHERE raw_sha256 BETWEEN 'raw100' AND 'raw149'").fetchone()[0] >= 50, True)
            after = applied["space_after"]
            self.assertGreater(after["freelist_pages"] + after["file_bytes"]["-wal"], 0)
            self.assertGreaterEqual(after["file_bytes"]["main"], size_before)  # no VACUUM: file does not shrink

    def test_latest_cell_is_protected_even_when_old(self):
        now = datetime(2026, 11, 18, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "feed.sqlite3"
            with closing(sqlite3.connect(db)) as con, con:
                con.execute(DDL_QUOTES)
                con.execute("CREATE TABLE session_raw_responses (sha256 TEXT PRIMARY KEY, received_at TEXT, source_channel TEXT, raw_json TEXT)")
                old = (now - timedelta(days=60)).strftime("%Y-%m-%dT%H:%M:%SZ")
                older = (now - timedelta(days=61)).strftime("%Y-%m-%dT%H:%M:%SZ")
                for i, stamp in ((1, older), (2, old)):
                    con.execute("INSERT INTO session_quotes VALUES(" + ",".join("?" * 18) + ")",
                                (i, 1, "SYN", "US.SYN", "PRE", "1", None, stamp, stamp, "p", None, None, "S",
                                 None, None, "U", "WS", f"r{i}"))
            res = retention.run(db, Path(tmp) / "a", 30, apply=False, now=now, root=ROOT)
            self.assertEqual(res["selected_rows"], 1)
            self.assertEqual(res["protected"]["latest_cell"], 1)

    def test_minimum_keep_and_no_research_reference(self):
        with self.assertRaises(ValueError):
            retention.candidates(sqlite3.connect(":memory:"), 7, datetime.now(timezone.utc))
        self.assertEqual(retention.scan_research_references(ROOT), {})


def provider_db(path: Path, rows: list[tuple]) -> None:
    with closing(sqlite3.connect(path)) as con, con:
        con.execute("CREATE TABLE provider_state(provider TEXT, state TEXT, error_class TEXT, worker_alive INTEGER)")
        con.executemany("INSERT INTO provider_state VALUES(?,?,?,?)", rows)


class SupervisorTests(unittest.TestCase):
    def test_feed_classification(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "f.sqlite3"
            enabled = {"moomoo": True, "jin10": False}
            provider_db(db, [("moomoo", "DISCONNECTED", "OAuthError", 0)])
            self.assertEqual(supervisor.classify_feed(db, enabled, True)[0], supervisor.HOLD)
            db.unlink()
            provider_db(db, [("moomoo", "DISCONNECTED", "ConnectionResetError", 0)])
            self.assertEqual(supervisor.classify_feed(db, enabled, True)[0], supervisor.RESTART)
            db.unlink()
            provider_db(db, [("moomoo", "CONNECTED_DELAY_UNKNOWN", None, 1),
                             ("moomoo_snapshot", "SECURITY_POLICY_BLOCKED", "WEBAPI_SECURITY_POLICY_BLOCKED", 0)])
            self.assertEqual(supervisor.classify_feed(db, enabled, True)[0], supervisor.OK)
            db.unlink()
            provider_db(db, [("moomoo", "AUTH_EXPIRED", "AUTHORIZATION", 0)])
            self.assertEqual(supervisor.classify_feed(db, enabled, False)[0], supervisor.HOLD)

    def test_daily_and_research_hold(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = Path(tmp) / "s.json"
            s.write_text(json.dumps({"last_supplier_http_status": 439}))
            self.assertEqual(supervisor.classify_daily(s, False, 300)[0], supervisor.HOLD)
            s.write_text(json.dumps({"state": "ERROR_PAUSED"}))
            self.assertEqual(supervisor.classify_research(s, Path(tmp) / "none", False, 300)[0], supervisor.HOLD)
            s.write_text(json.dumps({"state": "RUNNING"}))
            self.assertEqual(supervisor.classify_research(s, Path(tmp) / "none", False, 300)[0], supervisor.RESTART)

    def _run(self, classify, tmp):
        policy = supervisor.Policy(initial_backoff=0.2, max_backoff=0.8, max_restarts_per_hour=3,
                                   thread_dead_grace=0, poll=0.1, stop_grace=2)
        sup = supervisor.Supervisor("t", [sys.executable, "-c", "import sys,time;time.sleep(0.2);sys.exit(3)"],
                                    classify, Path(tmp) / "state.json", policy)
        import threading
        result = {}
        t = threading.Thread(target=lambda: result.setdefault("rc", sup.run()))
        t.start()
        deadline = __import__("time").monotonic() + 15
        while __import__("time").monotonic() < deadline:
            state = json.loads((Path(tmp) / "state.json").read_text()) if (Path(tmp) / "state.json").exists() else {}
            if state.get("state") == "HOLD":
                break
            __import__("time").sleep(0.05)
        sup.stopping = True
        t.join(10)
        return sup, json.loads((Path(tmp) / "state.json").read_text())

    def test_restart_limit_then_hold(self):
        with tempfile.TemporaryDirectory() as tmp:
            sup, last = self._run(lambda alive: (supervisor.OK, "x") if alive else (supervisor.RESTART, "exited"), tmp)
            self.assertEqual(len(sup.restarts), 3)
            self.assertIn(last["state"], ("HOLD", "STOPPED"))

    def test_terminal_error_never_restarts(self):
        with tempfile.TemporaryDirectory() as tmp:
            sup, last = self._run(lambda alive: (supervisor.OK, "x") if alive else (supervisor.HOLD, "auth"), tmp)
            self.assertEqual(len(sup.restarts), 0)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class OAuthMockTests(unittest.TestCase):
    def drive(self, case):
        port = free_port()
        proc = subprocess.Popen([sys.executable, str(ROOT / "deploy/linux/oauth_mock_check.py"), "serve", "--case", case,
                                 "--port", str(port), "--bind", "127.0.0.1"], stdout=subprocess.PIPE, text=True, cwd=ROOT)
        consent = json.loads(proc.stdout.readline())
        self.assertEqual(consent["event"], "consent")
        self.assertEqual(consent["redirect_uri"], f"http://127.0.0.1:{port}/callback")
        self.assertEqual(consent["scope"], "quote:read")
        self.assertEqual(consent["pkce_method"], "S256")
        try:
            urllib.request.urlopen(consent["redirect_uri"] + "?" + consent["callback_query"], timeout=10).read()
        except urllib.error.HTTPError:
            pass
        result = json.loads(proc.stdout.readline())
        self.assertEqual(proc.wait(30), 0, result)
        with socket.socket() as s:
            self.assertNotEqual(s.connect_ex(("127.0.0.1", port)), 0)  # port released
        return result

    def test_ok(self):
        r = self.drive("ok")
        self.assertEqual(r["stored_state"], "QUOTE_READ_AUTHORIZED")

    def test_bad_state(self):
        self.assertEqual(self.drive("bad_state")["stored_state"], "NOT_INSTALLED")

    def test_provider_error(self):
        self.assertEqual(self.drive("provider_error")["stored_state"], "NOT_INSTALLED")

    def test_redirect_mismatch(self):
        self.assertEqual(self.drive("redirect_mismatch")["stored_state"], "NOT_INSTALLED")


if __name__ == "__main__":
    unittest.main()
