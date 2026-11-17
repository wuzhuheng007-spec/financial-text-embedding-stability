"""Pinned LM/FinBERT English-headline scorer; no Qwen and no return data."""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from scripts.news_sentiment_factor_v3.score import lm_score, lm_sets
from .preflight import CONFIG, PRIVATE, PUBLIC, ROOT, save, sha
from .store import DB, english_candidate, init, summarize, text_hash

MODEL = ROOT / "artifacts/news_sentiment_factor_v2_20261117/inputs/finbert_2022"
LEXICON = ROOT / "artifacts/news_sentiment_factor_v2_20261117/inputs/Loughran-McDonald_MasterDictionary_1993-2025.csv"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def score_pending(*, limit: int = 50, db: Path = DB, only_prospective: bool = False) -> dict:
    """Score each candidate version at most once; failures are terminal in v4."""
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    frozen = json.loads((PUBLIC / "preflight_redacted.json").read_text(encoding="utf-8"))
    if sha(CONFIG) != frozen["config_sha256"]:
        raise ValueError("CONFIG_DRIFT")
    if sha(MODEL / "pytorch_model.bin") != cfg["scoring"]["finbert_weight_sha256"] or \
       sha(LEXICON) != cfg["scoring"]["lm_dictionary_sha256"]:
        raise ValueError("PINNED_MODEL_OR_DICTIONARY_DRIFT")
    if json.loads((MODEL / "config.json").read_text(encoding="utf-8"))["id2label"] != cfg["scoring"]["finbert_labels"]:
        raise ValueError("FINBERT_LABEL_MAP_DRIFT")
    init(db)
    with closing(sqlite3.connect(db)) as con:
        daily_remaining = None
        if only_prospective:
            start_local = datetime.now(ZoneInfo("Asia/Shanghai")).replace(hour=0, minute=0, second=0, microsecond=0)
            start_utc = start_local.astimezone(timezone.utc).isoformat()
            end_utc = (start_local + timedelta(days=1)).astimezone(timezone.utc).isoformat()
            used = con.execute("""SELECT COUNT(*) FROM scores WHERE score_origin='PROSPECTIVE_RECEIPT'
                AND scored_at_utc>=? AND scored_at_utc<?""", (start_utc, end_utc)).fetchone()[0]
            daily_remaining = max(0, cfg["prospective"]["daily_score_max"] - used)
            limit = min(limit, daily_remaining)
        candidates = con.execute("""SELECT a.provider,a.article_id,a.version,a.title,
            COALESCE(a.response_received_at_utc,a.first_received_at_utc),a.observation_mode FROM articles a
            WHERE a.language='ENGLISH_CANDIDATE' AND (?=0 OR a.observation_mode='PROSPECTIVE') AND NOT EXISTS
            (SELECT 1 FROM scores s WHERE s.provider=a.provider AND s.article_id=a.article_id
             AND s.version=a.version AND s.input_kind='TITLE' AND s.model_revision=?)
            ORDER BY a.first_received_at_utc,a.provider,a.article_id,a.version LIMIT ?""",
            (int(only_prospective), cfg["scoring"]["finbert_revision"], limit)).fetchall()
    receipt = {"schema": "news_sentiment_v4_score_run_redacted_1", "started_at_utc": utc_now(),
               "scorer_code_sha256": sha(Path(__file__)), "config_sha256": sha(CONFIG),
               "model_weight_sha256": sha(MODEL / "pytorch_model.bin"),
               "lm_dictionary_sha256": sha(LEXICON), "eligible_title_versions": len(candidates),
               "actual_finbert_inference_titles": 0, "actual_lm_scores": 0,
               "batch_calls": 0, "failures": 0, "score_origin": "OWNER_PRIVATE_SOURCE_TEXT_TONE_ONLY",
               "only_prospective": only_prospective,
               "daily_score_remaining_before": daily_remaining}
    if not candidates:
        receipt["completed_at_utc"] = utc_now()
        receipt["model_load"] = ("NOT_RUN_DAILY_SCORE_CAP" if only_prospective and daily_remaining == 0
                                 else "NOT_RUN_NO_ENGLISH_CANDIDATES")
        save(PUBLIC / "score_run_redacted.json", receipt)
        return receipt
    import torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer
    tokenizer = AutoTokenizer.from_pretrained(MODEL, local_files_only=True)
    model = AutoModelForSequenceClassification.from_pretrained(MODEL, local_files_only=True, use_safetensors=False).eval()
    device = "cuda" if torch.cuda.is_available() else "cpu"
    model.to(device)
    if device == "cpu":
        torch.set_num_threads(min(8, torch.get_num_threads()))
    positives, negatives = lm_sets()
    receipt["model_load"] = "SUCCESS"
    receipt["device"] = device
    started = time.monotonic()
    for provider, aid, version, title, received, mode in candidates:
        if not english_candidate(title):
            raise ValueError("LANGUAGE_GATE_CHANGED")
        tokens = tokenizer.encode(title, add_special_tokens=True)
        status, error, probs, net, lm = "DONE", None, None, None, None
        if len(tokens) > cfg["scoring"]["finbert_max_input_tokens_with_special"]:
            status, error = "FAILED", "TITLE_TOKEN_BUDGET_NO_TRUNCATION"
        else:
            try:
                encoded = tokenizer([title], padding=False, truncation=False, return_tensors="pt")
                with torch.inference_mode():
                    p = torch.softmax(model(**{k: v.to(device) for k, v in encoded.items()}).logits, dim=-1)[0].cpu().tolist()
                probs = {str(i): float(value) for i, value in enumerate(p)}
                net = probs["0"] - probs["1"]
                lm = lm_score(title, positives, negatives)
                receipt["actual_finbert_inference_titles"] += 1
                receipt["actual_lm_scores"] += 1
                receipt["batch_calls"] += 1
            except (RuntimeError, ValueError) as exc:
                status, error = "FAILED", type(exc).__name__
        scored = utc_now()
        available = max(received, scored) if status == "DONE" else None
        with closing(sqlite3.connect(db)) as con:
            con.execute("""INSERT INTO scores VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (provider, aid, version, "TITLE", cfg["scoring"]["finbert_revision"], sha(CONFIG),
                 text_hash(title), len(tokens), 0, json.dumps(lm) if lm else None,
                 json.dumps(probs) if probs else None, net, status, error, scored, available,
                 "PROSPECTIVE_RECEIPT" if mode == "PROSPECTIVE" else "RETROSPECTIVE_OBSERVATION"))
            con.commit()
        receipt["failures"] += status == "FAILED"
    receipt["elapsed_seconds"] = time.monotonic() - started
    receipt["completed_at_utc"] = utc_now()
    receipt["peak_cuda_allocated_bytes"] = torch.cuda.max_memory_allocated() if device == "cuda" else None
    save(PUBLIC / "score_run_redacted.json", receipt)
    summarize(db)
    return receipt


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--only-prospective", action="store_true")
    args = parser.parse_args()
    print(json.dumps(score_pending(limit=args.limit, only_prospective=args.only_prospective), ensure_ascii=False))
