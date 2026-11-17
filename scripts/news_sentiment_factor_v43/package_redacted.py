"""Build and verify an allowlisted v4.3 handoff without private inputs."""
from __future__ import annotations

import hashlib
import json
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from scripts.news_sentiment_factor_v41.freeze import ROOT

OUT = ROOT.parent / "quant" / "handoffs"
NAME = "news_sentiment_factor_v43_20261117_shareable"
ALLOW = [
    "README.md",
    "configs/news_sentiment_factor_v41_20261117_r2.json",
    "configs/news_sentiment_factor_v42_20261117.json",
    "configs/news_sentiment_factor_v42_aliases_20261117.json",
    "configs/news_sentiment_factor_v42_snapshot_r2_20261117.json",
    "configs/news_sentiment_factor_v42_us_calendar_2026.json",
    "configs/news_sentiment_factor_v43_20261117.json",
    "configs/news_sentiment_factor_v43_security_mapping_20261117.json",
    "scripts/news_sentiment_factor_v41/freeze.py",
    "scripts/news_sentiment_factor_v41/prepare_v2.py",
    "scripts/news_sentiment_factor_v41/score.py",
    "scripts/news_sentiment_factor_v42/association.py",
    "scripts/news_sentiment_factor_v42/snapshot.py",
    "scripts/news_sentiment_factor_v42/worker.py",
    "scripts/news_sentiment_factor_v43/__init__.py",
    "scripts/news_sentiment_factor_v43/audit.py",
    "scripts/news_sentiment_factor_v43/daily.py",
    "scripts/news_sentiment_factor_v43/freeze.py",
    "scripts/news_sentiment_factor_v43/final_runtime.py",
    "scripts/news_sentiment_factor_v43/verify_daily.py",
    "scripts/news_sentiment_factor_v43/package_redacted.py",
    "apps/quant_portal/core/research_chinese_cache.py",
    "apps/quant_portal/static/core/research_sentiment_v4.js",
    "apps/quant_portal/templates/core/dashboard.html",
    "docs/news_research/STATUS.json",
    "docs/news_research/CHANGELOG.md",
    "reports/2026-11-17_news_sentiment_factor_v43.zh.md",
    "reports/2026-11-17_news_sentiment_factor_v43.en.md",
    "results/news_sentiment_factor_v43_20261117/plan_execution.csv",
    "results/news_sentiment_factor_v43_20261117/v42_zip_count_correction_redacted.json",
    "results/news_sentiment_factor_v43_20261117/runtime_audit_redacted.json",
    "results/news_sentiment_factor_v43_20261117/run_lock_redacted.json",
    "results/news_sentiment_factor_v43_20261117/postfreeze_revision_redacted.json",
    "results/news_sentiment_factor_v43_20261117/postfreeze_mapping_revision_redacted.json",
    "results/news_sentiment_factor_v43_20261117/postfreeze_audit_path_revision_redacted.json",
    "results/news_sentiment_factor_v43_20261117/targeted_checks_redacted.json",
    "results/news_sentiment_factor_v43_20261117/browser_acceptance_redacted.json",
    "results/news_sentiment_factor_v43_20261117/first_cutoff_acceptance_redacted.json",
    "results/news_sentiment_factor_v43_20261117/final_runtime_redacted.json",
    "results/watchlist_live_20261115/security_mapping_combined_redacted.json",
]


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def run() -> dict:
    if len(ALLOW) != len(set(ALLOW)):
        raise ValueError("DUPLICATE_ALLOWLIST_PATH")
    forbidden = ("artifacts/", "/var/", "private", "raw_", "quote", "credential")
    for name in ALLOW:
        low = name.lower()
        if any(term in low for term in forbidden):
            raise ValueError(f"DISALLOWED_PATH:{name}")
    folder = OUT / NAME
    package = OUT / (NAME + ".zip")
    # A repeat build may replace only this round's own generated handoff.
    # Check both resolved targets before touching any existing output.
    out_resolved = OUT.resolve()
    for target in (folder, package):
        if target.exists() and target.resolve().parent != out_resolved:
            raise ValueError("HANDOFF_TARGET_OUTSIDE_EXPECTED_DIRECTORY")
    manifest_rows = []
    for name in ALLOW:
        src = ROOT / name
        if not src.is_file() or not src.resolve().is_relative_to(ROOT.resolve()):
            raise FileNotFoundError(name)
        raw = src.read_bytes()
        raw.decode("utf-8-sig")
        manifest_rows.append({"path": name.replace("\\", "/"), "sha256": sha(raw), "bytes": len(raw)})
    manifest = {"schema": "news_sentiment_v43_unique_handoff_manifest_1",
                "created_at_utc": datetime.now(timezone.utc).isoformat(),
                "file_rows": manifest_rows}
    manifest_raw = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    OUT.mkdir(parents=True, exist_ok=True)
    folder.mkdir(exist_ok=True)
    for row in manifest_rows:
        dst = folder / row["path"]
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / row["path"], dst)
    (folder / "manifest.sha256.json").write_bytes(manifest_raw)
    with zipfile.ZipFile(package, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for row in manifest_rows:
            z.write(folder / row["path"], row["path"])
        z.writestr("manifest.sha256.json", manifest_raw)
    with zipfile.ZipFile(package) as z:
        infos = z.infolist()
        names = [x.filename for x in infos]
        if z.testzip() is not None or len(names) != len(set(names)):
            raise ValueError("CRC_OR_DUPLICATE_ZIP_PATH")
        actual_manifest = json.loads(z.read("manifest.sha256.json").decode("utf-8"))
        if len(actual_manifest["file_rows"]) != len({x["path"] for x in actual_manifest["file_rows"]}):
            raise ValueError("DUPLICATE_MANIFEST_PATH")
        for row in actual_manifest["file_rows"]:
            raw = z.read(row["path"])
            if sha(raw) != row["sha256"] or len(raw) != row["bytes"]:
                raise ValueError("MEMBER_HASH_MISMATCH")
            raw.decode("utf-8-sig")
    zh = (folder / "reports/2026-11-17_news_sentiment_factor_v43.zh.md").read_text(encoding="utf-8")
    if "日度窗口" not in zh or "实际可用时间" not in zh:
        raise ValueError("CHINESE_REPORT_ENCODING_CHECK_FAILED")
    receipt = {"schema": "news_sentiment_v43_handoff_validation_1",
               "checked_at_utc": datetime.now(timezone.utc).isoformat(),
               "zip_location": "quant/handoffs/" + package.name,
               "zip_sha256": sha(package.read_bytes()),
               "zip_total_entries": len(infos),
               "zip_file_entries": sum(not x.is_dir() for x in infos),
               "zip_unique_paths": len(set(names)),
               "manifest_file_rows": len(manifest_rows),
               "manifest_unique_paths": len({x["path"] for x in manifest_rows}),
               "crc": "PASS", "member_sha256": "PASS", "utf8_and_chinese": "PASS",
               "private_inputs_in_allowlist": 0}
    (ROOT / "results/news_sentiment_factor_v43_20261117/handoff_validation_redacted.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return receipt


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False))
