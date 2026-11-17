"""Package an allowlisted, UTF-8, private-data-free v4.2 handoff."""
from __future__ import annotations

import hashlib
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from .worker import PUBLIC, ROOT, write_json

OUT=Path.home()/"Documents"/"ChatGPT"/"quant"/"handoffs"
NAME="news_sentiment_factor_v42_20261117"

FILES=[
 "README.md", "docs/news_research/STATUS.json", "docs/news_research/CHANGELOG.md",
 "reports/2026-11-17_news_sentiment_factor_v42.zh.md",
 "reports/2026-11-17_news_sentiment_factor_v42.en.md",
 "configs/news_sentiment_factor_v41_20261117_r2.json",
 "configs/news_sentiment_factor_v42_20261117.json",
 "configs/news_sentiment_factor_v42_aliases_20261117.json",
 "configs/news_sentiment_factor_v42_snapshot_r2_20261117.json",
 "configs/news_sentiment_factor_v42_us_calendar_2026.json",
 "configs/news_sentiment_factor_v42_future_evaluation_20261117.json",
 "apps/quant_portal/core/research_chinese_cache.py",
 "apps/quant_portal/static/core/research_sentiment_v4.js",
 "apps/quant_portal/static/core/portal.css",
 "apps/quant_portal/templates/core/dashboard.html",
 "results/news_sentiment_factor_v42_20261117/plan_execution.csv",
 "results/news_sentiment_factor_v42_20261117/association_review_redacted.json",
 "results/news_sentiment_factor_v42_20261117/association_review_v2_redacted.json",
 "results/news_sentiment_factor_v42_20261117/fault_drills_redacted.json",
 "results/news_sentiment_factor_v42_20261117/fault_drill_revision_redacted.json",
 "results/news_sentiment_factor_v42_20261117/metadata_correction_redacted.json",
 "results/news_sentiment_factor_v42_20261117/snapshot_checks_redacted.json",
 "results/news_sentiment_factor_v42_20261117/final_counts_redacted.json",
 "results/news_sentiment_factor_v42_20261117/browser_acceptance_redacted.json",
 "results/news_sentiment_factor_v42_20261117/single_instance_live_redacted.json",
 "results/news_sentiment_factor_v42_20261117/evidence_index_redacted.json",
 "results/news_sentiment_factor_v42_20261117/final_runtime_redacted.json",
]
FILES += [str(p.relative_to(ROOT)).replace("\\","/") for p in
          sorted((ROOT/"scripts/news_sentiment_factor_v42").glob("*.py"))]
FILES += [str(p.relative_to(ROOT)).replace("\\","/") for p in
          sorted(PUBLIC.glob("snapshot_*_redacted.json"))]


def package() -> dict:
    folder=OUT/NAME
    archive_path=OUT/(NAME+".zip")
    if folder.exists() or archive_path.exists():
        raise FileExistsError("HANDOFF_DESTINATION_ALREADY_EXISTS")
    # Validate every member before creating a partial handoff directory.
    validated=[]
    for relative in FILES:
        source=ROOT/relative
        if not source.is_file():
            raise FileNotFoundError(relative)
        payload=source.read_bytes()
        payload.decode("utf-8")
        private_home=str(Path.home()).encode("utf-8").lower()
        if private_home in payload.lower() or private_home.replace(b"\\",b"/") in payload.lower():
            raise ValueError("PRIVATE_ABSOLUTE_PATH_IN_MEMBER:"+relative)
        if relative.endswith((".sqlite3",".db",".png",".jpg")):
            raise ValueError("PRIVATE_DATA_EXTENSION:"+relative)
        validated.append((relative,payload))
    folder.mkdir(parents=True)
    manifest=[]
    for relative,payload in validated:
        target=folder/relative
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_bytes(payload)
        manifest.append({"path":relative,"sha256":hashlib.sha256(payload).hexdigest(),
                         "bytes":len(payload),"utf8":True})
    readme=folder/"HANDOFF_README.md"
    readme.write_text(
        "# News sentiment v4.2 handoff\n\n"
        "Use the Chinese/English reports and P0–P7 ledger first. This package contains code, "
        "frozen configs and aggregate receipts. The local private feed, research scores, "
        "database backups, snapshot membership, account credentials and provider text are absent.\n\n"
        "The v4.1 scoring model and text segmentation remain unchanged. The v4.2 worker "
        "uses a durable queue and an append-only snapshot store. The first development replay "
        "is a rejected diagnostic; the other replays are not prospective signals. The true "
        "America/New_York 16:15 prospective company snapshot had not occurred at handoff.\n\n"
        "Run from the original repository root with the pinned local research environment. "
        "Do not treat this ZIP as containing the licensed/private data needed to replay inference.\n",
        encoding="utf-8")
    manifest.append({"path":"HANDOFF_README.md","sha256":hashlib.sha256(readme.read_bytes()).hexdigest(),
                     "bytes":readme.stat().st_size,"utf8":True})
    manifest_path=folder/"MANIFEST.json"
    manifest_path.write_text(json.dumps({"schema":"news_sentiment_v42_handoff_manifest_1",
                                         "created_at_utc":datetime.now(timezone.utc).isoformat(),
                                         "members":manifest},ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    with zipfile.ZipFile(archive_path,"x",compression=zipfile.ZIP_DEFLATED) as output:
        for path in sorted(folder.rglob("*")):
            if path.is_file():
                output.write(path,path.relative_to(folder).as_posix())
    with zipfile.ZipFile(archive_path) as saved:
        if saved.testzip() is not None:
            raise ValueError("ZIP_CRC_FAILED")
        for item in manifest:
            content=saved.read(item["path"])
            if hashlib.sha256(content).hexdigest()!=item["sha256"]:
                raise ValueError("ZIP_MEMBER_HASH_FAILED:"+item["path"])
            content.decode("utf-8")
        saved.read("MANIFEST.json").decode("utf-8")
    result={"schema":"news_sentiment_v42_handoff_validation_1",
            "created_at_utc":datetime.now(timezone.utc).isoformat(),
            "zip_name":archive_path.name,"member_count":len(manifest)+1,
            "zip_sha256":hashlib.sha256(archive_path.read_bytes()).hexdigest(),
            "crc":"PASS","member_sha256":"PASS","utf8":"PASS",
            "private_database_raw_news_scores_credentials_included":False}
    write_json(PUBLIC/"handoff_validation_redacted.json",result)
    return result


if __name__=="__main__":
    print(json.dumps(package(),ensure_ascii=False))
