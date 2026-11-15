"""Isolated software checks; the guidance integration case uses a pinned redacted source summary."""
import copy
import json
from datetime import timedelta
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.request import urlopen

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import Invitation, PublicFactCard, PublicMarketSnapshot, UserPreferences, WatchlistItem, WorkerHeartbeat
from .management.commands.bootstrap_personal import Command as BootstrapCommand, checked_guidance_range, EXPECTED_FACTS_SHA256


PASSWORD = "GraniteRiver!23Complex"


class InvitationTests(TestCase):
    def test_invite_only_registration_consumes_code_and_creates_nonstaff_user(self):
        _, code = Invitation.issue("member@example.org", timezone.now() + timedelta(days=1))
        response = self.client.post(reverse("invite_register"), {
            "email": "Member@Example.org", "invite_code": code,
            "password1": PASSWORD, "password2": PASSWORD,
        })
        self.assertRedirects(response, reverse("dashboard"))
        user = get_user_model().objects.get(username="member@example.org")
        self.assertFalse(user.is_staff)
        self.assertTrue(Invitation.objects.get(email="member@example.org").consumed_at)
        self.assertTrue(UserPreferences.objects.filter(user=user).exists())
        self.client.logout()
        response = self.client.post(reverse("invite_register"), {
            "email": "member@example.org", "invite_code": code,
            "password1": PASSWORD, "password2": PASSWORD,
        })
        self.assertEqual(response.status_code, 200)

    def test_wrong_email_or_expired_code_cannot_register(self):
        _, code = Invitation.issue("target@example.org", timezone.now() + timedelta(days=1))
        payload = {"email": "other@example.org", "invite_code": code, "password1": PASSWORD, "password2": PASSWORD}
        self.assertEqual(self.client.post(reverse("invite_register"), payload).status_code, 200)
        self.assertFalse(get_user_model().objects.filter(username="other@example.org").exists())
        _, expired = Invitation.issue("old@example.org", timezone.now() - timedelta(seconds=1))
        payload.update(email="old@example.org", invite_code=expired)
        self.assertEqual(self.client.post(reverse("invite_register"), payload).status_code, 200)
        self.assertFalse(get_user_model().objects.filter(username="old@example.org").exists())

    @override_settings(PERSONAL_MODE=True)
    def test_personal_registration_seeds_only_candidate_watchlist(self):
        _, code = Invitation.issue("owner@local.invalid", timezone.now() + timedelta(days=1))
        self.client.post(reverse("invite_register"), {
            "email": "owner@local.invalid", "invite_code": code,
            "password1": PASSWORD, "password2": PASSWORD,
        })
        user = get_user_model().objects.get(username="owner@local.invalid")
        self.assertEqual(
            set(WatchlistItem.objects.filter(user=user).values_list("symbol", flat=True)),
            {"TQQQ", "SOXX", "SPY", "QQQ", "NVDA", "MU"},
        )

    def test_invite_code_is_written_to_private_file_not_stdout(self):
        with TemporaryDirectory(dir=Path(settings.BASE_DIR) / "var") as folder:
            path = Path(folder) / "invite.txt"
            out = StringIO()
            call_command("create_invite", "qa@example.org", output=str(path), stdout=out)
            code = path.read_text(encoding="utf-8").strip()
            self.assertTrue(code)
            self.assertNotIn(code, out.getvalue())
            self.assertEqual(Invitation.objects.count(), 1)


class IsolationTests(TestCase):
    def setUp(self):
        users = get_user_model().objects
        self.a = users.create_user(username="a@example.org", email="a@example.org", password=PASSWORD)
        self.b = users.create_user(username="b@example.org", email="b@example.org", password=PASSWORD)
        self.b_item = WatchlistItem.objects.create(user=self.b, symbol="NVDA", asset_type="EQUITY")

    def test_other_users_watchlist_is_not_readable_or_removable(self):
        self.client.force_login(self.a)
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context["watchlist"]), [])
        response = self.client.post(reverse("watchlist_remove", args=[self.b_item.pk]))
        self.assertEqual(response.status_code, 404)
        self.assertTrue(WatchlistItem.objects.filter(pk=self.b_item.pk).exists())

    def test_add_validates_symbol_and_settings_are_per_user(self):
        self.client.force_login(self.a)
        self.client.post(reverse("watchlist_add"), {"symbol": "mu", "display_name": "Micron", "asset_type": "EQUITY"})
        self.assertTrue(WatchlistItem.objects.filter(user=self.a, symbol="MU").exists())
        self.client.post(reverse("watchlist_add"), {"symbol": "<script>", "asset_type": "EQUITY"})
        self.assertEqual(WatchlistItem.objects.filter(user=self.a).count(), 1)
        self.client.post(reverse("user_settings"), {"display_timezone": "UTC"})
        self.assertEqual(UserPreferences.objects.get(user=self.a).display_timezone, "UTC")
        self.assertFalse(UserPreferences.objects.filter(user=self.b).exists())

    def test_anonymous_cannot_access_personal_pages(self):
        for url in (reverse("dashboard"), reverse("user_settings")):
            self.assertEqual(self.client.get(url).status_code, 302)


class SessionAndLimitTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="member@example.org", email="member@example.org", password=PASSWORD,
        )

    def test_logout_requires_post_and_invalidates_session(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(reverse("logout")).status_code, 405)
        self.assertEqual(self.client.post(reverse("logout")).status_code, 302)
        self.assertEqual(self.client.get(reverse("dashboard")).status_code, 302)

    def test_login_limiter_blocks_after_five_failed_attempts(self):
        login_url = reverse("login")
        for _ in range(5):
            self.assertEqual(self.client.post(login_url, {"username": "member@example.org", "password": "incorrect"}).status_code, 200)
        self.assertEqual(self.client.post(login_url, {"username": "member@example.org", "password": PASSWORD}).status_code, 429)

    def test_csrf_protects_state_changes(self):
        browser = Client(enforce_csrf_checks=True)
        browser.force_login(self.user)
        response = browser.post(reverse("watchlist_add"), {"symbol": "MU", "asset_type": "EQUITY"})
        self.assertEqual(response.status_code, 403)
        self.assertFalse(WatchlistItem.objects.filter(user=self.user).exists())

    @override_settings(DEBUG=False, PERSONAL_MODE=False)
    def test_admin_http_login_is_not_exposed(self):
        self.assertEqual(self.client.get("/admin/login/").status_code, 404)


class PublicCacheTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="reader@example.org", email="reader@example.org", password=PASSWORD,
        )
        WatchlistItem.objects.create(user=self.user, symbol="NVDA", asset_type="EQUITY")
        self.client.force_login(self.user)

    def test_provider_output_defaults_off_even_with_synthetic_licensed_row(self):
        PublicMarketSnapshot.objects.create(
            symbol="NVDA", provider="synthetic_test", price_text="123.45", currency="USD",
            provider_time=timezone.now(), received_at=timezone.now(),
            source_class=PublicMarketSnapshot.SourceClass.LICENSED,
            rights_basis="Synthetic software test only", display_approved=True,
        )
        response = self.client.get(reverse("dashboard"))
        self.assertIsNone(response.context["watch_rows"][0]["snapshot"])
        with override_settings(PUBLIC_MARKET_DATA_ENABLED=True):
            response = self.client.get(reverse("dashboard"))
            self.assertIsNotNone(response.context["watch_rows"][0]["snapshot"])

    def test_restricted_card_and_unsafe_link_cannot_be_public(self):
        PublicFactCard.objects.create(
            issuer="Synthetic", fiscal_period="Demo period", metric="Demo metric", value_text="N/A",
            source_url="https://example.org/source", as_of=timezone.now(),
            source_class=PublicFactCard.SourceClass.PROVIDER, display_approved=False,
        )
        row = PublicFactCard.objects.first()
        PublicFactCard.objects.filter(pk=row.pk).update(display_approved=True, rights_basis="unverified")
        self.assertEqual(len(self.client.get(reverse("dashboard")).context["fact_cards"]), 0)
        unsafe = PublicFactCard(
            issuer="Synthetic", fiscal_period="Demo period", metric="Demo metric", value_text="N/A",
            source_url="javascript:alert(1)", as_of=timezone.now(),
        )
        with self.assertRaises(ValidationError):
            unsafe.save()

    def test_personal_review_card_never_enters_shared_mode(self):
        PublicFactCard.objects.create(
            issuer="Synthetic", fiscal_period="Demo period", metric="Demo metric", value_text="N/A",
            source_url="https://example.org/source", source_date=timezone.now().date(),
            currency="USD", basis_text="GAAP",
            source_class=PublicFactCard.SourceClass.OFFICIAL,
            visibility_scope="LOCAL_ONLY", evidence_state="CODEX_REVIEW_DEVELOPMENT",
            source_review_status="CODEX_REVIEW_SOURCE_SUPPORTED",
        )
        with override_settings(PERSONAL_MODE=False):
            self.assertEqual(len(self.client.get(reverse("dashboard")).context["fact_cards"]), 0)
        with override_settings(PERSONAL_MODE=True):
            response = self.client.get(reverse("dashboard"))
            self.assertEqual(len(response.context["fact_cards"]), 1)
            self.assertContains(response, "GAAP")
            self.assertContains(response, "USD")

    @override_settings(PERSONAL_MODE=True)
    def test_ambiguous_local_official_card_remains_hidden(self):
        PublicFactCard.objects.create(
            issuer="Synthetic", fiscal_period="Demo period", metric="Demo metric", value_text="N/A",
            source_url="https://example.org/source", source_date=timezone.now().date(),
            source_class=PublicFactCard.SourceClass.OFFICIAL, visibility_scope="LOCAL_ONLY",
            evidence_state="CODEX_REVIEW_DEVELOPMENT", source_review_status="CODEX_REVIEW_AMBIGUOUS",
        )
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(len(response.context["fact_cards"]), 0)
        self.assertNotContains(response, "Demo metric")

    def test_approved_official_card_is_visible(self):
        PublicFactCard.objects.create(
            issuer="Synthetic", fiscal_period="Demo period", metric="Demo metric", value_text="N/A",
            source_url="https://example.org/source", as_of=timezone.now(),
            source_class=PublicFactCard.SourceClass.DEMO, evidence_state="DEMO",
            rights_basis="Synthetic software test only", display_approved=True,
            visibility_scope="SHARED_APPROVED",
        )
        self.assertEqual(len(self.client.get(reverse("dashboard")).context["fact_cards"]), 1)


class HistoricalGuidanceImportTests(TestCase):
    """The pinned, redacted SEC development summary is an integration fixture, not gold."""

    @staticmethod
    def source_path():
        return Path(__file__).resolve().parents[3] / "results" / "market_observer_financial_text_v2_20261115" / "fact_cards_v4_redacted.json"

    @override_settings(PERSONAL_MODE=True, DEBUG=True)
    def test_pinned_guidance_and_cash_flow_import_once_and_render_separately(self):
        from hashlib import sha256

        source_path = self.source_path()
        self.assertEqual(sha256(source_path.read_bytes()).hexdigest(), EXPECTED_FACTS_SHA256)
        with TemporaryDirectory() as private_root, override_settings(BASE_DIR=Path(private_root)):
            for _ in range(2):
                call_command("bootstrap_personal", facts_path=str(source_path), stdout=StringIO())
        self.assertEqual(PublicFactCard.objects.count(), 14)
        self.assertEqual(PublicFactCard.objects.filter(fact_kind="GUIDANCE").count(), 2)
        self.assertEqual(PublicFactCard.objects.filter(fact_topic="free_cash_flow").count(), 1)
        self.assertEqual(
            set(PublicFactCard.objects.filter(fact_kind="GUIDANCE").values_list("candidate_id", flat=True)),
            {"581e043fc41120b9a092", "8a350fb1e88c5c68f218"},
        )
        nvda = PublicFactCard.objects.get(candidate_id="581e043fc41120b9a092")
        self.assertEqual((nvda.fiscal_period, nvda.range_lower_text, nvda.range_upper_text),
                         ("Q1 FY27", "$76.44 billion", "$79.56 billion"))
        self.assertEqual(nvda.range_status, "DERIVED_FROM_SOURCE_RANGE")
        self.assertEqual(nvda.source_manifest_sha256,
                         json.loads(source_path.read_text(encoding="utf-8"))["source_manifest_sha256"])
        mu = PublicFactCard.objects.get(candidate_id="8a350fb1e88c5c68f218")
        self.assertEqual((mu.fiscal_period, mu.basis_text, mu.range_lower_text, mu.range_upper_text),
                         ("FQ3-26", "GAAP", "$32.75 billion", "$34.25 billion"))
        fcf = PublicFactCard.objects.get(candidate_id="54e6616f0199ce82f196")
        self.assertEqual((fcf.fact_kind, fcf.fiscal_period, fcf.basis_text),
                         ("ACTUAL", "FQ2-26", "UNKNOWN"))
        self.assertIn("Adjusted", fcf.basis_note)
        self.assertEqual(fcf.source_review_status, "CODEX_REVIEW_SOURCE_SUPPORTED")
        user = get_user_model().objects.create_user(username="qa@example.org", password=PASSWORD)
        self.client.force_login(user)
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(len(response.context["actual_fact_cards"]), 12)
        self.assertEqual(len(response.context["guidance_fact_cards"]), 2)
        self.assertEqual(response.context["missing_free_cash_flow"], ["NVIDIA"])
        self.assertContains(response, "历史训练期材料")
        self.assertContains(response, "计算下界")
        self.assertContains(response, "Codex 来源回读支持")
        self.assertContains(response, "NVIDIA尚无已核对数据")

    def test_ambiguous_guidance_and_cash_flow_excluded_and_bad_range_rejected(self):
        doc = json.loads(self.source_path().read_text(encoding="utf-8"))
        latest = {c["symbol"]: c for c in doc["cards"] if c["filing_date"] in ("2026-02-25", "2026-03-18")}
        nvda_guidance = next(f for f in latest["NVDA"]["reviewed_facts"] if f["topic"] == "guidance")
        bad_range = copy.deepcopy(nvda_guidance)
        bad_range["range"]["lower_text"] = "$0 billion"
        with self.assertRaises(CommandError):
            checked_guidance_range(bad_range)
        nvda_guidance["status"] = "CODEX_REVIEW_AMBIGUOUS"
        mu_fcf = next(f for f in latest["MU"]["reviewed_facts"] if f["topic"] == "free_cash_flow")
        mu_fcf["status"] = "CODEX_REVIEW_AMBIGUOUS"
        selected_ids = {fact["candidate_id"] for _, fact, _ in BootstrapCommand()._selected_facts(doc)}
        self.assertNotIn(nvda_guidance["candidate_id"], selected_ids)
        self.assertNotIn(mu_fcf["candidate_id"], selected_ids)

    @override_settings(PERSONAL_MODE=True, DEBUG=True)
    def test_late_provenance_conflict_rolls_back_entire_import(self):
        source_path = self.source_path()
        doc = json.loads(source_path.read_text(encoding="utf-8"))
        mu = next(c for c in doc["cards"] if c["symbol"] == "MU" and c["filing_date"] == "2026-03-18")
        guidance = next(f for f in mu["reviewed_facts"] if f["topic"] == "guidance")
        PublicFactCard.objects.create(
            candidate_id=guidance["candidate_id"], issuer="Conflict", fiscal_period="FQ3-26",
            metric="Revenue", value_text=guidance["value_text"], source_url=guidance["source_url"],
            source_sha256="0" * 64,
        )
        with TemporaryDirectory() as private_root, override_settings(BASE_DIR=Path(private_root)):
            with self.assertRaises(CommandError):
                call_command("bootstrap_personal", facts_path=str(source_path), stdout=StringIO())
        self.assertEqual(PublicFactCard.objects.count(), 1)
        self.assertEqual(PublicFactCard.objects.get().issuer, "Conflict")

    @override_settings(PERSONAL_MODE=True, DEBUG=True)
    def test_existing_candidate_rejects_input_manifest_mismatch(self):
        source_path = self.source_path()
        with TemporaryDirectory() as private_root, override_settings(BASE_DIR=Path(private_root)):
            call_command("bootstrap_personal", facts_path=str(source_path), stdout=StringIO())
            first = PublicFactCard.objects.order_by("id").first()
            PublicFactCard.objects.filter(pk=first.pk).update(source_manifest_sha256="0" * 64)
            with self.assertRaises(CommandError):
                call_command("bootstrap_personal", facts_path=str(source_path), stdout=StringIO())
        self.assertEqual(PublicFactCard.objects.count(), 14)
        self.assertEqual(PublicFactCard.objects.get(pk=first.pk).source_manifest_sha256, "0" * 64)


class OperationsTests(TestCase):
    def test_health_readiness_and_worker_once(self):
        self.assertEqual(self.client.get(reverse("healthz")).status_code, 200)
        self.assertEqual(self.client.get(reverse("readyz")).status_code, 200)
        call_command("run_worker", once=True, interval=5)
        self.assertTrue(WorkerHeartbeat.objects.filter(name="portal_worker").exists())


class StaticAssetTests(StaticLiveServerTestCase):
    def test_registration_css_is_served_over_http(self):
        with urlopen(self.live_server_url + "/static/core/portal.css", timeout=5) as response:
            body = response.read()
            self.assertEqual(response.status, 200)
            self.assertIn(b".shell", body)
