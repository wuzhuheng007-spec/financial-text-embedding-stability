"""Compare frozen source-only Codex judgments with later model scores."""
from __future__ import annotations

import json
import sqlite3
from collections import Counter

from .freeze import PRIVATE, PUBLIC, digest, now, sha, write
from .score import DB

LABELS = ("NEUTRAL", "POSITIVE", "NEGATIVE")


def main() -> dict:
    target = PUBLIC / "source_review_comparison_redacted.json"
    if target.exists():
        raise FileExistsError(target)
    reference = json.loads((PRIVATE / "source_review_reference_private.json").read_text(encoding="utf-8"))
    frozen = json.loads((PUBLIC / "source_review_frozen_redacted.json").read_text(encoding="utf-8"))
    if sha(PRIVATE / "source_review_reference_private.json") != frozen["reference_sha256"]:
        raise ValueError("SOURCE_REVIEW_REFERENCE_DRIFT")
    private = []
    matrix = {actual: {pred: 0 for pred in LABELS} for actual in LABELS}
    with sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True) as con:
        con.row_factory = sqlite3.Row
        for row in reference["rows"]:
            scored = con.execute("""SELECT s.p_neutral,s.p_positive,s.p_negative,s.net_score,
              s.status,s.input_sha256,x.company_association,x.association_status
              FROM scores s JOIN segments x USING(provider,article_id,version,segment_id,segment_rule_version)
              WHERE s.provider=? AND s.article_id=? AND s.version=? AND s.segment_id=?
              AND s.score_origin='CACHE_REPLAY_DEVELOPMENT'""",
              (row["provider"], row["article_id"], row["version"], row["segment_id"])).fetchone()
            if scored is None or scored["input_sha256"] != row["input_sha256"]:
                raise ValueError("FROZEN_REVIEW_INPUT_NOT_SCORED")
            values = [scored["p_neutral"], scored["p_positive"], scored["p_negative"]]
            predicted = LABELS[max(range(3), key=lambda i: values[i])] if scored["status"] == "DONE" else "FAILED"
            expected = row["source_tone_review"]
            if expected in LABELS and predicted in LABELS:
                matrix[expected][predicted] += 1
            private.append({**row, "predicted_text_tone": predicted, "net_score": scored["net_score"],
                            "probabilities": dict(zip(LABELS, values)),
                            "company_association": scored["company_association"],
                            "association_status": scored["association_status"],
                            "label_pair_status": "COMPARABLE" if expected in LABELS else "REFERENCE_UNRESOLVED_OR_MIXED"})
    write(PRIVATE / "source_review_comparison_private.json", private)
    comparable = [x for x in private if x["label_pair_status"] == "COMPARABLE"]
    mismatches = [x for x in comparable if x["source_tone_review"] != x["predicted_text_tone"]]
    public = {
        "schema": "news_sentiment_v41_source_review_comparison_redacted_1", "created_at_utc": now(),
        "reviewer": "CODEX_REVIEW_NOT_INDEPENDENT_GOLD", "reference_frozen_before_model_scores": True,
        "reference_sha256": frozen["reference_sha256"],
        "private_comparison_sha256": sha(PRIVATE / "source_review_comparison_private.json"),
        "reviewed_units": len(private), "three_class_comparable_units": len(comparable),
        "reference_unresolved_or_mixed": len(private) - len(comparable),
        "matrix_rows_reference_columns_model": matrix,
        "label_agreements_on_comparable": len(comparable) - len(mismatches),
        "label_disagreements_on_comparable": len(mismatches),
        "source_reference_distribution": dict(Counter(x["source_tone_review"] for x in private)),
        "model_distribution_on_review": dict(Counter(x["predicted_text_tone"] for x in private)),
        "disagreements_redacted": [{"case_hash": digest((x["provider"] + ":" + x["article_id"] + ":" + x["segment_id"]).encode())[:16],
                                    "provider": x["provider"], "source_reference": x["source_tone_review"],
                                    "model_label": x["predicted_text_tone"], "claim_nature": x["claim_nature_review"],
                                    "review_note_code": x["review_note_code"]} for x in mismatches],
        "not_general_accuracy_or_news_truth_verification": True,
    }
    write(target, public)
    return public


if __name__ == "__main__":
    print(json.dumps(main(), ensure_ascii=False))
