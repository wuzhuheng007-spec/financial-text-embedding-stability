"""Generate one source-bound numeric candidate per reviewed train topic.

Candidates and original quotes stay private. The later CODEX_REVIEW reads this
file without modifying it. No predictor, TF-IDF vectorizer, or model is fit.
"""

from __future__ import annotations

import hashlib
import json
import re
from decimal import Decimal
from pathlib import Path

from .freeze_content_checklist import OUT


CHECKLIST = OUT / "content_checklist_reviewed.private.json"
CANDIDATES = OUT / "fact_candidates.private.json"
NUM = re.compile(r"(?<![A-Za-z0-9])(?:\$\s*)?(?:\(\s*)?-?\d[\d,]*(?:\.\d+)?(?:\s*\))?(?:\s*%)?")
FISCAL = re.compile(r"\b(?:FQ[1-4]-\d{2}|Q[1-4]\s+FY\d{2,4})\b", re.I)
METRIC = {
    "revenue": "revenue",
    "net_income": "net_income",
    "eps": "diluted_eps",
    "gross_margin": "gross_margin_percent",
    "guidance": "revenue_guidance",
    "capital_expenditures": "capital_expenditures_or_combined_purchase",
    "operating_cash_flow": "operating_cash_flow",
    "free_cash_flow": "free_cash_flow",
    "segment_revenue": "segment_revenue",
}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def period_from_revenue(row: dict) -> str | None:
    evidence = row["topics"]["revenue"]["evidence"]
    if not evidence:
        return None
    for header in evidence[0].get("table_headers", []):
        match = FISCAL.search(header["rendered_visible_text"])
        if match:
            return match.group()
    return None


def source_evidence(row: dict, topic: str) -> tuple[str, list[dict], str | None]:
    ev = row["topics"][topic]["evidence"]
    if not ev:
        return "", [], None
    if topic == "guidance" or (topic == "segment_revenue" and row["symbol"] == "MU"):
        target = ev[-1]["rendered_visible_text"]
    elif topic == "gross_margin" and row["symbol"] == "MU":
        target = ev[-1]["rendered_visible_text"]
    else:
        target = ev[0]["rendered_visible_text"]
    return target, ev, ev[-1]["dom_path"]


def after_label(text: str, topic: str) -> str:
    patterns = {
        "revenue": r"^Revenue\b", "net_income": r"^Net income\b",
        "eps": r"^Diluted earnings per share(?:\s*\(EPS\))?\*?\b",
        "gross_margin": r"^(?:Gross margin|percent of revenue)\b",
        "guidance": r"^Revenue(?: is expected to be)?\b",
        "capital_expenditures": r"^(?:Investments in capital expenditures, net|Purchases? related to property and equipment and intangible assets)\b",
        "operating_cash_flow": r"^(?:GAAP )?Net cash provided by operating activities\b",
        "free_cash_flow": r"^(?:Adjusted )?Free cash flow\b",
        "segment_revenue": r"^(?:Data Center revenue of|Revenue)\b",
    }
    return re.sub(patterns[topic], "", text, count=1, flags=re.I).strip()


def first_value(text: str, topic: str) -> str | None:
    stripped = after_label(text, topic)
    if topic == "guidance":
        nvidia = re.search(r"\$\d[\d,.]*\s+billion,?\s+plus or minus\s+\d[\d,.]*\s*%", stripped, re.I)
        if nvidia:
            return nvidia.group()
        micron = re.search(r"\$\d[\d,.]*\s+billion\s*[±+]\s*\$\d[\d,.]*\s+million", stripped, re.I)
        if micron:
            return micron.group()
    match = NUM.search(stripped)
    if not match:
        return None
    value = match.group().strip()
    trailing = stripped[match.end():]
    unit = re.match(r"\s*(?:billion|million)\b", trailing, re.I)
    if unit:
        value += " " + unit.group().strip()
    if topic == "gross_margin" and "%" not in value and re.match(r"\s*%", trailing):
        value += "%"
    return value


def decimal_text(value: Decimal) -> str:
    s = format(value, "f")
    return s.rstrip("0").rstrip(".") if "." in s else s


def guidance_bounds(value_text: str | None) -> dict | None:
    if not value_text:
        return None
    a = re.fullmatch(r"\$(\d[\d,.]*)\s+billion,?\s+plus or minus\s+(\d[\d,.]*)\s*%", value_text, re.I)
    if a:
        center = Decimal(a.group(1).replace(",", ""))
        pct = Decimal(a.group(2).replace(",", "")) / Decimal(100)
        return {"lower_text": "$" + decimal_text(center * (1-pct)) + " billion",
                "upper_text": "$" + decimal_text(center * (1+pct)) + " billion",
                "derivation": "central_billion * (1 +/- percentage/100)", "status": "DERIVED_FROM_SOURCE_RANGE"}
    b = re.fullmatch(r"\$(\d[\d,.]*)\s+billion\s*[±+]\s*\$(\d[\d,.]*)\s+million", value_text, re.I)
    if b:
        center = Decimal(b.group(1).replace(",", ""))
        delta = Decimal(b.group(2).replace(",", "")) / Decimal(1000)
        return {"lower_text": "$" + decimal_text(center-delta) + " billion",
                "upper_text": "$" + decimal_text(center+delta) + " billion",
                "derivation": "central_billion +/- delta_million/1000", "status": "DERIVED_FROM_SOURCE_RANGE"}
    return None


def candidate(row: dict, topic: str) -> dict | None:
    source = row["topics"][topic]
    if source["status"] in {"NOT_FOUND", "PARSE_FAILED", "NOT_APPLICABLE"}:
        return None
    target, ev, locator = source_evidence(row, topic)
    value = first_value(target, topic)
    period = period_from_revenue(row)
    if topic == "guidance" and ev:
        match = FISCAL.search(ev[0]["rendered_visible_text"])
        period = match.group() if match else None
    row_header = next((h["rendered_visible_text"] for h in (ev[-1].get("table_headers", []) if ev else [])
                       if "million" in h["rendered_visible_text"].lower()), None)
    unit = None
    currency = None
    basis = "UNKNOWN"
    if topic in {"revenue", "net_income", "operating_cash_flow", "free_cash_flow", "capital_expenditures", "segment_revenue"}:
        if value and "$" in value:
            currency = "USD"
        if value and "billion" in value.lower():
            unit = "billion"
        elif value and "million" in value.lower():
            unit = "million"
        elif row_header or topic in {"revenue", "net_income"}:
            unit = "million"  # explicit summary-table heading, verified again in CODEX_REVIEW
    if topic == "eps":
        unit, currency = "USD/share", "USD"
    if topic == "gross_margin":
        unit = "percent"
    if topic == "guidance":
        unit, currency = "billion", "USD"
    if topic in {"revenue", "net_income", "eps", "gross_margin", "guidance"}:
        basis = "GAAP_FIRST_COLUMN" if row["symbol"] == "MU" else "GAAP_FIRST_TABLE_OR_UNSPECIFIED_GUIDANCE"
    if topic == "free_cash_flow" and row["symbol"] == "MU":
        basis = "ADJUSTED_SOURCE_LABEL"
    if topic == "segment_revenue":
        basis = "SOURCE_SEGMENT_OR_BUSINESS_UNIT"
    if topic == "capital_expenditures" and row["symbol"] == "NVDA":
        basis = "COMBINED_PPE_AND_INTANGIBLES_NOT_PURE_CAPEX"
    identifier = sha((row["event_id"] + "|" + topic + "|" + (value or "") + "|" + (locator or "")).encode())[:20]
    ambiguity = []
    if source["status"] == "BASIS_AMBIGUOUS":
        ambiguity.append(source["review_note"])
    if value is None:
        ambiguity.append("No source numeric expression recovered by deterministic candidate rule.")
    if period is None:
        ambiguity.append("Fiscal period not recovered from linked heading; no date inferred.")
    return {
        "candidate_id": identifier,
        "event_id": row["event_id"],
        "symbol": row["symbol"], "security_candidate": "US." + row["symbol"],
        "source_document_id": row["earnings_document_id"],
        "source_document_sha256": source["source_document_sha256"],
        "source_url": source["source_url"],
        "filing_date": row["filing_date"],
        "topic": topic, "metric_normalized": METRIC[topic],
        "metric_original": target.split("$")[0][:90].strip() if target else None,
        "fact_kind": "FUTURE_GUIDANCE" if topic == "guidance" else "HISTORICAL_ACTUAL_OR_SOURCE_DEFINED_MEASURE",
        "period_text": period, "period_start": None, "period_end": None,
        "value_text": value, "unit_text": unit, "currency": currency,
        "basis_candidate": basis,
        "comparison_value_text": None, "comparison_period_text": None,
        "range": guidance_bounds(value) if topic == "guidance" else None,
        "source_evidence": ev, "evidence_locator": locator,
        "extraction_method": "DETERMINISTIC_SOURCE_ROW_OR_NARRATIVE_RULE_V1",
        "status": "AUTOMATIC_CANDIDATE",
        "ambiguity": ambiguity,
    }


def main() -> None:
    if CANDIDATES.exists():
        raise FileExistsError("refusing to overwrite original candidate facts")
    reviewed_bytes = CHECKLIST.read_bytes()
    rows = json.loads(reviewed_bytes)["rows"]
    candidates = [c for row in rows for topic in METRIC if (c := candidate(row, topic)) is not None]
    ids = [c["candidate_id"] for c in candidates]
    if len(ids) != len(set(ids)):
        raise RuntimeError("candidate ID collision")
    payload = {"schema": "train_earnings_fact_candidates_v1",
               "role": "DETERMINISTIC_CANDIDATES_NOT_VERIFIED_FACTS",
               "reviewed_checklist_sha256": sha(reviewed_bytes),
               "train_only": True, "candidates": candidates}
    CANDIDATES.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"candidate_count": len(candidates),
                      "missing_value_candidates": sum(c["value_text"] is None for c in candidates),
                      "guidance_ranges_derived": sum(bool(c["range"]) for c in candidates),
                      "candidate_sha256": sha(CANDIDATES.read_bytes())}, ensure_ascii=False))


if __name__ == "__main__":
    main()
