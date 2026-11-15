"""Check Jin10 bilingual report links and receipt-bound numerical statements."""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from urllib.parse import unquote


ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results/news_jin10_20261115"
DESKTOP_RESULTS = ROOT / "results/news_jin10_desktop_20261115"
FILES = [
    "README.md", "docs/news_research/README.md",
    "docs/news_research/CHANGELOG.md", "docs/news_research/source_access.md",
    "docs/news_research/jin10/source_rights.md",
    "docs/news_research/earnings/roadmap.en.md",
    "docs/news_research/earnings/roadmap.zh.md",
    "reports/2026-11-15_jin10_mcp_intake.en.md",
    "reports/2026-11-15_jin10_mcp_intake.zh.md",
    "reports/2026-11-15_jin10_desktop_acceptance.en.md",
    "reports/2026-11-15_jin10_desktop_acceptance.zh.md",
]


def main() -> None:
    missing = []
    for name in FILES:
        path = ROOT / name
        text = path.read_text(encoding="utf-8")
        for target in re.findall(r"\[[^\]]*\]\(([^)]+)\)", text):
            if target.startswith(("https://", "http://", "mailto:", "#")):
                continue
            target = unquote(target.split("#", 1)[0])
            if target and not (path.parent / target).resolve().exists():
                missing.append((name, target))
    if missing:
        raise AssertionError(f"Missing relative links: {missing}")
    status = json.loads((ROOT / "docs/news_research/STATUS.json").read_text(encoding="utf-8"))
    probe = json.loads((RESULTS / "probe_redacted_run2.json").read_text(encoding="utf-8"))
    profile = json.loads((RESULTS / "probe_profile_run2.json").read_text(encoding="utf-8"))
    store = json.loads((RESULTS / "store_validation_v2.json").read_text(encoding="utf-8"))
    display = json.loads((RESULTS / "dashboard_static_validation_v2.json").read_text(encoding="utf-8"))
    guidance = json.loads((RESULTS / "guidance_v2/guidance_coverage_v2.json").read_text(encoding="utf-8"))
    native = json.loads((DESKTOP_RESULTS / "native_calendar_receipt.json").read_text(encoding="utf-8"))
    visual = json.loads((DESKTOP_RESULTS / "visual_acceptance_redacted.json").read_text(encoding="utf-8"))
    final_static = json.loads((DESKTOP_RESULTS / "dashboard_static_validation_final.json").read_text(encoding="utf-8"))
    assert status["version"] == "1.14"
    assert [(item["tool_name"], item["items"]) for item in probe["attempts"]] == [
        ("list_flash", 20), ("list_calendar", 213), ("list_flash", 20)]
    assert profile["calendar"]["missing_values"]["consensus"]["null"] == 129
    assert store["counts"]["logical_items"] == 253
    assert store["verified_fact_not_verified_records"] == 253
    assert display["related_flash_keyword_candidates_shown"] == 12
    assert display["calendar_rows_in_display_export"] == 50
    assert guidance["counts"]["guidance_rows"] == 19
    assert status["jin10_development"]["private_store_readback"].endswith("store_validation_v2.json")
    assert native["native_tool_name"] == "mcp__jin10__list_calendar"
    assert native["business_status"] == 200 and native["returned_row_count"] == 213
    assert native["retry_count"] == 0 and native["row_content_saved_or_published"] is False
    assert visual["private_html_sha256"] == final_static["private_local_html_sha256"]
    assert visual["visual_acceptance"] == "PASS_WITH_COSMETIC_LIMITATION"
    assert final_static["provider_flash_body_leaks_into_public_template"] == 0
    assert status["jin10_desktop_acceptance"]["native_calendar_calls_this_round"] == 1
    assert status["jin10_desktop_acceptance"]["native_calendar_rows_saved_to_private_store"] == 0
    with (RESULTS / "plan_execution.csv").open(encoding="utf-8", newline="") as stream:
        phases = list(csv.DictReader(stream))
    assert len(phases) == 8
    lines = ["DOC_VALIDATION_SCHEMA=jin10_docs_validation_desktop_v1",
             f"FILES_CHECKED={len(FILES)}", "markdown_relative_links PASS",
             "status_version_1_14 PASS", "probe_20_213_20_bound PASS",
             "calendar_null_consensus_129_bound PASS", "store_v2_253_bound PASS",
             "verified_fact_status_253_bound PASS", "display_12_50_bound PASS",
             "guidance_19_bound PASS", "ledger_eight_phases PASS",
             "desktop_native_213_once_not_saved PASS",
             "visual_private_html_hash_bound PASS", "public_template_leak_check PASS",
             "Source receipts: probe_redacted_run2.json; probe_profile_run2.json; "
             "store_validation_v2.json; dashboard_static_validation_v2.json; "
             "guidance_v2/guidance_coverage_v2.json.",
             "These are document checks, not additional provider calls or research experiments."]
    output = DESKTOP_RESULTS / "docs_validation.log"
    if output.exists():
        raise FileExistsError(output)
    output.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    print("\n".join(lines[:14]))


if __name__ == "__main__":
    main()
