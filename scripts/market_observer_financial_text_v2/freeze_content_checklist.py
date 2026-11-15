"""Freeze a train-only, source-located earnings content checklist.

The output contains source excerpts and stays under ignored artifacts/. It is a
CODEX_REFERENCE_DRAFT, not an independent gold set or a disclosure-completeness
judgment. This program never opens documents outside the frozen train window.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from bs4 import BeautifulSoup, Tag


ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "artifacts/market_observer_official_20261115/corpus_manifest.json"
OUT = ROOT / "artifacts/market_observer_financial_text_v2_20261115/fact_cards"
START, END = "2025-04-01", "2026-03-31"
TOPICS = {
    "revenue": r"\brevenues?\b|\bnet sales\b",
    "net_income": r"\bnet (?:income|loss|earnings)\b",
    "eps": r"\bearnings per share\b|\bEPS\b|\bnet income per share\b",
    "gross_margin": r"\bgross (?:profit )?margin\b",
    "guidance": r"\bguidance\b|\boutlook\b|\bexpects?\b|\bforecast\b",
    "capital_expenditures": r"\bcapital expenditures?\b|\bcapital spending\b|\bcapex\b|\bcapital investments?\b",
    "operating_cash_flow": r"\b(?:operating cash flow|cash flows? (?:from|provided by) operating|net cash provided by operating)\b",
    "free_cash_flow": r"\bfree cash flow\b",
    "segment_revenue": r"\b(?:data center|data centre|gaming|automotive|professional visualization|memory|cloud|dram|nand|hbm|compute and networking)\b.*\brevenue\b|\brevenue\b.*\b(?:data center|data centre|gaming|automotive|professional visualization|memory|cloud|dram|nand|hbm|compute and networking)\b",
}
NUMERIC = re.compile(r"(?:\$|\b\d[\d,.]*\b|\([^)]*\d[^)]*\))")


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def dom_path(tag: Tag) -> str:
    parts = []
    current: Tag | None = tag
    while current is not None and current.name != "[document]":
        parent = current.parent
        if not isinstance(parent, Tag):
            break
        peers = [x for x in parent.find_all(current.name, recursive=False)]
        index = peers.index(current) + 1
        parts.append(f"{current.name}[{index}]")
        current = parent
    return "/".join(reversed(parts))


def visible(tag: Tag) -> str:
    return re.sub(r"\s+", " ", tag.get_text(" ", strip=True)).strip()


def table_context(row: Tag) -> dict:
    table = row.find_parent("table")
    if table is None:
        return {}
    rows = table.find_all("tr")
    idx = rows.index(row)
    headers = []
    for prior in rows[:idx]:
        text = visible(prior)
        if not text:
            continue
        if prior.find("th") or re.search(r"\b(?:FY\d|FQ\d|quarter|year|GAAP|non-GAAP|millions|billions)\b", text, re.I):
            headers.append({"dom_path": dom_path(prior), "rendered_text": text[:350]})
    return {"table_dom_path": dom_path(table), "row_index": idx, "preceding_headers": headers[-3:]}


def elements(soup: BeautifulSoup) -> list[Tag]:
    found = []
    for tag in soup.find_all(["tr", "p", "h1", "h2", "h3", "li"]):
        if tag.name != "tr" and tag.find_parent("table") is not None:
            continue
        if tag.find_parent(["script", "style", "noscript"]) is not None:
            continue
        if visible(tag):
            found.append(tag)
    return found


def candidate(tag: Tag, topic: str, order: int) -> dict:
    text = visible(tag)
    match = re.search(TOPICS[topic], text, re.I)
    assert match is not None
    is_numeric = bool(NUMERIC.search(text))
    # Keep the whole visible row/paragraph when bounded; in long paragraphs
    # keep an exact, contiguous visible-text window around the matched phrase.
    if len(text) > 700:
        start = max(0, match.start() - 220)
        end = min(len(text), match.end() + 420)
        excerpt = text[start:end]
    else:
        start, end, excerpt = 0, len(text), text
    return {
        "source_element_order": order,
        "dom_path": dom_path(tag),
        "element_kind": "table_row" if tag.name == "tr" else tag.name,
        "rendered_visible_text_excerpt": excerpt,
        "excerpt_start_in_element_visible_text": start,
        "excerpt_end_in_element_visible_text": end,
        "element_visible_text_sha256": sha(text.encode("utf-8")),
        "numeric_in_same_element": is_numeric,
        "table_context": table_context(tag) if tag.name == "tr" else None,
    }


def main() -> None:
    if OUT.exists():
        raise FileExistsError("refusing to overwrite frozen fact-card directory")
    manifest_bytes = MANIFEST.read_bytes()
    docs = json.loads(manifest_bytes)["documents"]
    exhibits = sorted(
        (d for d in docs if d.get("source_kind") == "ISSUER_EARNINGS"
         and d.get("symbol") in {"NVDA", "MU"}
         and START <= d.get("filing_date", "") <= END),
        key=lambda d: (d["filing_date"], d["symbol"], d["document_id"]),
    )
    primaries = {d["event_id"]: d for d in docs if d.get("source_kind") == "SEC_FILINGS"
                 and d.get("symbol") in {"NVDA", "MU"}
                 and START <= d.get("filing_date", "") <= END}
    if len(exhibits) != 8 or any(d["event_id"] not in primaries for d in exhibits):
        raise RuntimeError("training exhibit or associated-primary scope differs from frozen expectation")
    rows = []
    source_hashes = {}
    for exhibit in exhibits:
        primary = primaries[exhibit["event_id"]]
        for doc in (exhibit, primary):
            path = Path(doc["raw_body_path"])
            raw = path.read_bytes()
            if sha(raw) != doc["raw_body_sha256"]:
                raise RuntimeError("raw source hash mismatch: " + doc["document_id"])
            source_hashes[doc["document_id"]] = sha(raw)
        raw = Path(exhibit["raw_body_path"]).read_bytes()
        soup = BeautifulSoup(raw, "html.parser")
        nodes = elements(soup)
        topics = {}
        for topic in TOPICS:
            candidates = [candidate(tag, topic, i) for i, tag in enumerate(nodes)
                          if re.search(TOPICS[topic], visible(tag), re.I)]
            # Deterministic triage: direct numeric row/paragraph first; source
            # order breaks ties. Keep alternatives for later Codex review.
            candidates.sort(key=lambda c: (not c["numeric_in_same_element"],
                                           c["element_kind"] != "table_row",
                                           c["source_element_order"]))
            topics[topic] = {
                "status": "LOCATED_CANDIDATE" if candidates else "NOT_FOUND_IN_SAVED_ORIGINAL_BY_RULE",
                "review_status": "CODEX_REFERENCE_DRAFT_NEEDS_FACT_REVIEW",
                "evidence_candidates": candidates[:5],
                "other_candidate_count": max(0, len(candidates) - 5),
            }
        rows.append({
            "symbol": exhibit["symbol"],
            "filing_date": exhibit["filing_date"],
            "event_id": exhibit["event_id"],
            "earnings_document_id": exhibit["document_id"],
            "earnings_source_url": exhibit["source_url"],
            "earnings_raw_sha256": exhibit["raw_body_sha256"],
            "associated_primary_document_id": primary["document_id"],
            "associated_primary_source_url": primary["source_url"],
            "associated_primary_raw_sha256": primary["raw_body_sha256"],
            "topics": topics,
        })
    OUT.mkdir(parents=True)
    checklist = {
        "schema": "train_earnings_content_checklist_v1",
        "role": "CODEX_REFERENCE_DRAFT",
        "scope": {"start": START, "end": END, "symbols": ["NVDA", "MU"],
                  "earnings_exhibits": 8, "associated_primary_filings": 8},
        "rule_limit": "Not-found means only absent under these saved-document phrase rules; it is not issuer nondisclosure.",
        "quote_basis": "Contiguous visible-text excerpt normalized for HTML whitespace, with DOM locator; not raw-byte offset.",
        "rows": rows,
    }
    path = OUT / "content_checklist_frozen.private.json"
    path.write_text(json.dumps(checklist, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    frozen = {
        "schema": "train_earnings_content_checklist_freeze_v1",
        "frozen_at_utc": datetime.now(timezone.utc).isoformat(),
        "checklist_sha256": sha(path.read_bytes()),
        "checklist_code_sha256": sha(Path(__file__).read_bytes()),
        "source_manifest_sha256": sha(manifest_bytes),
        "training_source_raw_sha256_by_document_id": source_hashes,
        "reference_role": "CODEX_REFERENCE_DRAFT; frozen before v2 content comparison",
    }
    (OUT / "checklist_freeze.private.json").write_text(
        json.dumps(frozen, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"train_exhibits": len(exhibits), "associated_primaries": len(exhibits),
                      "topic_rows": len(exhibits) * len(TOPICS),
                      "status_counts": {
                          label: sum(r["topics"][t]["status"] == label for r in rows for t in TOPICS)
                          for label in ("LOCATED_CANDIDATE", "NOT_FOUND_IN_SAVED_ORIGINAL_BY_RULE")},
                      "frozen_at_utc": frozen["frozen_at_utc"],
                      "checklist_sha256": frozen["checklist_sha256"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
