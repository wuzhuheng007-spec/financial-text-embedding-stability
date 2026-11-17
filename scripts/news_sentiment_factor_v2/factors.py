"""Build frozen event-level F1/F2 without opening any return labels."""
from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from .corpus import CONFIG, PRIVATE, PUBLIC, file_sha, write_json


def build() -> dict:
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    frozen = json.loads((PRIVATE / "input_manifest_private.json").read_text(encoding="utf-8"))
    pre = json.loads((PUBLIC / "score_preflight_redacted.json").read_text(encoding="utf-8"))
    run = json.loads((PUBLIC / "score_run_redacted.json").read_text(encoding="utf-8"))
    if run["input_manifest_sha256"] != file_sha(PRIVATE / "input_manifest_private.json") or \
       run["config_sha256"] != file_sha(CONFIG) or \
       pre["scorer_code_sha256"] != file_sha(Path(__file__).with_name("score.py")):
        raise ValueError("SCORE_FREEZE_IDENTITY_MISMATCH")
    scores = [json.loads(line) for line in (PRIVATE / "block_scores_private.jsonl").read_text(encoding="utf-8").splitlines()]
    by_id = {r["block_id"]: r for r in scores}
    if len(scores) != len(by_id) or len(scores) != len(frozen["blocks"]):
        raise ValueError("BLOCK_SCORE_COVERAGE_OR_DUPLICATION")
    by_event: dict[str, list[dict]] = defaultdict(list)
    for source in frozen["blocks"]:
        score = by_id.get(source["block_id"])
        if score is None or score["text_sha256"] != source["text_sha256"] or \
           score["source_raw_sha256"] != source["raw_body_sha256"]:
            raise ValueError("SCORE_SOURCE_MISMATCH")
        probs = score["finbert_probabilities_by_id"]
        if abs(sum(probs.values()) - 1) > 1e-5:
            raise ValueError("MODEL_PROBABILITIES_INVALID")
        by_event[source["event_group_id"]].append(score)
    factors: list[dict] = []
    history: dict[str, list[dict]] = defaultdict(list)
    for meta in frozen["selection_ledger"]:
        group = meta["event_group_id"]
        rows = by_event[group]
        if not rows or any(r["lm"]["net"] is None for r in rows):
            raise ValueError("EVENT_WITHOUT_COMPLETE_BLOCK_SCORES")
        lm_f1 = sum(r["lm"]["net"] for r in rows) / len(rows)
        bert_f1 = sum(r["finbert_positive_minus_negative"] for r in rows) / len(rows)
        past = history[meta["symbol"]]
        min_prior = config["factor_candidates"]["minimum_prior_events"]
        basis = past[-min_prior:] if len(past) >= min_prior else []
        item = {"event_group_id": group, "document_id": meta["document_id"],
                "symbol": meta["symbol"], "announcement_date": meta["announcement_date"],
                "source_raw_sha256": meta["raw_body_sha256"],
                "block_ids": [r["block_id"] for r in rows], "block_count": len(rows),
                "lm_F1": lm_f1, "finbert_F1": bert_f1,
                "lm_F2": lm_f1 - sum(x["lm_F1"] for x in basis) / len(basis) if basis else None,
                "finbert_F2": bert_f1 - sum(x["finbert_F1"] for x in basis) / len(basis) if basis else None,
                "F2_prior_event_ids": [x["event_group_id"] for x in basis],
                "time_evidence": "RETROSPECTIVE_ANNOUNCEMENT_DATE_PROXY",
                "source_label": config["factor_candidates"]["source_label"],
                "config_sha256": file_sha(CONFIG),
                "score_file_sha256": file_sha(PRIVATE / "block_scores_private.jsonl")}
        factors.append(item)
        past.append(item)
    target = PRIVATE / "event_factors_private.jsonl"
    if target.exists():
        raise FileExistsError("EVENT_FACTORS_ALREADY_EXISTS")
    target.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in factors), encoding="utf-8")
    count = Counter(x["symbol"] for x in factors)
    f2 = Counter(x["symbol"] for x in factors if x["finbert_F2"] is not None)
    gate = config["development_evaluation"]
    fit_eligible = len(factors) >= gate["fit_gate_min_independent_events"] and \
        all(count[s] >= gate["fit_gate_min_events_per_issuer"] for s in config["issuer_symbols"])
    receipt = {"schema": "sentiment_v2_factor_coverage_redacted_1",
               "computed_at_utc": datetime.now(timezone.utc).isoformat(),
               "config_sha256": file_sha(CONFIG), "score_file_sha256": file_sha(PRIVATE / "block_scores_private.jsonl"),
               "event_factor_private_sha256": file_sha(target),
               "independent_issuer_event_groups": len(factors), "issuer_counts": dict(count),
               "F1_available_events": len(factors), "F2_available_events": sum(f2.values()),
               "F2_by_issuer": dict(f2), "four_group_fit_gate": "READY" if fit_eligible else "INSUFFICIENT_INDEPENDENT_EVENTS",
               "fit_calls": 0, "labels_read_at_factor_build": False,
               "interpretation": "official issuer-disclosure tone development proxy, not a complete news archive"}
    write_json(PUBLIC / "factor_coverage_redacted.json", receipt)
    return {k: receipt[k] for k in ("independent_issuer_event_groups", "F1_available_events", "F2_available_events", "four_group_fit_gate")}


if __name__ == "__main__":
    print(json.dumps(build()))
