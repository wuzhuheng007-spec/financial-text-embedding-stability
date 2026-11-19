"""Rebuild the news cache from the raw messages it already holds, through the current normalizer.

python -m scripts.quant_news_v1.rebuild [--keep-suffix pre_idfix_20261119]
Run only while the collector is stopped (it takes the collector lock). The old file is renamed, never deleted;
every raw version and tombstone is re-applied in original receipt order with its original receipt time,
channel (via) and provider; provider_state and cursors are carried over. available_at in the new file is the
rebuild time (when the corrected form became available) - the original is still in the kept file.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys

from . import store
from .adapters import normalize_benzinga, normalize_benzinga_rest, to_utc
from .collector import _lock


def renormalize(raw: dict, via: str) -> dict:
    if isinstance(raw, dict) and isinstance(raw.get("data"), dict):
        return normalize_benzinga(raw)
    if isinstance(raw, dict) and via == "removed_poll":
        return {"upstream": "benzinga", "upstream_id": str(raw.get("id")), "message_id": None, "action": "deleted",
                "provider_updated_at": to_utc(raw.get("updated")), "provider_created_at": None, "symbols": [], "channels": [], "raw": raw}
    return normalize_benzinga_rest(raw)


def rebuild(src, keep_suffix: str) -> dict:
    kept = src.with_name(f"{src.stem}.{keep_suffix}{src.suffix}")
    if kept.exists():
        raise SystemExit(f"{kept.name} already exists; choose another suffix")
    old = sqlite3.connect(src); old.row_factory = sqlite3.Row
    old.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    events = [("v", r["received_at"], dict(r)) for r in old.execute("SELECT * FROM versions")]
    events += [("t", r["received_at"], dict(r)) for r in old.execute("SELECT * FROM tombstones")]
    states = [dict(r) for r in old.execute("SELECT * FROM provider_state")]
    cursors = [tuple(r) for r in old.execute("SELECT * FROM cursors")] if old.execute(
        "SELECT 1 FROM sqlite_master WHERE name='cursors'").fetchone() else []
    old.close()
    shutil.move(str(src), str(kept))
    for ext in ("-wal", "-shm"):
        side = src.with_name(src.name + ext)
        if side.exists():
            shutil.move(str(side), str(kept.with_name(kept.name + ext)))
    db = store.connect(src)
    results: dict = {}
    seen_msgs = set()
    for kind, received_at, r in sorted(events, key=lambda e: e[1]):
        raw = json.loads(r["raw_json"])
        n = renormalize(raw, r.get("via") or "") if kind == "v" else renormalize(raw, "removed_poll")
        if kind == "t" and n.get("action") != "deleted":
            n["action"] = "deleted"
        mid = n.get("message_id")
        if mid is not None and (r["provider"], mid) in seen_msgs:
            results["DUPLICATE_MESSAGE"] = results.get("DUPLICATE_MESSAGE", 0) + 1; continue
        if mid is not None:
            seen_msgs.add((r["provider"], mid)); store.seen_message(db, r["provider"], mid)
        via = r.get("via") or "removed_poll"
        res = store.apply(db, n, provider=r["provider"], via=via, received_at=received_at, online=(via == "stream"))
        results[res] = results.get(res, 0) + 1
    for s in states:
        cols = ",".join(s); db.execute(f"INSERT OR REPLACE INTO provider_state({cols}) VALUES({','.join('?' * len(s))})", tuple(s.values()))
    if cursors:
        db.execute("CREATE TABLE IF NOT EXISTS cursors (name TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL)")
        db.executemany("INSERT OR REPLACE INTO cursors VALUES(?,?,?)", cursors)
    db.commit()
    out = {"kept_original_as": kept.name, "events_reapplied": len(events), "results": results,
           "items": db.execute("SELECT COUNT(*) FROM items").fetchone()[0], "versions": db.execute("SELECT COUNT(*) FROM versions").fetchone()[0]}
    db.close()
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(); ap.add_argument("--keep-suffix", required=True)
    a = ap.parse_args(argv)
    lock = _lock()  # refuses while the collector runs
    try:
        print(json.dumps(rebuild(store.db_path(), a.keep_suffix), ensure_ascii=False))
    finally:
        lock.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
