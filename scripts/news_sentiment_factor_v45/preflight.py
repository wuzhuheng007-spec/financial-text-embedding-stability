"""One-time online private backups and frozen pre-edit input hashes."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
PRIVATE = ROOT / "artifacts/news_sentiment_factor_v45_20261117"
PUBLIC = ROOT / "results/news_sentiment_factor_v45_20261117"
CONFIG = ROOT / "configs/news_sentiment_factor_v45_20261117.json"
DATABASES = {
    "portal": "apps/quant_portal/var/dev.sqlite3",
    "feed": "apps/quant_portal/var/personal_feeds.sqlite3",
    "research_queue": "artifacts/news_sentiment_factor_v42_20261117/queue.sqlite3",
    "chinese_scores": "artifacts/news_sentiment_factor_v41_20261117/chinese_tone.sqlite3",
    "price_labels": "artifacts/news_sentiment_factor_v44_20261117/price_labels.sqlite3",
}
INPUTS = [
    "configs/news_sentiment_factor_v45_20261117.json",
    "configs/news_sentiment_factor_v44_20261117.json",
    "configs/news_sentiment_factor_v42_us_calendar_2026.json",
    "configs/news_sentiment_factor_v43_security_mapping_20261117.json",
    "scripts/news_sentiment_factor_v44/labels.py",
    "scripts/news_sentiment_factor_v43/daily.py",
    "scripts/news_sentiment_factor_v42/worker.py",
    "scripts/quant_personal_feed/session_poll.py",
    "scripts/news_sentiment_factor_v45/preflight.py",
]


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def dump(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def run() -> dict:
    now = datetime.now(timezone.utc)
    stamp = now.strftime("%Y%m%dT%H%M%S%fZ")
    destination = PRIVATE / "backups" / stamp
    destination.mkdir(parents=True, exist_ok=False)
    backups = []
    for name, relative in DATABASES.items():
        source = ROOT / relative
        if not source.is_file():
            backups.append({"name": name, "status": "SOURCE_NOT_PRESENT"})
            continue
        target = destination / (name + ".sqlite3")
        with sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True, timeout=30) as src:
            with sqlite3.connect(target, timeout=30) as dst:
                src.backup(dst)
        with sqlite3.connect(f"file:{target.as_posix()}?mode=ro", uri=True) as check:
            integrity = check.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError(f"ONLINE_BACKUP_INTEGRITY_{name}_{integrity}")
        backups.append({"name": name, "source": relative, "private_backup": str(target),
                        "bytes": target.stat().st_size, "sha256": sha(target), "integrity": integrity})
    inputs = [{"path": relative, "sha256": sha(ROOT / relative)} for relative in INPUTS]
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    receipt = {"schema": "news_sentiment_factor_v45_preflight_private_1",
               "checked_at_utc": now.isoformat(),
               "checked_at_asia_shanghai": now.astimezone(ZoneInfo("Asia/Shanghai")).isoformat(),
               "git_head": head, "backups": backups, "inputs": inputs}
    private_receipt = destination / "preflight_private.json"
    dump(private_receipt, receipt)
    redacted = {"schema": "news_sentiment_factor_v45_preflight_redacted_1",
                "checked_at_utc": receipt["checked_at_utc"],
                "checked_at_asia_shanghai": receipt["checked_at_asia_shanghai"],
                "git_head": head, "backup_integrity": {row["name"]: row.get("integrity", row.get("status")) for row in backups},
                "backup_count_ok": sum(row.get("integrity") == "ok" for row in backups),
                "input_count": len(inputs),
                "input_manifest_sha256": hashlib.sha256(json.dumps(inputs, sort_keys=True, ensure_ascii=False,
                    separators=(",", ":")).encode("utf-8")).hexdigest(),
                "private_receipt_sha256": sha(private_receipt)}
    dump(PUBLIC / "preflight_redacted.json", redacted)
    return redacted


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False))
