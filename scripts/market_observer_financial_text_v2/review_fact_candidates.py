"""Source-check train-only financial fact candidates without fitting a model.

This is a CODEX_REVIEW by the same execution team, not independent human gold.
The candidate file is immutable; corrections and source quotes go to a separate
private review file. Public output contains only short fact values and locators.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from bs4 import BeautifulSoup, Tag

from .freeze_content_checklist import MANIFEST, OUT, dom_path, visible


CANDIDATES = OUT / "fact_candidates_v2.private.json"
REVIEWED_CHECKLIST = OUT / "content_checklist_reviewed.private.json"
PRIVATE_REVIEW = OUT / "fact_review.private.json"
PUBLIC = Path(__file__).resolve().parents[2] / "results/market_observer_financial_text_v2_20261115"
PUBLIC_CARDS = PUBLIC / "fact_cards_redacted.json"
PUBLIC_SUMMARY = PUBLIC / "fact_review_summary_redacted.json"
FISCAL_WORD = {"first": 1, "second": 2, "third": 3, "fourth": 4}
CATEGORIES = {"revenue": "PERFORMANCE", "net_income": "PERFORMANCE", "eps": "PERFORMANCE",
              "gross_margin": "MARGIN", "guidance": "GUIDANCE", "capital_expenditures": "CAPEX",
              "operating_cash_flow": "CASH_FLOW", "free_cash_flow": "CASH_FLOW",
              "segment_revenue": "SEGMENT"}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def resolve(soup: BeautifulSoup, path: str) -> Tag:
    current: Tag = soup
    for segment in path.split("/"):
        match = re.fullmatch(r"([\w:-]+)\[(\d+)\]", segment)
        if not match:
            raise RuntimeError("invalid DOM locator syntax")
        children = current.find_all(match.group(1), recursive=False)
        index = int(match.group(2)) - 1
        if not 0 <= index < len(children):
            raise RuntimeError("DOM locator not found in original")
        current = children[index]
    return current


def short_evidence(tag: Tag) -> dict:
    text = visible(tag)
    return {"dom_path": dom_path(tag), "rendered_visible_text": text[:700],
            "text_sha256": sha(text.encode("utf-8")), "quote_truncated": len(text) > 700}


def find_narrative(soup: BeautifulSoup, expression: str) -> Tag | None:
    for node in soup.find_all(string=re.compile(expression, re.I)):
        tag = node.parent
        while isinstance(tag, Tag) and tag.name not in {"div", "p", "li", "tr"}:
            tag = tag.parent
        if isinstance(tag, Tag) and re.search(expression, visible(tag), re.I):
            return tag
    return None


def source_headings(candidate: dict) -> str:
    return " ".join(h["rendered_visible_text"] for e in candidate["source_evidence"]
                    for h in e.get("table_headers", []))


def source_fiscal_period(candidate: dict) -> str | None:
    for e in candidate["source_evidence"]:
        text = e["rendered_visible_text"]
        match = re.search(r"\b(FQ[1-4]-\d{2}|Q[1-4]\s+FY\d{2,4})\b", text, re.I)
        if match:
            return match.group()
        for h in e.get("table_headers", []):
            match = re.search(r"\b(FQ[1-4]-\d{2}|Q[1-4]\s+FY\d{2,4})\b", h["rendered_visible_text"], re.I)
            if match:
                return match.group()
    return None


def nvidia_guidance_period(candidate: dict) -> str | None:
    if len(candidate["source_evidence"]) < 2:
        return None
    heading = candidate["source_evidence"][0]["rendered_visible_text"]
    match = re.search(r"outlook for the (first|second|third|fourth) quarter of fiscal (20\d{2})", heading, re.I)
    return (f"Q{FISCAL_WORD[match.group(1).lower()]} FY{match.group(2)[-2:]}" if match else None)


def nvidia_report_period(soup: BeautifulSoup) -> tuple[str | None, dict | None]:
    tag = find_narrative(soup, r"Financial Results for (?:First|Second|Third|Fourth) Quarter Fiscal 20\d{2}")
    if not tag:
        return None, None
    match = re.search(r"Financial Results for (First|Second|Third|Fourth) Quarter Fiscal (20\d{2})", visible(tag), re.I)
    if not match:
        return None, None
    return f"Q{FISCAL_WORD[match.group(1).lower()]} FY{match.group(2)[-2:]}", short_evidence(tag)


def nvidia_period_end(soup: BeautifulSoup) -> tuple[str | None, dict | None]:
    tag = find_narrative(soup, r"(?:first|second|third|fourth) quarter ended\s+[A-Za-z]+\s+\d{1,2},\s+20\d{2}")
    if not tag:
        return None, None
    match = re.search(r"quarter ended\s+([A-Za-z]+\s+\d{1,2},\s+20\d{2})", visible(tag), re.I)
    if not match:
        return None, None
    return datetime.strptime(match.group(1), "%B %d, %Y").date().isoformat(), short_evidence(tag)


def micron_period_end(soup: BeautifulSoup) -> tuple[str | None, dict | None]:
    tag = find_narrative(soup, r"quarter(?: and full year)? of fiscal 20\d{2}, which ended\s+[A-Za-z]+\s+\d{1,2},\s+20\d{2}")
    if not tag:
        return None, None
    match = re.search(r"which ended\s+([A-Za-z]+\s+\d{1,2},\s+20\d{2})", visible(tag), re.I)
    if not match:
        return None, None
    return datetime.strptime(match.group(1), "%B %d, %Y").date().isoformat(), short_evidence(tag)


def cell_spans(row: Tag) -> list[tuple[Tag, int, int]]:
    position = 0
    result = []
    for cell in row.find_all(["td", "th"], recursive=False):
        width = int(cell.get("colspan") or 1)
        result.append((cell, position, position + width))
        position += width
    return result


def first_measure_cell(row: Tag) -> tuple[Tag, int, int] | None:
    cells = cell_spans(row)
    for cell, start, end in cells[1:]:
        if re.search(r"(?<![A-Za-z])\d[\d,.]*", visible(cell)):
            return cell, start, end
    return None


def overlapping_header(table: Tag, before: Tag, expression: str) -> tuple[Tag, Tag] | None:
    target = first_measure_cell(before)
    if target is None:
        return None
    column = target[1]
    rows = table.find_all("tr")
    for row in rows[:rows.index(before)]:
        for cell, start, end in cell_spans(row):
            if start <= column < end and re.search(expression, visible(cell), re.I):
                return cell, target[0]
    return None


def verify_candidate_source(candidate: dict, soup: BeautifulSoup) -> tuple[Tag, list[str]]:
    errors = []
    tag = resolve(soup, candidate["evidence_locator"])
    own = visible(tag)
    exact = candidate["value_text"] or ""
    if re.sub(r"\s+", " ", exact) not in re.sub(r"\s+", " ", own):
        errors.append("VALUE_NOT_IN_EXACT_LOCATOR")
    if not any(e["dom_path"] == candidate["evidence_locator"] and
               e["element_visible_text_sha256"] == sha(own.encode("utf-8"))
               for e in candidate["source_evidence"]):
        errors.append("LOCATOR_HASH_MISMATCH")
    return tag, errors


def explicit_unit(candidate: dict) -> str | None:
    value = candidate["value_text"] or ""
    if "billion" in value.lower():
        return "billion"
    if "million" in value.lower():
        return "million"
    if candidate["topic"] == "gross_margin" and "%" in value:
        return "percent"
    if candidate["topic"] == "eps" and "per share" in candidate["metric_original"].lower():
        return "per_share"
    headings = source_headings(candidate)
    if re.search(r"\b(?:in millions|\$ in millions)\b", headings, re.I):
        return "million"
    if candidate["topic"] in {"revenue", "net_income"} and re.search(r"\bin millions\b", headings, re.I):
        return "million"
    return None


def basis_from_exact_table(candidate: dict, value_tag: Tag) -> tuple[str | None, list[dict]]:
    topic = candidate["topic"]
    if topic not in {"revenue", "net_income", "eps", "gross_margin", "guidance"}:
        return None, []
    if topic == "guidance" and candidate["symbol"] == "NVDA":
        return None, []
    table = value_tag.find_parent("table")
    if table is None:
        return None, []
    rows = table.find_all("tr")
    pos = rows.index(value_tag)
    header = " ".join(visible(r) for r in rows[:pos])
    if re.search(r"\bGAAP\b", header, re.I) and not re.search(r"\bnon-GAAP\b", header, re.I):
        pair = overlapping_header(table, value_tag, r"^GAAP\b")
        return ("GAAP", [short_evidence(pair[0]), short_evidence(pair[1])]) if pair else (None, [])
    if re.search(r"\bGAAP\b.*\bnon-GAAP\b", header, re.I):
        pair = overlapping_header(table, value_tag, r"^GAAP\b")
        if pair:
            return "GAAP_FIRST_EXPLICIT_COLUMN", [short_evidence(pair[0]), short_evidence(pair[1])]
    return None, []


def micron_narrative_correction(candidate: dict, soup: BeautifulSoup) -> tuple[str | None, str | None, Tag | None]:
    topic = candidate["topic"]
    if topic == "operating_cash_flow":
        tag = find_narrative(soup, r"Operating cash flow of\s+\$\d")
        if tag:
            m = re.search(r"Operating cash flow of\s+(\$\d[\d,.]*\s+billion)", visible(tag), re.I)
            if m:
                return m.group(1), "billion", tag
    if topic == "capital_expenditures":
        tag = find_narrative(soup, r"investments in capital expenditures, net(?:\s*\(\d+\))?\s+were\s+\$\d")
        if tag:
            m = re.search(r"investments in capital expenditures, net(?:\s*\(\d+\))?\s+were\s+(\$\d[\d,.]*\s+(?:billion|million))", visible(tag), re.I)
            if m:
                return m.group(1), m.group(1).split()[-1].lower(), tag
    if topic == "free_cash_flow":
        tag = find_narrative(soup, r"adjusted free cash flow(?:\s*\(\d+\))?\s+was\s+\$\d")
        if tag:
            m = re.search(r"adjusted free cash flow(?:\s*\(\d+\))?\s+was\s+(\$\d[\d,.]*\s+(?:billion|million))", visible(tag), re.I)
            if m:
                return m.group(1), m.group(1).split()[-1].lower(), tag
    return None, None, None


def micron_narrative_period(tag: Tag) -> str | None:
    match = re.search(r"\b(?:for|in)\s+(?:the\s+)?(first|second|third|fourth) quarter of (20\d{2})\b", visible(tag), re.I)
    return f"FQ{FISCAL_WORD[match.group(1).lower()]}-{match.group(2)[-2:]}" if match else None


def micron_quarterly_ocf_period(candidate: dict, soup: BeautifulSoup,
                               narrative_value: str | None) -> tuple[str | None, list[dict]]:
    """Bind corrected OCF to a separate quarterly row and its date headers."""
    expected = candidate.get("period_hint_from_revenue_topic")
    m = re.fullmatch(r"FQ([1-4])-(\d{2})", expected or "", re.I)
    if not m:
        return None, []
    n = re.fullmatch(r"\$(\d[\d,.]*)\s+billion", narrative_value or "", re.I)
    if not n:
        return None, []
    report_billion = Decimal(n.group(1).replace(",", ""))
    decimals = len(n.group(1).split(".")[-1]) if "." in n.group(1) else 0
    quantum = Decimal(1).scaleb(-decimals)
    for row in soup.find_all("tr"):
        if not re.match(r"^GAAP net cash provided by operating activities\b", visible(row), re.I):
            continue
        table = row.find_parent("table")
        if table is None:
            continue
        prior = table.find_all("tr")[:table.find_all("tr").index(row)]
        first = " ".join(visible(x) for x in prior[:4])
        ordinal = {"1": "1st", "2": "2nd", "3": "3rd", "4": "4th"}[m.group(1)]
        if re.search(r"\b" + ordinal + r"\s+Qtr\.", first, re.I):
            measure = first_measure_cell(row)
            if measure is None:
                continue
            cell_number = re.search(r"\d[\d,]*(?:\.\d+)?", visible(measure[0]))
            if cell_number is None:
                continue
            exact_billion = Decimal(cell_number.group().replace(",", "")) / Decimal(1000)
            if exact_billion.quantize(quantum, rounding=ROUND_HALF_UP) != report_billion:
                continue
            evidence = [short_evidence(x) for x in prior[:4] if visible(x)]
            evidence.extend([short_evidence(row), short_evidence(measure[0])])
            return expected, evidence
    return None, []


def review_one(candidate: dict, soup: BeautifulSoup, source: dict) -> dict:
    tag, errors = verify_candidate_source(candidate, soup)
    topic, symbol = candidate["topic"], candidate["symbol"]
    final = {"metric_normalized": candidate["metric_normalized"],
             "metric_original": candidate["metric_original"],
             "value_text": candidate["value_text"],
             "unit_text": explicit_unit(candidate),
             "currency": "USD" if candidate["value_text"] and "$" in candidate["value_text"] else None,
             "period_text": source_fiscal_period(candidate),
             "period_start": None, "period_end": None,
             "basis": None,
             "fact_kind": candidate["fact_kind"],
             "range": ({**candidate["range"], "input_candidate_id": candidate["candidate_id"],
                        "input_value_text": candidate["value_text"]} if candidate["range"] else None),
             "source_url": candidate["source_url"],
             "source_document_sha256": candidate["source_document_sha256"],
             "evidence_locator": dom_path(tag)}
    support = {"metric": [short_evidence(tag)], "value": [short_evidence(tag)],
               "period": [], "unit": [], "basis": []}
    final["basis"], support["basis"] = basis_from_exact_table(candidate, tag)
    corrections = []
    unknowns = []
    if final["period_text"]:
        support["period"] = [{"dom_path": h["dom_path"], "rendered_visible_text": h["rendered_visible_text"]}
                             for e in candidate["source_evidence"] for h in e.get("table_headers", [])
                             if final["period_text"] in h["rendered_visible_text"]]
        support["period"] += [short_evidence(tag)] if final["period_text"] in visible(tag) else []
    if symbol == "NVDA":
        end_date, end_evidence = nvidia_period_end(soup)
        title_period, title_evidence = nvidia_report_period(soup)
        if end_date and topic != "guidance":
            final["period_end"] = end_date
            support["period"].append(end_evidence)
        if topic == "segment_revenue" and title_period and title_evidence:
            final["period_text"] = title_period
            support["period"].append(title_evidence)
            corrections.append("Segment fiscal period linked to same release title; original numeric segment line retained.")
        if topic == "guidance":
            p = nvidia_guidance_period(candidate)
            if p:
                final["period_text"] = p
                support["period"] = [candidate["source_evidence"][0]]
                corrections.append("NVDA narrative fiscal period recovered; candidate null preserved")
        if topic == "operating_cash_flow" and final["period_end"] and final["unit_text"]:
            table = tag.find_parent("table")
            pair = overlapping_header(table, tag, r"^Three Months Ended$") if table else None
            if pair:
                final["period_text"] = "Three Months Ended " + final["period_end"]
                support["period"].extend([short_evidence(pair[0]), short_evidence(pair[1])])
            else:
                final["period_text"] = None
                unknowns.append("First OCF value cell could not be tied to the three-month header.")
        if topic == "capital_expenditures":
            final["metric_normalized"] = "purchases_property_equipment_and_intangibles"
            final["basis"] = None
            unknowns.append("Combined PPE and intangible purchases cannot be a pure CapEx amount.")
        if topic == "free_cash_flow":
            unknowns.append("This reconciliation row lacks a complete in-table period/unit mapping; kept as candidate-level value.")
    if symbol == "MU":
        end_date, end_evidence = micron_period_end(soup)
        if end_date and topic != "guidance":
            final["period_end"] = end_date
            support["period"].append(end_evidence)
    if symbol == "MU" and topic in {"operating_cash_flow", "capital_expenditures", "free_cash_flow"}:
        corrected_value, corrected_unit, narrative = micron_narrative_correction(candidate, soup)
        if narrative is not None:
            if corrected_value != final["value_text"]:
                corrections.append("Replaced table candidate with source narrative value; original candidate retained separately."
                                   if topic != "operating_cash_flow" else
                                   "Original first cash-flow statement value was cumulative/annual, not current quarter; source narrative corrected value.")
            final["value_text"] = corrected_value
            final["unit_text"] = corrected_unit
            final["currency"] = "USD"
            final["evidence_locator"] = dom_path(narrative)
            support["value"] = [short_evidence(narrative)]
            support["metric"] = [short_evidence(narrative)]
            support["unit"] = [short_evidence(narrative)]
            if topic in {"capital_expenditures", "free_cash_flow"}:
                narrative_period = micron_narrative_period(narrative)
                if narrative_period:
                    final["period_text"] = narrative_period
                    support["period"] = [short_evidence(narrative)]
                if topic == "free_cash_flow":
                    final["basis"] = "ADJUSTED_SOURCE_LABEL"
                    support["basis"] = [short_evidence(narrative)]
            if topic == "operating_cash_flow":
                final["basis"] = None
                quarterly_period, quarterly_evidence = micron_quarterly_ocf_period(candidate, soup, corrected_value)
                final["period_text"] = quarterly_period
                support["period"] = quarterly_evidence
                if quarterly_period:
                    corrections.append("Quarterly period tied to separate same-release GAAP cash-flow row/header, not inherited from cumulative cash-flow row.")
                else:
                    unknowns.append("Quarterly period could not be linked to a source header; candidate period remains unresolved.")
        else:
            unknowns.append("Current-quarter narrative value not recovered; original table candidate is not promoted.")
    if topic in {"revenue", "net_income", "eps", "gross_margin"}:
        final["unit_text"] = explicit_unit(candidate)
    if topic == "segment_revenue" and symbol == "MU":
        unknowns.append("Business-unit table does not explicitly state amount scale in selected source context; numeric row remains ambiguous.")
    if topic == "guidance":
        final["fact_kind"] = "FUTURE_GUIDANCE"
        if final["range"] is None:
            unknowns.append("Guidance bounds not derived from explicit source range.")
        elif final["range"]["status"] != "DERIVED_FROM_SOURCE_RANGE":
            errors.append("INVALID_RANGE_STATUS")
    if final["unit_text"]:
        support["unit"] += [short_evidence(tag)] if re.search(r"\b(?:billion|million)\b|%", visible(tag), re.I) else []
        support["unit"] += [{"dom_path": h["dom_path"], "rendered_visible_text": h["rendered_visible_text"]}
                            for e in candidate["source_evidence"] for h in e.get("table_headers", [])
                            if re.search(r"\b(?:millions|per share)\b", h["rendered_visible_text"], re.I)]
    if final["period_text"] is None:
        unknowns.append("Fiscal or cumulative period not established for this value in its own source context.")
    if final["unit_text"] is None:
        unknowns.append("Amount unit/scale not established in source context.")
    if errors:
        unknowns += errors
    if final["period_text"] and not support["period"]:
        unknowns.append("Period text lacks an explicit source support locator.")
    if final["unit_text"] and not support["unit"]:
        unknowns.append("Unit text lacks an explicit source support locator.")
    if final["basis"] and not support["basis"]:
        unknowns.append("Accounting basis lacks an exact header/value-column witness.")
    if not support["value"] or not support["metric"]:
        unknowns.append("Metric/value lack a source locator.")
    supported = not unknowns and final["value_text"] is not None and final["period_text"] is not None and final["unit_text"] is not None
    status = "CODEX_REVIEW_SOURCE_SUPPORTED" if supported else "CODEX_REVIEW_AMBIGUOUS"
    return {"candidate_id": candidate["candidate_id"], "event_id": candidate["event_id"],
            "symbol": symbol, "filing_date": candidate["filing_date"], "topic": topic,
            "category": CATEGORIES[topic], "status": status, "final": final,
            "field_evidence": support, "corrections": corrections, "unknowns": unknowns,
            "review_role": "CODEX_REVIEW_NOT_INDEPENDENT_HUMAN_GOLD"}


def main() -> None:
    if PRIVATE_REVIEW.exists() or PUBLIC_CARDS.exists() or PUBLIC_SUMMARY.exists():
        raise FileExistsError("refusing to overwrite existing fact review/public cards")
    candidate_bytes = CANDIDATES.read_bytes()
    checklist_bytes = REVIEWED_CHECKLIST.read_bytes()
    payload = json.loads(candidate_bytes)
    if payload["reviewed_checklist_sha256"] != sha(checklist_bytes):
        raise RuntimeError("candidate/checklist version mismatch")
    manifest_bytes = MANIFEST.read_bytes()
    docs = json.loads(manifest_bytes)["documents"]
    train = {d["document_id"]: d for d in docs if d.get("source_kind") == "ISSUER_EARNINGS"
             and d.get("symbol") in {"NVDA", "MU"}
             and "2025-04-01" <= d.get("filing_date", "") <= "2026-03-31"}
    if len(train) != 8:
        raise RuntimeError("unexpected train exhibit count")
    soups = {}
    for doc_id, doc in train.items():
        raw = Path(doc["raw_body_path"]).read_bytes()
        if sha(raw) != doc["raw_body_sha256"]:
            raise RuntimeError("source changed: " + doc_id)
        soups[doc_id] = BeautifulSoup(raw, "html.parser")
    reviews = []
    for c in payload["candidates"]:
        doc = train[c["source_document_id"]]
        if c["source_document_sha256"] != doc["raw_body_sha256"]:
            raise RuntimeError("candidate source hash mismatch")
        reviews.append(review_one(c, soups[c["source_document_id"]], doc))
    if len(reviews) != len(payload["candidates"]):
        raise RuntimeError("some candidates not reviewed")
    private = {"schema": "train_earnings_fact_review_v1", "role": "CODEX_REVIEW_NOT_INDEPENDENT_HUMAN_GOLD",
               "candidate_sha256": sha(candidate_bytes), "reviewed_checklist_sha256": sha(checklist_bytes),
               "source_manifest_sha256": sha(manifest_bytes), "reviews": reviews}
    PRIVATE_REVIEW.write_text(json.dumps(private, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    cards = []
    for doc in sorted(train.values(), key=lambda d: (d["filing_date"], d["symbol"])):
        current = [r for r in reviews if r["event_id"] == doc["event_id"]]
        known = [r for r in current if r["status"] == "CODEX_REVIEW_SOURCE_SUPPORTED"]
        ambiguous = [r for r in current if r["status"] != "CODEX_REVIEW_SOURCE_SUPPORTED"]
        period = next((r["final"]["period_text"] for r in current if r["topic"] == "revenue"), None)
        cards.append({"event_id": doc["event_id"], "symbol": doc["symbol"],
                      "filing_date": doc["filing_date"], "financial_period": period,
                      "historical_training_material": True,
                      "source_url": doc["source_url"], "source_sha256": doc["raw_body_sha256"],
                      "reviewed_facts": [{"candidate_id": r["candidate_id"], "topic": r["topic"],
                                          "category": r["category"], "metric": r["final"]["metric_original"],
                                          "metric_normalized": r["final"]["metric_normalized"],
                                          "value_text": r["final"]["value_text"],
                                          "unit_text": r["final"]["unit_text"],
                                          "currency": r["final"]["currency"],
                                          "period_text": r["final"]["period_text"],
                                          "period_start": r["final"]["period_start"],
                                          "period_end": r["final"]["period_end"],
                                          "basis": r["final"]["basis"],
                                          "range": r["final"]["range"],
                                          "status": r["status"],
                                          "evidence_locator": r["final"]["evidence_locator"],
                                          "source_url": r["final"]["source_url"],
                                          "source_document_sha256": r["final"]["source_document_sha256"]}
                                         for r in known],
                      "unknown_items": [{"topic": r["topic"], "status": r["status"],
                                         "reason_codes": r["unknowns"]} for r in ambiguous],
                      "card_status": "CODEX_REVIEW_DEVELOPMENT_CARD_NOT_INDEPENDENT_GOLD"})
    PUBLIC.mkdir(parents=True, exist_ok=True)
    public = {"schema": "market_observer_financial_fact_cards_redacted_v1",
              "role": "TRAIN_ONLY_CODEX_REVIEW_DEVELOPMENT_NOT_MODEL_OUTPUT",
              "source_manifest_sha256": sha(manifest_bytes),
              "candidate_sha256": sha(candidate_bytes),
              "reviewed_checklist_sha256": sha(checklist_bytes),
              "cards": cards}
    PUBLIC_CARDS.write_text(json.dumps(public, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    counts = Counter(r["status"] for r in reviews)
    corrections = Counter(r["topic"] for r in reviews if r["corrections"])
    summary = {"schema": "market_observer_financial_fact_review_summary_v1",
               "scope": "eight NVDA/MU training-period SEC earnings exhibits; no validation/test bodies or labels",
               "review_role": "CODEX_REVIEW_NOT_INDEPENDENT_HUMAN_GOLD",
               "source_manifest_sha256": sha(manifest_bytes),
               "reviewed_checklist_sha256": sha(checklist_bytes),
               "candidate_sha256": sha(candidate_bytes),
               "private_review_sha256": sha(PRIVATE_REVIEW.read_bytes()),
               "document_count": len(train), "candidate_count": len(payload["candidates"]),
               "reviewed_count": len(reviews), "status_counts": dict(counts),
               "correction_rows_by_topic": dict(corrections),
               "public_cards_sha256": sha(PUBLIC_CARDS.read_bytes()),
               "fit_count": 0,
               "limits": ["Source-supported means same-team Codex review only, not independent human gold.",
                          "Unknown fields remain null; no prerelease consensus or share-price inference.",
                          "Original v1/v2 automatic candidates and all corrections remain separately versioned."]}
    PUBLIC_SUMMARY.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"document_count": len(train), "candidate_count": len(payload["candidates"]),
                      "reviewed_count": len(reviews), "status_counts": dict(counts),
                      "correction_rows_by_topic": dict(corrections),
                      "private_review_sha256": summary["private_review_sha256"],
                      "public_cards_sha256": summary["public_cards_sha256"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
