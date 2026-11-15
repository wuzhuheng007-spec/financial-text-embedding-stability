"""Check short-fact report links and receipt-bound status counts."""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from urllib.parse import unquote


ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results/news_shortfacts_20261115"
FILES = [
    "README.md",
    "docs/news_research/README.md",
    "docs/news_research/CHANGELOG.md",
    "docs/news_research/roadmap.en.md",
    "docs/news_research/roadmap.zh.md",
    "docs/news_research/earnings/roadmap.en.md",
    "docs/news_research/earnings/roadmap.zh.md",
    "reports/2026-11-15_news_shortfacts_qwen.en.md",
    "reports/2026-11-15_news_shortfacts_qwen.zh.md",
]


def main() -> None:
    checked_links = 0
    for name in FILES:
        path = ROOT / name
        body = path.read_text(encoding="utf-8")
        for target in re.findall(r"\[[^\]]*\]\(([^)]+)\)", body):
            if target.startswith(("https://", "http://", "mailto:", "#")):
                continue
            relative = unquote(target.split("#", 1)[0])
            if relative:
                assert (path.parent / relative).resolve().exists(), f"Broken link in {name}: {target}"
                checked_links += 1

    status = json.loads((ROOT / "docs/news_research/STATUS.json").read_text(encoding="utf-8"))
    run = json.loads((RESULTS / "run_receipt_redacted.json").read_text(encoding="utf-8"))
    validation = json.loads((RESULTS / "public_validation_summary_v2.json").read_text(encoding="utf-8"))
    manifest = json.loads((RESULTS / "input_manifest_redacted.json").read_text(encoding="utf-8"))
    correction = json.loads((RESULTS / "reference_correction_redacted.json").read_text(encoding="utf-8"))
    stage = status["shortfacts_qwen_development"]
    counts = validation["counts"]
    # This validates the frozen v1 result even when the shared news status
    # advances to a later stage; it is not a status-version pin.
    major, minor = (int(part) for part in status["version"].split(".", 1))
    assert (major, minor) >= (1, 16)
    assert manifest["task_count"] == len(manifest["tasks"]) == 6
    assert run["input_manifest_sha256"] == manifest["private_input_manifest_sha256"]
    assert run["model_revision"] == status["shortfacts_qwen_development"]["model_revision"]
    for key, count_key in (
        ("planned_tasks", "planned_tasks"),
        ("attempted", "attempted_tasks"),
        ("generation_started", "generation_started_tasks"),
        ("completed_responses", "completed_response_tasks"),
    ):
        assert stage[key] == run[key] == counts[count_key] == 6
    assert stage["strict_raw_json_valid"] == counts["strict_json_valid_tasks"] == 1
    assert stage["fenced_views_saved_and_schema_valid"] == counts["fenced_view_schema_valid_tasks"] == 5
    assert counts["strict_schema_valid_tasks"] == 1 and counts["schema_valid_tasks"] == 6
    assert stage["exact_quote_and_evidence_id_matches"] == counts["quote_present_facts"] == counts["valid_evidence_id_facts"] == 6
    assert stage["all_fields_correct"] == counts["all_fields_correct_facts"] == 0
    assert stage["missing_pre_run_codex_reference_facts"] == 8
    assert stage["pre_run_codex_reference_facts"] == counts["reference_facts"] == 14
    assert correction["primary_run_reference"] == "v1 FROZEN BEFORE INFERENCE"
    assert len(correction["changes"]) == 3
    with (RESULTS / "plan_execution.csv").open(encoding="utf-8", newline="") as stream:
        phases = list(csv.DictReader(stream))
    assert [row["stage"] for row in phases] == [f"P{i}" for i in range(6)]
    for language in ("en", "zh"):
        body = (ROOT / f"reports/2026-11-15_news_shortfacts_qwen.{language}.md").read_text(encoding="utf-8")
        assert "NOT_RUN" in body
        for number in ("1/6", "5/5", "6/6", "8/14", "12/12"):
            assert number in body, f"Missing reported bound {number} in {language}"
    print(f"PASS docs_files={len(FILES)} relative_links={checked_links} stages={len(phases)}")
    print("PASS run/input/status/validation counts and strict-versus-fenced denominator checks")
    print("PASS bilingual report bounds and reference correction disclosure")
    print("Document checks only; no provider, model or financial experiment ran here.")


if __name__ == "__main__":
    main()
