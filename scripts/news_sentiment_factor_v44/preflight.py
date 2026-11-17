"""Online private SQLite backups and immutable input manifest before v4.4 edits."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
PRIVATE = ROOT / "artifacts/news_sentiment_factor_v44_20261117"
PUBLIC = ROOT / "results/news_sentiment_factor_v44_20261117"
DATABASES = {
    "portal": "apps/quant_portal/var/dev.sqlite3",
    "feed": "apps/quant_portal/var/personal_feeds.sqlite3",
    "research_queue": "artifacts/news_sentiment_factor_v42_20261117/queue.sqlite3",
    "chinese_scores": "artifacts/news_sentiment_factor_v41_20261117/chinese_tone.sqlite3",
}
INPUTS = [
    "configs/news_sentiment_factor_v44_20261117.json",
    "configs/news_sentiment_factor_v43_20261117.json",
    "configs/news_sentiment_factor_v42_us_calendar_2026.json",
    "configs/news_sentiment_factor_v43_security_mapping_20261117.json",
    "configs/quant_portal_personal_sessions_v13_20261116.json",
    "scripts/news_sentiment_factor_v43/daily.py",
    "scripts/quant_personal_feed/session_poll.py",
    "scripts/quant_personal_feed/cli.py",
    "scripts/news_sentiment_factor_v44/preflight.py",
]


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run() -> dict:
    now = datetime.now(timezone.utc)
    ident = now.strftime("%Y%m%dT%H%M%SZ")
    destination = PRIVATE / "backups" / ident
    destination.mkdir(parents=True, exist_ok=False)
    files = []
    for name, relative in DATABASES.items():
        source = ROOT / relative
        if not source.is_file():
            files.append({"name": name, "status": "SOURCE_NOT_PRESENT"})
            continue
        target = destination / (name + ".sqlite3")
        with sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True, timeout=30) as src:
            with sqlite3.connect(target, timeout=30) as dst:
                src.backup(dst)
        with sqlite3.connect(f"file:{target.as_posix()}?mode=ro", uri=True) as check:
            integrity = check.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError(f"Backup integrity failed: {name}: {integrity}")
        files.append({"name": name, "source": relative, "backup": str(target),
                      "bytes": target.stat().st_size, "sha256": sha(target), "integrity": integrity})
    inputs = [{"path": relative, "sha256": sha(ROOT / relative)} for relative in INPUTS]
    receipt = {"schema": "news_sentiment_factor_v44_preflight_1", "utc": now.isoformat(),
               "asia_shanghai": now.astimezone(ZoneInfo("Asia/Shanghai")).isoformat(),
               "backups": files, "inputs": inputs}
    private = destination / "preflight_private.json"
    private.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    PUBLIC.mkdir(parents=True, exist_ok=True)
    redacted = {"schema": receipt["schema"], "utc": receipt["utc"],
                "asia_shanghai": receipt["asia_shanghai"],
                "backup_integrity": {x["name"]: x["status"] if "status" in x else x["integrity"] for x in files},
                "input_manifest_sha256": hashlib.sha256(json.dumps(inputs, sort_keys=True,
                    ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest(),
                "input_count": len(inputs),
                "private_receipt_sha256": sha(private)}
    (PUBLIC / "preflight_redacted.json").write_text(json.dumps(redacted, ensure_ascii=False, indent=2), encoding="utf-8")
    return redacted


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False))
