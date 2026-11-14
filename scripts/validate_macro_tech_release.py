"""Check the published macro-event artifacts against saved input hashes.

This checks local structure and references. It does not establish source truth,
first-public timing, historical security identity or financial validity.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
from pathlib import Path
from urllib.parse import unquote


ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "results/news_macro_tech_20261115/candidate_events.json"
RESULTS = ROOT / "results/news_macro_tech_20261115"
DOCUMENTS = (
    "README.md",
    "docs/news_research/README.md",
    "docs/news_research/CHANGELOG.md",
    "docs/news_research/macro_tech/README.md",
    "docs/news_research/macro_tech/SOURCE_REGISTER.md",
    "docs/news_research/macro_tech/preference_review_start.md",
    "docs/news_research/macro_tech/event_contract.md",
    "docs/news_research/macro_tech/candidates.en.md",
    "docs/news_research/macro_tech/candidates.zh.md",
    "reports/2026-11-15_macro_tech_candidates.en.md",
    "reports/2026-11-15_macro_tech_candidates.zh.md",
)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    receipt = json.loads((RESULTS / "catalog_build_receipt.json").read_text(encoding="utf-8"))
    ui = json.loads((RESULTS / "preference_ui_receipt.json").read_text(encoding="utf-8"))
    drafts = json.loads((RESULTS / "analyst_drafts.json").read_text(encoding="utf-8"))
    status = json.loads((ROOT / "docs/news_research/STATUS.json").read_text(encoding="utf-8"))
    catalog_hash = digest(CATALOG)
    assert receipt["catalog_sha256"] == ui["candidate_catalog_sha256"] == drafts["catalog_sha256"] == catalog_hash
    assert status["macro_tech_execution"]["catalog_sha256"] == catalog_hash
    assert len(catalog["events"]) == receipt["candidate_events"] == 12
    assert len({card["event_cluster"] for card in catalog["events"]}) == 12
    for rel, expected_hash in receipt["outputs_sha256"].items():
        path = ROOT / rel.replace("\\", "/")
        assert digest(path) == expected_hash, rel
    with (RESULTS / "source_evidence_index.csv").open(encoding="utf-8", newline="") as handle:
        index_rows = list(csv.DictReader(handle))
    expected = {
        (card["id"], str(item["fact_index"]), item["url"])
        for card in catalog["events"] for item in card["fact_evidence"]
    }
    actual = {(row["event_id"], row["fact_index"], row["source_url"])
              for row in index_rows}
    assert len(index_rows) == receipt["source_bound_fact_statements"] == 24
    assert actual == expected
    page = ROOT / "artifacts/macro_tech_20261115/preference_v1/index.html"
    assert digest(page) == ui["private_page_sha256"]
    assert ui["candidate_count"] == 12 and ui["prefilled_answers"] is False
    assert ui["human_gold_labels"] == drafts["model_calls"] == 0
    assert drafts["financial_labels"] == "NOT_RUN"
    assert status["macro_tech_execution"]["market_labels"] == 0
    g01 = next(card for card in catalog["events"] if card["id"] == "G01")
    assert g01["date"] == "2026-06-14"
    assert all(row["url"] == g01["date_evidence"]["url"] for row in g01["fact_evidence"])
    broken: list[str] = []
    checked_links = 0
    for rel in DOCUMENTS:
        path = ROOT / rel
        for target in re.findall(r"\]\(([^)]+)\)", path.read_text(encoding="utf-8")):
            if re.match(r"^[a-z]+://", target, re.IGNORECASE) or target.startswith("#"):
                continue
            target_path = (path.parent / unquote(target.split("#", 1)[0])).resolve()
            checked_links += 1
            if not target_path.is_file():
                broken.append(f"{rel} -> {target}")
    if broken:
        raise ValueError("Broken local links: " + "; ".join(broken))
    print(json.dumps({
        "status": "PASS_LOCAL_MECHANICAL_ONLY",
        "catalog_sha256": catalog_hash,
        "candidate_events": len(catalog["events"]),
        "source_fact_rows": len(index_rows),
        "local_markdown_links_checked": checked_links,
        "broken_local_links": 0,
        "private_page_sha256_verified": True,
        "drafts_catalog_bound": True,
        "limits": "Official-source content and financial admission require separate review",
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
