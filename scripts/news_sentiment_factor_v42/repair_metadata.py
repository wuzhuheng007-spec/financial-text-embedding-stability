"""One-time v4.2 metadata correction; source scores and old receipts untouched."""
from __future__ import annotations

import hashlib
import json
import sqlite3

from scripts.news_sentiment_factor_v41.score import DB
from scripts.news_sentiment_factor_v41.worker import utc
from .worker import PRIVATE, PUBLIC, open_queue, write_json


def main() -> dict:
    target=PRIVATE/"metadata_correction_private.json"
    if target.exists():
        raise FileExistsError(target)
    queue=open_queue()
    with sqlite3.connect(f"file:{DB.as_posix()}?mode=ro",uri=True) as score:
        score.row_factory=sqlite3.Row
        items=[]
        for job in queue.execute("SELECT provider,article_id,version,received_at,completed_at,recovery_delayed FROM jobs WHERE state='DONE'"):
            data=score.execute("""SELECT scored_at_utc,run_id FROM scores WHERE provider=? AND article_id=? AND version=?
                ORDER BY scored_at_utc DESC LIMIT 1""",(job["provider"],job["article_id"],job["version"])).fetchone()
            if data is None:
                continue
            run=queue.execute("SELECT started_at FROM worker_runs WHERE run_id=?",(data["run_id"],)).fetchone()
            delayed=bool(run and utc(job["received_at"])<utc(run[0]))
            if job["completed_at"]!=data["scored_at_utc"] or (delayed and not job["recovery_delayed"]):
                items.append({"provider":job["provider"],"article_id":job["article_id"],"version":job["version"],
                              "previous_completed_at":job["completed_at"],"actual_scored_at":data["scored_at_utc"],
                              "recovery_delayed":delayed})
    queue.execute("BEGIN IMMEDIATE")
    try:
        for item in items:
            queue.execute("UPDATE jobs SET completed_at=?,recovery_delayed=max(recovery_delayed,?) WHERE provider=? AND article_id=? AND version=?",
                          (item["actual_scored_at"],int(item["recovery_delayed"]),item["provider"],item["article_id"],item["version"]))
        queue.commit()
    except Exception:
        queue.rollback()
        raise
    write_json(target,{"schema":"v42_metadata_correction_1","reason":
        "Reconciliation initially used its own time as completion time; corrected to immutable actual score time. Gap-received versions marked RECOVERY_DELAYED.",
        "rows":items})
    result={"schema":"v42_metadata_correction_redacted_1","corrected_jobs":len(items),
            "recovery_delayed_jobs":sum(x["recovery_delayed"] for x in items),
            "private_sha256":hashlib.sha256(target.read_bytes()).hexdigest(),
            "source_score_rows_changed":0,"v41_receipts_changed":0}
    write_json(PUBLIC/"metadata_correction_redacted.json",result)
    return result


if __name__=="__main__":
    print(json.dumps(main(),ensure_ascii=False))
