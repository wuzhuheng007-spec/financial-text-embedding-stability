"""Add screening labels to legacy personal news without deleting old records."""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from .relevance_v15 import RULE_VERSION, screen
from .store import PersonalFeedStore

ROOT = Path(__file__).resolve().parents[2]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    if args.receipt.exists():
        raise FileExistsError(args.receipt)
    config = json.loads((ROOT / "configs/quant_portal_personal_feeds_v11_20261116.json").read_text(encoding="utf-8"))
    portal = ROOT / config["portal_db"]
    with sqlite3.connect(portal.resolve().as_uri() + "?mode=ro", uri=True) as db:
        row = db.execute("SELECT id FROM auth_user WHERE username=?", (config["owner_username"],)).fetchone()
        if row is None:
            raise RuntimeError("Owner missing")
        owner_id = row[0]
    store = PersonalFeedStore(args.database, owner_id)
    counts = Counter()
    try:
        rows = store.db.execute("""SELECT v.title,v.content,v.source_url,v.published_raw,v.received_at,v.content_sha256
          FROM news_versions v JOIN news_items i ON i.item_key=v.item_key AND v.version=i.latest_version
          WHERE i.owner_id=? ORDER BY i.latest_received_at DESC,i.item_key""", (owner_id,)).fetchall()
        for row in rows:
            item = {"title": row["title"], "content": row["content"],
                    "url": row["source_url"], "time": row["published_raw"]}
            decision = screen(item).as_dict()
            store.record_news_screening(owner_id,item,decision,received_at=row["received_at"],legacy=True)
            counts[decision["status"]] += 1
        after = store.db.execute("SELECT COUNT(*) FROM news_items WHERE owner_id=?", (owner_id,)).fetchone()[0]
        if after != len(rows):
            raise RuntimeError("Legacy news item count changed during screening backfill")
    finally:
        store.close()
    output = {"schema": "personal_demo_v15_legacy_screening", "at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00","Z"),
              "rule_version": RULE_VERSION, "legacy_latest_versions_reviewed": len(rows),
              "included": counts["INCLUDE"], "pending_review": counts["PENDING_REVIEW"],
              "excluded_hidden_not_deleted": counts["EXCLUDE"], "news_rows_after": after,
              "code_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "relevance_code_sha256": hashlib.sha256((ROOT / "scripts/quant_personal_feed/relevance_v15.py").read_bytes()).hexdigest(),
              "scope": "Existing private cache only; labels are deterministic relevance candidates, not verified news truth."}
    args.receipt.parent.mkdir(parents=True,exist_ok=True)
    args.receipt.write_text(json.dumps(output,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({k: output[k] for k in ("legacy_latest_versions_reviewed","included","pending_review","excluded_hidden_not_deleted","news_rows_after")},ensure_ascii=False))


if __name__ == "__main__":
    main()
