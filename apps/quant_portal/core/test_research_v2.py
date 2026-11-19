"""M1 endpoints: catalog in the registry JSON, capabilities, compare, historical import, artifact allowlist."""
import os as _os
_os.environ.setdefault("QUANT_CODE_VERSION", "test-code-version")
_os.environ.setdefault("QUANT_IMAGE_ID", "sha256:test-image")

import json
import os
import tempfile
from pathlib import Path

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from .personal_feed_cache import REPO_ROOT
import sys
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
from scripts.quant_research_v1 import catalog, registry  # noqa: E402

OWNER = "owner@local.invalid"


@override_settings(PERSONAL_MODE=True, PERSONAL_FEED_OWNER_USERNAME=OWNER)
class ResearchV2EndpointTests(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["QUANT_RESEARCH_ROOT"] = str(Path(self.tmp.name) / "research")
        self.user = get_user_model().objects.create_user(OWNER, password="test-password-long")
        db = registry.connect()
        catalog.register_builtin_specs(db); self.import_id = catalog.import_phase5(db)
        m = {"dataset_id": "phase5_bundle", "dataset_version": "c5b5ec22", "title": "phase5 bundle", "symbol": "US.QQQ,US..SOX,SH.000001,SH.000688",
             "frequency": "DAY", "adjustment": "per series", "start_date": "1996-01-02", "end_date": "2026-11-17", "rows": 30716,
             "content_sha256": "c5b5ec22b1d3773f6a0345004080d4d2ff10e4cc7d48be21fe13a99cbc6ebe7e", "audit_status": "EXTERNAL_SNAPSHOT_HASH_BOUND"}
        registry.register_dataset(db, m, "datasets/phase5_bundle/c5b5ec22"); catalog.register_dataset_meta(db, "phase5_bundle", "c5b5ec22", catalog.PHASE5_DATASET_META)
        db.close()

    def tearDown(self):
        os.environ.pop("QUANT_RESEARCH_ROOT", None); self.tmp.cleanup()

    def login(self):
        self.client.force_login(self.user)

    def test_registry_json_has_backend_eligibility_and_import(self):
        self.login()
        d = self.client.get(reverse("research_registry_json")).json()
        f = {x["factor_id"]: x for x in d["factors"]}
        e = f["phase5_strategy_v1"]["eligibility"]["phase5_bundle@c5b5ec22"]
        self.assertEqual(e["status"], "BLOCKED"); self.assertTrue(any("ENGINE_NOT_BUILT" in r for r in e["reasons"]))
        self.assertEqual(f["phase5_t1"]["eligibility"]["phase5_bundle@c5b5ec22"]["status"], "BLOCKED_INPUTS")
        self.assertEqual(d["external_runs"][0]["import_id"], self.import_id); self.assertEqual(d["runs"], [])
        self.assertIn("phase5_o2o_v1@1", d["engines"])

    def test_create_phase5_run_refused_without_built_engine_then_accepted(self):
        self.login()
        r = self.client.post(reverse("research_run_create"), {"dataset": "phase5_bundle@c5b5ec22", "factor": "phase5_strategy_v1@1", "protocol": "phase5_strategy_v1"})
        self.assertEqual(r.status_code, 400); self.assertIn("ENGINE_NOT_BUILT", r.json()["reason"])
        db = registry.connect()
        db.execute("INSERT INTO worker_capabilities VALUES('sha256:numeric','phase5_o2o_v1','1',?,?)", (json.dumps({"ok": True}), registry.utcnow())); db.commit(); db.close()
        r = self.client.post(reverse("research_run_create"), {"dataset": "phase5_bundle@c5b5ec22", "factor": "phase5_strategy_v1@1", "protocol": "phase5_strategy_v1"})
        self.assertEqual(r.status_code, 200, r.content)
        run = [x for x in self.client.get(reverse("research_registry_json")).json()["runs"] if x["run_id"] == r.json()["run_id"]][0]
        self.assertEqual((run["status"], run["engine_id"], run["contracts"]["metric_contract"]), ("QUEUED", "phase5_o2o_v1", "CAGR_ACTUAL_CALENDAR_ELAPSED"))
        # the browser cannot pick another strategy for a frozen protocol
        r = self.client.post(reverse("research_run_create"), {"dataset": "phase5_bundle@c5b5ec22", "factor": "phase5_p1_trend@1", "protocol": "phase5_strategy_v1"})
        self.assertEqual(r.status_code, 400)

    def test_capabilities_import_and_compare(self):
        self.login()
        c = self.client.get(reverse("research_capabilities_json")).json()
        self.assertEqual([x["engine"] for x in c["registered"]], ["legacy_mom20_v1@1"])
        x = self.client.get(reverse("research_import_json", args=[self.import_id])).json()
        self.assertTrue(x["record"]["not_a_platform_run"])
        self.assertEqual(self.client.get(reverse("research_import_json", args=["ext-x-..%2F"])).status_code, 404)
        db = registry.connect()
        db.execute("INSERT INTO worker_capabilities VALUES('sha256:numeric','phase5_o2o_v1','1',?,?)", (json.dumps({"ok": True}), registry.utcnow()))
        p5 = catalog.create_run_v2(db, ("phase5_bundle", "c5b5ec22"), ("phase5_strategy_v1", "1"),
                                   {"protocol_id": "phase5_strategy_v1", "factor": {"factor_id": "phase5_strategy_v1", "factor_version": "1"}, "benchmark_id": "MA200"}, ("phase5_o2o_v1", "1"))
        legacy = registry.create_run(db, ("phase5_bundle", "c5b5ec22"), ("mom20", "1"), {"protocol_id": "mom20-daily-long-cash", "factor": {"factor_id": "mom20", "factor_version": "1"}})
        db.close()
        r = self.client.get(reverse("research_compare_json") + f"?a={legacy}&b={p5}").json()
        self.assertEqual(r["result"], "NOT_COMPARABLE"); self.assertIn("metric_contract", r["differences"])
        self.assertEqual(self.client.get(reverse("research_compare_json") + "?a=../x&b=y").status_code, 404)

    def test_artifact_download_allowlist_and_traversal(self):
        self.login()
        db = registry.connect()
        db.execute("INSERT INTO worker_capabilities VALUES('sha256:numeric','phase5_o2o_v1','1',?,?)", (json.dumps({"ok": True}), registry.utcnow()))
        rid = catalog.create_run_v2(db, ("phase5_bundle", "c5b5ec22"), ("phase5_strategy_v1", "1"),
                                    {"protocol_id": "phase5_strategy_v1", "factor": {"factor_id": "phase5_strategy_v1", "factor_version": "1"}}, ("phase5_o2o_v1", "1"))
        d = registry.root() / "runs" / rid; d.mkdir(parents=True)
        (d / "metrics.csv").write_bytes(b"a,b\n1,2\n"); (d / "secret.txt").write_text("not registered", encoding="utf-8")
        catalog.register_artifacts(db, rid, d, ["metrics.csv"]); db.close()
        url = lambda n: reverse("research_run_artifact", args=[rid, n])
        r = self.client.get(url("metrics.csv")); self.assertEqual(r.status_code, 200); self.assertEqual(r.content, b"a,b\n1,2\n")
        self.assertEqual(r["X-Content-SHA256"], catalog.sha_file(d / "metrics.csv"))
        self.assertEqual(self.client.get(url("secret.txt")).status_code, 404)            # present but not registered
        self.assertEqual(self.client.get(url("..%2Fregistry.sqlite3")).status_code, 404)  # traversal
        other = get_user_model().objects.create_user("qa@example.org", password="test-password-long")
        self.client.force_login(other)
        self.assertEqual(self.client.get(url("metrics.csv")).status_code, 404)            # owner only
