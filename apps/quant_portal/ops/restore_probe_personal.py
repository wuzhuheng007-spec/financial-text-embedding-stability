"""Verify a personal backup on a scratch copy; never overwrite the live DB."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import tempfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
BACKUPS = ROOT / "backups"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("backup", type=Path)
    args = parser.parse_args()
    source = args.backup.resolve(strict=True)
    if BACKUPS.resolve() not in source.parents or source.suffix != ".sqlite3":
        raise SystemExit("Restore probe accepts only a .sqlite3 file in this app's private backups directory")
    receipt_path = source.with_suffix(".receipt.json")
    if not receipt_path.is_file():
        raise SystemExit("Matching backup receipt is missing")
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if sha256(source) != receipt["sha256"]:
        raise SystemExit("Backup SHA-256 mismatch")
    with tempfile.TemporaryDirectory(prefix="portal-restore-probe-", dir=BACKUPS) as scratch:
        restored = Path(scratch) / "restored.sqlite3"
        shutil.copy2(source, restored)
        with closing(sqlite3.connect(f"file:{restored}?mode=ro", uri=True)) as db:
            integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
            tables = db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        if integrity != "ok":
            raise SystemExit(f"Scratch restore integrity failed: {integrity}")
    probe = {
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "backup_sha256": receipt["sha256"],
        "scratch_restore_integrity": integrity,
        "table_count": len(tables),
        "live_db_modified": False,
    }
    (BACKUPS / "last_restore_probe.json").write_text(
        json.dumps(probe, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Scratch restore verified; {len(tables)} tables; live DB untouched")


if __name__ == "__main__":
    main()
