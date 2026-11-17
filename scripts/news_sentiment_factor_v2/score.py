"""One bounded, local scoring pass on frozen source blocks.

Run with the existing isolated Qwen inference Python environment. This code
loads a pinned FinBERT checkpoint locally; it does not call an inference API.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from .corpus import CONFIG, PRIVATE, PUBLIC, ROOT, file_sha, write_json

LEXICON = PRIVATE / "inputs/Loughran-McDonald_MasterDictionary_1993-2025.csv"
MODEL = PRIVATE / "inputs/finbert_2022"
INPUT = PRIVATE / "input_manifest_private.json"
FREEZE = PUBLIC / "freeze_redacted.json"
OUTPUT = PRIVATE / "block_scores_private.jsonl"


def lexicon_sets() -> tuple[set[str], set[str], int]:
    positive: set[str] = set()
    negative: set[str] = set()
    count = 0
    with LEXICON.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            count += 1
            word = row["Word"].upper()
            if int(row["Positive"] or 0) > 0:
                positive.add(word)
            if int(row["Negative"] or 0) > 0:
                negative.add(word)
    return positive, negative, count


def lexicon_score(text: str, positive: set[str], negative: set[str]) -> dict:
    tokens = [t.upper() for t in re.findall(r"[A-Za-z]+", text)]
    pos = sum(t in positive for t in tokens)
    neg = sum(t in negative for t in tokens)
    return {"all_alpha_tokens": len(tokens), "positive_count": pos,
            "negative_count": neg,
            "net": (pos - neg) / len(tokens) if tokens else None}


def common_checks() -> tuple[dict, list[dict]]:
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    freeze = json.loads(FREEZE.read_text(encoding="utf-8"))
    if file_sha(CONFIG) != freeze["config_sha256"] or file_sha(INPUT) != freeze["input_manifest_sha256"]:
        raise ValueError("FROZEN_CONFIG_OR_INPUT_HASH_CHANGED")
    rows = json.loads(INPUT.read_text(encoding="utf-8"))["blocks"]
    if len(rows) != freeze["blocks"] or len(rows) > config["finbert"]["max_actual_model_blocks"]:
        raise ValueError("FROZEN_BLOCK_COUNT_CHANGED")
    if file_sha(MODEL / "pytorch_model.bin") != config["finbert"]["expected_pytorch_model_sha256"]:
        raise ValueError("PINNED_WEIGHT_HASH_MISMATCH")
    model_config = json.loads((MODEL / "config.json").read_text(encoding="utf-8"))
    if model_config.get("id2label") != config["finbert"]["expected_id2label"]:
        raise ValueError("MODEL_LABEL_MAP_MISMATCH")
    if not LEXICON.is_file():
        raise FileNotFoundError("LM_DICTIONARY_MISSING")
    return config, rows


def preflight() -> dict:
    from transformers import AutoTokenizer
    config, rows = common_checks()
    tokenizer = AutoTokenizer.from_pretrained(MODEL, local_files_only=True)
    lengths = [len(tokenizer.encode(row["text"], add_special_tokens=True)) for row in rows]
    max_allowed = config["finbert"]["max_input_tokens_including_special"]
    too_long = [row["block_id"] for row, length in zip(rows, lengths) if length > max_allowed]
    receipt = {"schema": "sentiment_v2_preflight_redacted_1", "checked_at_utc": datetime.now(timezone.utc).isoformat(),
               "config_sha256": file_sha(CONFIG), "input_manifest_sha256": file_sha(INPUT),
               "scorer_code_sha256": file_sha(Path(__file__)), "lm_csv_sha256": file_sha(LEXICON),
               "model_files_sha256": {p.name: file_sha(p) for p in sorted(MODEL.iterdir()) if p.is_file()},
               "blocks": len(rows), "max_input_tokens_observed": max(lengths, default=0),
               "over_budget_count": len(too_long), "over_budget_block_ids": too_long,
               "ready": not too_long}
    write_json(PUBLIC / "score_preflight_redacted.json", receipt)
    return {"blocks": len(rows), "max_input_tokens_observed": receipt["max_input_tokens_observed"],
            "ready": receipt["ready"]}


def score() -> dict:
    import torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    config, rows = common_checks()
    pre = json.loads((PUBLIC / "score_preflight_redacted.json").read_text(encoding="utf-8"))
    if not pre["ready"] or pre["scorer_code_sha256"] != file_sha(Path(__file__)) or \
       pre["input_manifest_sha256"] != file_sha(INPUT) or pre["lm_csv_sha256"] != file_sha(LEXICON):
        raise ValueError("PREFLIGHT_NOT_FROZEN_OR_FAILED")
    if OUTPUT.exists():
        raise FileExistsError("SCORE_OUTPUT_ALREADY_EXISTS_NO_SILENT_RETRY")
    positive, negative, lm_rows = lexicon_sets()
    if lm_rows < 80000:
        raise ValueError("DICTIONARY_ROW_COUNT_UNEXPECTED")
    tokenizer = AutoTokenizer.from_pretrained(MODEL, local_files_only=True)
    model = AutoModelForSequenceClassification.from_pretrained(
        MODEL, local_files_only=True, use_safetensors=False).eval()
    device = "cuda" if torch.cuda.is_available() else "cpu"
    model.to(device)
    if device == "cuda":
        torch.cuda.reset_peak_memory_stats()
    start = time.monotonic()
    batch_size = config["finbert"]["batch_size"]
    batches = 0
    written = 0
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    try:
        with OUTPUT.open("x", encoding="utf-8") as out:
            for offset in range(0, len(rows), batch_size):
                batch = rows[offset:offset + batch_size]
                encoded = tokenizer([r["text"] for r in batch], padding=True,
                                    truncation=False, return_tensors="pt")
                if encoded["input_ids"].shape[1] > config["finbert"]["max_input_tokens_including_special"]:
                    raise ValueError("TOKENIZER_BUDGET_MISMATCH")
                encoded = {k: v.to(device) for k, v in encoded.items()}
                with torch.inference_mode():
                    logits = model(**encoded).logits
                    probs = torch.softmax(logits, dim=-1).cpu().tolist()
                for row, probability in zip(batch, probs):
                    scores = {str(i): float(p) for i, p in enumerate(probability)}
                    item = {"block_id": row["block_id"], "document_id": row["document_id"],
                            "event_group_id": row["event_group_id"], "symbol": row["symbol"],
                            "announcement_date": row["announcement_date"],
                            "text_sha256": row["text_sha256"], "dom_path": row["dom_path"],
                            "source_raw_sha256": row["raw_body_sha256"],
                            "lm": lexicon_score(row["text"], positive, negative),
                            "finbert_probabilities_by_id": scores,
                            "finbert_positive_minus_negative": scores["0"] - scores["1"],
                            "model_revision": config["finbert"]["revision"],
                            "config_sha256": file_sha(CONFIG),
                            "scored_at_utc": datetime.now(timezone.utc).isoformat()}
                    out.write(json.dumps(item, ensure_ascii=False) + "\n")
                    written += 1
                out.flush()
                batches += 1
    except BaseException as exc:
        write_json(PUBLIC / "score_failure_redacted.json", {
            "schema": "sentiment_v2_failure_redacted_1", "error_class": type(exc).__name__,
            "batches_completed": batches, "blocks_saved": written,
            "output_exists": OUTPUT.exists(), "no_automatic_retry": True})
        raise
    receipt = {"schema": "sentiment_v2_score_run_redacted_1",
               "completed_at_utc": datetime.now(timezone.utc).isoformat(),
               "input_manifest_sha256": file_sha(INPUT), "config_sha256": file_sha(CONFIG),
               "score_preflight_sha256": file_sha(PUBLIC / "score_preflight_redacted.json"),
               "block_scores_private_sha256": file_sha(OUTPUT),
               "lm_csv_sha256": file_sha(LEXICON), "lm_dictionary_rows": lm_rows,
               "finbert_revision": config["finbert"]["revision"], "model_weight_sha256": file_sha(MODEL / "pytorch_model.bin"),
               "label_mapping": config["finbert"]["expected_id2label"],
               "selected_blocks": len(rows), "actual_inference_blocks": written, "batches": batches,
               "cache_hits": 0, "failure_count": 0, "elapsed_seconds": time.monotonic() - start,
               "device": device,
               "peak_cuda_allocated_bytes": torch.cuda.max_memory_allocated() if device == "cuda" else None,
               "interpretation": "source wording only; not fact verification or return probability"}
    write_json(PUBLIC / "score_run_redacted.json", receipt)
    return {k: receipt[k] for k in ("actual_inference_blocks", "batches", "failure_count", "device")}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("preflight", "score"))
    args = parser.parse_args()
    print(json.dumps(preflight() if args.mode == "preflight" else score()))
