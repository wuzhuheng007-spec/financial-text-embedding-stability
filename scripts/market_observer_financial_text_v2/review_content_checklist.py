"""Train-only Codex review overlay for a frozen earnings topic checklist.

Preserves the first-pass checklist and documents predictable phrase-rule misses.
The source excerpts and reviewed overlay are private. No v2 input is opened.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from bs4 import BeautifulSoup, Tag

from .freeze_content_checklist import MANIFEST, OUT, START, END, dom_path, visible


REVIEW = OUT / "content_checklist_reviewed.private.json"
FREEZE = OUT / "review_freeze.private.json"
TOPIC_ORDER = (
    "revenue", "net_income", "eps", "gross_margin", "guidance",
    "capital_expenditures", "operating_cash_flow", "free_cash_flow", "segment_revenue",
)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def row_with(table_rows: list[Tag], pattern: str) -> Tag | None:
    return next((r for r in table_rows if re.search(pattern, visible(r), re.I)), None)


def table_rows(soup: BeautifulSoup) -> list[Tag]:
    return soup.find_all("tr")


def narrow_block_for_literal(soup: BeautifulSoup, expression: str) -> Tag | None:
    for node in soup.find_all(string=re.compile(expression, re.I)):
        tag = node.parent
        if not isinstance(tag, Tag):
            continue
        while tag.parent is not None and tag.name not in {"div", "p", "li", "tr", "h1", "h2", "h3"}:
            if not isinstance(tag.parent, Tag):
                break
            tag = tag.parent
        if re.search(expression, visible(tag), re.I):
            return tag
    return None


def select(row: dict, soup: BeautifulSoup, topic: str) -> tuple[list[Tag], str, str]:
    symbol = row["symbol"]
    trs = table_rows(soup)
    if topic == "revenue":
        tag = row_with(trs, r"^Revenue\s+\$?\s*\d")
        return ([tag] if tag else []), "LOCATED" if tag else "NOT_FOUND", "Current period is first GAAP column of the saved release summary table; period and unit require its header."
    if topic == "net_income":
        tag = row_with(trs, r"^Net income\s+\$?\s*\d")
        return ([tag] if tag else []), "LOCATED" if tag else "NOT_FOUND", "Current period and basis require the associated table header."
    if topic == "eps":
        tag = row_with(trs, r"^Diluted earnings per share(?:\s*\(EPS\))?\*?\s+\$?\s*\d")
        return ([tag] if tag else []), "LOCATED" if tag else "NOT_FOUND", "Current period and basis require the associated table header; per-share value is not in millions."
    if topic == "gross_margin":
        tag = row_with(trs, r"^Gross margin\s+\$?\s*\d")
        if tag is None:
            return [], "NOT_FOUND", "No numeric gross-margin row located under this structural rule."
        if symbol == "MU":
            table = tag.find_parent("table")
            sibling_rows = table.find_all("tr") if table else []
            index = sibling_rows.index(tag) if table else -1
            pct = sibling_rows[index + 1] if index >= 0 and index + 1 < len(sibling_rows) else None
            if pct is not None and re.match(r"^percent of revenue\b", visible(pct), re.I):
                return [tag, pct], "LOCATED", "MU has both gross-margin dollars and a following percentage-of-revenue row; do not conflate them."
            return [tag], "BASIS_AMBIGUOUS", "MU percentage row not structurally recovered."
        return [tag], "LOCATED", "NVIDIA row reports percentage gross margin under GAAP table header."
    if topic == "guidance":
        if symbol == "NVDA":
            tag = narrow_block_for_literal(soup, r"Revenue is expected to be\s+\$?\d")
            period = narrow_block_for_literal(soup, r"NVIDIA.s outlook for the (?:first|second|third|fourth) quarter of fiscal")
            if tag and period:
                return [period, tag], "LOCATED", "Narrative outlook period and revenue range are separate div blocks; retain both."
            return ([tag] if tag else []), "BASIS_AMBIGUOUS" if tag else "NOT_FOUND", "Narrative outlook period not recovered with value."
        for table in soup.find_all("table"):
            rows = table.find_all("tr")
            for j, candidate in enumerate(rows):
                if re.search(r"\bFQ[1-4]-\d\d\b.*\b(?:GAAP|Outlook)\b.*\bOutlook\b", visible(candidate), re.I):
                    revenue = row_with(rows[j + 1:j + 5], r"^Revenue\s+\$?\s*\d")
                    if revenue:
                        return [candidate, revenue], "LOCATED", "MU outlook heading identifies target fiscal quarter and GAAP/non-GAAP columns."
        return [], "NOT_FOUND", "No complete MU guidance header and numeric revenue row located."
    if topic == "capital_expenditures":
        if symbol == "MU":
            tag = row_with(trs, r"^Investments in capital expenditures, net\s+\(?\$?\s*\d")
            return ([tag] if tag else []), "LOCATED" if tag else "NOT_FOUND", "Net investments line as labeled in source; parentheses may indicate cash outflow."
        tag = row_with(trs, r"^Purchases? related to property and equipment and intangible assets\s+\(?\$?\s*\d")
        return ([tag] if tag else []), "BASIS_AMBIGUOUS" if tag else "NOT_FOUND", "Combined property/equipment and intangible purchases are not a clean standalone CapEx value."
    if topic == "operating_cash_flow":
        tag = row_with(trs, r"^(?:GAAP )?Net cash provided by operating activities\s+\$?\s*\d")
        return ([tag] if tag else []), "LOCATED" if tag else "NOT_FOUND", "Check quarterly versus year-to-date period in table header before fact-card use."
    if topic == "free_cash_flow":
        tag = row_with(trs, r"^(?:Adjusted )?Free cash flow\s+\$?\s*\d")
        return ([tag] if tag else []), "LOCATED" if tag else "NOT_FOUND", "Source-defined free-cash-flow variant; do not silently equate adjusted and unadjusted figures."
    if topic == "segment_revenue":
        if symbol == "NVDA":
            tag = narrow_block_for_literal(soup, r"Data Center revenue of\s+\$?\d")
            return ([tag] if tag else []), "LOCATED" if tag else "NOT_FOUND", "NVIDIA Data Center numeric narrative is in a div/font block, missed by first-pass p/tr scanner."
        for table in soup.find_all("table"):
            rows = table.find_all("tr")
            for j, candidate in enumerate(rows):
                if re.search(r"Cloud Memory Business Unit", visible(candidate), re.I):
                    rev = row_with(rows[j + 1:j + 4], r"^Revenue\s+\$?\s*\d")
                    if rev:
                        return [candidate, rev], "LOCATED", "MU business-unit name and revenue row are separate rows; retain period header."
        return [], "NOT_FOUND", "No numeric business-unit revenue row located in saved release by this rule."
    raise KeyError(topic)


def evidence(tag: Tag) -> dict:
    full = visible(tag)
    table = tag.find_parent("table")
    headers = []
    if table:
        rows = table.find_all("tr")
        pos = rows.index(tag)
        for prior in rows[:pos]:
            text = visible(prior)
            if len(text) <= 400 and (prior.find("th") or re.search(r"\b(?:GAAP|FQ[1-4]|FY\d|quarter|year ended|millions|billions|Q[1-4] FY)\b", text, re.I)):
                headers.append({"dom_path": dom_path(prior), "rendered_visible_text": text})
    return {
        "dom_path": dom_path(tag),
        "element_kind": tag.name,
        "rendered_visible_text": full if len(full) <= 700 else full[:700],
        "element_visible_text_sha256": sha(full.encode("utf-8")),
        "truncated_quote": len(full) > 700,
        "table_headers": headers[-4:],
    }


def main() -> None:
    if REVIEW.exists() or FREEZE.exists():
        raise FileExistsError("refusing to overwrite reviewed checklist or freeze receipt")
    initial_path = OUT / "content_checklist_frozen.private.json"
    initial_bytes = initial_path.read_bytes()
    initial = json.loads(initial_bytes)
    manifest_bytes = MANIFEST.read_bytes()
    docs = json.loads(manifest_bytes)["documents"]
    by_id = {d["document_id"]: d for d in docs if d.get("source_kind") == "ISSUER_EARNINGS"
             and START <= d.get("filing_date", "") <= END}
    reviewed_rows = []
    corrections = []
    for row in initial["rows"]:
        doc = by_id[row["earnings_document_id"]]
        raw = Path(doc["raw_body_path"]).read_bytes()
        if sha(raw) != row["earnings_raw_sha256"]:
            raise RuntimeError("source changed after initial checklist freeze")
        soup = BeautifulSoup(raw, "html.parser")
        topics = {}
        for topic in TOPIC_ORDER:
            selected, status, note = select(row, soup, topic)
            if status == "LOCATED" and not selected:
                raise RuntimeError("located without source evidence")
            old_status = row["topics"][topic]["status"]
            if old_status == "NOT_FOUND_IN_SAVED_ORIGINAL_BY_RULE" and selected:
                corrections.append({"event_id": row["event_id"], "topic": topic,
                                    "old_status": old_status, "reviewed_status": status,
                                    "reason": "First-pass phrase/element rule missed source evidence; original checklist preserved."})
            topics[topic] = {"status": status, "review_note": note,
                             "evidence": [evidence(tag) for tag in selected],
                             "source_document_sha256": row["earnings_raw_sha256"],
                             "source_url": row["earnings_source_url"]}
        reviewed_rows.append({"symbol": row["symbol"], "filing_date": row["filing_date"],
                              "event_id": row["event_id"], "earnings_document_id": row["earnings_document_id"],
                              "associated_primary_document_id": row["associated_primary_document_id"],
                              "topics": topics})
    result = {"schema": "train_earnings_content_checklist_reviewed_v1",
              "role": "CODEX_REFERENCE_DRAFT_PLUS_CODEX_REVIEW",
              "old_frozen_checklist_sha256": sha(initial_bytes),
              "source_manifest_sha256": sha(manifest_bytes),
              "allowed_statuses": ["LOCATED", "NOT_FOUND", "BASIS_AMBIGUOUS", "PARSE_FAILED", "NOT_APPLICABLE"],
              "status_limit": "NOT_FOUND means not located in saved source under reviewed rules; never issuer nondisclosure.",
              "quote_basis": "Visible HTML text with normalized whitespace and source DOM path; not raw-byte offsets.",
              "corrections_from_first_pass": corrections,
              "rows": reviewed_rows}
    REVIEW.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    receipt = {"schema": "train_earnings_content_review_freeze_v1",
               "frozen_at_utc": datetime.now(timezone.utc).isoformat(),
               "reviewed_checklist_sha256": sha(REVIEW.read_bytes()),
               "initial_checklist_sha256": sha(initial_bytes),
               "review_code_sha256": sha(Path(__file__).read_bytes()),
               "source_manifest_sha256": sha(manifest_bytes),
               "correction_count": len(corrections),
               "status_counts": {status: sum(r["topics"][t]["status"] == status
                                            for r in reviewed_rows for t in TOPIC_ORDER)
                                 for status in result["allowed_statuses"]},
               "role": "CODEX_REFERENCE_DRAFT; frozen before P4 old-v2 content comparison"}
    FREEZE.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: receipt[k] for k in ("frozen_at_utc", "reviewed_checklist_sha256",
                                              "initial_checklist_sha256", "correction_count", "status_counts")},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
