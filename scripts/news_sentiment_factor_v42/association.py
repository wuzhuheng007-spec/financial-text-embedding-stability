"""Source-grounded segment association; never changes scores or original segments."""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections import Counter
from datetime import datetime, timezone

from scripts.quant_personal_feed.event_rules import ENTITY_RULES
from scripts.news_sentiment_factor_v41.score import DB
from .worker import PRIVATE, PUBLIC, ROOT, open_queue, now, write_json

RULES=ROOT/"configs/news_sentiment_factor_v42_aliases_20261117.json"
VERSION="explicit_segment_alias_v42_1"


def associate(text: str) -> dict:
    matches=[]
    for entity,(name,kind,patterns) in ENTITY_RULES.items():
        if kind!="COMPANY":
            continue
        for pattern,flags in patterns:
            found=re.search(pattern,text,flags)
            if found:
                matches.append({"entity_id":entity,"phrase":found.group(0),"start":found.start(),"end":found.end(),
                                "basis":"BASE_VERIFIED_ALIAS","rule_version":VERSION})
                break
    extra=json.loads(RULES.read_text(encoding="utf-8"))["additions"]
    for item in extra:
        patterns=[(re.escape(x),re.I if x.isascii() else 0) for x in item["phrases"]]
        patterns.append((item["ticker_pattern"],0))
        for pattern,flags in patterns:
            found=re.search(pattern,text,flags)
            if found:
                matches.append({"entity_id":item["entity_id"],"phrase":found.group(0),"start":found.start(),
                                "end":found.end(),"basis":"OFFICIAL_COMPANY_ALIAS_AND_TICKER",
                                "rule_version":VERSION})
                break
    matches.sort(key=lambda x:(x["start"],x["entity_id"]))
    ids=sorted({x["entity_id"] for x in matches})
    return {"status":"ONE_EXPLICIT_COMPANY" if len(ids)==1 else "MULTI_COMPANY_UNRESOLVED" if ids else "NO_EXPLICIT_COMPANY",
            "entity_id":ids[0] if len(ids)==1 else None,"entity_ids":ids,"matches":matches,
            "rule_version":VERSION}


def freeze_and_annotate() -> dict:
    con=open_queue()
    con.executescript("""
      CREATE TABLE IF NOT EXISTS association_revisions(
        rule_version TEXT PRIMARY KEY,rule_sha256 TEXT NOT NULL,created_at_utc TEXT NOT NULL,
        reference_sha256 TEXT NOT NULL,review_units INTEGER NOT NULL);
      CREATE TABLE IF NOT EXISTS association_annotations(
        provider TEXT NOT NULL,article_id TEXT NOT NULL,version INTEGER NOT NULL,segment_id TEXT NOT NULL,
        rule_version TEXT NOT NULL,input_sha256 TEXT NOT NULL,status TEXT NOT NULL,entity_id TEXT,
        entity_ids_json TEXT NOT NULL,matches_json TEXT NOT NULL,annotated_at_utc TEXT NOT NULL,
        PRIMARY KEY(provider,article_id,version,segment_id,rule_version));
    """)
    existing=con.execute("SELECT 1 FROM association_revisions WHERE rule_version=?",(VERSION,)).fetchone()
    if existing:
        raise FileExistsError("ASSOCIATION_RULE_ALREADY_FROZEN")
    reference=PRIVATE/"association_reference_private.json"
    source=PRIVATE/"association_review_frozen_private.json"
    if not source.exists() or not reference.exists():
        raise FileNotFoundError("FROZEN_SOURCE_REVIEW_REQUIRED")
    ref=json.loads(reference.read_text(encoding="utf-8"))["rows"]
    rows=json.loads(source.read_text(encoding="utf-8"))["rows"]
    if len(ref)!=len(rows) or any(a["input_sha256"]!=b["input_sha256"] for a,b in zip(ref,rows)):
        raise ValueError("REFERENCE_AND_FROZEN_INPUT_MISMATCH")
    evaluation=[]
    for index,(item,truth) in enumerate(zip(rows,ref)):
        old=json.loads(item["entity_candidates_json"])
        old_ids=sorted({x["id"] for x in old})
        new=associate(item["text"])
        expected=truth["expected_entity_ids"]
        evaluation.append({"index":index,"cohort":item["cohort"],"input_sha256":item["input_sha256"],
                           "old_ids":old_ids,"new_ids":new["entity_ids"],"expected_ids":expected,
                           "old_exact":old_ids==expected,"new_exact":new["entity_ids"]==expected,
                           "new_false_ids":sorted(set(new["entity_ids"])-set(expected)),
                           "new_missed_ids":sorted(set(expected)-set(new["entity_ids"]))})
    private_eval=PRIVATE/"association_review_result_private.json"
    write_json(private_eval,{"schema":"news_sentiment_v42_association_review_1","rows":evaluation})
    checked=now()
    with sqlite3.connect(f"file:{DB.as_posix()}?mode=ro",uri=True,timeout=5) as scores:
        scores.row_factory=sqlite3.Row
        segments=scores.execute("SELECT provider,article_id,version,segment_id,text,input_sha256 FROM segments").fetchall()
    con.execute("BEGIN IMMEDIATE")
    try:
        con.execute("INSERT INTO association_revisions VALUES(?,?,?,?,?)",
                    (VERSION,hashlib.sha256(RULES.read_bytes()).hexdigest(),checked,
                     hashlib.sha256(reference.read_bytes()).hexdigest(),len(rows)))
        for segment in segments:
            mapped=associate(segment["text"])
            con.execute("""INSERT INTO association_annotations VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                        (segment["provider"],segment["article_id"],segment["version"],segment["segment_id"],
                         VERSION,segment["input_sha256"],mapped["status"],mapped["entity_id"],
                         json.dumps(mapped["entity_ids"]),json.dumps(mapped["matches"],ensure_ascii=False),checked))
        con.commit()
    except Exception:
        con.rollback()
        raise
    result={"schema":"news_sentiment_v42_association_review_redacted_1","rule_version":VERSION,
            "rule_effective_at_utc":checked,"units":len(rows),"held_out_units":sum(x["cohort"]=="HELD_OUT_DEVELOPMENT_CHECK" for x in evaluation),
            "old_exact":sum(x["old_exact"] for x in evaluation),"new_exact":sum(x["new_exact"] for x in evaluation),
            "new_correct_links":sum(len(set(x["new_ids"])-set(x["old_ids"])) for x in evaluation if x["new_exact"]),
            "new_false_links":sum(len(x["new_false_ids"]) for x in evaluation),
            "new_missed_links":sum(len(x["new_missed_ids"]) for x in evaluation),
            "unresolved_units":sum(len(x["new_ids"])>1 for x in evaluation),
            "annotated_segments":len(segments),"private_review_sha256":hashlib.sha256(private_eval.read_bytes()).hexdigest(),
            "reviewer":"CODEX_REVIEW_NOT_INDEPENDENT_GOLD","no_model_rerun":True}
    write_json(PUBLIC/"association_review_redacted.json",result)
    con.close()
    return result


if __name__=="__main__":
    print(json.dumps(freeze_and_annotate(),ensure_ascii=False))
