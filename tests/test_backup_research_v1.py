"""R1 backup boundaries: full streaming SQLite digests; research backup scope is explicit (a missing
research backup FAILS unless configured NOT_REQUIRED); the isolated restore re-runs only its own new
run ids and never consumes or changes original QUEUED/RUNNING requests."""
import os as _os
_os.environ.setdefault("QUANT_CODE_VERSION", "test-code-version")
_os.environ.setdefault("QUANT_IMAGE_ID", "sha256:test-image")

import contextlib
import importlib.util
import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
_os.environ.setdefault("QUANT_PROTOCOL_DIR", str(REPO / "configs"))  # in the image this is /app/configs
REQUIRED = json.loads((REPO / "deploy/linux/host/backup_scope.json").read_text(encoding="utf-8"))
NOT_REQUIRED = {"research": {"requirement": "NOT_REQUIRED", "required": {}}}


def load(name):
    spec = importlib.util.spec_from_file_location(name, REPO / "deploy/linux/host" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod); return mod


def quiet(fn, *a):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        res = fn(*a)
    return res, buf.getvalue()


class SqliteDigestTests(unittest.TestCase):
    def test_table_above_old_cap_is_fully_digested_restored_and_tamper_detected(self):
        sb = load("quant_sqlite_backup")
        with tempfile.TemporaryDirectory() as t:
            src, out, rst = Path(t) / "src", Path(t) / "out", Path(t) / "rst"; src.mkdir()
            con = sqlite3.connect(src / "big.sqlite3")
            con.execute("create table big(id integer primary key, v text)")
            con.executemany("insert into big(v) values(?)", ((f"row-{i}",) for i in range(200_001)))
            con.commit(); con.close()
            sb.backup(src, out)
            man = json.loads((out / "MANIFEST.json").read_text())
            self.assertEqual(man["big.sqlite3"]["copy"]["big"]["rows"], 200_001)
            self.assertIsNotNone(man["big.sqlite3"]["copy"]["big"]["sha256"])
            ok = sb.verify(out, rst)
            self.assertTrue(ok["ok"]); self.assertEqual(ok["databases"]["big.sqlite3"]["tables_without_digest"], [])
            con = sqlite3.connect(out / "big.sqlite3"); con.execute("update big set v='x' where id=150000"); con.commit(); con.close()
            self.assertFalse(sb.verify(out, Path(t) / "rst2")["ok"])

    def test_null_digest_is_never_a_match(self):
        sb = load("quant_sqlite_backup")
        with tempfile.TemporaryDirectory() as t:
            src, out = Path(t) / "src", Path(t) / "out"; src.mkdir()
            con = sqlite3.connect(src / "a.sqlite3"); con.execute("create table x(a)"); con.execute("insert into x values(1)"); con.commit(); con.close()
            sb.backup(src, out)
            man = json.loads((out / "MANIFEST.json").read_text()); man["a.sqlite3"]["copy"]["x"]["sha256"] = None
            (out / "MANIFEST.json").write_text(json.dumps(man))
            self.assertFalse(sb.verify(out, Path(t) / "r")["ok"])


class ResearchBackupTests(unittest.TestCase):
    """One synthetic research root with SUCCEEDED x2, an EARLIER QUEUED request, RUNNING, FAILED, CANCELLED."""

    def setUp(self):
        self.rb = load("quant_research_backup")
        from scripts.quant_research_v1 import engine, registry
        from scripts.quant_research_v1.worker import run_once
        self.tmp = tempfile.TemporaryDirectory(); t = Path(self.tmp.name)
        self.var = t / "var"; root = self.var / "research"
        os.environ["QUANT_RESEARCH_ROOT"] = str(root)
        src = t / "src.sqlite"; con = sqlite3.connect(src)
        con.execute("CREATE TABLE research_daily(symbol TEXT, autype INT, local_date TEXT, open REAL, high REAL, low REAL, close REAL, source_archive_sha256 TEXT)")
        p = 100.0
        for i in range(300):
            o = p; p *= 1.004 if (i // 25) % 2 == 0 else 0.997
            con.execute("INSERT INTO research_daily VALUES('SYN',1,?,?,?,?,?,'s')",
                        (f"{2019 + i // 250}-{1 + (i % 250) // 21:02d}-{1 + (i % 250) % 21:02d}", o, max(o, p), min(o, p), p))
        con.commit(); con.close()
        db = registry.connect()
        m = engine.build_snapshot(src, "SYN", 1, root / "datasets/syn/1", dataset_id="syn", dataset_version="1",
                                  title="s", source_receipt={}, audit_status="SYNTHETIC")
        registry.register_dataset(db, m, "datasets/syn/1"); registry.register_factor(db, engine.FACTORS[("mom20", "1")])
        cfg = json.loads((REPO / "configs/quant_research_mom20_v1.json").read_text(encoding="utf-8"))
        cfg["segments"] = [{"name": "development", "start": "2019-01-01", "end": "2019-12-31"}, {"name": "holdout", "start": "2020-01-01", "end": "2020-12-31"}]
        self.done = []
        for _ in range(2):
            self.done.append(registry.create_run(db, ("syn", "1"), ("mom20", "1"), cfg)); quiet(run_once, 60)
        self.queued = registry.create_run(db, ("syn", "1"), ("mom20", "1"), cfg)
        db.execute("UPDATE runs SET created_at='2000-01-01T00:00:00Z' WHERE run_id=?", (self.queued,))  # earlier than any rerun
        self.running = registry.create_run(db, ("syn", "1"), ("mom20", "1"), cfg)
        db.execute("UPDATE runs SET status='RUNNING', started_at='2026-01-01T00:00:00Z', worker_id='gone:1', lease_expires_at='2000-01-01T00:00:00Z' WHERE run_id=?", (self.running,))
        self.failed = registry.create_run(db, ("syn", "1"), ("mom20", "1"), cfg)
        db.execute("UPDATE runs SET status='FAILED', exit_reason='ERROR' WHERE run_id=?", (self.failed,))
        self.cancelled = registry.create_run(db, ("syn", "1"), ("mom20", "1"), cfg)
        db.commit()
        registry.request_cancel(db, self.cancelled)
        db.close()
        self.out = t / "backup"; self.out.mkdir()
        res, _ = quiet(self.rb.backup, self.var, self.out, REQUIRED)
        self.assertTrue(res["ok"])
        self.assertEqual(sorted(res["incomplete_runs"]), sorted([self.queued, self.running]))

    def tearDown(self):
        os.environ.pop("QUANT_RESEARCH_ROOT", None); os.environ.pop("QUANT_LIVE_WORKERS", None)
        self.tmp.cleanup()

    def verify(self, name):
        res, stdout = quiet(self.rb.verify, self.out, Path(self.tmp.name) / name, REQUIRED)
        self.assertEqual(stdout, "")  # stdout is reserved for the JSON report
        return res

    def test_complete_copy_reruns_only_new_ids_and_leaves_originals_unchanged(self):
        shutil.rmtree(self.var)  # the restore must not need (or read) the source
        res = self.verify("restore")
        self.assertTrue(res["ok"], res["problems"]); self.assertEqual(res["state"], "OK")
        self.assertEqual(sorted(r["original_run_id"] for r in res["reruns"]), sorted(self.done))
        for r in res["reruns"]:
            self.assertNotIn(r["new_run_id"], self.done)
            self.assertTrue(r["digest_equal"] and r["config_sha256_equal"] and all(r["ledgers_byte_equal"].values()))
        self.assertEqual(res["original_runs_changed_by_restore_check"], [])
        con = sqlite3.connect(Path(self.tmp.name) / "restore/research/registry.sqlite3")
        st = dict(con.execute("select run_id, status from runs"))
        con.close()
        self.assertEqual((st[self.queued], st[self.running], st[self.failed], st[self.cancelled]), ("QUEUED", "RUNNING", "FAILED", "CANCELLED"))
        self.assertFalse((Path(self.tmp.name) / "restore/research/runs" / self.queued).exists())  # never executed

    def test_missing_directory_fails(self):
        shutil.rmtree(self.out / "research")
        res = self.verify("r"); self.assertEqual((res["ok"], res["state"]), (False, "FAILED_MISSING_DIRECTORY"))

    def test_missing_registry_fails(self):
        (self.out / "research/registry.sqlite3").unlink()
        res = self.verify("r"); self.assertEqual((res["ok"], res["state"]), (False, "FAILED_MISSING_REGISTRY"))

    def test_missing_or_corrupt_manifest_fails(self):
        mp = self.out / "research/RESEARCH_MANIFEST.json"
        mp.write_text("{not json"); res = self.verify("r1")
        self.assertEqual((res["ok"], res["state"]), (False, "FAILED_CORRUPT_MANIFEST"))
        mp.unlink(); res = self.verify("r2")
        self.assertEqual((res["ok"], res["state"]), (False, "FAILED_MISSING_MANIFEST"))

    def test_required_file_missing_even_if_also_removed_from_manifest(self):
        rel = f"runs/{self.done[0]}/ledger_cost5bps.csv"
        (self.out / "research/files" / rel).unlink()
        mp = self.out / "research/RESEARCH_MANIFEST.json"; m = json.loads(mp.read_text())
        m["files"] = [f for f in m["files"] if f["rel"] != rel]; mp.write_text(json.dumps(m))
        res = self.verify("r")
        self.assertFalse(res["ok"]); self.assertEqual(res["state"], "FAILED_SCOPE_CHECK")
        self.assertIn(f"missing required file: {rel}", res["problems"])

    def test_skipped_only_when_explicitly_not_required(self):
        shutil.rmtree(self.out / "research")
        res, _ = quiet(self.rb.verify, self.out, Path(self.tmp.name) / "r", NOT_REQUIRED)
        self.assertEqual((res["ok"], res["state"]), (True, "SKIPPED_NOT_REQUIRED"))
        self.assertEqual(self.rb.load_scope(None)["research"]["requirement"], "REQUIRED")  # no scope file -> REQUIRED

    def test_backup_without_registry_fails_when_required(self):
        empty = Path(self.tmp.name) / "emptyvar"; empty.mkdir()
        res, _ = quiet(self.rb.backup, empty, Path(self.tmp.name) / "o2", REQUIRED)
        self.assertEqual((res["ok"], res["state"]), (False, "FAILED_MISSING_REGISTRY"))

    def test_command_line_exit_code_and_single_json(self):
        shutil.rmtree(self.out / "research")
        p = subprocess.run([sys.executable, str(REPO / "deploy/linux/host/quant_research_backup.py"), "verify", str(self.out),
                            str(Path(self.tmp.name) / "r"), "--scope", str(REPO / "deploy/linux/host/backup_scope.json")],
                           capture_output=True, text=True, cwd=REPO)
        self.assertEqual(p.returncode, 1)
        self.assertEqual(json.loads(p.stdout)["state"], "FAILED_MISSING_DIRECTORY")


if __name__ == "__main__":
    unittest.main()
