"""Recheck a frozen private 30-item manifest; write only aggregates publicly."""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path

from .event_rules import CATEGORY_RULES, RULE_VERSION, classify


def review(sample_path: Path, private_path: Path, redacted_path: Path) -> dict:
    raw=sample_path.read_bytes()
    rows=json.loads(raw)["rows"]
    if len(rows)>30:
        raise ValueError("Development manifest exceeds frozen 30-item bound")
    detail=[]
    categories=Counter()
    entities=Counter()
    groups=defaultdict(list)
    phrase_failures=0
    for rank,row in enumerate(rows,1):
        label=classify(row)
        source_text=(str(row.get("title") or "")+"\n"+str(row.get("content") or "")).casefold()
        phrase_supported=all(x.get("phrase") is None or x["phrase"].casefold() in source_text
                             for x in label["entities"]+label["categories"])
        phrase_failures+=int(not phrase_supported)
        categories.update(x["id"] for x in label["categories"])
        entities.update(x["id"] for x in label["entities"])
        groups[label["event_group_id"]].append(rank)
        detail.append({"rank":rank,"item_key":row["item_key"],"content_sha256":row["content_sha256"],
                       "title":row.get("title"),"content":row.get("content"),"source_url":row.get("source_url"),
                       "labels":label,"phrase_present_in_supplied_text":phrase_supported})
    private_path.parent.mkdir(parents=True,exist_ok=True)
    private_path.write_text(json.dumps({"review_kind":"CODEX_REVIEW_DEVELOPMENT_NOT_INDEPENDENT_GOLD",
        "rules":RULE_VERSION,"rows":detail},ensure_ascii=False,indent=2),encoding="utf-8")
    aggregate={"schema":"personal_event_dev_review_redacted_v12",
               "review_kind":"CODEX_REVIEW_DEVELOPMENT_NOT_GENERAL_ACCURACY",
               "private_manifest_sha256":hashlib.sha256(raw).hexdigest(),
               "rules":RULE_VERSION,"sampled_items":len(rows),
               "selection":"latest version per item, received_at DESC then row id DESC; frozen before rule edits",
               "nonempty_title_count":sum(bool(x.get("title")) for x in rows),
               "source_url_count":sum(bool(x.get("source_url")) for x in rows),
               "phrase_presence_failures":phrase_failures,
               "entity_counts":dict(entities),"category_counts":dict(categories),
               "categories_not_seen":[key for key in CATEGORY_RULES if not categories[key]],
               "suspected_related_groups":sum(len(ranks)>1 for ranks in groups.values()),
               "related_group_items":sum(len(ranks) for ranks in groups.values() if len(ranks)>1),
               "same_team_content_review_notes":[
                   "One company-governance report also mentions a forthcoming earnings date; the earnings label is contextual, not a confirmed earnings event.",
                   "One statement about future employment and income conditions was not caught by the narrow employment phrases.",
                   "The semiconductor category can include sector-price flashes; it does not establish a supply or product-price event.",
                   "The source corpus is supplier reporting; original-source factual verification was not performed."],
               "classification_accuracy":"NOT_ESTIMATED"}
    redacted_path.parent.mkdir(parents=True,exist_ok=True)
    redacted_path.write_text(json.dumps(aggregate,ensure_ascii=False,indent=2),encoding="utf-8")
    return aggregate


def main() -> None:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample",type=Path,required=True)
    parser.add_argument("--private-output",type=Path,required=True)
    parser.add_argument("--redacted-output",type=Path,required=True)
    args=parser.parse_args()
    result=review(args.sample,args.private_output,args.redacted_output)
    print(json.dumps({key:result[key] for key in ("private_manifest_sha256","sampled_items",
          "phrase_presence_failures","category_counts","entity_counts","suspected_related_groups")},ensure_ascii=True))


if __name__=="__main__":
    main()
