"""Session-quote retention with verified archival. Dry-run by default.

    python -m scripts.quant_personal_feed.retention --db <feed.sqlite3> --archive-dir <dir> [--keep-days 30]
    python -m scripts.quant_personal_feed.retention ... --apply          # archive, verify, then delete

Only ``session_quotes`` rows are considered. Protected (never selected):
  * received within ``keep_days`` (minimum 8, longer than the 7-day chart range);
  * the latest row of every owner/symbol/session cell (what the page shows);
  * rows whose raw provider response still exists (source evidence);
  * nothing outside this table: research scores, snapshots and labels do not
    read session quotes (checked by ``scan_research_references``).

``--apply`` writes a gzip JSONL archive and manifest first, re-reads it, checks
its SHA-256 and row digest, restores it into a scratch SQLite database and
compares again. Only after all three checks pass are rows deleted, by id, in
short ``BEGIN IMMEDIATE`` batches with a pause between batches so the collector
can take the writer slot. DELETE leaves free pages that SQLite reuses; the file
does not shrink without VACUUM, which this tool never runs on the live file.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import sqlite3
import tempfile
import time
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

TABLE = "session_quotes"
MIN_KEEP_DAYS = 8
RESEARCH_DIRS = ("news_sentiment_factor_v41", "news_sentiment_factor_v42", "news_sentiment_factor_v43",
                 "news_sentiment_factor_v44", "news_sentiment_factor_v45")


def scan_research_references(root: Path) -> dict:
    hits = {}
    for name in RESEARCH_DIRS:
        for path in (root / "scripts" / name).glob("*.py"):
            if TABLE in path.read_text(encoding="utf-8", errors="replace"):
                hits.setdefault(name, []).append(path.name)
    return hits


def _columns(con: sqlite3.Connection) -> list[str]:
    return [row[1] for row in con.execute(f"PRAGMA table_info({TABLE})")]


def _row_digest(rows: list[tuple]) -> str:
    h = hashlib.sha256()
    for row in rows:
        h.update(json.dumps(list(row), ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        h.update(b"\n")
    return h.hexdigest()


def candidates(con: sqlite3.Connection, keep_days: int, now: datetime) -> tuple[list[int], dict]:
    if keep_days < MIN_KEEP_DAYS:
        raise ValueError(f"keep_days must be >= {MIN_KEEP_DAYS}")
    cutoff = (now - timedelta(days=keep_days)).strftime("%Y-%m-%dT%H:%M:%S")
    # Read-only selection; no writer lock is taken here.
    rows = con.execute(f"""WITH latest AS (
        SELECT id, ROW_NUMBER() OVER (PARTITION BY owner_id,symbol,session
          ORDER BY (source_at IS NOT NULL) DESC, source_at DESC, parent_source_at_hint DESC,
                   received_at DESC, id DESC) AS rank_in_cell FROM {TABLE})
      SELECT q.id,
             q.received_at >= ? AS recent,
             l.rank_in_cell = 1 AS latest_cell,
             EXISTS(SELECT 1 FROM session_raw_responses r WHERE r.sha256 = q.raw_sha256) AS has_raw
      FROM {TABLE} q JOIN latest l USING(id)""", (cutoff,)).fetchall()
    selected, protected = [], {"recent_window": 0, "latest_cell": 0, "raw_evidence_present": 0}
    for row_id, recent, latest, has_raw in rows:
        if recent:
            protected["recent_window"] += 1
        elif latest:
            protected["latest_cell"] += 1
        elif has_raw:
            protected["raw_evidence_present"] += 1
        else:
            selected.append(row_id)
    return sorted(selected), {"cutoff_received_at": cutoff, "rows_total": len(rows), "protected": protected}


def _fetch(con: sqlite3.Connection, ids: list[int], columns: list[str]) -> list[tuple]:
    out = []
    for i in range(0, len(ids), 900):
        part = ids[i:i + 900]
        out.extend(con.execute(f"SELECT {','.join(columns)} FROM {TABLE} WHERE id IN ({','.join('?' * len(part))}) ORDER BY id",
                               part).fetchall())
    return out


def estimate_bytes(con: sqlite3.Connection, ids: list[int], columns: list[str]) -> int:
    if not ids:
        return 0
    expr = "+".join(f"coalesce(length(CAST({c} AS BLOB)),0)" for c in columns)
    total = 0
    for i in range(0, len(ids), 900):
        part = ids[i:i + 900]
        total += con.execute(f"SELECT sum({expr}) FROM {TABLE} WHERE id IN ({','.join('?' * len(part))})", part).fetchone()[0] or 0
    return total


def write_archive(rows: list[tuple], columns: list[str], ddl: str, archive_dir: Path, stamp: str) -> dict:
    archive_dir.mkdir(parents=True, exist_ok=True)
    data_path = archive_dir / f"{TABLE}_{stamp}.jsonl.gz"
    with gzip.open(data_path, "wt", encoding="utf-8", newline="\n") as fh:
        for row in rows:
            fh.write(json.dumps(list(row), ensure_ascii=False, separators=(",", ":")) + "\n")
    manifest = {"schema": "session_quote_archive_1", "table": TABLE, "columns": columns, "ddl": ddl,
                "rows": len(rows), "min_id": rows[0][0] if rows else None, "max_id": rows[-1][0] if rows else None,
                "row_digest_sha256": _row_digest(rows), "file": data_path.name,
                "file_sha256": hashlib.sha256(data_path.read_bytes()).hexdigest(), "created_at_utc": stamp}
    (archive_dir / f"{TABLE}_{stamp}.manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    return manifest


def verify_archive(archive_dir: Path, manifest: dict) -> dict:
    data_path = archive_dir / manifest["file"]
    file_ok = hashlib.sha256(data_path.read_bytes()).hexdigest() == manifest["file_sha256"]
    with gzip.open(data_path, "rt", encoding="utf-8") as fh:
        reread = [tuple(json.loads(line)) for line in fh]
    reread_ok = len(reread) == manifest["rows"] and _row_digest(reread) == manifest["row_digest_sha256"]
    with tempfile.TemporaryDirectory() as tmp, closing(sqlite3.connect(Path(tmp) / "restore.sqlite3")) as scratch:
        scratch.execute(manifest["ddl"])
        cols = manifest["columns"]
        scratch.executemany(f"INSERT INTO {TABLE}({','.join(cols)}) VALUES({','.join('?' * len(cols))})", reread)
        scratch.commit()
        restored = scratch.execute(f"SELECT {','.join(cols)} FROM {TABLE} ORDER BY id").fetchall()
    restore_ok = _row_digest(restored) == manifest["row_digest_sha256"]
    return {"file_sha256_ok": file_ok, "reread_digest_ok": reread_ok, "restore_digest_ok": restore_ok,
            "ok": file_ok and reread_ok and restore_ok}


def delete_batches(db_path: Path, ids: list[int], batch: int, pause: float, busy_ms: int) -> dict:
    holds = []
    with closing(sqlite3.connect(db_path, timeout=busy_ms / 1000, isolation_level=None)) as con:
        con.execute(f"PRAGMA busy_timeout={busy_ms}")
        for i in range(0, len(ids), batch):
            part = ids[i:i + batch]
            started = time.monotonic()
            con.execute("BEGIN IMMEDIATE")
            try:
                con.execute(f"DELETE FROM {TABLE} WHERE id IN ({','.join('?' * len(part))})", part)
                con.execute("COMMIT")
            except BaseException:
                con.execute("ROLLBACK")
                raise
            holds.append(time.monotonic() - started)
            time.sleep(pause)
    return {"batches": len(holds), "max_write_hold_ms": round(max(holds) * 1000, 1) if holds else 0,
            "mean_write_hold_ms": round(sum(holds) / len(holds) * 1000, 1) if holds else 0}


def space(db_path: Path) -> dict:
    with closing(sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)) as con:
        page = con.execute("PRAGMA page_size").fetchone()[0]
        pages = con.execute("PRAGMA page_count").fetchone()[0]
        free = con.execute("PRAGMA freelist_count").fetchone()[0]
    files = {suffix or "main": (db_path.with_name(db_path.name + suffix).stat().st_size
                                if db_path.with_name(db_path.name + suffix).exists() else 0)
             for suffix in ("", "-wal", "-shm")}
    return {"page_size": page, "page_count": pages, "freelist_pages": free,
            "reusable_free_bytes": free * page, "file_bytes": files}


def run(db: Path, archive_dir: Path, keep_days: int, apply: bool, batch: int = 2000,
        pause: float = 0.2, busy_ms: int = 2000, now: datetime | None = None, root: Path | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    root = root or Path(__file__).resolve().parents[2]
    refs = scan_research_references(root)
    if refs:
        raise RuntimeError(f"research code references {TABLE}; refuse: {refs}")
    with closing(sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True, timeout=busy_ms / 1000)) as con:
        columns = _columns(con)
        ddl = con.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (TABLE,)).fetchone()[0]
        ids, info = candidates(con, keep_days, now)
        payload = estimate_bytes(con, ids, columns)
        per_cell = con.execute(f"""SELECT symbol, session, count(*) FROM {TABLE}
            WHERE id IN (SELECT value FROM json_each(?)) GROUP BY symbol, session ORDER BY 3 DESC LIMIT 20""",
                               (json.dumps(ids),)).fetchall() if ids else []
        rows = _fetch(con, ids, columns) if apply and ids else []
    result = {"mode": "APPLY" if apply else "DRY_RUN", "keep_days": keep_days, **info,
              "selected_rows": len(ids), "selected_payload_bytes": payload,
              "estimated_bytes_incl_3_indexes": int(payload * 1.9),
              "selected_by_symbol_session_top20": per_cell, "research_references": refs,
              "space_before": space(db)}
    if not apply or not ids:
        return result
    manifest = write_archive(rows, columns, ddl, archive_dir, stamp)
    check = verify_archive(archive_dir, manifest)
    result.update(archive_manifest=manifest, archive_verification=check)
    if not check["ok"]:
        result["deleted_rows"] = 0
        result["refused"] = "ARCHIVE_VERIFICATION_FAILED"
        return result
    result["delete"] = delete_batches(db, ids, batch, pause, busy_ms)
    result["deleted_rows"] = len(ids)
    result["space_after"] = space(db)
    result["note"] = "free pages are reused by SQLite; the file shrinks only after VACUUM (not run)"
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--archive-dir", type=Path, required=True)
    parser.add_argument("--keep-days", type=int, default=30)
    parser.add_argument("--batch", type=int, default=2000)
    parser.add_argument("--pause", type=float, default=0.2)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--as-of", help="ISOLATED COPIES ONLY: evaluate as if now were this UTC time")
    args = parser.parse_args()
    now = datetime.fromisoformat(args.as_of.replace("Z", "+00:00")) if args.as_of else None
    print(json.dumps(run(args.db, args.archive_dir, args.keep_days, args.apply, args.batch, args.pause, now=now),
                     ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
