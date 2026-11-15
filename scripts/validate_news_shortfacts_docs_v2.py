"""Check v2 shortfacts public links and evidence-bound report counters.

This is a documentation check. It does not rerun the model or score content.
"""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from urllib.parse import unquote


ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / "results/news_shortfacts_v2_20261115"
DOCUMENTS = (
    "README.md",
    "docs/news_research/README.md",
    "docs/news_research/CHANGELOG.md",
    "reports/2026-11-15_news_shortfacts_v2.en.md",
    "reports/2026-11-15_news_shortfacts_v2.zh.md",
)


def main() -> None:
    link_count = 0
    for name in DOCUMENTS:
        path = ROOT / name
        body = path.read_text(encoding="utf-8")
        for target in re.findall(r"\[[^\]]*\]\(([^)]+)\)", body):
            if target.startswith(("http://", "https://", "mailto:", "#")):
                continue
            relative = unquote(target.split("#", 1)[0])
            if relative:
                assert (path.parent / relative).resolve().exists(), (name, target)
                link_count += 1

    status = json.loads((ROOT / "docs/news_research/STATUS.json").read_text(encoding="utf-8"))
    stage = status["shortfacts_v2_development"]
    contract = json.loads((PUBLIC / "contract_receipt.json").read_text(encoding="utf-8"))
    frozen = json.loads((PUBLIC / "pre_run_freeze_receipt.json").read_text(encoding="utf-8"))
    old = json.loads((PUBLIC / "retrospective_summary_redacted.json").read_text(encoding="utf-8"))
    new = json.loads((ROOT / stage["new_run_summary_redacted"]).read_text(encoding="utf-8"))
    assert status["timezone"] == "Asia/Shanghai"
    assert contract["input_manifest_sha256"] == frozen["input_manifest_sha256"] == old["manifest_sha256"] == new["manifest_sha256"]
    assert contract["reference_v3_sha256"] == frozen["private_reference_sha256"] == old["reference_sha256"] == new["reference_sha256"]
    assert contract["schema_sha256"] == frozen["schema_sha256"] == old["schema_sha256"] == new["schema_sha256"]
    assert stage["model_revision"] == frozen["model_revision"]
    assert old["counts"]["reference_facts"] == new["counts"]["reference_facts"] == contract["total_targets"]
    assert old["counts"]["planned_tasks"] == new["counts"]["planned_tasks"] == 6
    for item in (old, new):
        c = item["counts"]
        assert 0 <= c["completed_response_tasks"] <= c["attempted_tasks"] <= 6
        assert c["matched_reference_facts"] + c["unmatched_reference_facts"] == c["reference_facts"]
        assert 0 <= c["fully_correct_facts"] <= c["matched_reference_facts"]
        assert c["strict_raw_schema_valid_tasks"] <= c["schema_valid_after_optional_adapter_tasks"]
        assert c["strict_raw_json_valid_tasks"] + c["fence_adapted_tasks"] == c["json_valid_after_optional_adapter_tasks"]
        assert item["complete_correct_recall"] == {"numerator": c["fully_correct_facts"], "denominator": c["reference_facts"]}
    for key in ("attempted_tasks", "generation_started_tasks", "completed_response_tasks", "fully_correct_facts"):
        assert stage[key] == new["counts"][key]
    with (PUBLIC / "plan_execution.csv").open(encoding="utf-8", newline="") as handle:
        plan = list(csv.DictReader(handle))
    assert [row["stage"] for row in plan] == [f"P{i}" for i in range(8)]
    assert all(row["status"] in {"COMPLETE", "PARTIAL", "BLOCKED"} for row in plan)
    for language in ("en", "zh"):
        body = (ROOT / f"reports/2026-11-15_news_shortfacts_v2.{language}.md").read_text(encoding="utf-8")
        assert "NOT_RUN" in body
        assert "CODEX_REFERENCE_DRAFT" in body
    print(f"PASS docs={len(DOCUMENTS)} local_links={link_count} plan_stages={len(plan)}")
    print("PASS source/config/reference and old-versus-new denominator bindings")
    print("Documentation check only; no model or financial task ran here.")


if __name__ == "__main__":
    main()
