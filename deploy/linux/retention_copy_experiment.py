"""ISOLATED COPY experiment for session-quote retention (never the live file).

    python retention_copy_experiment.py <copy.sqlite3> <archive_dir> <as_of_utc> <keep_days>

1. simulate the collector's existing 48 h raw-response pruning as of ``as_of``
   (the live store does this continuously; the copy is only 2 days old);
2. retention dry-run, then apply (archive -> verify -> restore check -> batched delete)
   while a background writer mimics collector heartbeats and records its waits;
3. report free pages vs file size, then VACUUM the copy to show what VACUUM
   would return (the live file is never vacuumed by this tooling).
"""
from __future__ import annotations

import json
import sqlite3
import sys
import threading
import time
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, "/app")
from scripts.quant_personal_feed import retention  # noqa: E402

db, archive, as_of, keep = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3], int(sys.argv[4])
now = datetime.fromisoformat(as_of.replace("Z", "+00:00"))
out = {"copy": str(db), "as_of_utc": as_of, "keep_days": keep, "kind": "ISOLATED_COPY_NOT_PRODUCTION"}

cutoff = (now - timedelta(hours=48)).strftime("%Y-%m-%dT%H:%M:%S")
t = time.monotonic()
with closing(sqlite3.connect(db, isolation_level=None)) as con:
    before = con.execute("SELECT count(*) FROM session_raw_responses").fetchone()[0]
    stale = [r[0] for r in con.execute("""SELECT sha256 FROM session_raw_responses WHERE sha256 NOT IN (
        SELECT raw_sha256 FROM session_quotes WHERE received_at >= ?
        UNION SELECT raw_sha256 FROM (SELECT raw_sha256, ROW_NUMBER() OVER (PARTITION BY owner_id,symbol,session
            ORDER BY (source_at IS NOT NULL) DESC, source_at DESC, parent_source_at_hint DESC, received_at DESC, id DESC) rk
            FROM session_quotes) WHERE rk = 1)""", (cutoff,))]
    # Same shape as the live store: short batches, checkpoint so the WAL stays small.
    for i in range(0, len(stale), 400):
        part = stale[i:i + 400]
        con.execute("BEGIN IMMEDIATE")
        con.execute(f"DELETE FROM session_raw_responses WHERE sha256 IN ({','.join('?' * len(part))})", part)
        con.execute("COMMIT")
        if i % 20000 == 0:
            con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    after = con.execute("SELECT count(*) FROM session_raw_responses").fetchone()[0]
    con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
out["simulated_raw_prune"] = {"rows_before": before, "rows_after": after, "seconds": round(time.monotonic() - t, 1)}
out["space_after_raw_prune"] = retention.space(db)

t = time.monotonic()
dry = retention.run(db, archive, keep, apply=False, now=now)
out["dry_run"] = {k: dry[k] for k in ("rows_total", "protected", "selected_rows", "selected_payload_bytes")}
out["dry_run"]["seconds"] = round(time.monotonic() - t, 1)

waits, stop = [], threading.Event()


def writer():
    with closing(sqlite3.connect(db, timeout=10, isolation_level=None)) as con:
        con.execute("PRAGMA busy_timeout=10000")
        con.execute("CREATE TABLE IF NOT EXISTS hb(i INTEGER PRIMARY KEY, at REAL)")
        while not stop.is_set():
            started = time.monotonic()
            try:
                con.execute("BEGIN IMMEDIATE")
                con.execute("INSERT INTO hb(at) VALUES(?)", (time.time(),))
                con.execute("COMMIT")
                waits.append(time.monotonic() - started)
            except sqlite3.OperationalError as exc:
                waits.append(-1.0)
                out.setdefault("writer_errors", []).append(str(exc)[:60])
            time.sleep(0.05)


thread = threading.Thread(target=writer)
thread.start()
t = time.monotonic()
applied = retention.run(db, archive, keep, apply=True, batch=2000, pause=0.2, now=now)
stop.set()
thread.join()
ok_waits = sorted(w for w in waits if w >= 0)
out["apply"] = {k: applied.get(k) for k in ("deleted_rows", "delete", "archive_verification", "space_after", "refused")}
out["apply"]["seconds"] = round(time.monotonic() - t, 1)
out["apply"]["archive_bytes"] = sum(p.stat().st_size for p in archive.glob("*")) if archive.exists() else 0
out["concurrent_writer"] = {"writes": len(waits), "errors": sum(1 for w in waits if w < 0),
                            "p50_ms": round(ok_waits[len(ok_waits) // 2] * 1000, 2) if ok_waits else None,
                            "max_ms": round(ok_waits[-1] * 1000, 2) if ok_waits else None}
with closing(sqlite3.connect(db, isolation_level=None)) as con:
    con.execute("DROP TABLE IF EXISTS hb")
    con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
out["space_before_vacuum"] = retention.space(db)
t = time.monotonic()
with closing(sqlite3.connect(db, isolation_level=None)) as con:
    con.execute("VACUUM")
out["vacuum_copy_seconds"] = round(time.monotonic() - t, 1)
out["space_after_vacuum_copy"] = retention.space(db)
print(json.dumps(out, default=str))
