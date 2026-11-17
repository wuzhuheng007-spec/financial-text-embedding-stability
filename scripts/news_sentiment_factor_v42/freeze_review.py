"""Freeze source-only association development rows before v4.2 rule changes."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import Counter

from scripts.news_sentiment_factor_v41.score import DB
from scripts.news_sentiment_factor_v42.worker import PRIVATE, PUBLIC, write_json


def main() -> dict:
    target=PRIVATE/"association_review_frozen_private.json"
    if target.exists():
        raise FileExistsError(target)
    old=json.loads((PRIVATE.parent/"news_sentiment_factor_v41_20261117/source_review_comparison_private.json").read_text(encoding="utf-8"))
    old_keys={(x["provider"],x["article_id"],x["version"],x["segment_id"]) for x in old}
    with sqlite3.connect(f"file:{DB.as_posix()}?mode=ro",uri=True) as con:
        con.row_factory=sqlite3.Row
        rows=[dict(r) for r in con.execute("""SELECT a.received_at_utc,s.provider,s.article_id,s.version,s.segment_id,
          s.kind,s.text,s.input_sha256,s.association_status,s.company_association,s.entity_candidates_json
          FROM segments s JOIN articles a USING(provider,article_id,version)
          ORDER BY a.received_at_utc,s.provider,s.article_id,s.version,s.segment_id""")]
    by_key={(r["provider"],r["article_id"],r["version"],r["segment_id"]):r for r in rows}
    chosen=[]
    for previous in old:
        key=(previous["provider"],previous["article_id"],previous["version"],previous["segment_id"])
        if key in by_key:
            chosen.append({**by_key[key],"cohort":"KNOWN_REGRESSION","prior_source_review":previous["company_binding_review"]})
    # A predeclared missing watchlist identity and balanced status/source strata.
    for r in rows:
        key=(r["provider"],r["article_id"],r["version"],r["segment_id"])
        if key in old_keys or len(chosen)>=20:
            continue
        if r["association_status"]=="NO_EXPLICIT_COMPANY" and any(x in r["text"] for x in ("博通","Broadcom","AVGO")):
            chosen.append({**r,"cohort":"ALIAS_DIAGNOSTIC"})
    quotas={"ONE_EXPLICIT_COMPANY":7,"MULTI_COMPANY_UNRESOLVED":6,"NO_EXPLICIT_COMPANY":7}
    counts=Counter(r["association_status"] for r in chosen if r["cohort"]=="BALANCED_DEVELOPMENT")
    for r in rows:
        if len(chosen)>=32:
            break
        key=(r["provider"],r["article_id"],r["version"],r["segment_id"])
        if key in {(x["provider"],x["article_id"],x["version"],x["segment_id"]) for x in chosen}:
            continue
        status=r["association_status"]
        if counts[status]<quotas[status]:
            chosen.append({**r,"cohort":"BALANCED_DEVELOPMENT"})
            counts[status]+=1
    # Reserved rows are frozen before editing but excluded from rule debugging.
    for r in reversed(rows):
        if len(chosen)>=40:
            break
        key=(r["provider"],r["article_id"],r["version"],r["segment_id"])
        if key not in {(x["provider"],x["article_id"],x["version"],x["segment_id"]) for x in chosen}:
            chosen.append({**r,"cohort":"HELD_OUT_DEVELOPMENT_CHECK"})
    payload={"schema":"news_sentiment_v42_association_review_freeze_1","selection_rule":
             "old 12; earliest missing Broadcom aliases up to 8; balanced old statuses in source order; latest 8 held-out",
             "reviewer":"CODEX_REVIEW_NOT_INDEPENDENT_GOLD","rows":chosen}
    write_json(target,payload)
    public={"schema":payload["schema"],"units":len(chosen),"cohorts":dict(Counter(r["cohort"] for r in chosen)),
            "status_counts":dict(Counter(r["association_status"] for r in chosen)),
            "input_sha256":hashlib.sha256(target.read_bytes()).hexdigest(),
            "ids_and_hashes":[{k:r[k] for k in ("provider","article_id","version","segment_id","input_sha256","cohort")}
                              for r in chosen]}
    write_json(PUBLIC/"association_review_freeze_redacted.json",public)
    return public


if __name__=="__main__":
    print(json.dumps(main(),ensure_ascii=False))
