"""Synthetic, public wire objects for the model-free backend compatibility gate."""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path

from jsonschema import Draft202012Validator


def build(base: dict, source_schema: dict) -> list[dict]:
    cases: list[dict] = []
    for disposition, reason in (
        ("COMPANY_EVENTS_FOUND", "NONE"),
        ("NO_IDENTIFIABLE_EVENT", "NO_EVENT"),
        ("NO_SUPPORTED_COMPANY", "NO_COMPANY_LINK"),
        ("UNDETERMINED", "OTHER"),
    ):
        for market, direction in (("NO", "NOT_REPORTED"), ("YES", "UP"),
                                  ("UNKNOWN", "UNKNOWN")):
            obj = copy.deepcopy(base)
            obj["article_disposition"] = disposition
            obj["article_unknown_reason"] = reason
            obj["market_outcome_reported"] = market
            obj["market_outcome_direction"] = direction
            if disposition != "COMPANY_EVENTS_FOUND":
                obj["records"] = []
            cases.append({"name": f"root_{disposition}_{market}",
                          "expected_valid": True, "object": obj})

    for indirect in (False, True):
        for inference in (False, True):
            for unknown in (False, True):
                obj = copy.deepcopy(base)
                record = obj["records"][0]
                record["impact_path"] = "INDIRECT" if indirect else "DIRECT"
                record["relationship_evidence_spans"] = (
                    [{"role": "RELATIONSHIP", "text": "Synthetic company relationship."}]
                    if indirect else [])
                record["statement_status"] = (
                    "ANNOTATOR_INFERENCE" if inference else "EXPLICIT_IN_ARTICLE")
                record["inference_premise"] = (
                    "Synthetic premise in the same article." if inference else None)
                record["business_impact_direction"] = (
                    "UNKNOWN" if unknown else "NO_CLEAR_IMPACT")
                record["unknown_reason"] = (
                    "AMBIGUOUS_DIRECTION" if unknown else "NONE")
                cases.append({"name": f"record_indirect_{int(indirect)}_inference_{int(inference)}_unknown_{int(unknown)}",
                              "expected_valid": True, "object": obj})

    more = copy.deepcopy(base)
    more["article_evidence"] = [
        {"role": "EVENT", "text": f"Synthetic evidence {index}."}
        for index in range(21)
    ]
    cases.append({"name": "unbounded_article_evidence_21", "expected_valid": True,
                  "object": more})

    more = copy.deepcopy(base)
    more["records"] = [copy.deepcopy(base["records"][0]) for _ in range(21)]
    cases.append({"name": "unbounded_records_21", "expected_valid": True,
                  "object": more})

    more = copy.deepcopy(base)
    more["records"][0]["event_description"] = '公司甲 announced "A\\B".\nNext line.'
    more["records"][0]["evidence_spans"][0]["text"] = '公司甲 "A\\B"\nNext line.'
    cases.append({"name": "unicode_quotes_backslash_newline", "expected_valid": True,
                  "object": more, "serialization": "raw_unicode"})

    more = copy.deepcopy(base)
    more["records"][0]["event_description"] = "Twenty-five spaces: " + (" " * 25) + "end."
    cases.append({"name": "long_spaces_inside_string", "expected_valid": True,
                  "object": more})

    more = copy.deepcopy(base)
    cases.append({"name": "pretty_long_indent", "expected_valid": True,
                  "object": more, "serialization": "pretty_long_indent"})

    more = dict(reversed(list(copy.deepcopy(base).items())))
    more["records"][0] = dict(reversed(list(more["records"][0].items())))
    cases.append({"name": "reordered_properties", "expected_valid": True,
                  "object": more})

    validator = Draft202012Validator(source_schema)
    for case in cases:
        if not validator.is_valid(case["object"]):
            raise AssertionError(case["name"])
    return cases


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--source-schema", type=Path, required=True)
    ap.add_argument("--base-sentinels", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    if args.out.exists():
        raise FileExistsError(args.out)
    schema = json.loads(args.source_schema.read_text(encoding="utf-8"))
    base = json.loads(args.base_sentinels.read_text(encoding="utf-8"))[0]["object"]
    cases = build(base, schema)
    args.out.write_text(json.dumps(cases, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
    print(json.dumps({"cases": len(cases), "root_branches": 12,
                      "record_branches": 8, "model_calls": 0}))
