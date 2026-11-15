"""Add source-bound prior-quarter comparisons to preserved reviewed fact cards.

This v3 overlay reads only the eight saved training SEC earnings exhibits. It
never replaces automatic candidates or the v1/v2 reviews. Missing comparative
context stays null with a reason; no price or validation data are consulted.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from copy import deepcopy
from pathlib import Path

from bs4 import BeautifulSoup, Tag

from .freeze_content_checklist import MANIFEST, OUT, dom_path, visible
from .review_fact_candidates_v2 import cell_spans, resolve, short_evidence


PREVIOUS = OUT / "fact_review_v2.private.json"
CANDIDATES = OUT / "fact_candidates_v2.private.json"
PRIVATE = OUT / "fact_review_v3.private.json"
PUBLIC = Path(__file__).resolve().parents[2] / "results/market_observer_financial_text_v2_20261115"
CARDS = PUBLIC / "fact_cards_v3_redacted.json"
SUMMARY = PUBLIC / "fact_review_v3_summary_redacted.json"
CORE = {"revenue", "net_income", "eps", "gross_margin"}
FISCAL = re.compile(r"^(?:FQ[1-4]-\d{2}|Q[1-4]\s+FY\d{2,4})$", re.I)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def measure_cells(row: Tag) -> list[tuple[Tag, int, int]]:
    """Extract numeric measure cells, skipping the left metric label cell."""
    return [(cell, start, end) for cell, start, end in cell_spans(row)[1:]
            if re.fullmatch(r"\$?\s*\(?-?\d[\d,]*(?:\.\d+)?\)?\s*%?", visible(cell))]


def numeric_identity(text: str) -> str:
    match = re.search(r"\(?-?\d[\d,]*(?:\.\d+)?\)?", text)
    return match.group().replace(",", "") if match else ""


def header_for_column(table: Tag, value_row: Tag, column: int, pattern: re.Pattern[str]) -> Tag | None:
    rows = table.find_all("tr")
    for row in reversed(rows[:rows.index(value_row)]):
        for cell, start, end in cell_spans(row):
            if start <= column < end and pattern.fullmatch(visible(cell)):
                return cell
    return None


def gaap_header_for_column(table: Tag, value_row: Tag, column: int) -> Tag | None:
    return header_for_column(table, value_row, column, re.compile(r"GAAP(?:\s*\(\d+\))?", re.I))


def comparison(review: dict, candidate: dict, soup: BeautifulSoup) -> tuple[dict, dict]:
    """Return nullable comparison fields and separate evidence/decision."""
    empty = {"comparison_value_text": None, "comparison_period_text": None,
             "comparison_period_start": None, "comparison_period_end": None,
             "comparison_unit_text": None, "comparison_currency": None,
             "comparison_basis": None, "comparison_status": "UNKNOWN_OR_NOT_REQUIRED"}
    evidence = {"comparison_value": [], "comparison_period": [],
                "comparison_unit": [], "comparison_basis": [],
                "reason": "No safe same-metric, same-basis prior-quarter comparison reviewed for this topic."}
    if review["topic"] not in CORE or review["status"] != "CODEX_REVIEW_SOURCE_SUPPORTED":
        return empty, evidence
    row = resolve(soup, review["final"]["evidence_locator"])
    table = row.find_parent("table")
    if table is None:
        evidence["reason"] = "Current value is not in a source table."
        return empty, evidence
    cells = measure_cells(row)
    if len(cells) < 2:
        evidence["reason"] = "Fewer than two numeric measure cells in source row."
        return empty, evidence
    current, prior = cells[0], cells[1]
    if numeric_identity(review["final"]["value_text"] or "") != numeric_identity(visible(current[0])):
        evidence["reason"] = "Reviewed current value does not match first source measure cell."
        return empty, evidence
    current_period = header_for_column(table, row, current[1], FISCAL)
    prior_period = header_for_column(table, row, prior[1], FISCAL)
    if current_period is None or prior_period is None:
        evidence["reason"] = "Fiscal period header missing for current or comparison value column."
        return empty, evidence
    if visible(current_period) != review["final"]["period_text"]:
        evidence["reason"] = "Source current period column differs from reviewed period."
        return empty, evidence
    basis = review["final"]["basis"]
    basis_evidence: list[dict] = []
    if basis in {"GAAP", "GAAP_FIRST_EXPLICIT_COLUMN"}:
        current_basis = gaap_header_for_column(table, row, current[1])
        prior_basis = gaap_header_for_column(table, row, prior[1])
        if current_basis is None or prior_basis is None:
            evidence["reason"] = "Both numeric cells are not proven under the same GAAP header span."
            return empty, evidence
        basis_evidence = [short_evidence(current_basis), short_evidence(prior_basis)]
    unit = review["final"]["unit_text"]
    if unit is None or not review["field_evidence"]["unit"]:
        evidence["reason"] = "Comparison unit cannot be inherited without source unit evidence."
        return empty, evidence
    result = {"comparison_value_text": visible(prior[0]),
              "comparison_period_text": visible(prior_period),
              "comparison_period_start": None, "comparison_period_end": None,
              "comparison_unit_text": unit,
              "comparison_currency": review["final"]["currency"],
              "comparison_basis": basis,
              "comparison_status": "SOURCE_BOUND_PRIOR_QUARTER"}
    evidence = {"comparison_value": [short_evidence(prior[0]), short_evidence(row)],
                "comparison_period": [short_evidence(prior_period)],
                "comparison_unit": review["field_evidence"]["unit"],
                "comparison_basis": basis_evidence,
                "reason": None,
                "current_value_cell": short_evidence(current[0]),
                "current_period_header": short_evidence(current_period)}
    return result, evidence


def main() -> None:
    if PRIVATE.exists() or CARDS.exists() or SUMMARY.exists():
        raise FileExistsError("refusing to overwrite v3 comparison overlay")
    previous_bytes = PREVIOUS.read_bytes()
    candidate_bytes = CANDIDATES.read_bytes()
    manifest_bytes = MANIFEST.read_bytes()
    old = json.loads(previous_bytes)
    candidates = {c["candidate_id"]: c for c in json.loads(candidate_bytes)["candidates"]}
    docs = {d["document_id"]: d for d in json.loads(manifest_bytes)["documents"]
            if d.get("source_kind") == "ISSUER_EARNINGS" and d.get("symbol") in {"NVDA", "MU"}
            and "2025-04-01" <= d.get("filing_date", "") <= "2026-03-31"}
    if len(docs) != 8 or len(old["reviews"]) != 71:
        raise RuntimeError("unexpected train-only comparison scope")
    soups = {}
    for doc_id, doc in docs.items():
        raw = Path(doc["raw_body_path"]).read_bytes()
        if sha(raw) != doc["raw_body_sha256"]:
            raise RuntimeError("source changed: " + doc_id)
        soups[doc_id] = BeautifulSoup(raw, "html.parser")
    reviews = []
    for original in old["reviews"]:
        candidate = candidates[original["candidate_id"]]
        if candidate["source_document_sha256"] != docs[candidate["source_document_id"]]["raw_body_sha256"]:
            raise RuntimeError("candidate source hash mismatch")
        record = deepcopy(original)
        comparison_fields, comparison_evidence = comparison(record, candidate, soups[candidate["source_document_id"]])
        record["final"].update(comparison_fields)
        record["field_evidence"].update({key: value for key, value in comparison_evidence.items()
                                          if key.startswith("comparison_") and key != "comparison_status"})
        record["comparison_review_reason"] = comparison_evidence["reason"]
        record["comparison_binding_evidence"] = {
            key: value for key, value in comparison_evidence.items()
            if key in {"current_value_cell", "current_period_header"}}
        reviews.append(record)
    private = {"schema": "train_earnings_fact_review_v3",
               "role": "CODEX_REVIEW_NOT_INDEPENDENT_HUMAN_GOLD",
               "supersedes_review_v2_for_comparison_fields": True,
               "previous_review_sha256": sha(previous_bytes),
               "candidate_sha256": sha(candidate_bytes),
               "source_manifest_sha256": sha(manifest_bytes),
               "reviews": reviews}
    PRIVATE.write_text(json.dumps(private, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    cards = []
    for doc in sorted(docs.values(), key=lambda d: (d["filing_date"], d["symbol"])):
        relevant = [r for r in reviews if r["event_id"] == doc["event_id"]]
        facts = []
        for r in relevant:
            if r["status"] != "CODEX_REVIEW_SOURCE_SUPPORTED":
                continue
            f = r["final"]
            facts.append({"candidate_id": r["candidate_id"], "topic": r["topic"],
                          "category": r["category"], "metric": f["metric_original"],
                          "metric_normalized": f["metric_normalized"],
                          "value_text": f["value_text"], "unit_text": f["unit_text"],
                          "currency": f["currency"], "period_text": f["period_text"],
                          "period_start": f["period_start"], "period_end": f["period_end"],
                          "basis": f["basis"], "range": f["range"],
                          "comparison_value_text": f["comparison_value_text"],
                          "comparison_period_text": f["comparison_period_text"],
                          "comparison_period_start": f["comparison_period_start"],
                          "comparison_period_end": f["comparison_period_end"],
                          "comparison_unit_text": f["comparison_unit_text"],
                          "comparison_currency": f["comparison_currency"],
                          "comparison_basis": f["comparison_basis"],
                          "comparison_status": f["comparison_status"],
                          "status": r["status"], "evidence_locator": f["evidence_locator"],
                          "source_url": f["source_url"],
                          "source_document_sha256": f["source_document_sha256"]})
        cards.append({"event_id": doc["event_id"], "symbol": doc["symbol"],
                      "filing_date": doc["filing_date"],
                      "financial_period": next((r["final"]["period_text"] for r in relevant
                                                if r["topic"] == "revenue"), None),
                      "historical_training_material": True,
                      "source_url": doc["source_url"], "source_sha256": doc["raw_body_sha256"],
                      "reviewed_facts": facts,
                      "unknown_items": [{"topic": r["topic"], "status": r["status"],
                                         "reason_codes": r["unknowns"]} for r in relevant
                                        if r["status"] != "CODEX_REVIEW_SOURCE_SUPPORTED"],
                      "card_status": "CODEX_REVIEW_DEVELOPMENT_CARD_NOT_INDEPENDENT_GOLD"})
    PUBLIC.mkdir(parents=True, exist_ok=True)
    public = {"schema": "market_observer_financial_fact_cards_redacted_v3",
              "role": "TRAIN_ONLY_CODEX_REVIEW_DEVELOPMENT_NOT_MODEL_OUTPUT",
              "previous_review_sha256": sha(previous_bytes),
              "source_manifest_sha256": sha(manifest_bytes),
              "cards": cards}
    CARDS.write_text(json.dumps(public, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    count = Counter(r["final"]["comparison_status"] for r in reviews)
    summary = {"schema": "market_observer_financial_fact_review_summary_v3",
               "scope": "eight NVDA/MU training-period SEC earnings exhibits; no validation/test source or labels",
               "review_role": "CODEX_REVIEW_NOT_INDEPENDENT_HUMAN_GOLD",
               "source_manifest_sha256": sha(manifest_bytes),
               "candidate_sha256": sha(candidate_bytes),
               "previous_review_sha256": sha(previous_bytes),
               "private_review_sha256": sha(PRIVATE.read_bytes()),
               "public_cards_sha256": sha(CARDS.read_bytes()),
               "document_count": len(docs), "candidate_count": len(candidates),
               "reviewed_count": len(reviews),
               "source_supported_count": sum(r["status"] == "CODEX_REVIEW_SOURCE_SUPPORTED" for r in reviews),
               "ambiguous_count": sum(r["status"] != "CODEX_REVIEW_SOURCE_SUPPORTED" for r in reviews),
               "comparison_status_counts": dict(count),
               "fit_count": 0,
               "limits": ["Comparison is prior fiscal quarter from the same saved source row and GAAP column where bound; not prerelease market consensus.",
                          "Comparisons for guidance/cash/segment facts stay null unless independently source-bound.",
                          "This is a train-material content review, not an independent extraction accuracy estimate."]}
    SUMMARY.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"reviewed": len(reviews), "comparison_status_counts": dict(count),
                      "private_review_sha256": summary["private_review_sha256"],
                      "public_cards_sha256": summary["public_cards_sha256"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
