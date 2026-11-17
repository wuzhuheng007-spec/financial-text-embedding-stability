"""One-time v4.3 run lock; private database and article hashes stay local."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from scripts.news_sentiment_factor_v41.freeze import ROOT, sha
from scripts.news_sentiment_factor_v42.worker import write_json
from .daily import CFG_PATH, MAPPING_PATH

PUBLIC = ROOT / "results/news_sentiment_factor_v43_20261117"
PRIVATE = ROOT / "artifacts/news_sentiment_factor_v43_20261117"
BACKUP = PRIVATE / "backups/20261117T081417Z/backup_private.json"
FILES = [
    "configs/news_sentiment_factor_v43_20261117.json",
    "configs/news_sentiment_factor_v43_security_mapping_20261117.json",
    "configs/news_sentiment_factor_v41_20261117_r2.json",
    "configs/news_sentiment_factor_v42_20261117.json",
    "configs/news_sentiment_factor_v42_aliases_20261117.json",
    "configs/news_sentiment_factor_v42_us_calendar_2026.json",
    "configs/news_sentiment_factor_v42_snapshot_r2_20261117.json",
    "scripts/news_sentiment_factor_v43/daily.py",
    "scripts/news_sentiment_factor_v43/verify_daily.py",
    "scripts/news_sentiment_factor_v43/audit.py",
    "scripts/news_sentiment_factor_v43/freeze.py",
    "scripts/news_sentiment_factor_v42/worker.py",
    "scripts/news_sentiment_factor_v42/snapshot.py",
    "scripts/news_sentiment_factor_v42/association.py",
    "scripts/news_sentiment_factor_v41/score.py",
    "scripts/news_sentiment_factor_v41/prepare_v2.py",
    "apps/quant_portal/core/research_chinese_cache.py",
    "apps/quant_portal/static/core/research_sentiment_v4.js",
    "apps/quant_portal/templates/core/dashboard.html",
]


def run() -> dict:
    if (PUBLIC / "run_lock_redacted.json").exists():
        raise FileExistsError("V43_RUN_LOCK_ALREADY_EXISTS")
    when = datetime.now(timezone.utc)
    code = {name: sha(ROOT / name) for name in FILES}
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    diff = subprocess.check_output(["git", "diff", "--binary"], cwd=ROOT)
    with closing(sqlite3.connect(f"file:{(ROOT / 'artifacts/news_sentiment_factor_v41_20261117/chinese_tone.sqlite3').as_posix()}?mode=ro",
                                 uri=True)) as con:
        rows = con.execute("SELECT provider,article_id,version,source_text_hash,received_at_utc FROM articles ORDER BY provider,article_id,version").fetchall()
    private_manifest_hash = hashlib.sha256(json.dumps(rows, ensure_ascii=False).encode("utf-8")).hexdigest()
    backup = json.loads(BACKUP.read_text(encoding="utf-8"))
    private = {"schema": "news_sentiment_v43_run_lock_private_1", "locked_at_utc": when.isoformat(),
               "locked_at_asia_shanghai": when.astimezone(ZoneInfo("Asia/Shanghai")).isoformat(),
               "head": head, "tracked_diff_sha256": hashlib.sha256(diff).hexdigest(),
               "code_config_sha256": code, "private_article_manifest_sha256": private_manifest_hash,
               "private_backup_receipt": backup}
    PRIVATE.mkdir(parents=True, exist_ok=True)
    write_json(PRIVATE / "run_lock_private.json", private)
    redacted = {k: private[k] for k in ("schema", "locked_at_utc", "locked_at_asia_shanghai",
                                         "head", "tracked_diff_sha256", "code_config_sha256")}
    redacted["private_article_manifest_sha256"] = private_manifest_hash
    redacted["private_backup_integrity"] = "THREE_SQLITE_BACKUPS_PRAGMA_OK"
    redacted["no_new_model_fit_or_supplier_request"] = True
    write_json(PUBLIC / "run_lock_redacted.json", redacted)
    return redacted


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False))
