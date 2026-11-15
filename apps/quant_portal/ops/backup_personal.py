"""Online SQLite backup for the loopback-only personal portal."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "var" / "dev.sqlite3"
BACKUPS = ROOT / "backups"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    if not SOURCE.is_file():
        raise SystemExit("Local SQLite database is absent; no backup created")
    BACKUPS.mkdir(mode=0o700, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = BACKUPS / f"personal-{stamp}.sqlite3"
    if target.exists():
        raise SystemExit("Backup name collision; retry after one second")
    try:
        with closing(sqlite3.connect(f"file:{SOURCE}?mode=ro", uri=True)) as source:
            with closing(sqlite3.connect(target)) as destination:
                source.backup(destination)
                integrity = destination.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError(f"Backup integrity check failed: {integrity}")
        receipt = {
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "source": str(SOURCE),
            "backup": str(target),
            "sha256": sha256(target),
            "bytes": target.stat().st_size,
            "integrity_check": integrity,
        }
        target.with_suffix(".receipt.json").write_text(
            json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    print(f"Personal backup created: {target} (private; do not upload)")


if __name__ == "__main__":
    main()
