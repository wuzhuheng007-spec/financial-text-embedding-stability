"""Write a non-content evidence manifest for the bounded Jin10 round."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results/news_jin10_20261115"
CODE = [
    "scripts/build_earnings_dashboard.py", "scripts/build_jin10_dashboard_v2.py",
    "scripts/jin10_guidance_v2.py", "scripts/jin10_mcp_http.py",
    "scripts/jin10_probe_once.py", "scripts/jin10_profile_probe.py",
    "scripts/jin10_confirm_shapes.py", "scripts/jin10_validate_local_store.py",
    "scripts/jin10_validate_dashboard.py", "scripts/jin10_validate_docs.py",
    "scripts/jin10_review_staged.py", "scripts/jin10_mcp/schema.py",
    "scripts/jin10_mcp/store.py", "scripts/jin10_mcp/engine.py",
    "scripts/jin10_mcp/export.py", "scripts/jin10_mcp/cli.py",
]
TESTS = [
    "tests/test_jin10_mcp_http.py", "tests/test_jin10_probe.py",
    "tests/test_jin10_ingest.py", "tests/test_jin10_guidance_v2.py",
    "tests/test_jin10_dashboard_v2.py",
]
RECEIPTS = [
    "results/news_jin10_20261115/probe_redacted_run2.json",
    "results/news_jin10_20261115/probe_profile_run2.json",
    "results/news_jin10_20261115/shape_binding_receipt.json",
    "results/news_jin10_20261115/store_validation_v2.json",
    "results/news_jin10_20261115/dashboard_static_validation_v2.json",
    "results/news_jin10_20261115/guidance_v2/guidance_coverage_v2.json",
    "results/news_jin10_20261115/final_validation_v2.log",
    "results/news_jin10_20261115/legacy_dashboard_regression.log",
    "results/news_jin10_20261115/docs_validation_v2.log",
]
FROZEN_INPUTS = [
    "results/news_earnings_macro_20261115/company_cards.json",
    "results/news_earnings_macro_20261115/macro_cards.json",
    "results/news_earnings_macro_20261115/news_events.json",
    "results/news_earnings_macro_20261115/assembly_receipt.json",
]


def hash_files(names: list[str]) -> dict[str, str]:
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in names}


def main() -> None:
    output = RESULTS / "execution_manifest.json"
    if output.exists():
        raise FileExistsError(output)
    profile = json.loads((RESULTS / "probe_profile_run2.json").read_text(encoding="utf-8"))
    manifest = {
        "schema": "jin10_bounded_execution_manifest_v1",
        "created_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "report_timezone": "Asia/Shanghai",
        "remote_baseline_commit": "3620fc97f707ea6a5ff8818b092900e9dbacd14c",
        "private_probe_capture_sha256": profile["input_sha256"],
        "code_sha256": hash_files(CODE),
        "focused_test_source_sha256": hash_files(TESTS),
        "frozen_old_input_sha256": hash_files(FROZEN_INPUTS),
        "sanitized_receipt_sha256": hash_files(RECEIPTS),
        "private_provider_text_included": False,
        "interpretation": "File identity map only; hashes do not establish source completeness or financial validity",
    }
    output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                      encoding="utf-8", newline="\n")
    print(json.dumps({"manifest": output.relative_to(ROOT).as_posix(),
                      "code_files": len(CODE), "test_files": len(TESTS),
                      "receipt_files": len(RECEIPTS)}))


if __name__ == "__main__":
    main()
