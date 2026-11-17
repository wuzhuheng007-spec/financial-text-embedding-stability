"""Explain v2 versus v3 stepping dates on the same eight original events."""
from __future__ import annotations

import json
from pathlib import Path

from .source import PRIVATE, PUBLIC, digest, now, write_json

ROOT = Path(__file__).resolve().parents[2]
OLD = ROOT / "artifacts/news_sentiment_factor_v2_20261117/event_alignment_private.jsonl"
NEW = PRIVATE / "event_alignment_private.jsonl"


def compare():
    old = [json.loads(x) for x in OLD.read_text(encoding="utf-8").splitlines()]
    new = [json.loads(x) for x in NEW.read_text(encoding="utf-8").splitlines()]
    lookup = {(x["symbol"], x["filing_date"]): x for x in new}
    rows = []
    for x in old:
        candidate = lookup[(x["symbol"], x["announcement_date"])]
        rows.append({"symbol": x["symbol"], "filing_date": x["announcement_date"],
                     "old_entry_date": x["entry_date"], "new_entry_date": candidate["entry_date"],
                     "old_exit_1d": x["exit_date_1d"], "new_exit_1d": candidate["exit_1d"],
                     "old_exit_5d": x["exit_date_5d"], "new_exit_5d": candidate["exit_5d"],
                     "five_day_exit_changed": x["exit_date_5d"] != candidate["exit_5d"]})
    receipt = {"schema": "sentiment_v3_calendar_comparison_redacted_1", "checked_at_utc": now(),
               "old_alignment_sha256": digest(OLD), "new_alignment_sha256": digest(NEW),
               "old_eight_matched": len(rows), "five_day_exit_changes": sum(x["five_day_exit_changed"] for x in rows),
               "rows": rows, "reason": "v2 stepped only WHOLE dates; v3 counts MORNING half sessions as trading sessions",
               "old_results_overwritten": False}
    write_json(PUBLIC / "calendar_comparison_redacted.json", receipt)
    return {"matched": len(rows), "five_day_exit_changes": receipt["five_day_exit_changes"]}


if __name__ == "__main__":
    print(json.dumps(compare()))
