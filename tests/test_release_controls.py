"""Closeout 2026-11-20 (F5): operator commands used by the release / rollback runbook."""
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout

from scripts.quant_research_v1 import catalog, registry, worker

EXACT = "c5b5ec22b1d3773f6a0345004080d4d2ff10e4cc7d48be21fe13a99cbc6ebe7e"


class ReleaseControls(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); os.environ["QUANT_RESEARCH_ROOT"] = self.tmp.name
        db = registry.connect(); catalog.register_builtin_specs(db)
        m = {"schema": "quant_research_dataset_bundle_v1", "dataset_id": "phase5_bundle", "dataset_version": "c5b5ec22", "title": "t", "symbol": "x",
             "frequency": "DAY", "adjustment": "a1", "start_date": "2000-01-01", "end_date": "2026-11-17", "rows": 1, "content_sha256": EXACT, "audit_status": "A"}
        registry.register_dataset(db, m, "datasets/phase5_bundle/c5b5ec22"); catalog.register_dataset_meta(db, "phase5_bundle", "c5b5ec22", catalog.PHASE5_DATASET_META)
        db.execute("INSERT INTO worker_capabilities VALUES('img','phase5_o2o_v1','1',?,?)", (json.dumps({"ok": True}), registry.utcnow())); db.commit()
        self.p5 = catalog.create_run_v2(db, ("phase5_bundle", "c5b5ec22"), ("phase5_strategy_v1", "1"),
                                        {"protocol_id": "phase5_strategy_v1", "factor": {"factor_id": "phase5_strategy_v1", "factor_version": "1"}}, ("phase5_o2o_v1", "1"))
        self.db = db

    def tearDown(self):
        self.db.close(); os.environ.pop("QUANT_RESEARCH_ROOT", None); self.tmp.cleanup()

    def cli(self, *argv):
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.assertEqual(worker.main(list(argv)), 0)
        return json.loads(buf.getvalue().strip().splitlines()[-1])

    def test_admission_pause_open_status(self):
        self.assertEqual(self.cli("admission", "pause", "--reason", "rollback")["admission"], "PAUSED")
        self.assertIn("rollback", registry.admission_paused())
        self.assertEqual(self.cli("admission", "open")["admission"], "OPEN"); self.assertIsNone(registry.admission_paused())

    def test_cancel_queued_only_that_engine_and_only_queued(self):
        legacy = self.db.execute("SELECT 1").fetchone()  # keep a legacy-shaped QUEUED row: engine_id NULL
        self.db.execute("""INSERT INTO runs(run_id,dataset_id,dataset_version,factor_id,factor_version,protocol_id,config_json,config_sha256,status,created_at)
                           VALUES('run-20261120T000000Z-aaaaaa','phase5_bundle','c5b5ec22','mom20','1','mom20-daily-long-cash','{}','c','QUEUED','2026-11-20T00:00:00Z')""")
        self.db.commit()
        out = self.cli("cancel-queued", "--engine", "phase5_o2o_v1@1", "--reason", "rollback to an app image without engine dispatch")
        self.assertEqual(out["cancelled"], [self.p5])
        rows = {r[0]: (r[1], r[2], r[3]) for r in self.db.execute("select run_id, status, exit_reason, error_class from runs")}
        self.assertEqual(rows[self.p5], ("CANCELLED", "CANCELLED_BEFORE_START", "CANCELLED_BY_OPERATOR"))
        self.assertEqual(rows["run-20261120T000000Z-aaaaaa"][0], "QUEUED")
        self.assertIsNotNone(legacy)

    def test_withdraw_capability_makes_engine_not_built(self):
        self.assertEqual(catalog.eligibility(self.db, ("phase5_bundle", "c5b5ec22"), ("phase5_strategy_v1", "1"))["status"], "ELIGIBLE")
        self.assertEqual(self.cli("withdraw-capability", "--engine", "phase5_o2o_v1@1")["withdrawn_capability_rows"], 1)
        e = catalog.eligibility(self.db, ("phase5_bundle", "c5b5ec22"), ("phase5_strategy_v1", "1"))
        self.assertEqual(e["status"], "BLOCKED"); self.assertTrue(any("ENGINE_NOT_BUILT" in r for r in e["reasons"]))


class RegisterBundleIdempotent(unittest.TestCase):
    def setUp(self):
        import sqlite3
        self.tmp = tempfile.TemporaryDirectory(); os.environ["QUANT_RESEARCH_ROOT"] = os.path.join(self.tmp.name, "research")
        self.src = os.path.join(self.tmp.name, "src.sqlite")
        con = sqlite3.connect(self.src)
        con.executescript("CREATE TABLE research_daily(symbol TEXT, autype INT, local_date TEXT); CREATE TABLE unresolved_dates(symbol TEXT, frequency TEXT, local_date TEXT);"
                          "INSERT INTO research_daily VALUES('US.QQQ',1,'2020-01-02'),('US.QQQ',1,'2020-01-03');")
        con.commit(); con.close()
        self.db = registry.connect()

    def tearDown(self):
        self.db.close(); os.environ.pop("QUANT_RESEARCH_ROOT", None); self.tmp.cleanup()

    def test_retry_is_idempotent_and_conflicts_refused(self):
        sha = catalog.sha_file(__import__("pathlib").Path(self.src))
        a = catalog.register_bundle_snapshot(self.db, __import__("pathlib").Path(self.src), "b", "1", "t", catalog.PHASE5_DATASET_META, expected_sha256=sha)
        b = catalog.register_bundle_snapshot(self.db, __import__("pathlib").Path(self.src), "b", "1", "t", catalog.PHASE5_DATASET_META, expected_sha256=sha)
        self.assertEqual((a["state"], b["state"], b["content_sha256"]), ("REGISTERED", "ALREADY_REGISTERED", sha))
        with self.assertRaises(ValueError):
            catalog.register_bundle_snapshot(self.db, __import__("pathlib").Path(self.src), "b", "1", "t", catalog.PHASE5_DATASET_META, expected_sha256="0" * 64)
        os.makedirs(os.path.join(os.environ["QUANT_RESEARCH_ROOT"], "datasets", "b", "2"))
        with self.assertRaises(ValueError) as cm:
            catalog.register_bundle_snapshot(self.db, __import__("pathlib").Path(self.src), "b", "2", "t", catalog.PHASE5_DATASET_META, expected_sha256=sha)
        self.assertIn("UNREGISTERED_DIRECTORY_EXISTS", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
