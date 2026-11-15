"""Version the six issuer guidance cards without changing frozen earnings output.

The source release clock is separate from independently observed first
availability. A date implied by a fiscal-period label is never treated as an
issuer-confirmed target date. No Jin10 response or financial market data is used.
"""

from __future__ import annotations

import copy
import hashlib
import json
from collections import Counter
from datetime import date, timedelta
from decimal import Decimal
from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INPUT = ROOT / "results/news_earnings_macro_20261115/company_cards.json"
SOURCE_MANIFEST = ROOT / "results/news_earnings_macro_20261115/source_manifest.json"
CALENDAR_CAPTURE = ROOT / "artifacts/jin10_guidance_v2/intel_2025_10k.html"
CALENDAR_RECEIPT = ROOT / "artifacts/jin10_guidance_v2/intel_2025_10k.receipt.json"
OUTPUT_DIR = ROOT / "results/news_jin10_20261115/guidance_v2"
INTEL_Q3_END = date(2026, 9, 26)
INTEL_FY26_END = date(2026, 12, 26)
INTEL_10K_URL = (
    "https://www.intc.com/filings-reports/all-sec-filings/content/"
    "0000050863-26-000011/intc-20251227.htm"
)
KOREA = {
    "SAMSUNG_ELECTRONICS": ("2026-10-29", "Asia/Seoul", "Korea on October 29, 2026"),
    "SK_HYNIX": ("2026-10-28", "Asia/Seoul", "Seoul, October 28, 2026"),
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class _Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def _validate_intel_calendar(receipt: dict) -> None:
    if receipt["requested_url"] != INTEL_10K_URL or sha256(CALENDAR_CAPTURE) != receipt["sha256"]:
        raise ValueError("Intel official fiscal-calendar capture mismatch")
    parser = _Text()
    parser.feed(CALENDAR_CAPTURE.read_text(encoding="utf-8", errors="replace"))
    content = " ".join(" ".join(parser.parts).split())
    if "Fiscal 2026 is a 52-week fiscal year" not in content:
        raise ValueError("Intel fiscal-year basis not located")
    if "last Saturday in December" not in content:
        raise ValueError("Intel fiscal year-end basis not located")
    if INTEL_FY26_END.weekday() != 5 or INTEL_FY26_END - INTEL_Q3_END != timedelta(weeks=13):
        raise ValueError("Intel inferred Q4 dates do not match official fiscal rule")


def normalize_guidance(cards: dict, manifest: dict, calendar_receipt: dict) -> tuple[dict, dict]:
    _validate_intel_calendar(calendar_receipt)
    registered = {
        (row["issuer_id"], row["url"], row["sha256"]): row
        for row in manifest["sources"] if row["issuer_id"] != "MACRO"
    }
    output_cards: list[dict] = []
    changes: list[dict] = []
    for card in cards["cards"]:
        issuer = card["issuer"]
        rows: list[dict] = []
        for ordinal, previous in enumerate(card["guidance"]):
            row = copy.deepcopy(previous)
            row["guidance_id"] = f"{issuer}:guidance:{ordinal + 1:02d}:{row['source_sha256'][:10]}"
            row.setdefault("first_available_at", None)
            source_key = (issuer, row["source_url"], row["source_sha256"])
            source = registered.get(source_key)
            if source is None or not source["captured_local_bytes_verified"]:
                raise ValueError(f"Guidance lacks captured official source: {row['guidance_id']}")
            source_path = (ROOT / source["local_ignored_artifact"]).resolve()
            if not source_path.is_relative_to(ROOT) or sha256(source_path) != row["source_sha256"]:
                raise ValueError(f"Guidance source capture hash mismatch: {row['guidance_id']}")
            source_text = source_path.read_text(encoding="utf-8", errors="replace")
            row["source_retrieved_at_utc"] = source["retrieved_at"]
            row["source_capture_version"] = "CURRENT_CAPTURE_NOT_RELEASE_TIME_SNAPSHOT"
            row["first_available_status"] = "NOT_INDEPENDENTLY_OBSERVED"
            row["published_time_basis"] = "ISSUER_STATED_RELEASE_DATE_OR_CLOCK"
            row["published_timezone"] = None
            row["target_period_date_status"] = (
                "DATES_UNKNOWN" if row.get("target_period_start") is None
                else "RETAINED_V1_BOUNDS_NOT_INDEPENDENTLY_RECHECKED"
            )
            row["target_period_date_basis"] = None
            if issuer == "INTEL":
                if "Released Oct 29, 2026" not in source_text or "4:01 PM EDT" not in source_text:
                    raise ValueError("Intel stated release clock not present in verified capture")
                if row.get("first_available_at") is not None:
                    changes.append({"guidance_id": row["guidance_id"], "field": "first_available_at",
                                    "old_status": "ISSUER_CLOCK_MISCLASSIFIED", "new_value": None,
                                    "reason": "Issuer-stated release clock is not an independently observed first-available time"})
                row["first_available_at"] = None
                row["published_timezone"] = "America/New_York"
                row["published_time_basis"] = "INTEL_IR_RELEASED_OCT_29_2026_4_01_PM_EDT"
                if row["target_period_start"] != "2026-09-27" or row["target_period_end"] != "2026-12-26":
                    raise ValueError("Intel Q4 date bounds differ from reviewed inference")
                row["target_period_date_status"] = "INFERRED_FROM_OFFICIAL_FISCAL_CALENDAR_NOT_GUIDANCE_RELEASE"
                row["target_period_date_basis"] = {
                    "official_guidance_period_label": "Q4 2026",
                    "formula": "Q3 quarter-end 2026-09-26 + 1 day through FY2026 last Saturday 2026-12-26 (13 weeks)",
                    "input_sources": [
                        {"url": row["source_url"], "sha256": row["source_sha256"],
                         "locator": "Business Outlook, Q4 2026 table and Q3 2026 quarter-end"},
                        {"url": INTEL_10K_URL, "sha256": calendar_receipt["sha256"],
                         "locator": "2025 Form 10-K Note 1 Basis of Presentation: fiscal 2026 is a 52-week year ending last Saturday in December"},
                    ],
                    "not_official_exact_target_dates": True,
                }
                if row["metric"] == "gross_margin" and row["currency"] == "USD":
                    row["currency"] = "NONE"
                    changes.append({"guidance_id": row["guidance_id"], "field": "currency",
                                    "old_value": "USD", "new_value": "NONE",
                                    "reason": "Percent margin is dimensionless"})
            elif issuer in KOREA:
                published, zone, header = KOREA[issuer]
                day = "29" if issuer == "SAMSUNG_ELECTRONICS" else "28"
                if f"October {day}, 2026" not in source_text:
                    raise ValueError(f"Korean issuer date not present in verified capture: {issuer}")
                if row.get("published_at") != published or row.get("timestamp_precision") != "DATE_ONLY":
                    changes.append({"guidance_id": row["guidance_id"], "field": "published_at/timestamp_precision",
                                    "old_status": "UNKNOWN", "new_value": f"{published} DATE_ONLY",
                                    "reason": f"Official page date header: {header}"})
                row["published_at"] = published
                row["timestamp_precision"] = "DATE_ONLY"
                row["published_timezone"] = zone
                row["published_time_basis"] = f"ISSUER_PAGE_DATE_HEADER:{header}"
            rows.append(row)
        output_cards.append({"issuer_id": issuer, "report_period": card["report_period"], "guidance": rows})
    product = {
        "schema": "issuer_guidance_time_and_basis_v2",
        "source_cutoff_utc": cards["as_of_utc"],
        "input_company_cards_sha256": sha256(INPUT),
        "input_source_manifest_sha256": sha256(SOURCE_MANIFEST),
        "intel_calendar_source_url": INTEL_10K_URL,
        "intel_calendar_capture_sha256": calendar_receipt["sha256"],
        "intel_calendar_capture_retrieved_at_utc": calendar_receipt["retrieved_at_utc"],
        "original_72_core_metrics_changed": False,
        "cards": output_cards,
    }
    validate_product(product, registered)
    counts = Counter()
    for card in output_cards:
        for row in card["guidance"]:
            counts["guidance_rows"] += 1
            counts[f"issuer_{card['issuer_id']}"] += 1
            counts["published_date_or_clock_known"] += row["published_at"] is not None
            counts["first_available_independent"] += row["first_available_at"] is not None
            counts["target_bounds_present"] += row.get("target_period_start") is not None
            counts["target_bounds_intel_inferred"] += row["target_period_date_status"].startswith("INFERRED_FROM_OFFICIAL")
            counts["target_bounds_unknown"] += row["target_period_date_status"] == "DATES_UNKNOWN"
            counts["numeric_guidance"] += any(row.get(key) is not None for key in ("low", "high", "point_estimate"))
            counts["qualitative_only"] += not any(row.get(key) is not None for key in ("low", "high", "point_estimate"))
    coverage = {
        "schema": "guidance_v2_coverage_receipt", "source_cutoff_utc": cards["as_of_utc"],
        "counts": dict(sorted(counts.items())), "changes": changes,
        "core_metric_check_count_reference_only": 72,
        "guidance_counts_are_separate_from_core_metrics": True,
        "source_capture_note": "Official current captures, not historical first-availability snapshots",
    }
    return product, coverage


def validate_product(product: dict, registered: dict) -> None:
    ids: set[str] = set()
    for card in product["cards"]:
        for row in card["guidance"]:
            guid = row["guidance_id"]
            if guid in ids:
                raise ValueError("Duplicate guidance ID")
            ids.add(guid)
            if not row.get("source_url", "").startswith("https://") or not row.get("source_locator"):
                raise ValueError(f"Missing source reference: {guid}")
            if (card["issuer_id"], row["source_url"], row["source_sha256"]) not in registered:
                raise ValueError(f"Unregistered source reference: {guid}")
            if not row.get("unit") or not row.get("currency") or not row.get("basis"):
                raise ValueError(f"Missing unit/currency/basis: {guid}")
            if row["unit"] in ("percent", "narrative") and row["currency"] != "NONE":
                raise ValueError(f"Dimensionless metric has currency: {guid}")
            if row["first_available_at"] is not None or row["first_available_status"] != "NOT_INDEPENDENTLY_OBSERVED":
                raise ValueError(f"First availability is unsupported: {guid}")
            if row.get("published_at") is None or not row.get("timestamp_precision"):
                raise ValueError(f"Publication date or precision unknown: {guid}")
            start, end = row.get("target_period_start"), row.get("target_period_end")
            if (start is None) != (end is None):
                raise ValueError(f"Partial target date range: {guid}")
            if start is not None and date.fromisoformat(start) > date.fromisoformat(end):
                raise ValueError(f"Inverted target period: {guid}")
            if start is None and row["target_period_date_status"] != "DATES_UNKNOWN":
                raise ValueError(f"Unmarked target date gap: {guid}")
            values = [row.get(key) for key in ("low", "high", "point_estimate")]
            numeric = [Decimal(str(value)) for value in values if value is not None]
            if any(not value.is_finite() for value in numeric):
                raise ValueError(f"Nonfinite guidance number: {guid}")
            if row.get("low") is not None and row.get("high") is not None:
                low, high = Decimal(str(row["low"])), Decimal(str(row["high"]))
                if low > high:
                    raise ValueError(f"Inverted guidance range: {guid}")
                point = row.get("point_estimate")
                if point is not None and not low <= Decimal(str(point)) <= high:
                    raise ValueError(f"Point estimate outside guidance range: {guid}")
            if row.get("formula") and not row.get("input_row_ids"):
                raise ValueError(f"Formula without input IDs: {guid}")
            if card["issuer_id"] == "INTEL" and row["target_period_date_status"] != "INFERRED_FROM_OFFICIAL_FISCAL_CALENDAR_NOT_GUIDANCE_RELEASE":
                raise ValueError(f"Intel period inferred but not marked: {guid}")


def main() -> None:
    targets = (OUTPUT_DIR / "guidance_cards_v2.json", OUTPUT_DIR / "guidance_coverage_v2.json")
    if any(path.exists() for path in targets):
        raise FileExistsError("Preserve existing v2 outputs")
    cards = _read(INPUT)
    manifest = _read(SOURCE_MANIFEST)
    receipt = _read(CALENDAR_RECEIPT)
    product, coverage = normalize_guidance(cards, manifest, receipt)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for path, payload in zip(targets, (product, coverage), strict=True):
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({"guidance_rows": coverage["counts"]["guidance_rows"],
                      "published_known": coverage["counts"]["published_date_or_clock_known"],
                      "target_bounds_unknown": coverage["counts"]["target_bounds_unknown"],
                      "first_available_independent": coverage["counts"]["first_available_independent"]}))


if __name__ == "__main__":
    main()
