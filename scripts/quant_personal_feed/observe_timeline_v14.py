"""Three five-second local-cache samples; this script never calls providers."""
from __future__ import annotations

import argparse
import json
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

from .timeline import read_timeline


ROOT = Path(__file__).resolve().parents[2]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    feed = json.loads((ROOT / "configs/quant_portal_personal_feeds_v11_20261116.json").read_text(encoding="utf-8"))
    portal = ROOT / feed["portal_db"]
    cache = ROOT / feed["cache_db"]
    with sqlite3.connect(portal.resolve().as_uri() + "?mode=ro", uri=True) as db:
        owner = db.execute("SELECT id FROM auth_user WHERE username=?", (feed["owner_username"],)).fetchone()
    if owner is None:
        raise RuntimeError("Owner account missing")
    samples = []
    for index in range(3):
        if index:
            time.sleep(5)
        stamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        symbols = []
        for symbol in ("TQQQ", "NVDA"):
            result = read_timeline(cache, owner[0], symbol=symbol, session="OVERNIGHT", range_key="2h")
            times = [point["source_at"] for point in result["points"]]
            symbols.append({"symbol": symbol, "displayed_points": len(times),
                            "latest_source_at": max(times) if times else None,
                            "related_news": len(result["news"]),
                            "cache_state": result["cache_state"]})
        samples.append({"read_at_utc": stamp, "symbols": symbols})
    output = {"schema": "quant_portal_personal_timeline_v14_local_samples",
              "sample_count": len(samples), "requested_interval_seconds": 5,
              "supplier_requests_from_script": 0, "source": "OWNER_BOUND_LOCAL_SQLITE_CACHE",
              "samples": samples,
              "interpretation_limit": "Local cache progress only; browser visual acceptance is recorded separately."}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"receipt": str(args.output), "samples": len(samples),
                      "symbols": [x["symbol"] for x in symbols]}))


if __name__ == "__main__":
    main()
