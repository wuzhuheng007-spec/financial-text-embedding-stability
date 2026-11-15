"""One-person local bootstrap with source-bound historical summaries."""
import hashlib
import json
import re
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from core.invite_files import write_private_invite
from core.models import Invitation, PublicFactCard


EXPECTED_FACTS_SHA256 = "2024676c3ae30a602ed6b75155a736b74ac3dc05121d502668bf7f0efc1f4ee4"
FACT_TOPICS = (
    "revenue", "net_income", "eps", "gross_margin", "capital_expenditures",
    "operating_cash_flow", "free_cash_flow", "guidance",
)
ISSUER_NAMES = {"NVDA": "NVIDIA", "MU": "Micron Technology"}


def checked_guidance_range(fact):
    """Accept only the source-bound percentage or dollar tolerance in the pinned summary."""
    amount = fact["value_text"]
    percent_match = re.fullmatch(r"\$(\d+(?:\.\d+)?) billion, plus or minus (\d+(?:\.\d+)?)%", amount)
    absolute_match = re.fullmatch(r"\$(\d+(?:\.\d+)?) billion ± \$(\d+(?:\.\d+)?) million", amount)
    if percent_match:
        center, pct = (Decimal(v) for v in percent_match.groups())
        delta = center * pct / Decimal(100)
        derivation = "central_billion * (1 +/- percentage/100)"
    elif absolute_match:
        center, delta_million = (Decimal(v) for v in absolute_match.groups())
        delta = delta_million / Decimal(1000)
        derivation = "central_billion +/- delta_million/1000"
    else:
        raise CommandError("Guidance range expression is outside the frozen supported formats")
    source_range = fact.get("range") or {}
    lower_text = source_range.get("lower_text", "")
    upper_text = source_range.get("upper_text", "")
    lower_match = re.fullmatch(r"\$(\d+(?:\.\d+)?) billion", lower_text)
    upper_match = re.fullmatch(r"\$(\d+(?:\.\d+)?) billion", upper_text)
    if (
        source_range.get("status") != "DERIVED_FROM_SOURCE_RANGE"
        or source_range.get("input_candidate_id") != fact["candidate_id"]
        or source_range.get("input_value_text") != amount
        or source_range.get("derivation") != derivation
        or not lower_match or not upper_match
        or Decimal(lower_match.group(1)) != center - delta
        or Decimal(upper_match.group(1)) != center + delta
    ):
        raise CommandError("Guidance bounds do not reproduce from the source expression")
    return source_range


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
        with transaction.atomic():
            for card, fact, manifest_sha in facts:
                source_range = checked_guidance_range(fact) if fact["topic"] == "guidance" else {}
                defaults = {
                    "issuer": ISSUER_NAMES[card["symbol"]],
                    "fiscal_period": fact["period_text"],
                    "fact_kind": (PublicFactCard.FactKind.GUIDANCE if fact["topic"] == "guidance"
                                  else PublicFactCard.FactKind.ACTUAL),
                    "fact_topic": fact["topic"],
                    "metric": fact["metric"],
                    "value_text": fact["value_text"],
                    "range_lower_text": source_range.get("lower_text", ""),
                    "range_upper_text": source_range.get("upper_text", ""),
                    "range_derivation": source_range.get("derivation", ""),
                    "range_status": source_range.get("status", ""),
                    "unit_text": fact.get("unit_text") or "",
                    "currency": fact.get("currency") or "",
                    "basis_text": fact.get("basis") or "",
                    "basis_note": fact.get("basis_note") or "",
                    "source_url": fact["source_url"],
                    "as_of": None,
                    "source_date": date.fromisoformat(card["filing_date"]),
                    "source_time_precision": "DATE_ONLY",
                    "source_event_id": card["event_id"],
                    "source_sha256": fact["source_document_sha256"],
                    "source_manifest_sha256": manifest_sha,
                    "evidence_locator": fact["evidence_locator"],
                    "evidence_state": "CODEX_REVIEW_DEVELOPMENT",
                    "source_review_status": fact["status"],
                    "source_class": PublicFactCard.SourceClass.OFFICIAL,
                    "visibility_scope": "LOCAL_ONLY",
                    "display_approved": False,
                    "rights_basis": "",
                }
                row, created = PublicFactCard.objects.get_or_create(candidate_id=fact["candidate_id"], defaults=defaults)
                imported += int(created)
                if not created:
                    if (row.source_sha256 != defaults["source_sha256"]
                            or row.source_manifest_sha256 != defaults["source_manifest_sha256"]
                            or row.source_url != defaults["source_url"]):
                        raise CommandError("Existing candidate provenance differs; no automatic overwrite")
                    for field in (
                        "issuer", "fiscal_period", "fact_kind", "metric", "value_text", "unit_text",
                        "source_date", "source_time_precision", "source_event_id", "evidence_locator",
                        "evidence_state", "source_class", "visibility_scope", "display_approved",
                    ):
                        if getattr(row, field) != defaults[field]:
                            raise CommandError(f"Existing candidate {field} differs; no automatic overwrite")
                    changed = []
                    for field in (
                        "currency", "basis_text", "basis_note", "fact_topic", "source_review_status",
                        "range_lower_text", "range_upper_text", "range_derivation", "range_status",
                    ):
                        if getattr(row, field) and getattr(row, field) != defaults[field]:
                            raise CommandError(f"Existing candidate {field} differs; no automatic overwrite")
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
                if fact["topic"] == "guidance":
                    if (fact.get("category") != "GUIDANCE" or not fact.get("period_text")
                            or not fact.get("metric_normalized", "").endswith("_guidance")):
                        raise CommandError("Selected guidance lacks a supported category, metric or period")
                    checked_guidance_range(fact)
                if fact["topic"] == "free_cash_flow" and fact.get("category") != "CASH_FLOW":
                    raise CommandError("Selected free cash flow lacks the supported actual category")
                selected.append((card, fact, document["source_manifest_sha256"]))
        return selected
