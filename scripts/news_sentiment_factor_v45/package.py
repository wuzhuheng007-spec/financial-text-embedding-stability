"""Build and verify one small redacted handoff ZIP outside the repository."""
from __future__ import annotations

import hashlib
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .preflight import PUBLIC, ROOT, dump, sha

DESTINATION = ROOT.parent / "quant" / "handoffs" / "news_sentiment_factor_v45_20261117.zip"
STATIC_PATHS = [
    "configs/news_sentiment_factor_v45_20261117.json",
    "configs/news_sentiment_factor_v44_20261117.json",
    "configs/news_sentiment_factor_v42_us_calendar_2026.json",
    "configs/news_sentiment_factor_v43_security_mapping_20261117.json",
    "scripts/news_sentiment_factor_v44/labels.py",
    "scripts/news_sentiment_factor_v43/daily.py",
    "apps/quant_portal/core/research_chinese_cache.py",
    "apps/quant_portal/static/core/research_sentiment_v4.js",
    "apps/quant_portal/templates/core/dashboard.html",
    "docs/news_research/README.md",
    "docs/news_research/STATUS.json",
    "docs/news_research/CHANGELOG.md",
    "reports/2026-11-17_news_sentiment_factor_v45.zh.md",
    "reports/2026-11-17_news_sentiment_factor_v45.en.md",
    "reports/2026-11-17_moomoo_rest_439_support_draft.zh.md",
]
RESULT_FILES = [
    "preflight_redacted.json", "metadata_redacted.json",
    "metadata_scope_correction_redacted.json", "protocol_amendment_redacted.json",
    "snapshot_acceptance_redacted.json", "price_qualification_redacted.json",
    "label_gate_redacted.json", "rest_439_redacted.json",
    "software_checks_redacted.json", "browser_acceptance_redacted.json",
    "postrun_hashes_redacted.json", "final_runtime_redacted.json",
    "plan_execution.csv", "final_remote_readback_redacted.json",
]
TEXT_SUFFIXES = {".py", ".json", ".csv", ".md", ".js", ".html"}


def run() -> dict:
    at = datetime.now(timezone.utc)
    paths = STATIC_PATHS + [p.relative_to(ROOT).as_posix()
                           for p in sorted((ROOT / "scripts/news_sentiment_factor_v45").glob("*.py"))]
    paths += [f"results/news_sentiment_factor_v45_20261117/{name}" for name in RESULT_FILES]
    if len(paths) != len(set(paths)):
        raise ValueError("DUPLICATE_HANDOFF_PATH")
    paths = sorted(paths)
    manifest_rows = []
    for relative in paths:
        path = ROOT / relative
        if not path.is_file():
            raise FileNotFoundError(relative)
        data = path.read_bytes()
        if path.suffix in TEXT_SUFFIXES:
            data.decode("utf-8-sig")
        manifest_rows.append({"path": relative, "bytes": len(data),
                              "sha256": hashlib.sha256(data).hexdigest()})
    manifest = {"schema": "news_sentiment_factor_v45_handoff_manifest_1",
                "created_at_utc": at.isoformat(),
                "created_at_asia_shanghai": at.astimezone(ZoneInfo("Asia/Shanghai")).isoformat(),
                "contains_private_raw_news_prices_databases_credentials_or_weights": False,
                "files": manifest_rows}
    DESTINATION.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(DESTINATION, "w", compression=zipfile.ZIP_DEFLATED,
                         compresslevel=9) as archive:
        for row in manifest_rows:
            archive.write(ROOT / row["path"], row["path"])
        archive.writestr("HANDOFF_MANIFEST.json",
                         json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    with zipfile.ZipFile(DESTINATION) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)) or archive.testzip() is not None:
            raise ValueError("DUPLICATE_OR_BAD_CRC")
        expected = {row["path"] for row in manifest_rows} | {"HANDOFF_MANIFEST.json"}
        if set(names) != expected:
            raise ValueError("MANIFEST_COVERAGE_MISMATCH")
        embedded = json.loads(archive.read("HANDOFF_MANIFEST.json"))
        if embedded != manifest:
            raise ValueError("MANIFEST_CONTENT_MISMATCH")
        for row in manifest_rows:
            data = archive.read(row["path"])
            if len(data) != row["bytes"] or hashlib.sha256(data).hexdigest() != row["sha256"]:
                raise ValueError("MEMBER_HASH_MISMATCH")
            if Path(row["path"]).suffix in TEXT_SUFFIXES:
                data.decode("utf-8-sig")
    result = {"schema": "news_sentiment_factor_v45_handoff_validation_redacted_1",
              "created_at_utc": at.isoformat(),
              "created_at_asia_shanghai": at.astimezone(ZoneInfo("Asia/Shanghai")).isoformat(),
              "zip_path": str(DESTINATION), "zip_sha256": sha(DESTINATION),
              "zip_entries": len(names), "file_members": len(manifest_rows),
              "manifest_unique_file_paths": len({row["path"] for row in manifest_rows}),
              "crc_ok": True, "sha256_and_utf8_ok": True,
              "manifest_member_coverage_ok": True, "private_source_files_included": False}
    dump(PUBLIC / "handoff_validation_redacted.json", result)
    return result


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False))
