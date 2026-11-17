"""One local, immutable LM and pinned FinBERT pass over frozen v3 text."""
from __future__ import annotations

import csv
import json
import re
import time
from collections import Counter
from pathlib import Path

from .source import CONFIG, PRIVATE, PUBLIC, digest, now, write_json

ROOT = Path(__file__).resolve().parents[2]
MODEL = ROOT / "artifacts/news_sentiment_factor_v2_20261117/inputs/finbert_2022"
LEXICON = ROOT / "artifacts/news_sentiment_factor_v2_20261117/inputs/Loughran-McDonald_MasterDictionary_1993-2025.csv"
INPUT = PRIVATE / "text_input_private.json"
SCORES = PRIVATE / "text_scores_private.jsonl"


def lm_sets():
    positive, negative = set(), set()
    with LEXICON.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            word = row["Word"].upper()
            if int(row["Positive"] or 0) > 0:
                positive.add(word)
            if int(row["Negative"] or 0) > 0:
                negative.add(word)
    return positive, negative


def lm_score(value, positive, negative):
    words = [x.upper() for x in re.findall(r"[A-Za-z]+", value)]
    p = sum(x in positive for x in words)
    n = sum(x in negative for x in words)
    return {"positive_count": p, "negative_count": n, "alpha_words": len(words),
            "net": (p - n) / len(words) if words else None}


def preflight():
    from transformers import AutoTokenizer

    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    freeze = json.loads((PUBLIC / "text_freeze_redacted.json").read_text(encoding="utf-8"))
    if digest(CONFIG) != freeze["config_sha256"] or digest(INPUT) != freeze["input_sha256"]:
        raise ValueError("INPUT_OR_CONFIG_DRIFT")
    if digest(LEXICON) != config["models"]["lm_dictionary_sha256"]:
        raise ValueError("LM_VERSION_DRIFT")
    if digest(MODEL / "pytorch_model.bin") != config["models"]["finbert_weight_sha256"]:
        raise ValueError("FINBERT_WEIGHT_DRIFT")
    if json.loads((MODEL / "config.json").read_text(encoding="utf-8"))["id2label"] != config["models"]["finbert_label_map"]:
        raise ValueError("FINBERT_LABEL_MAPPING_DRIFT")
    rows = json.loads(INPUT.read_text(encoding="utf-8"))["rows"]
    tokenizer = AutoTokenizer.from_pretrained(MODEL, local_files_only=True)
    lengths = [len(tokenizer.encode(x["text"], add_special_tokens=True)) for x in rows]
    bad = [x["block_id"] for x, n in zip(rows, lengths) if n > config["text_selection"]["max_finbert_tokens_including_special"]]
    receipt = {"schema": "sentiment_v3_score_preflight_1", "checked_at_utc": now(),
               "input_sha256": digest(INPUT), "config_sha256": digest(CONFIG),
               "scorer_sha256": digest(Path(__file__)), "lexicon_sha256": digest(LEXICON),
               "weight_sha256": digest(MODEL / "pytorch_model.bin"),
               "tokenizer_files_sha256": {p.name: digest(p) for p in MODEL.iterdir() if p.is_file() and p.name != "pytorch_model.bin"},
               "blocks": len(rows), "maximum_actual_tokens": max(lengths),
               "over_budget": len(bad), "over_budget_ids": bad, "ready": not bad}
    write_json(PUBLIC / "score_preflight_redacted.json", receipt)
    return receipt


def score():
    import torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    if SCORES.exists():
        raise FileExistsError("RAW_SCORES_EXIST_NO_SILENT_RERUN")
    pre = json.loads((PUBLIC / "score_preflight_redacted.json").read_text(encoding="utf-8"))
    if not pre["ready"] or pre["input_sha256"] != digest(INPUT) or pre["scorer_sha256"] != digest(Path(__file__)):
        raise ValueError("SCORE_PREFLIGHT_MISSING_OR_DRIFTED")
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    rows = json.loads(INPUT.read_text(encoding="utf-8"))["rows"]
    positive, negative = lm_sets()
    tokenizer = AutoTokenizer.from_pretrained(MODEL, local_files_only=True)
    model = AutoModelForSequenceClassification.from_pretrained(MODEL, local_files_only=True, use_safetensors=False).eval()
    device = "cuda" if torch.cuda.is_available() else "cpu"
    model.to(device)
    if device == "cpu":
        torch.set_num_threads(min(8, torch.get_num_threads()))
    else:
        torch.cuda.reset_peak_memory_stats()
    started = time.monotonic()
    batch_size = 16
    attempts, completed = 0, 0
    with SCORES.open("x", encoding="utf-8") as output:
        for offset in range(0, len(rows), batch_size):
            attempts += 1
            batch = rows[offset:offset + batch_size]
            encoded = tokenizer([r["text"] for r in batch], padding=True, truncation=False, return_tensors="pt")
            if encoded["input_ids"].shape[1] > config["text_selection"]["max_finbert_tokens_including_special"]:
                raise ValueError("ACTUAL_BATCH_TOKEN_BUDGET_CHANGED")
            with torch.inference_mode():
                probabilities = torch.softmax(model(**{k: v.to(device) for k, v in encoded.items()}).logits, dim=-1).cpu().tolist()
            for row, probs in zip(batch, probabilities):
                p = {str(i): float(x) for i, x in enumerate(probs)}
                item = {"block_id": row["block_id"], "event_id": row["event_id"],
                        "symbol": row["symbol"], "section": row["section"],
                        "text_sha256": row["text_sha256"], "source_sha256": row["source_sha256"],
                        "lm": lm_score(row["text"], positive, negative),
                        "finbert_probabilities_by_id": p, "finbert_net": p["0"] - p["1"],
                        "model_revision": config["models"]["finbert_revision"],
                        "config_sha256": digest(CONFIG), "computed_at_utc": now()}
                output.write(json.dumps(item, ensure_ascii=False) + "\n")
                completed += 1
            output.flush()
    receipt = {"schema": "sentiment_v3_score_run_redacted_1", "completed_at_utc": now(),
               "config_sha256": digest(CONFIG), "input_sha256": digest(INPUT),
               "scorer_sha256": digest(Path(__file__)), "scores_sha256": digest(SCORES),
               "actual_blocks_scored": completed, "events": len(set(x["event_id"] for x in rows)),
               "actual_batches": attempts, "failed_blocks": len(rows) - completed,
               "duration_seconds": time.monotonic() - started, "device": device,
               "torch_version": torch.__version__,
               "peak_cuda_allocated_bytes": torch.cuda.max_memory_allocated() if device == "cuda" else None,
               "finbert_revision": config["models"]["finbert_revision"],
               "lexicon_sha256": digest(LEXICON), "model_weight_sha256": digest(MODEL / "pytorch_model.bin"),
               "meaning": "source text tone; not issuer fact certification or return probability"}
    write_json(PUBLIC / "score_run_redacted.json", receipt)
    return {k: receipt[k] for k in ("actual_blocks_scored", "events", "actual_batches", "failed_blocks", "duration_seconds", "device")}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("preflight", "score"))
    args = parser.parse_args()
    print(json.dumps(preflight() if args.mode == "preflight" else score()))
