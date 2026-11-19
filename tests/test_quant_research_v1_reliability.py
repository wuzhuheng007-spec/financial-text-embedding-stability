"""R2/R3 regressions: independent audit catches tampering and swapped factors; lost workers,
cancellation, timeouts and commit races end in explicit states. Synthetic data, isolated roots."""
import copy
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from scripts.quant_research_v1 import engine, registry
from scripts.quant_research_v1.audit import audit_run

REPO = Path(__file__).resolve().parents[1]
PROTOCOL = json.loads((REPO / "configs/quant_research_mom20_v1.json").read_text(encoding="utf-8"))


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name) / "research"
        os.environ["QUANT_RESEARCH_ROOT"] = str(self.root)
        src = Path(self.tmp.name) / "src.sqlite"
        con = sqlite3.connect(src)
        con.execute("CREATE TABLE research_daily(symbol TEXT, autype INT, local_date TEXT, open REAL, high REAL, low REAL, close REAL, source_archive_sha256 TEXT)")
        p = 100.0
        for i in range(400):  # synthetic zig-zag; not market data
            o = p; p *= 1.01 if (i // 30) % 2 == 0 else 0.992
            con.execute("INSERT INTO research_daily VALUES('SYN',1,?,?,?,?,?,'synthetic')",
                        (f"{2000 + i // 250}-{1 + (i % 250) // 21:02d}-{1 + (i % 250) % 21:02d}", o, max(o, p), min(o, p), p))
        con.commit(); con.close()
        self.db = registry.connect()
        m = engine.build_snapshot(src, "SYN", 1, self.root / "datasets/syn/1", dataset_id="syn", dataset_version="1",
                                  title="syn", source_receipt={}, audit_status="SYNTHETIC")
        registry.register_dataset(self.db, m, "datasets/syn/1")
        registry.register_factor(self.db, engine.FACTORS[("mom20", "1")])
        self.cfg = copy.deepcopy(PROTOCOL)
        self.cfg["segments"] = [{"name": "development", "start": "2000-01-01", "end": "2000-12-31"},
                                {"name": "holdout", "start": "2001-01-01", "end": "2001-12-31"}]

    def tearDown(self):
        self.db.close()
        for k in ("QUANT_RESEARCH_ROOT", "QUANT_LIVE_WORKERS", "QUANT_RESEARCH_TEST_DELAY"):
            os.environ.pop(k, None)
        self.tmp.cleanup()

    def run_ok(self):
        from scripts.quant_research_v1.worker import run_once
        rid = registry.create_run(self.db, ("syn", "1"), ("mom20", "1"), self.cfg)
        self.assertEqual(run_once(60), 0)
        return rid, self.root / "runs" / rid

    def status(self, rid):
        return tuple(self.db.execute("SELECT status, exit_reason, error_class FROM runs WHERE run_id=?", (rid,)).fetchone())


class AuditTests(Base):
    def test_clean_run_passes_audit_and_records_bindings(self):
        rid, d = self.run_ok()
        self.assertEqual(self.status(rid)[:2], ("SUCCEEDED", "COMPLETED"))
        a = audit_run(self.root, rid)
        self.assertTrue(a["ok"], a["problems"])
        self.assertTrue((d / "audit_audit-v2.0.json").is_file())
        m = json.loads((d / "metrics.json").read_text(encoding="utf-8"))
        self.assertEqual(m["factor"]["impl_sha256"], self.db.execute("SELECT impl_sha256 FROM factors").fetchone()[0])

    def test_A_tampered_full_period_metric_fails(self):
        rid, d = self.run_ok()
        m = json.loads((d / "metrics.json").read_text(encoding="utf-8"))
        m["scenarios"][0]["full_period"]["strategy"]["total_return"] = 999.0
        (d / "metrics.json").write_text(json.dumps(m), encoding="utf-8")
        a = audit_run(self.root, rid)
        self.assertFalse(a["ok"]); self.assertTrue(any("full_period.strategy.total_return" in p for p in a["problems"]))

    def test_B_swapped_factor_function_is_refused(self):
        from scripts.quant_research_v1.worker import run_once
        spec = engine.FACTORS[("mom20", "1")]; orig = spec["fn"]
        spec["fn"] = lambda bars, lookback_days: [1.0] * len(bars)
        try:
            rid = registry.create_run(self.db, ("syn", "1"), ("mom20", "1"), self.cfg)
            self.assertEqual(run_once(60), 1)
        finally:
            spec["fn"] = orig
        st = self.db.execute("SELECT status, error_detail FROM runs WHERE run_id=?", (rid,)).fetchone()
        self.assertEqual(st[0], "FAILED"); self.assertIn("FACTOR_IMPL_MISMATCH", st[1])

    def test_B_outputs_from_a_different_factor_fail_independent_recompute(self):
        rid, d = self.run_ok()
        bars = engine.load_snapshot(self.root / "datasets/syn/1", self.db.execute("SELECT content_sha256 FROM datasets").fetchone()[0])
        from scripts.quant_research_v1.worker import results_digest
        m = json.loads((d / "metrics.json").read_text(encoding="utf-8")); ledgers = {}
        for s in m["scenarios"]:  # rewrite everything consistently from a constant "factor"
            res = engine.backtest(bars, [1.0] * len(bars), self.cfg, s["cost_bps"])
            name = f"ledger_cost{int(s['cost_bps'])}bps.csv"; ledgers[name] = engine.ledger_csv(res["ledger"])
            (d / name).write_text(ledgers[name], encoding="utf-8"); s["segments"], s["full_period"] = res["segments"], res["full_period"]
        m["results_digest"] = results_digest(m["scenarios"], ledgers)
        (d / "metrics.json").write_text(json.dumps(m), encoding="utf-8")
        self.db.execute("UPDATE runs SET results_digest=? WHERE run_id=?", (m["results_digest"], rid)); self.db.commit()
        a = audit_run(self.root, rid)
        self.assertFalse(a["ok"]); self.assertTrue(any(p.startswith("ledger_0bps_eq_independent") for p in a["problems"]))

    def test_tampered_price_config_and_ledger_variants_fail(self):
        rid, d = self.run_ok()
        snap = self.root / "datasets/syn/1/data.csv"; orig_snap = snap.read_bytes()
        lines = orig_snap.decode().splitlines(); c = lines[100].split(","); c[4] = repr(float(c[4]) * 1.01); lines[100] = ",".join(c)
        snap.write_bytes(("\n".join(lines) + "\n").encode())
        self.assertIn("dataset_hash_file_eq_registry", " ".join(audit_run(self.root, rid)["problems"]))
        snap.write_bytes(orig_snap)
        cfgp = d / "config.json"; orig_cfg = cfgp.read_text(encoding="utf-8"); cfg = json.loads(orig_cfg)
        cfg["costs"]["scenarios_bps_one_way"] = [0, 5]; cfgp.write_text(json.dumps(cfg), encoding="utf-8")
        self.assertIn("config_hash_file_eq_registry", " ".join(audit_run(self.root, rid)["problems"]))
        cfgp.write_text(orig_cfg, encoding="utf-8")
        led = d / "ledger_cost5bps.csv"; orig_bytes = led.read_bytes(); orig_led = orig_bytes.decode()
        for mutate in (lambda L: L[:1] + L[1:-1] + [L[-1].replace(L[-1].split(",")[13], "nan", 1)],  # NaN
                       lambda L: L[:-1],                                                          # missing row
                       lambda L: L + [L[-1]],                                                     # duplicate date
                       lambda L: [L[0].replace(",nav,", ",nav_x,")] + L[1:]):                      # column renamed
            L = orig_led.splitlines(); led.write_text("\n".join(mutate(L)) + "\n", encoding="utf-8")
            self.assertFalse(audit_run(self.root, rid)["ok"])
        led.write_bytes(orig_bytes)
        (d / "ledger_cost7bps.csv").write_text(orig_led, encoding="utf-8")  # extra scenario
        self.assertIn("ledger_file_set_eq_config", " ".join(audit_run(self.root, rid)["problems"]))
        (d / "ledger_cost7bps.csv").unlink()
        self.assertTrue(audit_run(self.root, rid)["ok"])

    def test_snapshot_reader_rejects_invalid_rows(self):
        d = self.root / "datasets/bad"; d.mkdir(parents=True)
        for body in ("2020-01-01,1,1,1,1\n2020-13-01,1,1,1,1\n", "2020-01-01,1,1,1,inf\n", "2020-01-01,1,0.5,0.9,1\n",
                     "2020-01-02,1,1,1,1\n2020-01-01,1,1,1,1\n", "2020-01-01,-1,1,-1,1\n"):
            data = ("date,open,high,low,close\n" + body).encode()
            (d / "data.csv").write_bytes(data)
            import hashlib
            with self.assertRaises(ValueError):
                engine.load_snapshot(d, hashlib.sha256(data).hexdigest())


class JobLifecycleTests(Base):
    def spawn(self, extra_env):
        code = "import sys; from scripts.quant_research_v1.worker import run_once; sys.exit(run_once(120))"
        return subprocess.Popen([sys.executable, "-c", code], cwd=REPO, env={**os.environ, **extra_env})

    def wait_status(self, rid, want, seconds=30):
        t = time.time()
        while time.time() - t < seconds:
            st = self.status(rid)
            if st[0] in want:
                return st, time.time() - t
            time.sleep(0.2)
        return self.status(rid), None

    def test_sigkilled_worker_is_recovered_and_next_job_runs(self):
        from scripts.quant_research_v1.worker import run_once
        a = registry.create_run(self.db, ("syn", "1"), ("mom20", "1"), self.cfg)
        p = self.spawn({"QUANT_RESEARCH_TEST_DELAY": "30", "QUANT_RESEARCH_LEASE_SECONDS": "2", "QUANT_RESEARCH_HEARTBEAT_SECONDS": "0.5"})
        self.wait_status(a, ("RUNNING",)); time.sleep(1.5); p.kill(); p.wait()
        self.assertEqual(self.status(a)[0], "RUNNING")
        time.sleep(3)  # lease expires
        b = registry.create_run(self.db, ("syn", "1"), ("mom20", "1"), self.cfg)
        os.environ["QUANT_LIVE_WORKERS"] = ""  # host reports no live job container
        self.assertEqual(run_once(60), 0)
        self.assertEqual(self.status(a), ("FAILED", "WORKER_LOST", "WORKER_LOST"))
        self.assertTrue((self.root / "runs" / a / "run.log").is_file())  # partial artifacts kept
        self.assertTrue((self.root / "runs" / a / "recovery.json").is_file())
        self.assertEqual(self.status(b)[0], "SUCCEEDED")
        r = registry.retry_run(self.db, a)
        self.assertEqual(self.db.execute("SELECT attempt_of, status FROM runs WHERE run_id=?", (r,)).fetchone()[:], (a, "QUEUED"))

    def test_live_worker_with_expired_lease_is_not_declared_lost(self):
        rid = registry.create_run(self.db, ("syn", "1"), ("mom20", "1"), self.cfg)
        registry.claim_next(self.db, "abc123def456:42")
        self.db.execute("UPDATE runs SET lease_expires_at='2000-01-01T00:00:00Z' WHERE run_id=?", (rid,)); self.db.commit()
        self.assertEqual(registry.recover_lost(self.db, {"abc123def456"}), [])
        self.assertEqual(self.status(rid)[0], "RUNNING")
        self.assertEqual(len(registry.recover_lost(self.db, set())), 1)

    def test_cancel_while_running_stops_the_job(self):
        rid = registry.create_run(self.db, ("syn", "1"), ("mom20", "1"), self.cfg)
        p = self.spawn({"QUANT_RESEARCH_TEST_DELAY": "30", "QUANT_RESEARCH_HEARTBEAT_SECONDS": "0.3"})
        self.wait_status(rid, ("RUNNING",)); time.sleep(0.5)
        self.assertEqual(registry.request_cancel(self.db, rid), "RUNNING")
        st, secs = self.wait_status(rid, ("CANCELLED",), 15)
        p.wait(timeout=15)
        self.assertEqual(st[:2], ("CANCELLED", "CANCELLED_WHILE_RUNNING")); self.assertLess(secs, 5)

    def test_timeout_inside_a_long_stage(self):
        rid = registry.create_run(self.db, ("syn", "1"), ("mom20", "1"), self.cfg)
        code = "import sys; from scripts.quant_research_v1.worker import run_once; sys.exit(run_once(2))"
        p = subprocess.Popen([sys.executable, "-c", code], cwd=REPO, env={**os.environ, "QUANT_RESEARCH_TEST_DELAY": "30", "QUANT_RESEARCH_HEARTBEAT_SECONDS": "0.3"})
        self.assertEqual(p.wait(timeout=30), 1)
        self.assertEqual(self.status(rid)[:2], ("FAILED", "TIMEOUT"))

    def test_cancel_racing_success_commit_ends_cancelled(self):
        rid = registry.create_run(self.db, ("syn", "1"), ("mom20", "1"), self.cfg)
        registry.claim_next(self.db, "w:1")
        registry.request_cancel(self.db, rid)
        self.assertEqual(registry.finish(self.db, rid, "SUCCEEDED", results_digest="x"), "CANCELLED")
        self.assertEqual(self.status(rid)[:2], ("CANCELLED", "CANCELLED_BEFORE_COMMIT"))

    def test_registered_fingerprint_cannot_be_silently_changed(self):
        spec = dict(engine.FACTORS[("mom20", "1")]); spec["fn"] = lambda bars, lookback_days: [0.0] * len(bars)
        with self.assertRaises(ValueError):
            registry.register_factor(self.db, spec)


if __name__ == "__main__":
    unittest.main()
