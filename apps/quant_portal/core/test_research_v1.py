"""Research workbench endpoints: owner-only, web only queues (never runs), results and ledger download."""
import os as _os
_os.environ.setdefault("QUANT_CODE_VERSION", "test-code-version")
_os.environ.setdefault("QUANT_IMAGE_ID", "sha256:test-image")

import os
import sqlite3
import tempfile
from pathlib import Path

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from .personal_feed_cache import REPO_ROOT
import sys
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
from scripts.quant_research_v1 import registry  # noqa: E402
from scripts.quant_research_v1.engine import FACTORS, build_snapshot  # noqa: E402

OWNER = "owner@local.invalid"


@override_settings(PERSONAL_MODE=True, PERSONAL_FEED_OWNER_USERNAME=OWNER)
class ResearchEndpointTests(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["QUANT_RESEARCH_ROOT"] = str(Path(self.tmp.name) / "research")
        self.user = get_user_model().objects.create_user(OWNER, password="test-password-long")

    def tearDown(self):
        os.environ.pop("QUANT_RESEARCH_ROOT", None)
        self.tmp.cleanup()

    def seed(self):
        src = Path(self.tmp.name) / "src.sqlite"
        con = sqlite3.connect(src)
        con.execute("CREATE TABLE research_daily(symbol TEXT, autype INT, local_date TEXT, open REAL, high REAL, low REAL, close REAL, source_archive_sha256 TEXT)")
        p = 100.0
        for i in range(300):
            o = p; p *= 1.002 if (i // 25) % 2 == 0 else 0.998
            con.execute("INSERT INTO research_daily VALUES('SYN',1,?,?,?,?,?,'synthetic')",
                        (f"{2019 + i // 250}-{1 + (i % 250) // 21:02d}-{1 + (i % 250) % 21:02d}", o, max(o, p), min(o, p), p))
        con.commit(); con.close()
        db = registry.connect()
        m = build_snapshot(src, "SYN", 1, registry.root() / "datasets/syn/1", dataset_id="syn", dataset_version="1",
                           title="synthetic", source_receipt={}, audit_status="SYNTHETIC_TEST_ONLY")
        registry.register_dataset(db, m, "datasets/syn/1"); registry.register_factor(db, FACTORS[("mom20", "1")]); db.close()

    def test_non_owner_gets_404_everywhere(self):
        other = get_user_model().objects.create_user("qa@example.org", password="test-password-long")
        self.client.force_login(other)
        self.assertEqual(self.client.get(reverse("research_registry_json")).status_code, 404)
        self.assertEqual(self.client.post(reverse("research_run_create"), {}).status_code, 404)

    def test_empty_registry_is_a_state_not_an_error(self):
        self.client.force_login(self.user)
        data = self.client.get(reverse("research_registry_json")).json()
        self.assertEqual(data["state"], "NOT_INITIALIZED")
        self.assertEqual(data["protocols"][0]["protocol_id"], "mom20-daily-long-cash")

    def test_create_queues_only_and_cancel_and_download(self):
        self.seed(); self.client.force_login(self.user)
        bad = self.client.post(reverse("research_run_create"), {"dataset": "syn@1", "factor": "mom20@1", "protocol": "custom-code"})
        self.assertEqual(bad.status_code, 400)  # only protocols frozen in the repository
        r = self.client.post(reverse("research_run_create"), {"dataset": "syn@1", "factor": "mom20@1", "protocol": "mom20-daily-long-cash"}).json()
        self.assertEqual(r["result"], "QUEUED")
        runs = self.client.get(reverse("research_registry_json")).json()["runs"]
        self.assertEqual([x["status"] for x in runs], ["QUEUED"])  # the web request did not execute it
        from scripts.quant_research_v1.worker import run_once
        self.assertEqual(run_once(60), 0)
        detail = self.client.get(reverse("research_run_json", args=[r["run_id"]])).json()
        self.assertEqual(detail["run"]["status"], "SUCCEEDED"); self.assertTrue(detail["verify"]["ok"])
        led = self.client.get(reverse("research_run_ledger", args=[r["run_id"]]) + "?bps=5")
        self.assertEqual(led.status_code, 200); self.assertIn(b"net_return", led.content)
        self.assertEqual(self.client.get(reverse("research_run_ledger", args=[r["run_id"]]) + "?bps=7").status_code, 404)
        q = self.client.post(reverse("research_run_create"), {"dataset": "syn@1", "factor": "mom20@1", "protocol": "mom20-daily-long-cash"}).json()
        self.assertEqual(self.client.post(reverse("research_run_cancel", args=[q["run_id"]])).json()["result"], "CANCELLED")
        self.assertEqual(self.client.get("/research/runs/..%2Fetc.json").status_code, 404)
        self.assertEqual(self.client.get("/research/runs/not-a-run/ledger.csv?bps=0").status_code, 404)


@override_settings(PERSONAL_MODE=True, PERSONAL_FEED_OWNER_USERNAME=OWNER)
class ResearchRerunTests(ResearchEndpointTests):
    def test_rerun_links_retries_and_reports_audit(self):
        self.seed(); self.client.force_login(self.user)
        from scripts.quant_research_v1.worker import run_once
        r = self.client.post(reverse("research_run_create"), {"dataset": "syn@1", "factor": "mom20@1", "protocol": "mom20-daily-long-cash"}).json()
        run_once(60)
        runs = self.client.get(reverse("research_registry_json")).json()["runs"]
        self.assertTrue(runs[0]["latest_audit"]["ok"]); self.assertEqual(runs[0]["latest_audit"]["verifier_version"], "audit-v2.1")
        again = self.client.post(reverse("research_run_rerun", args=[r["run_id"]])).json()
        self.assertEqual(again["result"], "QUEUED")
        db = registry.connect()
        # M2 change (task E5/E6): every rerun, also of a SUCCEEDED run, is a new run linked by attempt_of
        self.assertEqual(db.execute("select attempt_of from runs where run_id=?", (again["run_id"],)).fetchone()[0], r["run_id"])
        registry.request_cancel(db, again["run_id"]); db.close()
        retry = self.client.post(reverse("research_run_rerun", args=[again["run_id"]])).json()
        db = registry.connect()
        self.assertEqual(db.execute("select attempt_of from runs where run_id=?", (retry["run_id"],)).fetchone()[0], again["run_id"]); db.close()
        detail = self.client.get(reverse("research_run_json", args=[r["run_id"]])).json()
        self.assertTrue(detail["latest_audit"]["ok"])
