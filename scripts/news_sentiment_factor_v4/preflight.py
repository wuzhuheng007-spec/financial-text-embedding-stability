"""Freeze v4 inputs and online-backup the running owner's SQLite databases."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PRIVATE = ROOT / "artifacts/news_sentiment_factor_v4_20261117"
PUBLIC = ROOT / "results/news_sentiment_factor_v4_20261117"
CONFIG = ROOT / "configs/news_sentiment_factor_v4_20261117.json"
DBS = {
    "portal": ROOT / "apps/quant_portal/var/dev.sqlite3",
    "personal_feed": ROOT / "apps/quant_portal/var/personal_feeds.sqlite3",
    "personal_news": ROOT / "apps/quant_portal/var/personal_news_v18.sqlite3",
    "personal_analysis": ROOT / "apps/quant_portal/var/personal_analysis_v16.sqlite3",
}


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def save(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


def main() -> dict:
    PRIVATE.mkdir(parents=True, exist_ok=True)
    if (PUBLIC / "preflight_redacted.json").exists():
        raise FileExistsError("V4_PREFLIGHT_ALREADY_EXISTS")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_dir = PRIVATE / "backups" / stamp
    backup_dir.mkdir(parents=True, exist_ok=False)
    backups = []
    for key, source in DBS.items():
        if not source.is_file():
            backups.append({"role": key, "status": "ABSENT"})
            continue
        target = backup_dir / f"{key}.sqlite3"
        with sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True) as src:
            with sqlite3.connect(target) as dst:
                src.backup(dst)
        with sqlite3.connect(f"file:{target.as_posix()}?mode=ro", uri=True) as check:
            integrity = check.execute("PRAGMA quick_check").fetchone()[0]
        backups.append({"role": key, "status": "BACKED_UP", "private_path": str(target),
                        "bytes": target.stat().st_size, "sha256": sha(target), "quick_check": integrity})
    now = datetime.now(timezone.utc)
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    if config["request_budget"]["total_max"] != 30 or sum(config["request_budget"][k] for k in
        ("moomoo_webapi_max", "wrds_ravenpack_max", "massive_benzinga_max")) != 30:
        raise ValueError("REQUEST_BUDGET_NOT_FROZEN_TO_30")
    private = {"schema": "news_sentiment_v4_preflight_private_1", "run_at_utc": now.isoformat(),
               "run_at_asia_shanghai": now.astimezone(__import__("zoneinfo").ZoneInfo("Asia/Shanghai")).isoformat(),
               "head": head, "config_sha256": sha(CONFIG), "backups": backups,
               "model_weight_sha256": sha(ROOT / "artifacts/news_sentiment_factor_v2_20261117/inputs/finbert_2022/pytorch_model.bin"),
               "lm_dictionary_sha256": sha(ROOT / "artifacts/news_sentiment_factor_v2_20261117/inputs/Loughran-McDonald_MasterDictionary_1993-2025.csv")}
    save(PRIVATE / "preflight_private.json", private)
    redacted = {k: private[k] for k in ("schema", "run_at_utc", "run_at_asia_shanghai", "head",
                                          "config_sha256", "model_weight_sha256", "lm_dictionary_sha256")}
    redacted["backup_roles"] = [{"role": b["role"], "status": b["status"],
                                 "quick_check": b.get("quick_check"), "bytes": b.get("bytes")}
                                for b in backups]
    redacted["private_receipt_sha256"] = sha(PRIVATE / "preflight_private.json")
    save(PUBLIC / "preflight_redacted.json", redacted)
    return redacted


if __name__ == "__main__":
    print(json.dumps(main(), ensure_ascii=False))
