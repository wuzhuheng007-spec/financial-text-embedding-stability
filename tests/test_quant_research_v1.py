"""Research workflow v1: hand-computed backtest, no look-ahead, registry, worker, verifier, determinism."""
import os as _os
_os.environ.setdefault("QUANT_CODE_VERSION", "test-code-version")
_os.environ.setdefault("QUANT_IMAGE_ID", "sha256:test-image")

import copy
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts.quant_research_v1 import registry
from scripts.quant_research_v1.engine import backtest, build_snapshot, factor_mom
from scripts.quant_research_v1.verify import verify_run

PROTOCOL = json.loads((Path(__file__).resolve().parents[1] / "configs/quant_research_mom20_v1.json").read_text(encoding="utf-8"))


def bars_from(ohlc):
    return [{"date": f"2020-01-{i + 1:02d}", "open": o, "high": max(o, c), "low": min(o, c), "close": c}
            for i, (o, c) in enumerate(ohlc)]


SMALL = bars_from([(100, 100), (100, 100), (100, 105), (106, 110), (108, 99), (97, 100)])


def small_cfg():
    cfg = copy.deepcopy(PROTOCOL)
    cfg["warmup_trading_days"] = 2
    cfg["metrics"]["min_days_for_metrics"] = 1
    cfg["segments"] = [{"name": "all", "start": "2020-01-01", "end": "2020-12-31"}]
    return cfg


class HandComputedTests(unittest.TestCase):
    def test_alignment_delay_costs_match_hand_calculation(self):
        res = backtest(SMALL, factor_mom(SMALL, 2), small_cfg(), 10)
        L = res["ledger"]
        self.assertEqual([r["date"] for r in L], ["2020-01-04", "2020-01-05", "2020-01-06"])
        # Day 4: factor at close of day 3 (105/100-1 > 0) -> long from the OPEN of day 4 (106), not day 3's close.
        self.assertEqual((L[0]["position_overnight"], L[0]["position_after_open"], L[0]["exec_price"], L[0]["turnover"]), (0.0, 1.0, 106, 1.0))
        self.assertAlmostEqual(L[0]["nav"], 0.999 * 110 / 106, places=12)
        self.assertAlmostEqual(L[0]["gross_return"], 110 / 106 - 1, places=12)
        # Day 5: still long (110/100-1 > 0), no trade, no cost.
        self.assertEqual((L[1]["turnover"], L[1]["cost"]), (0.0, 0.0))
        self.assertAlmostEqual(L[1]["nav"], 0.999 * 99 / 106, places=12)
        # Day 6: factor at close of day 5 = 99/105-1 < 0 -> sell at the open (97), held overnight 99 -> 97.
        self.assertEqual((L[2]["position_overnight"], L[2]["position_after_open"], L[2]["turnover"]), (1.0, 0.0, 1.0))
        self.assertAlmostEqual(L[2]["nav"], 0.999 ** 2 * 97 / 106, places=12)
        # Benchmark: bought once at the open of day 4 (same cost), held: 0.999 * 100/106.
        self.assertAlmostEqual(L[-1]["benchmark_nav"], 0.999 * 100 / 106, places=12)
        self.assertEqual(res["segments"][0]["strategy"]["trades"], 2)
        self.assertAlmostEqual(res["segments"][0]["strategy"]["turnover_sum"], 2.0)

    def test_no_look_ahead_todays_close_cannot_change_todays_position(self):
        base = backtest(SMALL, factor_mom(SMALL, 2), small_cfg(), 0)["ledger"]
        bumped = copy.deepcopy(SMALL); bumped[3]["close"] = 50.0  # crash at the close of day 4
        alt = backtest(bumped, factor_mom(bumped, 2), small_cfg(), 0)["ledger"]
        self.assertEqual(base[0]["position_after_open"], alt[0]["position_after_open"])
        self.assertNotEqual(base[1]["position_after_open"], alt[1]["position_after_open"])  # reacts the NEXT open

    def test_zero_cost_equals_price_path(self):
        L = backtest(SMALL, factor_mom(SMALL, 2), small_cfg(), 0)["ledger"]
        self.assertAlmostEqual(L[-1]["nav"], 97 / 106, places=12)


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); root = Path(self.tmp.name)
        os.environ["QUANT_RESEARCH_ROOT"] = str(root / "research")
        src = root / "src.sqlite"
        con = sqlite3.connect(src)
        con.execute("CREATE TABLE research_daily(symbol TEXT, autype INT, local_date TEXT, open REAL, high REAL, low REAL, close REAL, source_archive_sha256 TEXT)")
        price = 100.0
        for i in range(400):  # synthetic, deterministic zig-zag trend; not market data
            o = price; price = price * (1 + (0.01 if (i // 30) % 2 == 0 else -0.008)); c = price
            con.execute("INSERT INTO research_daily VALUES('SYN.TEST',1,?,?,?,?,?, 'synthetic')",
                        (f"{2000 + i // 250}-{1 + (i % 250) // 21:02d}-{1 + (i % 250) % 21:02d}", o, max(o, c), min(o, c), c))
        con.commit(); con.close()
        self.db = registry.connect()
        m = build_snapshot(src, "SYN.TEST", 1, registry.root() / "datasets/syn/1", dataset_id="syn", dataset_version="1",
                           title="synthetic", source_receipt={"synthetic": True}, audit_status="SYNTHETIC_TEST_ONLY")
        registry.register_dataset(self.db, m, "datasets/syn/1")
        from scripts.quant_research_v1.engine import FACTORS
        registry.register_factor(self.db, FACTORS[("mom20", "1")])
        self.cfg = copy.deepcopy(PROTOCOL)
        self.cfg["segments"] = [{"name": "development", "start": "2000-01-01", "end": "2000-12-31"},
                                {"name": "holdout", "start": "2001-01-01", "end": "2001-12-31"}]

    def tearDown(self):
        self.db.close(); os.environ.pop("QUANT_RESEARCH_ROOT", None); self.tmp.cleanup()

    def test_run_succeeds_verifies_and_reruns_identically(self):
        from scripts.quant_research_v1.worker import run_once
        a = registry.create_run(self.db, ("syn", "1"), ("mom20", "1"), self.cfg)
        b = registry.create_run(self.db, ("syn", "1"), ("mom20", "1"), self.cfg)
        self.assertEqual(run_once(60), 0); self.assertEqual(run_once(60), 0)
        rows = {r["run_id"]: dict(r) for r in self.db.execute("SELECT * FROM runs")}
        self.assertEqual({rows[a]["status"], rows[b]["status"]}, {"SUCCEEDED"})
        self.assertEqual(rows[a]["results_digest"], rows[b]["results_digest"])  # same input+config -> same content
        self.assertNotEqual(rows[a]["artifact_dir"], rows[b]["artifact_dir"])  # never overwritten
        self.assertTrue(verify_run(registry.root() / rows[a]["artifact_dir"])["ok"])
        for bps in (0, 5, 10):
            self.assertTrue((registry.root() / rows[a]["artifact_dir"] / f"ledger_cost{bps}bps.csv").is_file())

    def test_tampered_ledger_is_detected(self):
        from scripts.quant_research_v1.worker import run_once
        rid = registry.create_run(self.db, ("syn", "1"), ("mom20", "1"), self.cfg); run_once(60)
        led = registry.root() / "runs" / rid / "ledger_cost5bps.csv"
        lines = led.read_text().splitlines(); cells = lines[50].split(","); cells[12] = repr(float(cells[12]) + 0.01)
        lines[50] = ",".join(cells); led.write_text("\n".join(lines) + "\n")
        self.assertFalse(verify_run(registry.root() / "runs" / rid)["ok"])

    def test_failed_run_is_recorded_not_raised_and_cancel_queued(self):
        from scripts.quant_research_v1.worker import run_once
        rid = registry.create_run(self.db, ("syn", "1"), ("mom20", "1"), self.cfg)
        (registry.root() / "datasets/syn/1/data.csv").write_text("date,open,high,low,close\n")  # corrupt snapshot
        self.assertEqual(run_once(60), 1)
        row = self.db.execute("SELECT status,error_class FROM runs WHERE run_id=?", (rid,)).fetchone()
        self.assertEqual(tuple(row), ("FAILED", "ValueError"))
        q = registry.create_run(self.db, ("syn", "1"), ("mom20", "1"), self.cfg)
        self.assertEqual(registry.request_cancel(self.db, q), "CANCELLED")
        self.assertEqual(run_once(60), 0)  # nothing queued

    def test_registry_guards(self):
        with self.assertRaises(LookupError):
            registry.create_run(self.db, ("nope", "1"), ("mom20", "1"), self.cfg)
        other = copy.deepcopy(self.cfg); other["factor"]["factor_version"] = "2"
        with self.assertRaises(ValueError):
            registry.create_run(self.db, ("syn", "1"), ("mom20", "1"), other)
        m = json.loads((registry.root() / "datasets/syn/1/manifest.json").read_text()); m["content_sha256"] = "0" * 64
        with self.assertRaises(ValueError):
            registry.register_dataset(self.db, m, "x")


if __name__ == "__main__":
    unittest.main()
