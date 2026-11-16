"""Online, integrity-checked private SQLite snapshots before personal v1.6 changes."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .freeze_v15 import ROOT, backup

SOURCES = {
    "portal": ROOT / "apps/quant_portal/var/dev.sqlite3",
    "feed": ROOT / "apps/quant_portal/var/personal_feeds.sqlite3",
    "analysis_v15b": ROOT / "apps/quant_portal/var/personal_analysis_v15b.sqlite3",
    "approved_v15d": ROOT / "apps/quant_portal/var/personal_analysis_v15d_approved.sqlite3",
}
PRIVATE_ROOT = ROOT / "apps/quant_portal/var"
PUBLIC = ROOT / "results/quant_portal_personal_v16_20261116"


def main() -> None:
    now = datetime.now(timezone.utc)
    private = PRIVATE_ROOT / ("v16_baseline_" + now.strftime("%Y%m%dT%H%M%SZ"))
    if private.exists():
        raise FileExistsError(private)
    private.mkdir(parents=True)
    rows = []
    private_rows = []
    for name, source in SOURCES.items():
        if not source.is_file():
            rows.append({"database": name, "status": "ABSENT"})
            continue
        target = private / f"{name}.sqlite3"
        digest = backup(source, target)
        with sqlite3.connect(target.resolve().as_uri() + "?mode=ro", uri=True) as db:
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            counts = {}
            for table in (
                "auth_user", "core_watchlistitem", "core_userpreferences", "core_publicfactcard",
                "quotes", "session_quotes", "news_items", "analysis_jobs", "reviewed_cards",
            ):
                if table in tables:
                    counts[table] = db.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            quick_check = db.execute("PRAGMA quick_check").fetchone()[0]
        row = {"database": name, "status": "BACKED_UP", "sha256": digest,
               "bytes": target.stat().st_size, "quick_check": quick_check, "counts": counts}
        rows.append(row)
        private_rows.append({**row, "source": str(source), "snapshot": str(target)})
    receipt = {"schema": "quant_personal_v16_baseline_redacted_v1",
               "at_utc": now.isoformat(),
               "at_asia_shanghai": now.astimezone(ZoneInfo("Asia/Shanghai")).isoformat(),
               "databases": rows}
    (private / "backup_manifest_private.json").write_text(
        json.dumps({"databases": private_rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    PUBLIC.mkdir(parents=True, exist_ok=True)
    out = PUBLIC / "backup_redacted.json"
    if out.exists():
        raise FileExistsError(out)
    out.write_text(json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
                   encoding="utf-8", newline="\n")
    print(json.dumps({"private_backup_dir": str(private), "public_receipt": str(out),
                      "database_status": {row["database"]: row["status"] for row in rows}}))


if __name__ == "__main__":
    main()
