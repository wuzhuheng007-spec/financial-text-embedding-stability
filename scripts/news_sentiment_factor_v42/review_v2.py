"""Versioned evaluator correction: entity sets are unordered; v1 stays intact."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .worker import PRIVATE, PUBLIC, write_json


def main() -> dict:
    original=PRIVATE/"association_review_result_private.json"
    target=PRIVATE/"association_review_result_v2_private.json"
    if target.exists():
        raise FileExistsError(target)
    source=json.loads(original.read_text(encoding="utf-8"))["rows"]
    output=[]
    for row in source:
        expected=set(row["expected_ids"])
        old=set(row["old_ids"])
        new=set(row["new_ids"])
        output.append({**row,"old_exact":old==expected,"new_exact":new==expected,
                       "new_false_ids":sorted(new-expected),"new_missed_ids":sorted(expected-new)})
    write_json(target,{"schema":"news_sentiment_v42_association_review_2","revision_reason":
        "Entity membership is a set; v1 incorrectly treated multi-entity list order as semantic disagreement.",
        "v1_sha256":hashlib.sha256(original.read_bytes()).hexdigest(),"rows":output})
    public={"schema":"news_sentiment_v42_association_review_redacted_2","v1_preserved":True,
            "revision_reason":"order-only mismatch in multi-company reference",
            "units":len(output),"old_exact":sum(x["old_exact"] for x in output),
            "new_exact":sum(x["new_exact"] for x in output),
            "new_correct_links":sum(len(set(x["new_ids"])-set(x["old_ids"])) for x in output if x["new_exact"]),
            "new_false_links":sum(len(x["new_false_ids"]) for x in output),
            "new_missed_links":sum(len(x["new_missed_ids"]) for x in output),
            "unresolved_multi_company_units":sum(len(x["new_ids"])>1 for x in output),
            "unresolved_case_indices":[x["index"] for x in output if not x["new_exact"]],
            "private_review_sha256":hashlib.sha256(target.read_bytes()).hexdigest()}
    write_json(PUBLIC/"association_review_v2_redacted.json",public)
    return public


if __name__=="__main__":
    print(json.dumps(main(),ensure_ascii=False))
