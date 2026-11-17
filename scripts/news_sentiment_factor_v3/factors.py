"""Aggregate frozen text scores without reading returns or market bars."""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

from .source import CONFIG, PRIVATE, PUBLIC, digest, now, write_json
from .score import INPUT, SCORES


def build():
    target = PRIVATE / "event_factors_private.jsonl"
    if target.exists():
        raise FileExistsError("EVENT_FACTORS_ALREADY_FROZEN")
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    frozen = json.loads(INPUT.read_text(encoding="utf-8"))
    run = json.loads((PUBLIC / "score_run_redacted.json").read_text(encoding="utf-8"))
    if run["input_sha256"] != digest(INPUT) or run["scores_sha256"] != digest(SCORES) or run["config_sha256"] != digest(CONFIG):
        raise ValueError("FROZEN_SCORE_IDENTITY_DRIFT")
    scores = [json.loads(line) for line in SCORES.read_text(encoding="utf-8").splitlines()]
    if len(scores) != len(frozen["rows"]) or len({x["block_id"] for x in scores}) != len(scores):
        raise ValueError("BLOCK_SCORE_COVERAGE_OR_DUPLICATES")
    lookup = {x["block_id"]: x for x in scores}
    by_event = defaultdict(lambda: defaultdict(list))
    for row in frozen["rows"]:
        result = lookup.get(row["block_id"])
        if result is None or result["text_sha256"] != row["text_sha256"] or result["source_sha256"] != row["source_sha256"]:
            raise ValueError("SCORE_SOURCE_MISMATCH")
        probabilities = result["finbert_probabilities_by_id"]
        if abs(sum(probabilities.values()) - 1) > 1e-5 or result["lm"]["net"] is None:
            raise ValueError("INVALID_BLOCK_SCORE")
        by_event[row["event_id"]][row["section"]].append(result)
    review = json.loads((PRIVATE / "source_review_private.json").read_text(encoding="utf-8"))
    accepted = [x for x in review["rows"] if x["review_status"] == "ACCEPT_EARNINGS_EX99_1_SOURCE_HEADING"]
    accepted.sort(key=lambda x: (x["acceptanceDateTime"], x["accessionNumber"]))
    if len(accepted) != len(by_event):
        raise ValueError("ACCEPTED_EVENT_SCORE_COVERAGE_MISMATCH")
    past = defaultdict(list)
    output = []
    for source in accepted:
        event_id = source["accessionNumber"]
        sections = by_event[event_id]
        if not sections:
            raise ValueError("EMPTY_EVENT")
        section_lm = {s: sum(x["lm"]["net"] for x in rows) / len(rows) for s, rows in sections.items()}
        section_bert = {s: sum(x["finbert_net"] for x in rows) / len(rows) for s, rows in sections.items()}
        lm_f1 = sum(section_lm.values()) / len(section_lm)
        bert_f1 = sum(section_bert.values()) / len(section_bert)
        prior = past[source["symbol"]][-2:] if len(past[source["symbol"]]) >= 2 else []
        item = {"event_id": event_id, "symbol": source["symbol"],
                "acceptance_utc": source["header_acceptance_utc"],
                "filing_date": source["filingDate"], "time_evidence": "SEC_ACCEPTANCE_ANCHOR_NOT_FIRST_VISIBILITY",
                "source_sha256": source["exhibit_sha256"], "score_file_sha256": digest(SCORES),
                "section_block_counts": {s: len(rows) for s, rows in sections.items()},
                "section_lm_net": section_lm, "section_finbert_net": section_bert,
                "lm_F1": lm_f1, "finbert_F1": bert_f1,
                "lm_F2": lm_f1 - sum(x["lm_F1"] for x in prior) / 2 if prior else None,
                "finbert_F2": bert_f1 - sum(x["finbert_F1"] for x in prior) / 2 if prior else None,
                "F2_prior_event_ids": [x["event_id"] for x in prior],
                "document_count": 1, "config_sha256": digest(CONFIG)}
        output.append(item)
        past[source["symbol"]].append(item)
    target.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in output), encoding="utf-8")
    receipt = {"schema": "sentiment_v3_factor_coverage_redacted_1", "computed_at_utc": now(),
               "config_sha256": digest(CONFIG), "builder_sha256": digest(Path(__file__)),
               "score_file_sha256": digest(SCORES), "event_factor_private_sha256": digest(target),
               "events_F1": len(output), "events_F2": sum(x["finbert_F2"] is not None for x in output),
               "events_by_symbol": dict(Counter(x["symbol"] for x in output)),
               "block_count": sum(sum(x["section_block_counts"].values()) for x in output),
               "document_count_unique_values": sorted({x["document_count"] for x in output}),
               "count_feature_degenerate": len({x["document_count"] for x in output}) == 1,
               "labels_opened": False, "meaning": "official disclosure language development proxy"}
    write_json(PUBLIC / "factor_coverage_redacted.json", receipt)
    return {k: receipt[k] for k in ("events_F1", "events_F2", "block_count", "count_feature_degenerate")}


if __name__ == "__main__":
    print(json.dumps(build()))
