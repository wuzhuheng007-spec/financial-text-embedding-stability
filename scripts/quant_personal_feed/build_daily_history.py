"""Build a versioned portal snapshot from validated moomoo daily CSV exports."""
import argparse
import csv
import hashlib
import io
import json
import math
import os
import re
import sqlite3
import tempfile
from contextlib import closing
from datetime import date, datetime, timezone
from pathlib import Path


def build_snapshot(output: Path, exports: list[Path]) -> dict:
    manifest = []
    coverage = {}
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise FileExistsError(output)
    with tempfile.TemporaryDirectory(dir=output.parent) as tmp:
        staged = Path(tmp) / "history.sqlite"
        with closing(sqlite3.connect(staged)) as db, db:
            db.executescript("""
                CREATE TABLE series(series_id TEXT PRIMARY KEY, symbol TEXT NOT NULL,
                                    frequency TEXT NOT NULL, autype INTEGER NOT NULL);
                CREATE TABLE observations(series_id TEXT NOT NULL, local_date TEXT NOT NULL,
                    open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL, close REAL NOT NULL,
                    research_eligible INTEGER NOT NULL, source_sha256 TEXT NOT NULL,
                    PRIMARY KEY(series_id, local_date));
                CREATE TABLE snapshot_inputs(name TEXT, sha256 TEXT, rows INTEGER);
            """)
            for path in exports:
                raw = path.read_bytes()
                sha = hashlib.sha256(raw).hexdigest()
                count = 0
                reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig")))
                for row in reader:
                    if row["frequency"] != "DAY" or row["autype"] != "0":
                        raise ValueError(f"Expected unadjusted daily data: {path.name}:{reader.line_num}")
                    if row["research_eligible"] != "1":
                        continue
                    symbol = row["symbol"]
                    if not re.fullmatch(r"US\.[A-Z0-9.]{1,16}", symbol):
                        raise ValueError(f"Expected a US symbol: {path.name}:{reader.line_num}")
                    session = date.fromisoformat(row["local_date"]).isoformat()
                    o, h, l, c = (float(row[key]) for key in ("open", "high", "low", "close"))
                    if not all(math.isfinite(p) and p > 0 for p in (o, h, l, c)) or not l <= min(o, c) <= max(o, c) <= h:
                        raise ValueError(f"Invalid OHLC: {path.name}:{reader.line_num}")
                    db.execute("INSERT OR IGNORE INTO series VALUES (?, ?, 'DAY', 0)", (symbol, symbol))
                    db.execute("INSERT INTO observations VALUES (?, ?, ?, ?, ?, ?, 1, ?)",
                               (symbol, session, o, h, l, c, sha))
                    count += 1
                manifest.append({"name": path.name, "sha256": sha, "rows": count})
                db.execute("INSERT INTO snapshot_inputs VALUES (?, ?, ?)", (path.name, sha, count))
            for sym, count, first, last in db.execute(
                "SELECT series_id, COUNT(*), MIN(local_date), MAX(local_date) FROM observations GROUP BY series_id"
            ):
                coverage[sym] = {"rows": count, "first_date": first, "last_date": last}
            if not coverage:
                raise ValueError("No eligible daily bars")
            if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("Snapshot integrity check failed")
        os.link(staged, output)
    return {"source": "moomoo", "built_at_utc": datetime.now(timezone.utc).isoformat(),
            "output": str(output), "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
            "inputs": manifest, "coverage": coverage}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("exports", type=Path, nargs="+")
    args = parser.parse_args()
    print(json.dumps(build_snapshot(args.output, args.exports), indent=2))


if __name__ == "__main__":
    main()
