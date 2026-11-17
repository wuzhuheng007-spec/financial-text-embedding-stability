"""Redacted code/input hash ledger and one verified handoff ZIP."""
from __future__ import annotations

import hashlib
import json
import subprocess
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .preflight import PRIVATE, PUBLIC, ROOT, sha

CODE_INPUTS = [
    "configs/news_sentiment_factor_v44_20261117.json",
    "configs/news_sentiment_factor_v43_20261117.json",
    "configs/news_sentiment_factor_v42_us_calendar_2026.json",
    "configs/news_sentiment_factor_v43_security_mapping_20261117.json",
    "configs/quant_portal_personal_sessions_v13_20261116.json",
    "scripts/news_sentiment_factor_v42/worker.py",
    "scripts/news_sentiment_factor_v43/daily.py",
    "scripts/news_sentiment_factor_v44/__init__.py",
    "scripts/news_sentiment_factor_v44/preflight.py",
    "scripts/news_sentiment_factor_v44/price_pilot.py",
    "scripts/news_sentiment_factor_v44/labels.py",
    "scripts/news_sentiment_factor_v44/verify.py",
    "scripts/news_sentiment_factor_v44/audit.py",
    "scripts/news_sentiment_factor_v44/qualification.py",
    "scripts/news_sentiment_factor_v44/finalize.py",
    "scripts/quant_personal_feed/session_poll.py",
    "apps/quant_portal/core/research_chinese_cache.py",
    "apps/quant_portal/static/core/research_sentiment_v4.js",
    "apps/quant_portal/templates/core/dashboard.html",
]
SHAREABLE = CODE_INPUTS + [
    "reports/2026-11-17_news_sentiment_factor_v44.zh.md",
    "reports/2026-11-17_news_sentiment_factor_v44.en.md",
    "docs/news_research/news_sentiment_v44_handoff.md",
    "docs/news_research/README.md",
    "docs/news_research/STATUS.json",
    "docs/news_research/CHANGELOG.md",
    "results/news_sentiment_factor_v44_20261117/plan_execution.csv",
    "results/news_sentiment_factor_v44_20261117/preflight_redacted.json",
    "results/news_sentiment_factor_v44_20261117/preflight_hash_correction_redacted.json",
    "results/news_sentiment_factor_v44_20261117/price_pilot_redacted.json",
    "results/news_sentiment_factor_v44_20261117/price_supplement_redacted.json",
    "results/news_sentiment_factor_v44_20261117/price_qualification_redacted.json",
    "results/news_sentiment_factor_v44_20261117/runtime_audit_pre_restart_redacted.json",
    "results/news_sentiment_factor_v44_20261117/runtime_audit_midrun_redacted.json",
    "results/news_sentiment_factor_v44_20261117/runtime_audit_redacted.json",
    "results/news_sentiment_factor_v44_20261117/v43_regression_checks_redacted.json",
    "results/news_sentiment_factor_v44_20261117/v44_targeted_checks_redacted.json",
    "results/news_sentiment_factor_v44_20261117/django_checks_redacted.log",
    "results/news_sentiment_factor_v44_20261117/software_checks_redacted.json",
    "results/news_sentiment_factor_v44_20261117/service_status_redacted.json",
    "results/news_sentiment_factor_v44_20261117/postrun_hashes_redacted.json",
]


def postrun_hashes() -> dict:
    original = sorted((PRIVATE / "backups").glob("*/preflight_private.json"))[-1]
    receipt = json.loads(original.read_text(encoding="utf-8"))
    data = {
        "schema": "news_sentiment_v44_postrun_hashes_redacted_1",
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "code_and_config": [{"path": path, "sha256": sha(ROOT / path)} for path in CODE_INPUTS],
        "online_backup_sha256": {row["name"]: row["sha256"] for row in receipt["backups"] if "sha256" in row},
        "frozen_pre_edit_input_list": [{"path": row["path"], "sha256": row["sha256"]} for row in receipt["inputs"]],
        "private_raw_news_and_price_not_included": True,
    }
    PUBLIC.mkdir(parents=True, exist_ok=True)
    (PUBLIC / "postrun_hashes_redacted.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"code_and_config_files": len(data["code_and_config"]),
            "backup_hashes": len(data["online_backup_sha256"]),
            "frozen_input_files": len(data["frozen_pre_edit_input_list"])}


def package(destination: Path) -> dict:
    if destination.exists():
        raise FileExistsError("One handoff ZIP already exists; choose a new name")
    if len(SHAREABLE) != len(set(SHAREABLE)):
        raise ValueError("DUPLICATE_MEMBER_PATH")
    members = []
    for relative in SHAREABLE:
        if relative.startswith("artifacts/") or ".." in Path(relative).parts:
            raise ValueError("PRIVATE_OR_UNSAFE_MEMBER")
        source = ROOT / relative
        if not source.is_file():
            raise FileNotFoundError(relative)
        payload = source.read_bytes()
        if source.suffix.lower() in {".py", ".json", ".md", ".csv", ".js", ".html", ".log"}:
            payload.decode("utf-8")
        members.append({"path": relative, "sha256": hashlib.sha256(payload).hexdigest(),
                        "bytes": len(payload)})
    now = datetime.now(timezone.utc)
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    manifest = {"schema": "news_sentiment_v44_handoff_manifest_1",
                "created_utc": now.isoformat(),
                "created_asia_shanghai": now.astimezone(ZoneInfo("Asia/Shanghai")).isoformat(),
                "git_head_at_package": head,
                "files": members,
                "manifest_excludes_itself": True}
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for item in members:
            archive.write(ROOT / item["path"], item["path"])
        archive.writestr("MANIFEST.sha256.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    with zipfile.ZipFile(destination) as archive:
        paths = archive.namelist()
        if len(paths) != len(set(paths)) or archive.testzip() is not None:
            raise ValueError("ZIP_DUPLICATE_OR_CRC_FAILURE")
        check = json.loads(archive.read("MANIFEST.sha256.json").decode("utf-8"))
        if len(check["files"]) + 1 != len(paths):
            raise ValueError("MANIFEST_COVERAGE_FAILURE")
        for item in check["files"]:
            payload = archive.read(item["path"])
            if len(payload) != item["bytes"] or hashlib.sha256(payload).hexdigest() != item["sha256"]:
                raise ValueError("ZIP_MEMBER_HASH_FAILURE")
            if Path(item["path"]).suffix.lower() in {".py", ".json", ".md", ".csv", ".js", ".html", ".log"}:
                payload.decode("utf-8")
    result = {"schema": "news_sentiment_v44_handoff_validation_1",
              "zip_path": str(destination), "zip_sha256": sha(destination),
              "zip_entries": len(paths), "unique_paths": len(set(paths)),
              "file_entries": len(paths), "manifest_file_members": len(members),
              "crc": "PASS", "sha256": "PASS", "utf8": "PASS",
              "git_head_at_package": head}
    (PUBLIC / "handoff_validation_local.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["hashes", "package"])
    parser.add_argument("--destination", type=Path)
    args = parser.parse_args()
    if args.action == "hashes":
        print(json.dumps(postrun_hashes(), ensure_ascii=False))
    else:
        if args.destination is None:
            raise ValueError("--destination required")
        print(json.dumps(package(args.destination), ensure_ascii=False))
