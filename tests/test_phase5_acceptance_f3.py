"""Closeout 2026-11-20, finding F3: the reference reconciliation is a REQUIRED acceptance layer.

The review probes (review_probes.py) showed that (a) a run whose metrics differed from the reference by 0.98 and
whose ledger set did not match still returned mechanical PASS / software SUCCEEDED, and (b) the old comparator hid a
one-sided NaN, an extra reference row and a duplicate reference key. Each scenario below must now be REJECTED.

Synthetic files only (marked FAULT_INJECTION); the frozen program is replaced by a fake child; no market data, no
financial result. The frozen research files are not touched (FROZEN_DIR is patched to a temporary copy).
"""
import copy
import csv
import gzip
import hashlib
import io
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.quant_research_v1.engines import phase5_o2o as adapter

LEDGER_COLS = ["signal_date", "exec_date", "mark_date", "target_weight", "weight_during", "end_nav", "return_net"]
LEDGERS = {"ledgers/US_QQQ/primary/MA200_c10.csv.gz": [["2015-01-02", "2015-01-05", "2015-01-06", "1", "1", "1.01", "0.01"],
                                                       ["2015-01-05", "2015-01-06", "2015-01-07", "0", "1", "1.02", "0.0099"]],
           "ledgers/US_QQQ/primary/P1_TREND_c10.csv.gz": [["2015-01-02", "2015-01-05", "2015-01-06", "0", "0", "1", "0"],
                                                          ["2015-01-05", "2015-01-06", "2015-01-07", "1", "0", "1", "0"]]}
M_COLS = ["symbol", "period", "cost_bps", "strategy", "status", "first_exec", "last_mark", "intervals", "terminal_wealth", "cagr",
          "max_drawdown", "descriptive_sharpe_sqrt252", "mean_exposure", "cost_fraction_sum"]
METRICS = [["US.QQQ", "primary", "10", "MA200", "EXECUTED_PRICE_ONLY", "2015-01-05", "2015-01-07", "2", "1.02", "0.01", "-0.1", "0.5", "1", "0.001"],
           ["US.QQQ", "primary", "10", "P1_TREND", "EXECUTED_PRICE_ONLY", "2015-01-05", "2015-01-07", "2", "1", "0", "0", "", "0", "0"],
           ["SH.000688", "development", "10", "P1_TREND", "NO_ELIGIBLE_INTERVALS", "", "", "0", "", "", "", "", "", ""]]
C_COLS = ["symbol", "strategy", "period", "cost_bps", "n", "block", "seed", "primary_test", "mean_daily_net_delta", "ci_low_daily", "ci_high_daily",
          "p_one_sided_positive", "mean_daily_delta_cost25", "holm_p", "first_screen_return_increment_pass"]
CONTRASTS = [["US.QQQ", "P1_TREND", "primary", "10", "2", "126", "20261119", "True", "-0.0003", "-0.0006", "-0.000002", "0.976", "-0.0003", "1", "False"]]
F_COLS = ["symbol", "period", "factor", "n_labels", "spearman_8w"]
FACTORS = [["US.QQQ", "full", "trend_score", "10", "-0.05"]]


def csv_text(cols, rows):
    b = io.StringIO(); w = csv.writer(b, lineterminator="\n"); w.writerow(cols); w.writerows(rows); return b.getvalue()


def gz(text):
    return gzip.compress(text.encode(), mtime=0)


class Fixture:
    """A temporary frozen dir (results = reference), registry-like dataset and an injectable fake child."""

    def __init__(self, td: Path):
        self.td = td
        self.frozen = td / "frozen"; (self.frozen / "results").mkdir(parents=True)
        self.root = td / "research"; dsdir = self.root / "datasets/synthetic/1"; dsdir.mkdir(parents=True)
        (dsdir / "market_research.sqlite").write_bytes(b"FAULT_INJECTION_NOT_A_PRICE_DATABASE")
        (dsdir / "manifest.json").write_text(json.dumps({"end_date": "2026-11-17", "series": [], "unresolved_dates": []}))
        self.sha = hashlib.sha256(b"FAULT_INJECTION_NOT_A_PRICE_DATABASE").hexdigest()
        (self.frozen / "config.json").write_text(json.dumps({"expected_db_sha256": self.sha, "data_end": "2026-11-17"}))
        self.ref = {"metrics": copy.deepcopy(METRICS), "contrasts": copy.deepcopy(CONTRASTS), "factors": copy.deepcopy(FACTORS),
                    "ledgers": copy.deepcopy(LEDGERS)}
        self.act = copy.deepcopy(self.ref)
        self.m_cols, self.c_cols = list(M_COLS), list(C_COLS)
        self.claim_override = None; self.self_check_status = "PASS_SAME_AUTHOR_LEDGER_AND_INPUT_CHECK"; self.symlink = False
        self.write_reference()

    @staticmethod
    def index(ledgers):
        return {k: {"uncompressed_sha256": hashlib.sha256(csv_text(LEDGER_COLS, v).encode()).hexdigest(), "rows": len(v)} for k, v in ledgers.items()}

    def write_reference(self):
        r = self.frozen / "results"
        (r / "metrics.csv").write_text(csv_text(M_COLS, self.ref["metrics"]))
        (r / "contrasts.csv").write_text(csv_text(C_COLS, self.ref["contrasts"]))
        (r / "factor_diagnostics.csv").write_text(csv_text(F_COLS, self.ref["factors"]))
        (r / "private_output_hashes.json").write_text(json.dumps(self.index(self.ref["ledgers"])))

    def register_reference_ledgers(self, ledgers=None):
        d = self.root / "references/phase5_strategy_v1" / self.sha
        for k, v in (ledgers or self.ref["ledgers"]).items():
            (d / k).parent.mkdir(parents=True, exist_ok=True); (d / k).write_bytes(gz(csv_text(LEDGER_COLS, v)))
        (d / "REFERENCE_MANIFEST.json").write_text("{}")

    def child(self, cmd, cwd, log, stop_now):
        priv = Path(cmd[cmd.index("--private-out") + 1]); pub = Path(cmd[cmd.index("--public-out") + 1])
        priv.mkdir(parents=True, exist_ok=True); pub.mkdir(parents=True, exist_ok=True)
        if "run.py" in cmd:
            for k, v in self.act["ledgers"].items():
                (priv / k).parent.mkdir(parents=True, exist_ok=True); (priv / k).write_bytes(gz(csv_text(LEDGER_COLS, v)))
            (pub / "metrics.csv").write_text(csv_text(self.m_cols, self.act["metrics"]))
            (pub / "contrasts.csv").write_text(csv_text(self.c_cols, self.act["contrasts"]))
            (pub / "factor_diagnostics.csv").write_text(csv_text(F_COLS, self.act["factors"]))
            (pub / "run_manifest.json").write_text("{}")
            (pub / "private_output_hashes.json").write_text(json.dumps(self.claim_override or self.index(self.act["ledgers"])))
            if self.symlink:
                os.symlink("/etc/passwd", priv / "planted_link")
        else:
            n = len(json.loads((pub / "private_output_hashes.json").read_text()))
            (pub / "self_check.json").write_text(json.dumps({"real_run": {"status": self.self_check_status, "ledger_files_recomputed": n}}))
        return (0, 0.1, None)

    def execute(self, content_sha=None):
        out = self.td / "out"
        if out.exists():
            shutil.rmtree(out)
        out.mkdir()
        cap = {"ok": True, "frozen_files": {n: "c" * 64 for n in adapter.FROZEN_SHA256}, "libs": {"numpy": "MOCK", "pandas": "MOCK", "scipy": "MOCK"}}
        ds = {"snapshot_path": "datasets/synthetic/1", "content_sha256": content_sha or self.sha, "dataset_id": "synthetic", "dataset_version": "1"}
        run = {"config_json": "{}", "contracts_json": "{}", "run_id": "synthetic-gate-only"}
        env = dict(os.environ); env.pop("QUANT_RESEARCH_INPUT_ROOT", None)
        with patch.dict(os.environ, env, clear=True), patch.object(adapter, "FROZEN_DIR", self.frozen), \
                patch.object(adapter, "capability", return_value=cap), patch.object(adapter, "_child", side_effect=self.child):
            try:
                fields = adapter.execute(run, ds, self.root, out, lambda m: None, lambda: None)
                exc = None
            except Exception as e:  # noqa: BLE001 - the test inspects the type
                fields, exc = None, e
        mech = json.loads((out / "mechanical_check.json").read_text()) if (out / "mechanical_check.json").is_file() else None
        return fields, exc, mech


class AcceptanceGate(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory(); self.fx = Fixture(Path(self._td.name))

    def tearDown(self):
        self._td.cleanup()

    def rejected(self, kind=adapter.MechanicalCheckFailed, layer=None, problem=None):
        fields, exc, mech = self.fx.execute()
        self.assertIsNone(fields, "a mismatching run must not be accepted")
        self.assertIsInstance(exc, kind)
        self.assertIsNotNone(mech); self.assertNotEqual(mech["status"], "PASS")
        if layer:
            self.assertNotEqual(mech["layers"][layer]["status"], "PASS")
        if problem:
            self.assertIn(problem, json.dumps(mech))
        return mech

    # ---- the legitimate case, including legal empty cells and the NO_ELIGIBLE_INTERVALS status
    def test_identical_outputs_pass_all_three_layers(self):
        fields, exc, mech = self.fx.execute()
        self.assertIsNone(exc); self.assertEqual(mech["status"], "PASS")
        self.assertEqual({k: v["status"] for k, v in mech["layers"].items()},
                         {"internal_self_check": "PASS", "output_integrity": "PASS", "reference_reconciliation": "PASS"})
        self.assertEqual(mech["layers"]["reference_reconciliation"]["ledgers"]["path"], "RAW_SHA256_IDENTICAL")
        self.assertEqual(fields["_grade"]["software"], "SUCCEEDED"); self.assertEqual(mech["rule_id"], "phase5_reference_reconcile_v1")

    def test_within_predeclared_tolerance_passes_but_is_not_byte_identical(self):
        self.fx.act["metrics"][0][9] = "0.010000000001"  # |diff| 1e-12 < 1e-8
        fields, exc, mech = self.fx.execute()
        self.assertIsNone(exc); self.assertEqual(mech["status"], "PASS")
        self.assertFalse(mech["layers"]["reference_reconciliation"]["tables"]["metrics.csv"]["byte_identical"])

    # ---- the review probe scenario (adapter_gate_fault), now rejected
    def test_review_probe_adapter_gate_fault_rejected(self):
        self.fx.act["metrics"][0][9] = "0.99"; self.fx.ref["metrics"][0][9] = "0.01"; self.fx.write_reference()
        self.fx.act["ledgers"] = {}
        self.fx.claim_override = {"different-ledger": {"uncompressed_sha256": "b" * 64}}
        mech = self.rejected(layer="output_integrity")
        self.assertEqual(mech["layers"]["reference_reconciliation"]["status"], "FAIL")
        self.assertIn("TOLERANCE_EXCEEDED", json.dumps(mech["layers"]["reference_reconciliation"]["tables"]["metrics.csv"]))

    # ---- table faults
    def test_tampered_metric_beyond_tolerance_rejected(self):
        self.fx.act["metrics"][0][9] = "0.010001"
        self.rejected(layer="reference_reconciliation", problem="TOLERANCE_EXCEEDED")

    def test_extra_actual_row_rejected(self):
        self.fx.act["metrics"].append(["US.QQQ", "check", "10", "MA200", "EXECUTED_PRICE_ONLY", "", "", "0", "", "", "", "", "", ""])
        self.rejected(layer="reference_reconciliation", problem="EXTRA_ROWS_IN_ACTUAL")

    def test_missing_actual_row_rejected(self):
        del self.fx.act["contrasts"][0]
        self.rejected(layer="reference_reconciliation", problem="MISSING_ROWS_IN_ACTUAL")

    def test_duplicate_actual_key_rejected(self):
        self.fx.act["metrics"].append(list(self.fx.act["metrics"][0]))
        self.rejected(layer="reference_reconciliation", problem="DUPLICATE_KEYS_ACTUAL")

    def test_one_sided_nan_rejected(self):
        self.fx.act["metrics"][0][10] = "nan"
        self.rejected(layer="reference_reconciliation", problem="CELL_CLASS_DIFFERS")

    def test_one_sided_inf_rejected(self):
        self.fx.act["metrics"][0][11] = "inf"
        self.rejected(layer="reference_reconciliation", problem="POS_INF")

    def test_empty_versus_number_rejected(self):
        self.fx.act["metrics"][1][11] = "0.3"  # reference cell is empty (legal missing value); actual invents a number
        self.rejected(layer="reference_reconciliation", problem="CELL_CLASS_DIFFERS")

    def test_schema_change_rejected(self):
        self.fx.m_cols = M_COLS[:-1] + ["renamed_column"]
        self.rejected(layer="reference_reconciliation", problem="SCHEMA_MISMATCH")

    def test_illegal_status_rejected(self):
        self.fx.act["metrics"][2][4] = "MADE_UP_STATUS"
        self.rejected(layer="reference_reconciliation", problem="ILLEGAL_STATUS")

    # ---- ledger faults
    def test_missing_ledger_rejected(self):
        del self.fx.act["ledgers"]["ledgers/US_QQQ/primary/P1_TREND_c10.csv.gz"]
        mech = self.rejected(layer="reference_reconciliation")
        self.assertEqual(mech["layers"]["reference_reconciliation"]["ledgers"]["path"], "LEDGER_SET_DIFFERS")

    def test_extra_ledger_rejected(self):
        self.fx.act["ledgers"]["ledgers/US_QQQ/primary/P2_RECOVERY_c10.csv.gz"] = LEDGERS["ledgers/US_QQQ/primary/MA200_c10.csv.gz"]
        mech = self.rejected(layer="reference_reconciliation")
        self.assertIn("P2_RECOVERY", json.dumps(mech["layers"]["reference_reconciliation"]["ledgers"]["extra_in_actual"]))

    def test_program_claim_not_matching_disk_rejected(self):
        idx = Fixture.index(self.fx.act["ledgers"]); k = sorted(idx)[0]; idx[k] = {"uncompressed_sha256": "a" * 64, "rows": 2}
        self.fx.claim_override = idx
        mech = self.rejected(layer="output_integrity")
        self.assertIn(k, mech["layers"]["output_integrity"]["hash_differs_from_claim"])

    def test_ledger_differs_without_reference_ledgers_is_incomplete(self):
        self.fx.act["ledgers"]["ledgers/US_QQQ/primary/MA200_c10.csv.gz"][0][5] = "1.0100000000001"
        mech = self.rejected(kind=adapter.ReferenceUnavailable)
        self.assertEqual(mech["status"], "INCOMPLETE")
        self.assertEqual(mech["layers"]["reference_reconciliation"]["ledgers"]["path"], "ROW_BY_ROW_REQUIRED")

    def test_ledger_row_path_reads_the_reference_ledgers(self):
        self.fx.register_reference_ledgers()
        self.fx.act["ledgers"]["ledgers/US_QQQ/primary/MA200_c10.csv.gz"][0][5] = "1.0100000000001"  # within tolerance
        fields, exc, mech = self.fx.execute()
        self.assertIsNone(exc); self.assertEqual(mech["status"], "PASS")
        self.assertEqual(mech["layers"]["reference_reconciliation"]["ledgers"]["path"], "ROW_BY_ROW_AGAINST_REFERENCE_LEDGERS")

    def test_ledger_row_path_beyond_tolerance_rejected(self):
        self.fx.register_reference_ledgers()
        self.fx.act["ledgers"]["ledgers/US_QQQ/primary/MA200_c10.csv.gz"][1][5] = "1.03"
        mech = self.rejected(layer="reference_reconciliation", problem="TOLERANCE_EXCEEDED")
        self.assertEqual(mech["layers"]["reference_reconciliation"]["ledgers"]["ledgers_failing"], 1)

    def test_ledger_row_order_change_rejected(self):
        self.fx.register_reference_ledgers()
        led = self.fx.act["ledgers"]["ledgers/US_QQQ/primary/MA200_c10.csv.gz"]; led.reverse()
        self.rejected(layer="reference_reconciliation", problem="ROW_ORDER_DIFFERS")

    def test_corrupt_reference_ledgers_rejected(self):
        bad = copy.deepcopy(LEDGERS); bad["ledgers/US_QQQ/primary/MA200_c10.csv.gz"][0][5] = "9"
        self.fx.register_reference_ledgers(bad)
        self.fx.act["ledgers"]["ledgers/US_QQQ/primary/MA200_c10.csv.gz"][0][5] = "1.0100000000001"
        mech = self.rejected(layer="reference_reconciliation")
        self.assertTrue(mech["layers"]["reference_reconciliation"]["ledgers"]["reference_ledgers_corrupt"])

    # ---- other layers / inputs
    def test_internal_self_check_failure_rejected(self):
        self.fx.self_check_status = "FAIL"
        self.rejected(layer="internal_self_check")

    def test_missing_reference_for_external_input_is_incomplete(self):
        cfg = json.loads((self.fx.frozen / "config.json").read_text()); cfg["expected_db_sha256"] = "0" * 64
        (self.fx.frozen / "config.json").write_text(json.dumps(cfg))
        mech = self.rejected(kind=adapter.ReferenceUnavailable)
        self.assertEqual(mech["layers"]["reference_reconciliation"]["status"], "NOT_AVAILABLE"); self.assertEqual(mech["status"], "INCOMPLETE")

    def test_symlink_in_work_tree_rejected(self):
        if not hasattr(os, "symlink") or os.name == "nt":
            self.skipTest("symlinks need privileges on Windows; covered in the Linux image run")
        self.fx.symlink = True
        fields, exc, mech = self.fx.execute()
        self.assertIsNone(fields); self.assertIn("UNSAFE_WORK_TREE", str(exc))


class StrictComparator(unittest.TestCase):
    """review_probe_results.json#/numeric_diff_edges: each hidden mismatch is now reported."""

    def test_review_probe_edges_now_reported(self):
        cases = {"one_sided_nan": ([{"id": "a", "value": "nan"}], [{"id": "a", "value": "1"}], "CELL_CLASS_DIFFERS"),
                 "extra_reference_row": ([{"id": "a", "value": "1"}], [{"id": "a", "value": "1"}, {"id": "b", "value": "999"}], "MISSING_ROWS_IN_ACTUAL"),
                 "duplicate_reference_key": ([{"id": "a", "value": "1"}], [{"id": "a", "value": "999"}, {"id": "a", "value": "1"}], "DUPLICATE_KEYS_REFERENCE")}
        for name, (a, r, problem) in cases.items():
            with self.subTest(name):
                res = adapter._num_diff(a, r, ("id",))
                self.assertFalse(res["ok"]); self.assertIn(problem, json.dumps(res["problems"]))
                self.assertNotEqual(res, {"unmatched_rows": 0, "max_abs_diff": {"value": 0.0}})

    def test_both_sides_nan_or_empty_are_equal(self):
        self.assertTrue(adapter.reconcile_rows([{"id": "a", "v": "nan", "w": ""}], [{"id": "a", "v": "NaN", "w": ""}], ("id",))["ok"])

    def test_negative_inf_class(self):
        res = adapter.reconcile_rows([{"id": "a", "v": "-inf"}], [{"id": "a", "v": "inf"}], ("id",))
        self.assertFalse(res["ok"]); self.assertIn("NEG_INF", json.dumps(res["problems"]))


class RulePinned(unittest.TestCase):
    def test_rule_file_matches_pinned_hash(self):
        self.assertEqual(adapter.sha_file(adapter.RULE_PATH), adapter.RULE_SHA256)
        r = adapter.load_rule()
        self.assertEqual((r["rule_id"], r["finite_numeric"]["abs_tol"], r["finite_numeric"]["rel_tol"]), ("phase5_reference_reconcile_v1", 1e-08, 1e-10))

    def test_edited_rule_is_refused(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "rule.json"; r = adapter.load_rule(); r["finite_numeric"]["abs_tol"] = 1.0
            p.write_text(json.dumps(r))
            with patch.object(adapter, "RULE_PATH", p):
                with self.assertRaises(adapter.MechanicalCheckFailed):
                    adapter.mechanical_check(Path(td), {}, "exact", "x", Path(td))


class InProcessRefused(unittest.TestCase):
    def test_worker_does_not_claim_v2_in_process(self):
        from scripts.quant_research_v1 import worker
        with patch.dict(os.environ, {"QUANT_WORKER_ENGINES": "legacy_mom20_v1@1,phase5_o2o_v1@1"}, clear=False):
            os.environ.pop("QUANT_RESEARCH_INPROCESS_V2", None)
            self.assertEqual(worker.worker_engines(None), [("legacy_mom20_v1", "1")])


if __name__ == "__main__":
    unittest.main()
