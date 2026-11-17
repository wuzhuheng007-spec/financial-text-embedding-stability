"""Check stored snapshot invariants without reading return labels or raw news."""
from __future__ import annotations

import json
import sqlite3
from collections import Counter
from datetime import datetime, timedelta, timezone

from .snapshot import candidate_members, create_snapshot, schema
from .worker import PUBLIC, QUEUE_DB, write_json


def run() -> dict:
    checks: dict[str, bool] = {}
    synthetic = [
        {"provider": "p", "article_id": "a", "version": 1,
         "received_at_utc": "2026-11-17T01:00:00+00:00", "scored_at_utc": "2026-11-17T01:02:00+00:00",
         "association_status": "ONE_EXPLICIT_COMPANY", "company_association": "NVDA",
         "kind": "BODY_SEGMENT", "segment_id": "s1", "event_group_id": "e",
         "source_text_hash": "old", "net_score": 0.1},
        {"provider": "p", "article_id": "a", "version": 2,
         "received_at_utc": "2026-11-17T02:00:00+00:00", "scored_at_utc": "2026-11-17T02:02:00+00:00",
         "association_status": "ONE_EXPLICIT_COMPANY", "company_association": "NVDA",
         "kind": "BODY_SEGMENT", "segment_id": "s2", "event_group_id": "e",
         "source_text_hash": "new", "net_score": -0.2},
    ]
    selected = candidate_members(synthetic)
    checks["latest_available_article_revision_only"] = len(selected) == 1 and selected[0]["version"] == 2

    with sqlite3.connect(":memory:") as temp:
        schema(temp)
        temp.execute("""INSERT INTO snapshots VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                     ("immutable",) + ("x",) * 13)
        temp.commit()
        try:
            temp.execute("UPDATE snapshots SET kind='changed' WHERE snapshot_id='immutable'")
        except sqlite3.IntegrityError as exc:
            checks["snapshot_update_rejected"] = "IMMUTABLE_SNAPSHOT" in str(exc)
        else:
            checks["snapshot_update_rejected"] = False
        try:
            temp.execute("DELETE FROM snapshots WHERE snapshot_id='immutable'")
        except sqlite3.IntegrityError as exc:
            checks["snapshot_delete_rejected"] = "IMMUTABLE_SNAPSHOT" in str(exc)
        else:
            checks["snapshot_delete_rejected"] = False

    try:
        create_snapshot((datetime.now(timezone.utc) + timedelta(minutes=1)).isoformat())
    except ValueError as exc:
        checks["future_as_of_rejected"] = str(exc) == "FUTURE_AS_OF_NOT_ALLOWED"
    else:
        checks["future_as_of_rejected"] = False

    with sqlite3.connect(f"file:{QUEUE_DB.as_posix()}?mode=ro", uri=True) as con:
        con.row_factory = sqlite3.Row
        snapshots = con.execute("SELECT * FROM snapshots ORDER BY generated_at_utc").fetchall()
        members = con.execute("SELECT * FROM snapshot_members").fetchall()
        company_rows = con.execute("SELECT * FROM snapshot_company").fetchall()
        labels = con.execute("SELECT status FROM future_label_queue").fetchall()
    by_id = {row["snapshot_id"]: row for row in snapshots}
    checks["stored_member_cutoffs"] = all(
        datetime.fromisoformat(row["received_at_utc"].replace("Z", "+00:00")) <=
        datetime.fromisoformat(by_id[row["snapshot_id"]]["as_of_utc"].replace("Z", "+00:00"))
        and datetime.fromisoformat(row["scored_at_utc"].replace("Z", "+00:00")) <=
        datetime.fromisoformat(by_id[row["snapshot_id"]]["as_of_utc"].replace("Z", "+00:00"))
        for row in members)
    checks["member_count_matches_manifest"] = all(
        sum(row["snapshot_id"] == snap["snapshot_id"] for row in members) == snap["member_count"]
        for snap in snapshots)
    checks["one_article_revision_per_snapshot_company"] = all(
        count == 1 for count in Counter((r["snapshot_id"], r["entity_id"], r["provider"], r["article_id"])
                                        for r in members).values())
    checks["no_labels_matured"] = all(row["status"] == "PENDING" for row in labels)
    checks["replays_not_prospective"] = all(row["kind"] == "DEVELOPMENT_REPLAY" for row in snapshots)
    receipt = {"schema": "news_sentiment_v42_snapshot_checks_1", "checks": checks,
               "passed": sum(checks.values()), "total": len(checks),
               "stored_snapshots": len(snapshots), "stored_members": len(members),
               "stored_company_rows": len(company_rows), "future_label_rows": len(labels),
               "snapshot_kinds": dict(Counter(row["kind"] for row in snapshots)),
               "no_return_labels_read": True, "synthetic_and_actual_checks_separate": True}
    write_json(PUBLIC / "snapshot_checks_redacted.json", receipt)
    if not all(checks.values()):
        raise AssertionError(json.dumps(receipt, ensure_ascii=False))
    return receipt


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False))
