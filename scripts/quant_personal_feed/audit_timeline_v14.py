"""Read-only real-cache audit; public receipt contains no prices or news text."""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .timeline import read_timeline


ROOT = Path(__file__).resolve().parents[2]


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--feed-config", type=Path,
                        default=ROOT / "configs/quant_portal_personal_feeds_v11_20261116.json")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--upgrade-start-utc", help="Actual new-worker start used to audit new raw-response retention")
    parser.add_argument("--snapshot-path", type=Path,
                        help="New ignored private SQLite snapshot for a stable audit input; refuses overwrite")
    args = parser.parse_args()
    feed = json.loads(args.feed_config.read_text(encoding="utf-8"))
    cache_path = ROOT / feed["cache_db"]
    portal_path = ROOT / feed["portal_db"]
    with sqlite3.connect(portal_path) as db:
        owner = db.execute("SELECT id FROM auth_user WHERE username=?", (feed["owner_username"],)).fetchone()
    if owner is None:
        raise RuntimeError("Owner account missing")
    owner_id = owner[0]
    audit_path = cache_path
    if args.snapshot_path:
        audit_path = args.snapshot_path.resolve()
        if audit_path.exists():
            raise FileExistsError(f"Refusing to overwrite existing audit snapshot: {audit_path}")
        audit_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(cache_path.resolve().as_uri() + "?mode=ro", uri=True) as source:
            with sqlite3.connect(audit_path) as target:
                source.backup(target)
    db = sqlite3.connect(audit_path.resolve().as_uri() + "?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    try:
        now = datetime.now(timezone.utc)
        quote_count = db.execute("SELECT COUNT(*) FROM session_quotes WHERE owner_id=?", (owner_id,)).fetchone()[0]
        raw_count = db.execute("SELECT COUNT(*) FROM session_raw_responses").fetchone()[0]
        news_count = db.execute("SELECT COUNT(*) FROM news_items WHERE owner_id=?", (owner_id,)).fetchone()[0]
        old_raw_missing = db.execute("""SELECT COUNT(*) FROM session_quotes q LEFT JOIN session_raw_responses r
          ON r.sha256=q.raw_sha256 WHERE q.owner_id=? AND r.sha256 IS NULL""", (owner_id,)).fetchone()[0]
        states = [dict(row) for row in db.execute("""SELECT provider,state,worker_alive,
          heartbeat_at,last_success_at FROM provider_state ORDER BY provider""")]
        recent_raw = None
        if args.upgrade_start_utc:
            from .timeline import parse_utc
            if parse_utc(args.upgrade_start_utc) is None:
                raise ValueError("--upgrade-start-utc must be an ISO datetime with timezone")
            recent_raw = dict(db.execute("""SELECT COUNT(*) AS quote_rows,
              SUM(CASE WHEN r.sha256 IS NULL THEN 1 ELSE 0 END) AS rows_without_raw
              FROM session_quotes q LEFT JOIN session_raw_responses r ON r.sha256=q.raw_sha256
              WHERE q.owner_id=? AND q.received_at>=?""",
              (owner_id,args.upgrade_start_utc)).fetchone())
            recent_raw["upgrade_start_utc"] = args.upgrade_start_utc
        audited = []
        for symbol in ("TQQQ", "QQQ", "SOXX", "SPY", "NVDA", "MU"):
            result = read_timeline(audit_path, owner_id, symbol=symbol, range_key="all", now=now)
            points = result.get("points", [])
            picks = sorted({0, len(points)//2, len(points)-1}) if points else []
            point_checks = []
            for index in picks:
                point = points[index]
                match = db.execute("""SELECT COUNT(*) FROM session_quotes
                  WHERE owner_id=? AND symbol=? AND session=? AND source_at=?
                    AND received_at=? AND price_text=?""", (owner_id,symbol,
                    point["session"],point["source_at"],point["received_at"],
                    point["price_text"])).fetchone()[0]
                # Stored ISO precision can differ from the normalized API view.
                if not match:
                    rows = db.execute("""SELECT source_at,received_at,price_text FROM session_quotes
                      WHERE owner_id=? AND symbol=? AND session=? AND price_text=?""",
                      (owner_id,symbol,point["session"],point["price_text"])).fetchall()
                    from .timeline import parse_utc
                    match = sum(1 for row in rows if parse_utc(row["source_at"]) == parse_utc(point["source_at"])
                                and parse_utc(row["received_at"]) == parse_utc(point["received_at"]))
                point_checks.append({"sample_index": index, "saved_row_exact_match": bool(match)})
            news_checks = []
            for item in result.get("news", [])[:3]:
                row = db.execute("""SELECT v.published_at_utc,i.first_received_at FROM news_items i
                  JOIN news_versions v ON v.item_key=i.item_key AND v.version=i.latest_version
                  WHERE i.owner_id=? AND i.item_key=?""", (owner_id,item["item_key"])).fetchone()
                from .timeline import parse_utc
                news_checks.append({"supplier_time_matches_saved_version": bool(row and
                    parse_utc(row["published_at_utc"]) == parse_utc(item["published_at_utc"])),
                    "first_receipt_matches_saved_item": bool(row and
                    parse_utc(row["first_received_at"]) == parse_utc(item["first_received_at"]))})
            audited.append({"symbol": symbol,
                "coverage": [{"session": row["session"], "saved_rows": row["saved_rows"],
                              "source_time_known_rows": row["source_time_known_rows"],
                              "first_source_at": row["first_source_at"],
                              "last_source_at": row["last_source_at"]} for row in result["coverage"]],
                "returned_points": len(points), "valid_unique_points": result["point_metrics"]["valid_unique_points"],
                "sampled": result["point_metrics"]["sampled"], "point_checks": point_checks,
                "related_news_returned": len(result["news"]), "news_time_checks": news_checks})
        output = {"schema": "quant_portal_personal_timeline_v14_audit",
            "observed_at_utc": now.isoformat().replace("+00:00", "Z"),
            "observed_at_asia_shanghai": now.astimezone(ZoneInfo("Asia/Shanghai")).isoformat(),
            "feed_config_sha256": sha(args.feed_config),
            "timeline_config_sha256": sha(ROOT / "configs/quant_portal_personal_timeline_v14_20261116.json"),
            "timeline_code_sha256": sha(ROOT / "scripts/quant_personal_feed/timeline.py"),
            "store_code_sha256": sha(ROOT / "scripts/quant_personal_feed/store.py"),
            "classifier_code_sha256": sha(ROOT / "scripts/quant_personal_feed/event_rules.py"),
            "audit_code_sha256": sha(Path(__file__)),
            "private_input_snapshot_path": str(audit_path.relative_to(ROOT)) if audit_path.is_relative_to(ROOT) else str(audit_path),
            "private_input_snapshot_sha256": sha(audit_path) if args.snapshot_path else None,
            "input_snapshot_bytes": audit_path.stat().st_size,
            "cache_db_bytes": cache_path.stat().st_size,
            "cache_wal_bytes": cache_path.with_name(cache_path.name+"-wal").stat().st_size
              if cache_path.with_name(cache_path.name+"-wal").exists() else 0,
            "session_quote_rows": quote_count, "raw_response_rows": raw_count,
            "quote_rows_without_retained_raw_response": old_raw_missing,
            "post_upgrade_raw_availability": recent_raw,
            "news_item_rows": news_count,
            "provider_states": states, "audited_symbols": audited,
            "supplier_requests_added_by_audit": 0,
            "scope_note": "Owner-only read of saved cache; prices, bodies and item keys omitted."}
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(output,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
        print(json.dumps({"receipt": str(args.output), "symbols": len(audited),
                          "quote_rows": quote_count, "news_items": news_count,
                          "checks_passed": all(x["saved_row_exact_match"] for a in audited for x in a["point_checks"])
                           and all(all(x.values()) for a in audited for x in a["news_time_checks"])},
                         ensure_ascii=False))
    finally:
        db.close()


if __name__ == "__main__":
    main()
