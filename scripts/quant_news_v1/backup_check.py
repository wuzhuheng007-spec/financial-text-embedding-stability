"""News cache backup: restore a FIXED consistent copy into an isolated directory and reconcile it in full.

python -m scripts.quant_news_v1.backup_check digest DB
python -m scripts.quant_news_v1.backup_check restore-check FIXED_COPY RESTORE_DIR

The fixed copy is the SQLite online-backup file written by the host backup tool (quant_sqlite_backup.py),
never the live database that the collector keeps writing. restore-check copies it into a NEW directory
(refuses to overwrite), runs a full integrity_check (not only quick_check) and compares, table by table,
the row count and a SHA-256 over every row ordered by all columns, plus semantic counts: items, visible /
deleted items, versions per item, tombstones, sources (provider x via), cursors (watermarks/continuations)
and provider_state. Prints one JSON document; exit 0 only when everything matches.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import sys
from pathlib import Path


def digest(path: Path) -> dict:
    con = sqlite3.connect(f"file:{Path(path).resolve().as_posix()}?mode=ro", uri=True)
    try:
        tables = {}
        for (name,) in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"):
            cols = [r[1] for r in con.execute(f'PRAGMA table_info("{name}")')]
            q = ",".join(f'"{c}"' for c in cols)
            h, n = hashlib.sha256(), 0
            for row in con.execute(f'SELECT {q} FROM "{name}" ORDER BY {q}'):
                h.update(json.dumps(row, default=str, ensure_ascii=False).encode()); h.update(b"\n"); n += 1
            tables[name] = {"rows": n, "sha256": h.hexdigest(), "columns": cols}
        one = lambda sql: con.execute(sql).fetchone()[0]
        has = lambda t: t in tables
        sem = {
            "items": one("SELECT COUNT(*) FROM items") if has("items") else None,
            "items_visible": one("SELECT COUNT(*) FROM items WHERE deleted=0 AND latest_version>0") if has("items") else None,
            "items_deleted": one("SELECT COUNT(*) FROM items WHERE deleted=1") if has("items") else None,
            "versions": one("SELECT COUNT(*) FROM versions") if has("versions") else None,
            "versions_per_item": dict(con.execute("SELECT versions_n, COUNT(*) FROM items GROUP BY 1").fetchall()) if has("items") else None,
            "tombstones": one("SELECT COUNT(*) FROM tombstones") if has("tombstones") else None,
            "sources": [list(r) for r in con.execute("SELECT provider, via, COUNT(*) FROM versions GROUP BY 1, 2 ORDER BY 1, 2")] if has("versions") else None,
            "cursors": [list(r) for r in con.execute("SELECT name, value FROM cursors ORDER BY name")] if has("cursors") else [],
            "provider_state": [list(r) for r in con.execute("SELECT provider, state, last_heartbeat_at FROM provider_state ORDER BY provider")] if has("provider_state") else [],
        }
        return {"tables": tables, "semantic": sem}
    finally:
        con.close()


def restore_check(fixed: Path, restore_dir: Path) -> dict:
    fixed = Path(fixed); restore_dir = Path(restore_dir)
    out = {"fixed_copy": fixed.name, "fixed_copy_sha256": hashlib.sha256(fixed.read_bytes()).hexdigest(), "problems": []}
    restore_dir.mkdir(parents=True, exist_ok=False)
    restored = restore_dir / fixed.name
    shutil.copy2(fixed, restored)
    out["restored_sha256"] = hashlib.sha256(restored.read_bytes()).hexdigest()
    if out["restored_sha256"] != out["fixed_copy_sha256"]:
        out["problems"].append("restored file bytes differ from the fixed copy")
    con = sqlite3.connect(restored)
    out["integrity_check"] = [r[0] for r in con.execute("PRAGMA integrity_check")]
    out["journal_mode"] = con.execute("PRAGMA journal_mode").fetchone()[0]
    con.close()
    if out["integrity_check"] != ["ok"]:
        out["problems"].append(f"integrity_check: {out['integrity_check'][:3]}")
    a, b = digest(fixed), digest(restored)
    out["tables"] = {k: {"rows": v["rows"], "sha256": v["sha256"], "restored_equal": b["tables"].get(k) == v} for k, v in a["tables"].items()}
    out["tables_only_in_restored"] = sorted(set(b["tables"]) - set(a["tables"]))
    for k, v in out["tables"].items():
        if not v["restored_equal"]:
            out["problems"].append(f"table {k} differs after restore")
    out["semantic"] = a["semantic"]; out["semantic_equal"] = a["semantic"] == b["semantic"]
    if not out["semantic_equal"]:
        out["problems"].append("semantic summary differs after restore")
    out["ok"] = not out["problems"]
    out["meaning"] = "Restore of a fixed backup copy reconciled in full; NOT a comparison with the live, still-written database."
    return out


def main(argv=None) -> int:
    argv = argv or sys.argv[1:]
    if argv[0] == "digest":
        print(json.dumps(digest(Path(argv[1])), ensure_ascii=False, indent=1)); return 0
    if argv[0] == "restore-check":
        r = restore_check(Path(argv[1]), Path(argv[2])); print(json.dumps(r, ensure_ascii=False, indent=1)); return 0 if r["ok"] else 1
    raise SystemExit(__doc__)


if __name__ == "__main__":
    sys.exit(main())
