"""Opt-in, owner-local future shadow tone scoring; never reads return labels.

The private input JSON carries one saved source version. This command is not a
collector or a background scheduler. It writes an immutable private record and
does not update the portal, model registry, or historical experiment.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from .corpus import CONFIG, PRIVATE, file_sha, write_json
from .score import LEXICON, MODEL, lexicon_score, lexicon_sets


def _utc(value: str) -> datetime:
    date = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if date.tzinfo is None:
        raise ValueError("NAIVE_TIME_NOT_ALLOWED")
    return date.astimezone(timezone.utc)


def score_file(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    required = {"news_id", "news_version", "event_id", "entity_id", "source_text",
                "source_sha256", "source_language", "first_received_at_utc",
                "published_at_utc", "source_permission"}
    if set(data) != required:
        raise ValueError("INPUT_KEYS_MISMATCH")
    source = data["source_text"]
    if not isinstance(source, str) or not source.strip():
        raise ValueError("EMPTY_SOURCE_TEXT")
    if hashlib.sha256(source.encode("utf-8")).hexdigest() != data["source_sha256"]:
        raise ValueError("SOURCE_HASH_MISMATCH")
    if data["source_permission"] != "LOCAL_RESEARCH_ALLOWED":
        raise ValueError("SOURCE_PERMISSION_NOT_ESTABLISHED")
    received = _utc(data["first_received_at_utc"])
    if data["published_at_utc"] is not None:
        _utc(data["published_at_utc"])
    now = datetime.now(timezone.utc)
    if received > now:
        raise ValueError("FUTURE_FIRST_RECEIVED_AT")
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    model_sha = file_sha(MODEL / "pytorch_model.bin")
    if model_sha != config["finbert"]["expected_pytorch_model_sha256"]:
        raise ValueError("PINNED_MODEL_HASH_MISMATCH")
    if file_sha(LEXICON) != json.loads((PRIVATE.parent.parent / "results/news_sentiment_factor_v2_20261117/score_preflight_redacted.json").read_text(encoding="utf-8"))["lm_csv_sha256"]:
        raise ValueError("LEXICON_HASH_CHANGED")
    result = {k: data[k] for k in required if k != "source_text"}
    result.update({"schema": "private_sentiment_v2_shadow_score_1",
                   "score_started_at_utc": now.isoformat(),
                   "model_revision": config["finbert"]["revision"],
                   "model_weight_sha256": model_sha,
                   "config_sha256": file_sha(CONFIG),
                   "source_file_sha256": file_sha(path),
                   "truth_status": "UNVERIFIED_SOURCE_WORDING",
                   "historical_outcome_status": "PENDING",
                   "training_or_promotion": "NONE"})
    if data["source_language"].lower() != "en":
        result.update({"status": "LANGUAGE_UNSUPPORTED_NO_SCORE", "lm": None,
                       "finbert_probabilities_by_id": None, "finbert_positive_minus_negative": None,
                       "score_completed_at_utc": datetime.now(timezone.utc).isoformat(),
                       "signal_available_at_utc": None})
    else:
        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer
        tokenizer = AutoTokenizer.from_pretrained(MODEL, local_files_only=True)
        encoded = tokenizer(source, return_tensors="pt", truncation=False)
        tokens = int(encoded["input_ids"].shape[1])
        if tokens > config["finbert"]["max_input_tokens_including_special"]:
            result.update({"status": "TEXT_OVER_BUDGET_NO_SCORE", "input_tokens": tokens,
                           "lm": None, "finbert_probabilities_by_id": None,
                           "finbert_positive_minus_negative": None,
                           "score_completed_at_utc": datetime.now(timezone.utc).isoformat(),
                           "signal_available_at_utc": None})
        else:
            model = AutoModelForSequenceClassification.from_pretrained(MODEL, local_files_only=True,
                                                                       use_safetensors=False).eval()
            if {str(k): v for k, v in model.config.id2label.items()} != config["finbert"]["expected_id2label"]:
                raise ValueError("MODEL_LABEL_MAP_MISMATCH")
            with torch.inference_mode():
                probability = torch.softmax(model(**encoded).logits, dim=-1)[0].tolist()
            positive, negative, _ = lexicon_sets()
            completed = datetime.now(timezone.utc)
            result.update({"status": "SCORED_CANDIDATE_UNREVIEWED", "input_tokens": tokens,
                           "lm": lexicon_score(source, positive, negative),
                           "finbert_probabilities_by_id": {str(i): float(p) for i, p in enumerate(probability)},
                           "finbert_positive_minus_negative": float(probability[0] - probability[1]),
                           "score_completed_at_utc": completed.isoformat(),
                           "signal_available_at_utc": max(received, completed).isoformat()})
    identity = hashlib.sha256("|".join(str(result[k]) for k in
        ("news_id", "news_version", "source_sha256", "model_revision", "config_sha256"))
        .encode("utf-8")).hexdigest()[:24]
    target = PRIVATE / "shadow_candidates" / f"{identity}.json"
    if target.exists():
        raise FileExistsError("SHADOW_VERSION_ALREADY_RECORDED")
    write_json(target, result)
    return {"status": result["status"], "record_sha256": file_sha(target),
            "source_sha256": data["source_sha256"],
            "signal_available_at_utc": result["signal_available_at_utc"]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--private-source-json", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(score_file(args.private_source_json), ensure_ascii=False))
