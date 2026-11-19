"""M1 catalog v2: additive registry, strict versioning, backend eligibility, historical import, comparability."""
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts.quant_research_v1 import catalog, registry

# closeout 2026-11-20 (F3): phase5 runs need a designated reference, which exists only for the frozen exact input
EXACT = "c5b5ec22b1d3773f6a0345004080d4d2ff10e4cc7d48be21fe13a99cbc6ebe7e"

LEGACY_SCHEMA = """
CREATE TABLE datasets (dataset_id TEXT NOT NULL, dataset_version TEXT NOT NULL, title TEXT NOT NULL, symbol TEXT NOT NULL, frequency TEXT NOT NULL,
  adjustment TEXT NOT NULL, start_date TEXT NOT NULL, end_date TEXT NOT NULL, rows INTEGER NOT NULL, content_sha256 TEXT NOT NULL,
  audit_status TEXT NOT NULL, manifest_json TEXT NOT NULL, snapshot_path TEXT NOT NULL, registered_at TEXT NOT NULL, PRIMARY KEY (dataset_id, dataset_version));
CREATE TABLE factors (factor_id TEXT NOT NULL, factor_version TEXT NOT NULL, title TEXT NOT NULL, entry TEXT NOT NULL, params_json TEXT NOT NULL,
  input_requirements_json TEXT NOT NULL, output_definition TEXT NOT NULL, missing_rule TEXT NOT NULL, registered_at TEXT NOT NULL, PRIMARY KEY (factor_id, factor_version));
CREATE TABLE runs (run_id TEXT PRIMARY KEY, dataset_id TEXT NOT NULL, dataset_version TEXT NOT NULL, factor_id TEXT NOT NULL, factor_version TEXT NOT NULL,
  protocol_id TEXT NOT NULL, config_json TEXT NOT NULL, config_sha256 TEXT NOT NULL, code_version TEXT, status TEXT NOT NULL, requested_by TEXT,
  created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT, cancel_requested INTEGER NOT NULL DEFAULT 0, error_class TEXT, error_detail TEXT,
  artifact_dir TEXT, results_digest TEXT, summary_json TEXT);
"""


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); os.environ["QUANT_RESEARCH_ROOT"] = self.tmp.name
        self.db = registry.connect()

    def tearDown(self):
        self.db.close(); os.environ.pop("QUANT_RESEARCH_ROOT", None); self.tmp.cleanup()

    def dataset(self, did="qqq_daily", ver="1", content="abc"):
        m = {"dataset_id": did, "dataset_version": ver, "title": "t", "symbol": "US.QQQ", "frequency": "DAY", "adjustment": "a1",
             "start_date": "2000-01-01", "end_date": "2026-11-17", "rows": 10, "content_sha256": content, "audit_status": "AUDITED"}
        registry.register_dataset(self.db, m, f"datasets/{did}/{ver}")
        return (did, ver)


class LegacyCompatibility(Base):
    def test_legacy_registry_reads_with_unknown_v2_fields(self):
        p = Path(self.tmp.name) / "legacy" / "registry.sqlite3"; p.parent.mkdir()
        con = sqlite3.connect(p); con.executescript(LEGACY_SCHEMA)
        con.execute("INSERT INTO datasets VALUES('d','1','t','US.QQQ','DAY','a1','2000-01-01','2026-01-01',5,'h','AUDITED','{}','datasets/d/1','2026-11-18T00:00:00Z')")
        con.execute("INSERT INTO factors VALUES('mom20','1','m','e','{\"lookback_days\": 20}','{\"frequency\": \"DAY\", \"fields\": [\"close\"]}','o','m','2026-11-18T00:00:00Z')")
        con.execute("INSERT INTO runs(run_id,dataset_id,dataset_version,factor_id,factor_version,protocol_id,config_json,config_sha256,status,created_at)"
                    " VALUES('run-20261118T000000Z-aaaaaa','d','1','mom20','1','mom20-daily-long-cash','{}','c','SUCCEEDED','2026-11-18T00:00:00Z')")
        con.commit(); con.close()
        db = registry.connect(p); db.close()  # additive migration on an old file
        data = registry.read_all(p)
        self.assertEqual(data["datasets"][0]["meta"]["history_kind"], "UNKNOWN"); self.assertFalse(data["datasets"][0]["meta_recorded"])
        self.assertEqual(data["factors"][0]["spec"]["evaluation_mode"], "UNKNOWN")
        r = data["runs"][0]
        self.assertEqual((r["engine_id"], r["status"], r["grade"]), ("legacy_mom20_v1", "SUCCEEDED", None))
        con = sqlite3.connect(p); self.assertEqual(con.execute("select count(*) from runs").fetchone()[0], 1); con.close()


class SpecVersioning(Base):
    def test_idempotent_and_any_difference_rejected(self):
        specs = {f"{s['factor_id']}@{s['factor_version']}": s for s in catalog.builtin_specs()}
        first = catalog.register_builtin_specs(self.db)
        self.assertTrue(all(v == "REGISTERED" for v in first.values()))
        self.assertTrue(all(v == "UNCHANGED" for v in catalog.register_builtin_specs(self.db).values()))
        changed = dict(specs["phase5_p2_recovery@1"]); changed["warmup_rule"] = "200 closes"  # not entry, not params
        with self.assertRaises(ValueError) as c:
            catalog.register_spec(self.db, changed)
        self.assertIn("FACTOR_SPEC_CONFLICT", str(c.exception)); self.assertIn("warmup_rule", str(c.exception))
        legacy = dict(specs["mom20@1"]); legacy["input_data_kind"] = "NEWS_PANEL_HISTORICAL"
        with self.assertRaises(ValueError):
            catalog.register_spec(self.db, legacy)

    def test_incomplete_spec_rejected(self):
        s = dict(catalog.builtin_specs()[1]); s.pop("expected_direction")
        with self.assertRaises(ValueError):
            catalog.register_spec(self.db, s)

    def test_dataset_meta_conflict(self):
        d = self.dataset()
        meta = dict(catalog.PHASE5_DATASET_META); meta["data_kind"] = "OHLC_DAILY_SINGLE_CSV"
        self.assertEqual(catalog.register_dataset_meta(self.db, *d, meta), "REGISTERED")
        self.assertEqual(catalog.register_dataset_meta(self.db, *d, meta), "UNCHANGED")
        with self.assertRaises(ValueError):
            catalog.register_dataset_meta(self.db, *d, {**meta, "history_kind": "POINT_IN_TIME"})


class Eligibility(Base):
    def setUp(self):
        super().setUp(); catalog.register_builtin_specs(self.db)
        self.ds = self.dataset("phase5_bundle", "c5b5ec22", content=EXACT)
        catalog.register_dataset_meta(self.db, *self.ds, catalog.PHASE5_DATASET_META)

    def cap(self):
        self.db.execute("INSERT INTO worker_capabilities VALUES('sha256:img','phase5_o2o_v1','1',?,?)", (json.dumps({"ok": True}), registry.utcnow())); self.db.commit()

    def test_external_input_has_no_reference_and_is_blocked(self):
        self.cap()
        other = self.dataset("phase5_bundle", "other", content="f" * 64)
        catalog.register_dataset_meta(self.db, *other, catalog.PHASE5_DATASET_META)
        e = catalog.eligibility(self.db, other, ("phase5_strategy_v1", "1"))
        self.assertEqual(e["status"], "BLOCKED_INPUTS"); self.assertTrue(any("NO_ACCEPTANCE_REFERENCE" in r for r in e["reasons"]))

    def test_unbuilt_engine_blocks_then_capability_allows(self):
        e = catalog.eligibility(self.db, self.ds, ("phase5_strategy_v1", "1"))
        self.assertEqual(e["status"], "BLOCKED"); self.assertTrue(any("ENGINE_NOT_BUILT" in r for r in e["reasons"]))
        self.cap()
        self.assertEqual(catalog.eligibility(self.db, self.ds, ("phase5_strategy_v1", "1"))["status"], "ELIGIBLE")

    def test_text_candidate_blocked_inputs(self):
        self.cap()
        e = catalog.eligibility(self.db, self.ds, ("phase5_t1", "1"))
        self.assertEqual(e["status"], "BLOCKED_INPUTS"); self.assertTrue(any("NEWS_PANEL_HISTORICAL" in r for r in e["reasons"]))

    def test_cross_section_on_market_proxies_and_false_pit(self):
        self.cap()
        s = dict(catalog.builtin_specs()[1]); s.update(factor_id="xs_test", evaluation_mode="EQUITY_CROSS_SECTION", signal_available_at_rule="POINT_IN_TIME availability required")
        catalog.register_spec(self.db, s)
        e = catalog.eligibility(self.db, self.ds, ("xs_test", "1"))
        self.assertEqual(e["status"], "BLOCKED_INPUTS")
        self.assertTrue(any("WRONG_EVALUATION_MODE" in r for r in e["reasons"])); self.assertTrue(any("NOT_POINT_IN_TIME" in r for r in e["reasons"]))

    def test_create_run_v2_refuses_ineligible_and_binds_contracts(self):
        with self.assertRaises(ValueError):
            catalog.create_run_v2(self.db, self.ds, ("phase5_strategy_v1", "1"), {"protocol_id": "phase5_strategy_v1", "factor": {"factor_id": "phase5_strategy_v1", "factor_version": "1"}},
                                  ("phase5_o2o_v1", "1"))
        self.cap()
        rid = catalog.create_run_v2(self.db, self.ds, ("phase5_strategy_v1", "1"),
                                    {"protocol_id": "phase5_strategy_v1", "factor": {"factor_id": "phase5_strategy_v1", "factor_version": "1"}, "benchmark_id": "MA200_SAME_INTERVALS",
                                     "sample_mask": {"periods": "config.json"}}, ("phase5_o2o_v1", "1"))
        r = self.db.execute("select * from runs where run_id=?", (rid,)).fetchone()
        c = json.loads(r["contracts_json"])
        self.assertEqual((r["engine_id"], c["metric_contract"], c["boundary_contract"]), ("phase5_o2o_v1", "CAGR_ACTUAL_CALENDAR_ELAPSED", "RESET_EVALUATION_PERIOD_EPISODE_CLOSE"))
        # the legacy worker (default capability) never claims it
        self.assertIsNone(registry.claim_next(self.db, "legacy-worker"))
        self.assertIsNone(registry.claim_run(self.db, rid, "legacy-worker"))
        self.assertEqual(self.db.execute("select status from runs where run_id=?", (rid,)).fetchone()[0], "QUEUED")
        got = registry.claim_next(self.db, "numeric-worker", engines=[("phase5_o2o_v1", "1")])
        self.assertEqual(got["run_id"], rid)


class ImportAndCompare(Base):
    def test_phase5_import_is_not_a_run(self):
        iid = catalog.import_phase5(self.db)
        self.assertEqual(catalog.import_phase5(self.db), iid)  # idempotent
        self.assertEqual(self.db.execute("select count(*) from runs").fetchone()[0], 0)
        data = registry.read_all()
        x = data["external_runs"][0]
        self.assertEqual(x["record"]["kind"], "EXTERNAL_IMPORT"); self.assertTrue(x["record"]["not_a_platform_run"])
        self.assertEqual(x["record"]["results"]["primary_pass_count"], 0); self.assertEqual(len(x["record"]["results"]["primary_tests"]), 12)
        g = x["grade"]
        self.assertEqual((g["software_status"], g["scientific_status"]), ("EXTERNAL_IMPORT_NOT_PLATFORM_EXECUTED", "FIRST_SCREEN_NOT_PASSED"))
        self.db.execute("update external_runs set record_sha256='tampered' where import_id=?", (iid,)); self.db.commit()
        with self.assertRaises(ValueError):
            catalog.import_phase5(self.db)

    def test_compare_requires_equal_contracts(self):
        catalog.register_builtin_specs(self.db); ds = self.dataset(content=EXACT)
        legacy = registry.create_run(self.db, ds, ("mom20", "1"), {"protocol_id": "mom20-daily-long-cash", "factor": {"factor_id": "mom20", "factor_version": "1"}, "segments": [1]})
        legacy2 = registry.create_run(self.db, ds, ("mom20", "1"), {"protocol_id": "mom20-daily-long-cash", "factor": {"factor_id": "mom20", "factor_version": "1"}, "segments": [1]})
        self.db.execute("INSERT INTO worker_capabilities VALUES('img','phase5_o2o_v1','1',?,?)", (json.dumps({"ok": True}), registry.utcnow()))
        catalog.register_dataset_meta(self.db, *ds, {**catalog.PHASE5_DATASET_META})
        p5 = catalog.create_run_v2(self.db, ds, ("phase5_strategy_v1", "1"), {"protocol_id": "phase5_strategy_v1", "factor": {"factor_id": "phase5_strategy_v1", "factor_version": "1"},
                                                                          "benchmark_id": "MA200_SAME_INTERVALS"}, ("phase5_o2o_v1", "1"))
        r = catalog.compare(self.db, legacy, p5)
        self.assertEqual(r["result"], "NOT_COMPARABLE")
        self.assertTrue({"execution_contract", "cost_contract", "metric_contract", "boundary_contract", "benchmark_id"} <= set(r["differences"]))
        self.assertIsNone(r["ranking"])
        # closeout 2026-11-20 (F2): these minimal configs state no costs/cash/annualization/benchmark/execution, so the
        # conditions are UNKNOWN; two UNKNOWNs never match (was COMPARABLE before the fix). Full configs: test_quant_research_compare_f2.py
        same = catalog.compare(self.db, legacy, legacy2)
        self.assertEqual(same["result"], "NOT_COMPARABLE"); self.assertFalse(same["ranking_allowed"])
        self.assertTrue({"cost", "cash", "annualization"} <= set(same["unknown_conditions"]))


class LegacyDatasetEligibility(Base):
    def test_mom20_on_legacy_v1_dataset_stays_eligible(self):
        catalog.register_builtin_specs(self.db)
        m = {"schema": "quant_research_dataset_v1", "dataset_id": "qqq", "dataset_version": "1", "title": "t", "symbol": "US.QQQ", "frequency": "DAY",
             "adjustment": "a1", "start_date": "2000-01-01", "end_date": "2026-11-17", "rows": 10, "content_sha256": "x", "audit_status": "AUDITED"}
        registry.register_dataset(self.db, m, "datasets/qqq/1")
        self.assertEqual(catalog.eligibility(self.db, ("qqq", "1"), ("mom20", "1"))["status"], "ELIGIBLE")
        self.assertEqual(catalog.eligibility(self.db, ("qqq", "1"), ("phase5_strategy_v1", "1"))["status"], "BLOCKED_INPUTS")


if __name__ == "__main__":
    unittest.main()
