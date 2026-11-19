"""Closeout 2026-11-20, finding F2: comparison eligibility uses the RESOLVED economic and evaluation conditions.

The review probe (review_probes.py compare_costs) showed that a copy of the real mom20 protocol with costs [100]
instead of [0, 5, 10] was COMPARABLE with ranking allowed. These regressions require that every changed condition is
detected, that unknown conditions never match, that a different factor under identical conditions stays comparable
(no whole-config hash requirement), and that legacy vs phase5 stays NOT_COMPARABLE.
"""
import copy
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path

from scripts.quant_research_v1 import catalog, registry

REPO = Path(__file__).resolve().parents[1]
LEGACY = json.loads((REPO / "configs/quant_research_mom20_v1.json").read_text(encoding="utf-8"))
EXACT = "c5b5ec22b1d3773f6a0345004080d4d2ff10e4cc7d48be21fe13a99cbc6ebe7e"


class CompareConditions(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); os.environ["QUANT_RESEARCH_ROOT"] = self.tmp.name
        self.db = registry.connect(); catalog.register_builtin_specs(self.db)
        self.ds = self.dataset("review-synthetic", "1", hashlib.sha256(b"metadata-only-no-market-data").hexdigest())
        self.db.execute("""INSERT INTO factors(factor_id,factor_version,title,entry,params_json,input_requirements_json,output_definition,missing_rule,registered_at)
                           VALUES('mom60','1','t','e','{"lookback_days": 60}','{}','o','m','2026-11-20T00:00:00Z')"""); self.db.commit()

    def tearDown(self):
        self.db.close(); os.environ.pop("QUANT_RESEARCH_ROOT", None); self.tmp.cleanup()

    def dataset(self, did, ver, content):
        m = {"schema": "quant_research_dataset_v1", "dataset_id": did, "dataset_version": ver, "title": "metadata only", "symbol": "US.QQQ",
             "frequency": "DAY", "adjustment": "a1", "start_date": "2000-01-01", "end_date": "2026-11-17", "rows": 10, "content_sha256": content,
             "audit_status": "REVIEW_SYNTHETIC"}
        registry.register_dataset(self.db, m, f"datasets/{did}/{ver}")
        return (did, ver)

    def run_(self, cfg, ds=None, factor=("mom20", "1")):
        return registry.create_run(self.db, ds or self.ds, factor, cfg)

    def cmp(self, cfg_b, **kw):
        a = self.run_(copy.deepcopy(LEGACY)); b = self.run_(cfg_b, **kw)
        return catalog.compare(self.db, a, b)

    # ---- the review probe, now rejected
    def test_review_probe_cost_change_is_not_rankable(self):
        b = copy.deepcopy(LEGACY); b["protocol_version"] = "review-cost-100"; b["costs"]["scenarios_bps_one_way"] = [100]
        r = self.cmp(b)
        self.assertEqual(r["result"], "NOT_COMPARABLE")
        self.assertIn("cost", r["differences"]); self.assertEqual(r["differences"]["cost"]["b"]["scenarios_bps_one_way"], [100.0])
        self.assertFalse(r["ranking_allowed"]); self.assertIsNone(r["ranking"])
        self.assertEqual(r["sensitivity_axis"], "cost"); self.assertTrue(r["side_by_side_allowed"])

    def test_cost_definition_text_change_detected(self):
        b = copy.deepcopy(LEGACY); b["costs"]["definition"] = "cost = 2 * bps/10000 * turnover"
        self.assertIn("cost", self.cmp(b)["differences"])

    def test_cash_change_detected(self):
        b = copy.deepcopy(LEGACY); b["cash_return"]["daily"] = 0.0001
        r = self.cmp(b)
        self.assertEqual(r["result"], "NOT_COMPARABLE"); self.assertIn("cash", r["differences"]); self.assertIsNone(r["sensitivity_axis"])

    def test_annualization_change_detected(self):
        b = copy.deepcopy(LEGACY); b["metrics"]["annualization_days"] = 365
        r = self.cmp(b)
        self.assertEqual(r["result"], "NOT_COMPARABLE"); self.assertIn("annualization", r["differences"])

    def test_sample_mask_change_detected(self):
        b = copy.deepcopy(LEGACY); b["segments"][2]["end"] = "2025-12-31"
        r = self.cmp(b)
        self.assertEqual(r["result"], "NOT_COMPARABLE"); self.assertIn("sample_mask", r["differences"]); self.assertFalse(r["ranking_allowed"])

    def test_warmup_change_is_a_mask_change(self):
        b = copy.deepcopy(LEGACY); b["warmup_trading_days"] = 60
        self.assertIn("sample_mask", self.cmp(b)["differences"])

    def test_input_content_change_detected(self):
        other = self.dataset("review-synthetic", "2", "e" * 64)
        r = self.cmp(copy.deepcopy(LEGACY), ds=other)
        self.assertEqual(r["result"], "NOT_COMPARABLE"); self.assertIn("input_content", r["differences"])

    # ---- what must stay comparable
    def test_same_condition_rerun_is_comparable(self):
        a = self.run_(copy.deepcopy(LEGACY))
        self.db.execute("UPDATE runs SET status='FAILED' WHERE run_id=?", (a,)); self.db.commit()
        b = registry.retry_run(self.db, a)
        r = catalog.compare(self.db, a, b)
        self.assertEqual(r["result"], "COMPARABLE"); self.assertTrue(r["ranking_allowed"]); self.assertTrue(r["same_factor"])
        self.assertEqual(r["differences"], {}); self.assertEqual(r["unknown_conditions"], {})

    def test_different_factor_same_conditions_is_comparable(self):
        b = copy.deepcopy(LEGACY); b["factor"] = {"factor_id": "mom60", "factor_version": "1", "params": {"lookback_days": 60}}
        b["protocol_id"] = "mom60-daily-long-cash"
        a = self.run_(copy.deepcopy(LEGACY)); rb = self.run_(b, factor=("mom60", "1"))
        rows = [self.db.execute("select config_sha256 from runs where run_id=?", (x,)).fetchone()[0] for x in (a, rb)]
        self.assertNotEqual(rows[0], rows[1])  # whole-config hashes differ; that alone must not block a fair comparison
        r = catalog.compare(self.db, a, rb)
        self.assertEqual(r["result"], "COMPARABLE"); self.assertTrue(r["ranking_allowed"]); self.assertFalse(r["same_factor"])

    # ---- unknown never matches
    def test_unknown_condition_never_matches(self):
        b = copy.deepcopy(LEGACY); del b["cash_return"]
        r = self.cmp(b)
        self.assertEqual(r["result"], "NOT_COMPARABLE"); self.assertEqual(r["unknown_conditions"].get("cash"), ["b"])
        self.assertFalse(r["ranking_allowed"]); self.assertTrue(any("现金收益未知" in x for x in r["reasons_zh"]))

    def test_both_unknown_still_not_comparable(self):
        a_cfg = copy.deepcopy(LEGACY); del a_cfg["metrics"]["annualization_days"]
        b_cfg = copy.deepcopy(LEGACY); del b_cfg["metrics"]["annualization_days"]
        r = catalog.compare(self.db, self.run_(a_cfg), self.run_(b_cfg))
        self.assertEqual(r["result"], "NOT_COMPARABLE"); self.assertEqual(r["unknown_conditions"]["annualization"], ["a", "b"])

    # ---- engines with different accounting
    def phase5_run(self, protocol_extra=None):
        ds = ("phase5_bundle", "c5b5ec22")
        if not self.db.execute("select 1 from datasets where dataset_id=? and dataset_version=?", ds).fetchone():
            self.dataset(*ds, EXACT); catalog.register_dataset_meta(self.db, *ds, catalog.PHASE5_DATASET_META)
        self.db.execute("INSERT OR REPLACE INTO worker_capabilities VALUES('img','phase5_o2o_v1','1',?,?)", (json.dumps({"ok": True}), registry.utcnow()))
        cfg_path = REPO / catalog.PHASE5_DIR / "config.json"; cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        proto = {"protocol_id": "phase5_strategy_v1", "protocol_version": "1", "factor": {"factor_id": "phase5_strategy_v1", "factor_version": "1"},
                 "evaluation_mode": "MARKET_TIME_SERIES", "benchmark_id": "MA200_SAME_INTERVALS (BUY_HOLD reported alongside)",
                 "sample_mask": {"periods": cfg["periods"], "series": cfg["series"]}, "frozen_config_sha256": hashlib.sha256(cfg_path.read_bytes()).hexdigest()}
        proto.update(protocol_extra or {})
        return ds, catalog.create_run_v2(self.db, ds, ("phase5_strategy_v1", "1"), proto, ("phase5_o2o_v1", "1"))

    def test_legacy_vs_phase5_not_comparable(self):
        _, p5 = self.phase5_run()
        r = catalog.compare(self.db, self.run_(copy.deepcopy(LEGACY)), p5)
        self.assertEqual(r["result"], "NOT_COMPARABLE")
        self.assertTrue({"execution_contract", "cost_contract", "metric_contract", "cost", "annualization"} <= set(r["differences"]))
        self.assertIsNone(r["sensitivity_axis"])

    def test_phase5_same_frozen_config_comparable_and_unhashed_config_unknown(self):
        ds, a = self.phase5_run()
        b = catalog.create_run_v2(self.db, ds, ("phase5_strategy_v1", "1"), json.loads(self.db.execute("select config_json from runs where run_id=?", (a,)).fetchone()[0]),
                                  ("phase5_o2o_v1", "1"), attempt_of=a)
        self.assertEqual(catalog.compare(self.db, a, b)["result"], "COMPARABLE")
        _, c = self.phase5_run({"frozen_config_sha256": "0" * 64})  # recorded hash does not match the frozen file -> unresolvable
        r = catalog.compare(self.db, a, c)
        self.assertEqual(r["result"], "NOT_COMPARABLE"); self.assertIn("cost", r["unknown_conditions"])


if __name__ == "__main__":
    unittest.main()
