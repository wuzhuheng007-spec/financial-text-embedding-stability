"""One-person local bootstrap with source-bound historical summaries."""
import hashlib
import json
from datetime import date, timedelta
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from core.invite_files import write_private_invite
from core.models import Invitation, PublicFactCard


EXPECTED_FACTS_SHA256 = "2024676c3ae30a602ed6b75155a736b74ac3dc05121d502668bf7f0efc1f4ee4"
FACT_TOPICS = ("revenue", "net_income", "eps", "gross_margin", "capital_expenditures", "operating_cash_flow")
ISSUER_NAMES = {"NVDA": "NVIDIA", "MU": "Micron Technology"}


class Command(BaseCommand):
    help = "Prepare a local owner invite and reviewed SEC-derived historical fact summaries."

    def add_arguments(self, parser):
        parser.add_argument("--owner-email", default="owner@local.invalid")
        parser.add_argument("--facts-path", default=None)
        parser.add_argument("--no-facts", action="store_true")

    def handle(self, *args, **options):
        if not settings.PERSONAL_MODE:
            raise CommandError("bootstrap_personal requires DEBUG=1 and PERSONAL_MODE=1")
        root = Path(settings.BASE_DIR)
        owner_email = options["owner_email"].strip().lower()
        if not owner_email or len(owner_email) > 150:
            raise CommandError("Invalid owner email")
        invite_path = root / "var" / "personal_invite.txt"
        source_path = (Path(options["facts_path"]) if options["facts_path"] else
                       root.parents[1] / "results" / "market_observer_financial_text_v2_20261115" / "fact_cards_v4_redacted.json")
        facts = []
        if not options["no_facts"]:
            if not source_path.is_file():
                self.stdout.write("Reviewed source summary unavailable; fact cards remain empty.")
            else:
                source_bytes = source_path.read_bytes()
                digest = hashlib.sha256(source_bytes).hexdigest()
                if digest != EXPECTED_FACTS_SHA256:
                    raise CommandError("Fact-card source SHA-256 differs from pinned reviewed file")
                facts = self._selected_facts(json.loads(source_bytes.decode("utf-8")))
        imported = 0
        backfilled = 0
        for card, fact, manifest_sha in facts:
            defaults = {
                "issuer": ISSUER_NAMES[card["symbol"]],
                "fiscal_period": fact["period_text"],
                "metric": fact["metric"],
                "value_text": fact["value_text"],
                "unit_text": fact.get("unit_text") or "",
                "currency": fact.get("currency") or "",
                "basis_text": fact.get("basis") or "",
                "source_url": fact["source_url"],
                "as_of": None,
                "source_date": date.fromisoformat(card["filing_date"]),
                "source_time_precision": "DATE_ONLY",
                "source_event_id": card["event_id"],
                "source_sha256": fact["source_document_sha256"],
                "source_manifest_sha256": manifest_sha,
                "evidence_locator": fact["evidence_locator"],
                "evidence_state": "CODEX_REVIEW_DEVELOPMENT",
                "source_class": PublicFactCard.SourceClass.OFFICIAL,
                "visibility_scope": "LOCAL_ONLY",
                "display_approved": False,
                "rights_basis": "",
            }
            row, created = PublicFactCard.objects.get_or_create(candidate_id=fact["candidate_id"], defaults=defaults)
            imported += int(created)
            if not created:
                if row.source_sha256 != defaults["source_sha256"] or row.source_url != defaults["source_url"]:
                    raise CommandError("Existing candidate provenance differs; no automatic overwrite")
                changed = []
                for field in ("currency", "basis_text"):
                    if not getattr(row, field) and defaults[field]:
                        setattr(row, field, defaults[field])
                        changed.append(field)
                if changed:
                    row.save(update_fields=changed)
                    backfilled += 1
        if not get_user_model().objects.filter(username=owner_email).exists() and not invite_path.exists():
            with transaction.atomic():
                _, code = Invitation.issue(owner_email, timezone.now() + timedelta(days=30))
                write_private_invite(invite_path, code)
        self.stdout.write(
            f"Personal setup ready; newly imported reviewed fact summaries: {imported}; "
            f"provenance-checked basis/currency backfills: {backfilled}."
        )
        self.stdout.write(f"Registration email: {owner_email}")
        self.stdout.write(f"One-time code file (private; never publish): {invite_path}")

    def _selected_facts(self, document):
        if document.get("schema") != "market_observer_financial_fact_cards_redacted_v4":
            raise CommandError("Unexpected fact-card schema")
        cards = {}
        for card in document.get("cards", []):
            if (card.get("symbol") in ISSUER_NAMES and card.get("historical_training_material") is True
                    and "2025-04-01" <= card.get("filing_date", "") <= "2026-03-31"):
                symbol = card["symbol"]
                if symbol not in cards or card["filing_date"] > cards[symbol]["filing_date"]:
                    cards[symbol] = card
        if set(cards) != set(ISSUER_NAMES):
            raise CommandError("Pinned source lacks latest train-period cards for both issuers")
        selected = []
        for symbol in ("NVDA", "MU"):
            card = cards[symbol]
            for topic in FACT_TOPICS:
                fact = next((f for f in card["reviewed_facts"] if f.get("topic") == topic
                             and f.get("status") == "CODEX_REVIEW_SOURCE_SUPPORTED"), None)
                if fact is None:
                    continue
                if (fact.get("source_url") != card["source_url"] or
                        fact.get("source_document_sha256") != card["source_sha256"] or
                        not fact.get("source_url", "").startswith("https://www.sec.gov/Archives/") or
                        not fact.get("candidate_id") or not fact.get("evidence_locator")):
                    raise CommandError("Selected fact lacks matching SEC provenance")
                selected.append((card, fact, document["source_manifest_sha256"]))
        return selected
