"""Create a small, explicit, redacted v1.5d handoff for another coding agent.

Run after the public commit is pushed and read back. No private database, news
body, raw model response, account credential or screenshot is selected.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT.parent / "quant" / "handoffs" / "quant_personal_demo_v15d_20261116"
FILES = [
    "README.md",
    "apps/quant_portal/README.md",
    "docs/news_research/README.md",
    "docs/news_research/STATUS.json",
    "docs/news_research/CHANGELOG.md",
    "docs/news_research/market_observer/source_access.md",
    "reports/2026-11-16_quant_portal_personal_v15.en.md",
    "reports/2026-11-16_quant_portal_personal_v15.zh.md",
    "reports/2026-11-16_quant_portal_personal_v15d.en.md",
    "reports/2026-11-16_quant_portal_personal_v15d.zh.md",
    "results/quant_portal_personal_v15_20261116/model_run_redacted.json",
    "results/quant_portal_personal_v15_20261116/semantic_review_redacted.json",
    "results/quant_portal_personal_v15c_20261116/input_manifest_redacted.json",
    "results/quant_portal_personal_v15c_20261116/plan_execution.csv",
    "results/quant_portal_personal_v15c_20261116/run_config.json",
    "results/quant_portal_personal_v15c_20261116/run_redacted.json",
    "results/quant_portal_personal_v15c_20261116/semantic_review_redacted.json",
    "results/quant_portal_personal_v15c_20261116/semantic_review_reconciliation_redacted.json",
    "results/quant_portal_personal_v15c_20261116/token_preflight_redacted.json",
    "results/quant_portal_personal_v15d_20261116/input_manifest_redacted.json",
    "results/quant_portal_personal_v15d_20261116/plan_execution.csv",
    "results/quant_portal_personal_v15d_20261116/run_config.json",
    "results/quant_portal_personal_v15d_20261116/run_redacted.json",
    "results/quant_portal_personal_v15d_20261116/semantic_review_redacted.json",
    "results/quant_portal_personal_v15d_20261116/runtime_redacted.json",
    "results/quant_portal_personal_v15d_20261116/token_preflight_redacted.json",
    "results/quant_portal_personal_v15d_20261116/focused_tests.log",
    "results/quant_portal_personal_v15d_20261116/django_tests.log",
    "scripts/quant_personal_feed/analysis_contract_v15b.py",
    "scripts/quant_personal_feed/analysis_contract_v15c.py",
    "scripts/quant_personal_feed/analysis_contract_v15d.py",
    "scripts/quant_personal_feed/analysis_v15b.py",
    "scripts/quant_personal_feed/generate_v15b.py",
    "scripts/quant_personal_feed/generate_v15c.py",
    "scripts/quant_personal_feed/generate_v15d.py",
    "scripts/quant_personal_feed/preflight_v15c.py",
    "scripts/quant_personal_feed/preflight_v15d.py",
    "scripts/quant_personal_feed/review_v15d.py",
    "scripts/quant_personal_feed/run_v15c_batch.py",
    "scripts/quant_personal_feed/run_v15d_batch.py",
    "scripts/quant_personal_feed/runtime_v15d.py",
    "scripts/quant_personal_feed/view_v15.py",
    "tests/test_quant_personal_v15c.py",
    "tests/test_quant_personal_v15d.py",
]


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> None:
    target_zip = OUT.with_suffix(".zip")
    if OUT.exists() or target_zip.exists():
        raise FileExistsError("Handoff target already exists; preserve prior package")
    forbidden = ("/var/", "/local_only/", "/backups/", "raw_response", "screenshot")
    for rel in FILES:
        normalized = "/" + rel.replace("\\", "/").lower()
        if any(token in normalized for token in forbidden):
            raise ValueError(f"Private path in handoff list: {rel}")
        if not (ROOT / rel).is_file():
            raise FileNotFoundError(rel)
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    OUT.mkdir(parents=True)
    hashes: dict[str, str] = {}
    for rel in FILES:
        target = OUT / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / rel, target)
        hashes[rel] = sha(target.read_bytes())
    readme = (
        "# Personal Demo v1.5d redacted handoff\n\n"
        f"Repository: https://github.com/wuzhuheng007-spec/financial-text-embedding-stability\n"
        f"Branch: codex/sync-research-progress-20261113\nCommit: {commit}\n"
        "Date: 2026-11-16, Asia/Shanghai.\n\n"
        "Read order: reports/2026-11-16_quant_portal_personal_v15d.zh.md; "
        "reports/2026-11-16_quant_portal_personal_v15d.en.md; "
        "results/quant_portal_personal_v15d_20261116/plan_execution.csv; "
        "docs/news_research/STATUS.json; code and redacted receipts.\n\n"
        "Actual: 20 reused development messages, 30 v1.5d generations, 11 mechanical "
        "completions, two same-team source-compatible cards. Nine mechanical completions "
        "were rejected. Supplier reports remain unverified. The two approved full cards, "
        "all raw news/model text and private SQLite databases are deliberately absent. "
        "The continuous analysis worker still uses v1.5b; v1.5d was a frozen batch only. "
        "Moomoo English news is not connected to the personal feed. Browser visual QA "
        "for this revision is NOT_RUN. Financial v2.5 remains REJECTED_CANDIDATE; "
        "no financial prediction or trade ran.\n\n"
        "The full repository is needed to run these scripts because this package contains "
        "only changed and immediately supporting public files.\n"
    )
    (OUT / "HANDOFF_README.md").write_text(readme, encoding="utf-8")
    hashes["HANDOFF_README.md"] = sha((OUT / "HANDOFF_README.md").read_bytes())
    (OUT / "SHA256SUMS.json").write_text(
        json.dumps(hashes, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    with zipfile.ZipFile(target_zip, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for rel in sorted(hashes):
            archive.write(OUT / rel, rel)
        archive.write(OUT / "SHA256SUMS.json", "SHA256SUMS.json")
    with zipfile.ZipFile(target_zip) as archive:
        if archive.testzip() is not None:
            raise AssertionError("ZIP CRC failure")
        for rel, expected in hashes.items():
            if sha(archive.read(rel)) != expected:
                raise AssertionError(f"ZIP member hash mismatch: {rel}")
        archive.read("HANDOFF_README.md").decode("utf-8")
        archive.read("reports/2026-11-16_quant_portal_personal_v15d.zh.md").decode("utf-8")
    print(json.dumps({"zip": str(target_zip), "folder": str(OUT),
                      "members": len(hashes) + 1, "crc": "PASS",
                      "zip_sha256": sha(target_zip.read_bytes()), "commit": commit}))


if __name__ == "__main__":
    main()
