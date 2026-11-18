import sqlite3
from contextlib import closing
import sys
import tempfile
from pathlib import Path

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import WatchlistItem
from .personal_feed_cache import REPO_ROOT

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
from scripts.quant_cloud_official import sec_filings  # noqa: E402


class OfficialFilingsAndDeploymentTests(TestCase):
    def owner(self):
        user = get_user_model().objects.create_user("owner@local.invalid", password="test-password-long")
        WatchlistItem.objects.create(user=user, symbol="NVDA")
        self.client.force_login(user)
        return user

    def test_filings_endpoint_is_owner_only_and_reports_no_data(self):
        with tempfile.TemporaryDirectory() as folder, override_settings(
                PERSONAL_MODE=True, OFFICIAL_FILINGS_DB_PATH=Path(folder) / "none.sqlite3"):
            other = get_user_model().objects.create_user("other@example.test", password="test-password-long")
            self.client.force_login(other)
            self.assertEqual(self.client.get(reverse("official_filings_json")).status_code, 404)
            self.owner()
            data = self.client.get(reverse("official_filings_json")).json()
            self.assertEqual(data["state"], "NO_DATA")
            self.assertEqual(data["watchlist"], ["NVDA"])

    def test_filings_endpoint_reads_imported_rows_only_for_watchlist(self):
        with tempfile.TemporaryDirectory() as folder:
            db = Path(folder) / "f.sqlite3"
            with closing(sqlite3.connect(db)) as con, con:
                con.executescript(sec_filings.SCHEMA)
                con.execute("INSERT INTO source_state(source,last_attempt_status,last_success_at_utc,data_as_of,last_success_batch_id)"
                            " VALUES('SEC_EDGAR','SUCCESS','2026-11-19T00:00:00Z','2026-11-17','b1')")
                con.execute("INSERT INTO batches(batch_id,started_at_utc,status,config_sha256,window_days,symbols_json)"
                            " VALUES('b1','2026-11-19T00:00:00Z','SUCCESS','x',30,'[]')")
                for acc, sym in (("0000000001-26-000001", "NVDA"), ("0000000002-26-000001", "MU")):
                    con.execute("INSERT INTO filings(accession_number,cik,form,filing_date,index_url,in_window,content_sha256,"
                                "first_seen_batch,last_seen_batch,first_seen_at_utc,last_seen_at_utc,source_url)"
                                " VALUES(?,1,'8-K','2026-11-17','https://www.sec.gov/x',1,'h','b1','b1','t','t','u')", (acc,))
                    con.execute("INSERT INTO filing_symbols VALUES(?,?,'ISSUER')", (acc, sym))
            with override_settings(PERSONAL_MODE=True, OFFICIAL_FILINGS_DB_PATH=db):
                self.owner()
                data = self.client.get(reverse("official_filings_json")).json()
            self.assertEqual(data["state"], "SUCCESS")
            self.assertEqual([f["symbols"] for f in data["filings"]], ["NVDA"])  # MU is not on this watchlist

    def test_cloud_label_and_provider_gates(self):
        with override_settings(PERSONAL_MODE=True, DEPLOYMENT_KIND="cloud_private",
                               PROVIDER_GATES={"moomoo": "PENDING_SUPPORT_REPLY", "jin10": "PENDING_WRITTEN_PERMISSION"}):
            self.owner()
            html = self.client.get(reverse("dashboard")).content.decode()
        self.assertIn("云端私人部署 · 只读", html)
        self.assertNotIn("本机测试 · 只读", html)
        self.assertIn("等待 moomoo 对云服务器使用的答复", html)
        self.assertIn("金十用户协议要求书面授权", html)

    def test_local_label_unchanged_without_gates(self):
        with override_settings(PERSONAL_MODE=True, DEPLOYMENT_KIND="local", PROVIDER_GATES={"moomoo": "", "jin10": ""}):
            self.owner()
            html = self.client.get(reverse("dashboard")).content.decode()
        self.assertIn("本机测试 · 只读", html)
        self.assertNotIn("此部署尚未启用的数据源", html)


class DeploymentLabelEverywhereTests(TestCase):
    def test_cloud_labels_on_base_settings_and_login(self):
        with override_settings(PERSONAL_MODE=True, DEPLOYMENT_KIND="cloud_private"):
            login_html = self.client.get(reverse("login")).content.decode()
            user = get_user_model().objects.create_user("owner@local.invalid", password="test-password-long")
            self.client.force_login(user)
            pages = [self.client.get(reverse(n)).content.decode() for n in ("dashboard", "user_settings")]
        self.assertIn("PRIVATE CLOUD DEPLOYMENT", login_html)
        for html in pages + [login_html]:
            self.assertNotIn("本机个人测试", html)
            self.assertNotIn("LOCAL PERSONAL TEST", html)
        self.assertIn("云端私人部署 · 显示时区", pages[0])
