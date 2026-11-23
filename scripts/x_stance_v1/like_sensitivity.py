"""Sensitivity diagnostic for the like floor, from posts already in the store. Read-only; no request, no label.

The floor in force stays what the configuration says (20). This only shows what other floors WOULD admit, so the
owner can see what the rule removes. It must not be used to pick the floor that gives the nicest result: nothing
here looks at stance or at later prices.

For floors 0 / 5 / 10 / 20 (and any others asked for): post-topic pairs, distinct posts, distinct accounts, and
coverage by topic, language, New York date and source (keyword search / watched author), plus the age of a post
when it was fetched. A post whose like count is MISSING is reported on its own line: it is not a post with 0 likes.
The like count is the one stored at fetch time - never a later one.
"""
from __future__ import annotations

import json
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timezone
from statistics import median
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
FLOORS = (0, 5, 10, 20)


def _t(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def rows(db, version: str) -> list[dict]:
    """Every pair the vocabulary matched in one sample version, whatever the like rule then did with it."""
    out = []
    watch = set()
    try:
        watch = {r[0] for r in db.execute("SELECT DISTINCT rp.post_id FROM x_run_posts rp JOIN x_query_runs r ON r.run_id = rp.run_id WHERE r.topic_id LIKE 'watch:%'")}
        kw = {r[0] for r in db.execute("SELECT DISTINCT rp.post_id FROM x_run_posts rp JOIN x_query_runs r ON r.run_id = rp.run_id WHERE r.topic_id NOT LIKE 'watch%'")}
    except sqlite3.OperationalError:
        kw = set()
    q = ("SELECT t.topic_id, t.target_id, t.quality, p.post_id, p.author_id, p.lang, p.created_at_utc, p.fetched_at_utc, p.ingest_mode, p.engagement_snapshot_json "
         "FROM x_post_topics t JOIN x_posts p USING(provider, post_id) WHERE t.topic_version=?")
    for r in db.execute(q, (version,)):
        e = json.loads(r["engagement_snapshot_json"]) if r["engagement_snapshot_json"] else {}
        likes = e.get("likeCount")
        src = "both" if (r["post_id"] in watch and r["post_id"] in kw) else "watched_author" if r["post_id"] in watch else "keyword"
        out.append({"topic_id": r["topic_id"], "target_id": r["target_id"], "quality": r["quality"], "post_id": r["post_id"], "author_id": r["author_id"], "lang": r["lang"] or "und",
                    "ny_date": _t(r["created_at_utc"]).astimezone(NY).date().isoformat(), "age_hours_at_fetch": (_t(r["fetched_at_utc"]) - _t(r["created_at_utc"])).total_seconds() / 3600,
                    "mode": r["ingest_mode"], "likes": likes if isinstance(likes, int) else None, "source": src})
    return out


def _cover(rs: list[dict]) -> dict:
    ages = sorted(r["age_hours_at_fetch"] for r in rs)
    q = lambda p: round(ages[min(len(ages) - 1, int(p * len(ages)))], 1) if ages else None
    by = lambda k: dict(sorted(Counter(r[k] for r in rs).items()))
    return {"pairs": len(rs), "posts": len({r["post_id"] for r in rs}), "accounts": len({r["author_id"] for r in rs}), "by_topic": by("topic_id"), "by_lang": by("lang"),
            "by_source": by("source"), "by_mode": by("mode"), "ny_dates_covered": len({r["ny_date"] for r in rs}),
            "age_hours_at_fetch": {"median": round(median(ages), 1) if ages else None, "p10": q(0.10), "p90": q(0.90)}}


def table(db, version: str, floors=FLOORS, in_force: int = 20) -> dict:
    rs = rows(db, version)
    known = [r for r in rs if r["likes"] is not None]
    missing = [r for r in rs if r["likes"] is None]
    out = {"sample_version": version, "floor_in_force": in_force, "matched_pairs_all": len(rs), "like_count_missing": _cover(missing), "floors": {},
           "likes_distribution_known": {"0": sum(1 for r in known if r["likes"] == 0), "1-4": sum(1 for r in known if 1 <= r["likes"] < 5), "5-9": sum(1 for r in known if 5 <= r["likes"] < 10),
                                        "10-19": sum(1 for r in known if 10 <= r["likes"] < 20), "20-99": sum(1 for r in known if 20 <= r["likes"] < 100), "100+": sum(1 for r in known if r["likes"] >= 100)},
           "warning": "diagnostic only: the floor in force is unchanged, and a floor must not be chosen by which one gives a preferred result"}
    full_dates = {r["ny_date"] for r in rs}
    for f in floors:
        keep = [r for r in known if r["likes"] >= f]
        c = _cover(keep)
        per_date = defaultdict(int)
        for r in keep:
            per_date[r["ny_date"]] += 1
        c["ny_dates_with_no_pair_left"] = len(full_dates - set(per_date))
        c["pairs_per_ny_date"] = {"median": median(sorted(per_date.values())) if per_date else 0, "min": min(per_date.values()) if per_date else 0}
        c["share_of_known"] = round(len(keep) / len(known), 4) if known else None
        out["floors"][str(f)] = c
    # what the rule in force actually did, as a cross-check against the stored membership
    out["stored_membership_check"] = {"FILTERED_LOW_LIKES": sum(1 for r in rs if r["quality"] == "FILTERED_LOW_LIKES"), "FILTERED_LIKES_UNKNOWN": sum(1 for r in rs if r["quality"] == "FILTERED_LIKES_UNKNOWN"),
                                      "known_below_floor_in_force": sum(1 for r in known if r["likes"] < in_force), "missing": len(missing)}
    return out


def all_versions(db, floors=FLOORS, in_force: int = 20) -> dict:
    vs = [r[0] for r in db.execute("SELECT DISTINCT topic_version FROM x_post_topics ORDER BY 1")]
    return {"generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "versions": [table(db, v, floors, in_force) for v in vs]}


if __name__ == "__main__":
    import sys
    con = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    res = all_versions(con)
    if len(sys.argv) > 2:
        open(sys.argv[2], "w", encoding="utf-8").write(json.dumps(res, indent=1, ensure_ascii=False) + "\n")
    for v in res["versions"]:
        print(v["sample_version"][-40:], "all", v["matched_pairs_all"], "missing", v["like_count_missing"]["pairs"],
              {f: (c["pairs"], c["posts"], c["accounts"], c["ny_dates_covered"]) for f, c in v["floors"].items()})
