"""R1: full streaming SQLite digests above the old 200,000-row cap; research backup and an
isolated restore that reads only the backup copy and reruns completed runs under new ids."""
import os as _os
_os.environ.setdefault("QUANT_CODE_VERSION", "test-code-version")
_os.environ.setdefault("QUANT_IMAGE_ID", "sha256:test-image")

import copy
import importlib.util
import json
import os
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, REPO / "deploy/linux/host" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod); return mod


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
            bad = sb.verify(out, Path(t) / "rst2")
            self.assertFalse(bad["ok"])

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
    def test_backup_restore_rerun_matches_and_reads_backup_only(self):
        rb = load("quant_research_backup")
        from scripts.quant_research_v1 import engine, registry
        from scripts.quant_research_v1.worker import run_once
        with tempfile.TemporaryDirectory() as t:
            var = Path(t) / "var"; root = var / "research"
            os.environ["QUANT_RESEARCH_ROOT"] = str(root)
            try:
                src = Path(t) / "src.sqlite"; con = sqlite3.connect(src)
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
                rid = registry.create_run(db, ("syn", "1"), ("mom20", "1"), cfg); db.close()
                self.assertEqual(run_once(60), 0)
                out = Path(t) / "backup"; out.mkdir()
                b = rb.backup(var, out)
                self.assertEqual((b["state"], b["succeeded_runs"], b["incomplete_runs"]), ("OK", 1, []))
                shutil.rmtree(var)  # the restore must not need (or read) the source
                res = rb.verify(out, Path(t) / "restore")
                self.assertTrue(res["ok"], res["problems"])
                r = res["reruns"][0]
                self.assertEqual(r["original_run_id"], rid); self.assertNotEqual(r["new_run_id"], rid)
                self.assertTrue(r["digest_equal"]); self.assertTrue(all(r["ledgers_byte_equal"].values()))
                # tamper with one backed-up file -> restore check fails
                led = next((out / "research/files/runs" / rid).glob("ledger_cost5bps.csv"))
                led.write_bytes(led.read_bytes().replace(b"\n2019-", b"\n2018-", 1))
                self.assertFalse(rb.verify(out, Path(t) / "restore2")["ok"])
            finally:
                os.environ.pop("QUANT_RESEARCH_ROOT", None); os.environ.pop("QUANT_LIVE_WORKERS", None)


if __name__ == "__main__":
    unittest.main()
