"""Run a private, bounded historical publication-proxy alignment diagnostic."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path

from .historical_alignment import align_events


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _events(path: Path) -> list[dict]:
    if path.suffix == ".jsonl":
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()]
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, list):
        raise ValueError("event input must be a JSON array or JSONL")
    return value


def run(events_path: Path, price_path: Path, private_out: Path,
        public_summary: Path, *, end: date) -> dict:
    with price_path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    days = sorted({date.fromisoformat(row["market_date"]) for row in rows
                   if row["symbol"] == "US.TQQQ" and row["adjustment"] == "1"})
    if not days or days[-1] != end:
        raise ValueError("price calendar does not cover requested end date")
    events = _events(events_path)
    aligned = align_events(events, days, end=end)
    private_out.parent.mkdir(parents=True, exist_ok=True)
    private_out.write_text(json.dumps(aligned, indent=2, ensure_ascii=False) + "\n",
                           encoding="utf-8")
    counts = Counter(row["status"] for row in aligned)
    by_type = Counter(row.get("source_type", "UNKNOWN") for row in aligned)
    summary = {
        "schema": "historical_event_alignment_summary_v1",
        "run_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "alignment_code_sha256": _hash(Path(__file__).with_name("historical_alignment.py")),
        "runner_code_sha256": _hash(Path(__file__)),
        "events_input_sha256": _hash(events_path),
        "prices_input_sha256": _hash(price_path),
        "private_alignment_sha256": _hash(private_out),
        "trading_dates": len(days),
        "input_events": len(events),
        "status_counts": dict(sorted(counts.items())),
        "source_type_counts": dict(sorted(by_type.items())),
        "strict_pit_eligible": sum(bool(row["strict_pit_eligible"]) for row in aligned),
        "rule": "first US trading date strictly after the source publication or official release-not-before calendar date; 18:00 America/New_York decision proxy",
        "limitations": [
            "historical retrieval is not historical first availability",
            "publication alignment is not a qualified point-in-time news feature",
            "incomplete keyword-search coverage is not zero news",
        ],
    }
    public_summary.parent.mkdir(parents=True, exist_ok=True)
    public_summary.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n",
                              encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events", required=True, type=Path)
    parser.add_argument("--prices", required=True, type=Path)
    parser.add_argument("--private-out", required=True, type=Path)
    parser.add_argument("--public-summary", required=True, type=Path)
    parser.add_argument("--end", default="2026-07-31")
    args = parser.parse_args()
    result = run(args.events, args.prices, args.private_out, args.public_summary,
                 end=date.fromisoformat(args.end))
    print(json.dumps({"input_events": result["input_events"],
                      "status_counts": result["status_counts"],
                      "strict_pit_eligible": result["strict_pit_eligible"]}))


if __name__ == "__main__":
    main()
