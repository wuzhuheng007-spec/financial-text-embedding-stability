"""Check event-impact publication links and receipt-bound status statements."""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from urllib.parse import unquote


ROOT = Path(__file__).resolve().parents[1]
FILES = [
    "README.md",
    "docs/news_research/README.md",
    "docs/news_research/CHANGELOG.md",
    "docs/news_research/earnings/roadmap.en.md",
    "docs/news_research/earnings/roadmap.zh.md",
    "reports/2026-11-15_event_impact_cards.en.md",
    "reports/2026-11-15_event_impact_cards.zh.md",
]
RESULTS = ROOT / "results/news_event_impact_20261115"


def main() -> None:
    links_checked = 0
    for name in FILES:
        path = ROOT / name
        body = path.read_text(encoding="utf-8")
        for target in re.findall(r"\[[^\]]*\]\(([^)]+)\)", body):
            if target.startswith(("https://", "http://", "mailto:", "#")):
                continue
            relative = unquote(target.split("#", 1)[0])
            if relative:
                resolved = (path.parent / relative).resolve()
                assert resolved.exists(), f"Broken link in {name}: {target}"
                links_checked += 1

    status = json.loads((ROOT / "docs/news_research/STATUS.json").read_text(encoding="utf-8"))
    selection = json.loads((RESULTS / "selection_manifest.json").read_text(encoding="utf-8"))
    receipt = json.loads((RESULTS / "static_source_validation_release.json").read_text(encoding="utf-8"))
    visual = json.loads((RESULTS / "visual_acceptance_release.json").read_text(encoding="utf-8"))
    findings = json.loads((RESULTS / "review_findings.json").read_text(encoding="utf-8"))
    assert tuple(map(int, status["version"].split("."))) >= (1, 15)
    impact = status["event_impact_development"]
    assert impact["status"] == "ANALYSIS_TEMPLATES_MADE_PENDING_CONTENT_EVALUATION"
    assert [event["case"] for event in selection["events"]] == ["a_nvidia", "b_coreweave", "c_cpi"]
    assert len(selection["events"]) == impact["cards"] == 3
    assert sum(len(event["source_files"]) for event in selection["events"]) == receipt["official_source_files_read_back"] == impact["source_files_read_back"] == 6
    for key, expected in (
        ("source_bound_facts", 44),
        ("code_derived_calculations", 10),
        ("transmission_edges", 9),
    ):
        assert receipt[key] == impact[key] == expected
    assert impact["browser_viewports"] == visual["viewports"]
    assert len(findings["findings"]) == 5
    assert impact["financial_experiments"] == "NOT_RUN"
    assert impact["new_model_inference"] == "NOT_RUN"
    assert visual["provider_requests_during_browser_check"] == 0
    assert receipt["captured_flash_body_matches_in_public_html"] == 0

    with (RESULTS / "plan_execution.csv").open(encoding="utf-8", newline="") as stream:
        phases = list(csv.DictReader(stream))
    assert [row["stage"] for row in phases] == [f"P{i}" for i in range(9)]
    for language in ("en", "zh"):
        report = (ROOT / f"reports/2026-11-15_event_impact_cards.{language}.md").read_text(encoding="utf-8")
        for number in ("44", "10", "9", "390"):
            assert number in report, f"Missing bound {number} in {language} report"
        assert "NOT_RUN" in report and "AI_ANALYST_DRAFT" in report
    print(f"PASS docs_files={len(FILES)} relative_links={links_checked} stages={len(phases)}")
    print("PASS case/source/fact/calculation/edge counts match receipts and STATUS")
    print("PASS bilingual reports preserve draft and NOT_RUN labels")
    print("Document checks only; no provider, model or financial experiment ran.")


if __name__ == "__main__":
    main()
