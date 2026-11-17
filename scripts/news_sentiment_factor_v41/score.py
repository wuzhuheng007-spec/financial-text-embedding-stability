"""Pinned local Chinese FinBERT scoring; no return labels or supplier calls."""
from __future__ import annotations

import json
import sqlite3
import subprocess
import time
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from .freeze import MODEL, PRIVATE, PUBLIC, ROOT, digest, now, sha, write
from .prepare_v2 import CFG

DB = PRIVATE / "chinese_tone.sqlite3"
MODEL_ID = "e91b1a3af10e1e8c9c03429d3cd7d5e9a1c8000d"


def init(db_path: Path = DB) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(db_path, timeout=30)) as con:
        con.executescript("""
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS articles (
          provider TEXT NOT NULL, article_id TEXT NOT NULL, version INTEGER NOT NULL,
          publisher TEXT, source_url TEXT, title TEXT, body TEXT NOT NULL,
          published_at_utc TEXT, first_received_at_utc TEXT NOT NULL,
          received_at_utc TEXT NOT NULL, source_text_hash TEXT NOT NULL,
          raw_response_hash TEXT NOT NULL, acquisition_batch TEXT NOT NULL,
          cohort TEXT NOT NULL, event_group_id TEXT, grouping_basis TEXT,
          source_version_status TEXT NOT NULL,
          PRIMARY KEY(provider,article_id,version));
        CREATE TABLE IF NOT EXISTS segments (
          provider TEXT NOT NULL, article_id TEXT NOT NULL, version INTEGER NOT NULL,
          segment_id TEXT NOT NULL, kind TEXT NOT NULL, text TEXT NOT NULL,
          input_sha256 TEXT NOT NULL, token_count INTEGER NOT NULL,
          source_field TEXT NOT NULL, source_start INTEGER NOT NULL, source_end INTEGER NOT NULL,
          source_mapping_method TEXT NOT NULL, entity_candidates_json TEXT NOT NULL,
          company_association TEXT, association_status TEXT NOT NULL,
          semantic_kind_candidate TEXT NOT NULL, event_group_id TEXT NOT NULL,
          segment_rule_version TEXT NOT NULL,
          PRIMARY KEY(provider,article_id,version,segment_id,segment_rule_version));
        CREATE TABLE IF NOT EXISTS scores (
          provider TEXT NOT NULL, article_id TEXT NOT NULL, version INTEGER NOT NULL,
          segment_id TEXT NOT NULL, segment_rule_version TEXT NOT NULL,
          model_revision TEXT NOT NULL, config_sha256 TEXT NOT NULL,
          input_sha256 TEXT NOT NULL, token_count INTEGER NOT NULL,
          p_neutral REAL, p_positive REAL, p_negative REAL, net_score REAL,
          status TEXT NOT NULL, error_class TEXT, device TEXT NOT NULL,
          started_at_utc TEXT NOT NULL, scored_at_utc TEXT NOT NULL,
          received_at_utc TEXT NOT NULL, signal_available_at_utc TEXT,
          score_origin TEXT NOT NULL, run_id TEXT NOT NULL,
          PRIMARY KEY(provider,article_id,version,segment_id,segment_rule_version,model_revision,config_sha256));
        CREATE INDEX IF NOT EXISTS scores_origin_time ON scores(score_origin,scored_at_utc);
        CREATE TABLE IF NOT EXISTS attempted_versions (
          provider TEXT NOT NULL, article_id TEXT NOT NULL, version INTEGER NOT NULL,
          segment_rule_version TEXT NOT NULL, model_revision TEXT NOT NULL,
          config_sha256 TEXT NOT NULL, source_text_hash TEXT NOT NULL,
          received_at_utc TEXT NOT NULL, attempted_at_utc TEXT NOT NULL,
          state TEXT NOT NULL, error_class TEXT,
          PRIMARY KEY(provider,article_id,version,segment_rule_version,model_revision,config_sha256));
        CREATE TABLE IF NOT EXISTS worker_runs (
          run_id TEXT PRIMARY KEY, started_at_utc TEXT NOT NULL, stopped_at_utc TEXT,
          state TEXT NOT NULL, new_versions INTEGER NOT NULL DEFAULT 0,
          scored_units INTEGER NOT NULL DEFAULT 0, failures INTEGER NOT NULL DEFAULT 0,
          last_checked_at_utc TEXT, error_class TEXT);
        """)


def lock(stage: str) -> dict:
    cfg = json.loads(CFG.read_text(encoding="utf-8"))
    manifest = json.loads((PUBLIC / "segment_manifest_r2_redacted.json").read_text(encoding="utf-8"))
    review = json.loads((PUBLIC / "source_review_frozen_redacted.json").read_text(encoding="utf-8"))
    if sha(CFG) != manifest["config_sha256"] or sha(PRIVATE / "segments_v2_private.json") != manifest["segments_sha256"]:
        raise ValueError("FROZEN_CONFIG_OR_SEGMENT_DRIFT")
    if sha(PRIVATE / "source_review_reference_private.json") != review["reference_sha256"]:
        raise ValueError("SOURCE_REVIEW_REFERENCE_DRIFT")
    if cfg["model"]["id2label"] != json.loads((MODEL / "config.json").read_text(encoding="utf-8"))["id2label"]:
        raise ValueError("LABEL_MAP_CONFLICT")
    model_lock = json.loads((PUBLIC / "input_manifest_redacted.json").read_text(encoding="utf-8"))["model_files"]
    for name, meta in model_lock.items():
        if sha(MODEL / name) != meta["sha256"]:
            raise ValueError("MODEL_FILE_DRIFT:" + name)
    code_files = [Path(__file__), ROOT / "scripts/news_sentiment_factor_v41/prepare_v2.py",
                  ROOT / "scripts/news_sentiment_factor_v41/prepare.py",
                  ROOT / "scripts/news_sentiment_factor_v41/freeze.py",
                  ROOT / "scripts/quant_personal_feed/event_rules.py"]
    import torch, transformers
    data = {"schema": "news_sentiment_v41_run_lock_1", "stage": stage,
            "locked_at_utc": now(), "head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
            "config_sha256": sha(CFG), "input_manifest_sha256": cfg["frozen_input_manifest_sha256"],
            "segments_sha256": manifest["segments_sha256"],
            "source_review_reference_sha256": review["reference_sha256"],
            "model_revision": cfg["model"]["revision"], "model_weight_sha256": model_lock["model.safetensors"]["sha256"],
            "model_config_sha256": model_lock["config.json"]["sha256"],
            "code_sha256": {str(p.relative_to(ROOT)).replace("\\", "/"): sha(p) for p in code_files},
            "torch_version": torch.__version__, "transformers_version": transformers.__version__,
            "seed": 1729, "device_capability": "CPU_ONLY_IN_ISOLATED_V3_RESEARCH_ENV"}
    path = PRIVATE / f"run_lock_{stage.lower()}_private.json"
    if path.exists():
        raise FileExistsError(path)
    write(path, data)
    write(PUBLIC / f"run_lock_{stage.lower()}_redacted.json", data)
    return data


def load_model():
    import torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer
    cfg = json.loads(CFG.read_text(encoding="utf-8"))
    tokenizer = AutoTokenizer.from_pretrained(MODEL, local_files_only=True, trust_remote_code=False)
    model = AutoModelForSequenceClassification.from_pretrained(
        MODEL, local_files_only=True, trust_remote_code=False, use_safetensors=True).eval()
    torch.set_num_threads(min(8, torch.get_num_threads()))
    torch.manual_seed(1729)
    if cfg["model"]["id2label"] != {str(k): v for k, v in model.config.id2label.items()}:
        raise ValueError("LOADED_MODEL_LABEL_MAP_CONFLICT")
    return tokenizer, model, torch


def infer_batch(texts: list[str], tokenizer, model, torch) -> list[list[float]]:
    if any(not text.strip() for text in texts):
        raise ValueError("EMPTY_INPUT")
    cfg = json.loads(CFG.read_text(encoding="utf-8"))
    maximum = cfg["scoring"]["max_tokens_including_special"]
    if any(len(tokenizer.encode(x, add_special_tokens=True, truncation=False)) > maximum for x in texts):
        raise ValueError("INPUT_TOKEN_LIMIT_EXCEEDED_NO_TRUNCATION")
    encoded = tokenizer(texts, padding=True, truncation=False, return_tensors="pt")
    with torch.inference_mode():
        p = torch.softmax(model(**encoded).logits, dim=-1).cpu().tolist()
    return [[float(x) for x in row] for row in p]


def synthetic_smoke() -> dict:
    if (PUBLIC / "synthetic_smoke_redacted.json").exists():
        raise FileExistsError("SMOKE_ALREADY_RUN")
    lock("SYNTHETIC_SMOKE")
    tokenizer, model, torch = load_model()
    samples = [
        ("positive", "公司利润增长，订单改善。"),
        ("negative", "公司亏损扩大，订单减少。"),
        ("negation", "公司否认亏损扩大的传闻。"),
        ("number", "公司营收同比增长12%。"),
        ("prediction", "分析师预计该公司利润可能下降。"),
    ]
    started = time.monotonic()
    batch = infer_batch([x[1] for x in samples], tokenizer, model, torch)
    singles = [infer_batch([x[1]], tokenizer, model, torch)[0] for x in samples]
    delta = max(abs(a - b) for row_a, row_b in zip(batch, singles) for a, b in zip(row_a, row_b))
    empty_rejected = False
    try:
        infer_batch([""], tokenizer, model, torch)
    except ValueError:
        empty_rejected = True
    from .prepare import token_safe_spans
    long_text = "公司利润增长，但仍存在不确定性。" * 120
    spans = token_safe_spans(long_text, 0, len(long_text), tokenizer,
                             json.loads(CFG.read_text(encoding="utf-8"))["scoring"]["max_tokens_including_special"])
    assert "".join(long_text[a:b] for a, b, _ in spans) == long_text
    out = {"schema": "news_sentiment_v41_synthetic_smoke_redacted_1", "checked_at_utc": now(),
           "model_revision": MODEL_ID, "synthetic_inputs": len(samples),
           "probabilities_by_case": {name: {"neutral": row[0], "positive": row[1],
                                           "negative": row[2], "net": row[1] - row[2]}
                                     for (name, _), row in zip(samples, batch)},
           "empty_input_rejected": empty_rejected, "long_text_characters": len(long_text),
           "long_text_pieces": len(spans), "long_text_complete_coverage": True,
           "batch_single_max_abs_difference": delta,
           "batch_single_tolerance": 1e-5,
           "elapsed_seconds": time.monotonic() - started,
           "synthetic_not_real_accuracy": True}
    write(PUBLIC / "synthetic_smoke_redacted.json", out)
    return out


def ingest_frozen(db_path: Path = DB) -> int:
    init(db_path)
    articles = json.loads((PRIVATE / "input_manifest_private.json").read_text(encoding="utf-8"))["rows"]
    segments = json.loads((PRIVATE / "segments_v2_private.json").read_text(encoding="utf-8"))
    rule = json.loads(CFG.read_text(encoding="utf-8"))["scoring"]["segment_rule_version"]
    event_by_article = {(x["provider"], x["article_id"], x["version"]): x for x in segments}
    with sqlite3.connect(db_path, timeout=30) as con:
        for a in articles:
            event = event_by_article.get((a["provider"], a["article_id"], a["version"]))
            con.execute("""INSERT OR IGNORE INTO articles VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (a["provider"], a["article_id"], a["version"], a.get("publisher"),
                         a.get("source_url"), a["title"], a["body"], a.get("published_at_utc"),
                         a["first_received_at_utc"], a["received_at_utc"], a["source_text_hash"],
                         a["raw_response_hash"], a["acquisition_batch"], a["cohort"],
                         event["event_group_id"] if event else None,
                         event["grouping_basis"] if event else None, a["source_version_status"]))
        for s in segments:
            con.execute("""INSERT OR IGNORE INTO segments VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (s["provider"], s["article_id"], s["version"], s["segment_id"], s["kind"],
                         s["text"], s["input_sha256"], s["token_count"], s["source_field"],
                         s["source_start"], s["source_end"], s["source_mapping_method"],
                         json.dumps(s["entity_candidates"], ensure_ascii=False),
                         s["company_association"], s["association_status"],
                         s["semantic_kind_candidate"], s["event_group_id"], rule))
    return len(segments)


def score_frozen(db_path: Path = DB) -> dict:
    if (PUBLIC / "real_score_run_redacted.json").exists():
        raise FileExistsError("REAL_SCORE_RUN_ALREADY_EXISTS")
    run_lock = lock("REAL_REPLAY")
    cfg = json.loads(CFG.read_text(encoding="utf-8"))
    total = ingest_frozen(db_path)
    if total > cfg["scoring"]["max_real_inference_units"]:
        raise ValueError("FROZEN_INFERENCE_CAP_EXCEEDED")
    tokenizer, model, torch = load_model()
    with sqlite3.connect(db_path, timeout=30) as con:
        con.row_factory = sqlite3.Row
        rows = con.execute("""SELECT s.provider,s.article_id,s.version,s.segment_id,s.text,s.input_sha256,
            s.token_count,a.received_at_utc FROM segments s JOIN articles a USING(provider,article_id,version)
            WHERE a.cohort='CACHE_REPLAY' ORDER BY a.received_at_utc,s.provider,s.article_id,s.version,s.segment_id""").fetchall()
    run_id = "v41_replay_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    started = time.monotonic()
    done = failed = batches = 0
    batch_size = cfg["scoring"]["batch_size"]
    for offset in range(0, len(rows), batch_size):
        batch = rows[offset:offset + batch_size]
        batch_start = now()
        error = None
        try:
            probs = infer_batch([x["text"] for x in batch], tokenizer, model, torch)
        except (RuntimeError, ValueError) as exc:
            probs = [None] * len(batch)
            error = type(exc).__name__
        batches += 1
        with sqlite3.connect(db_path, timeout=30) as con:
            for row, p in zip(batch, probs):
                if p is None:
                    failed += 1
                else:
                    done += 1
                scored_at = now()
                con.execute("""INSERT OR IGNORE INTO scores VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                            (row["provider"], row["article_id"], row["version"], row["segment_id"],
                             cfg["scoring"]["segment_rule_version"], MODEL_ID, run_lock["config_sha256"],
                             row["input_sha256"], row["token_count"],
                             p[0] if p else None, p[1] if p else None, p[2] if p else None,
                             p[1] - p[2] if p else None, "DONE" if p else "FAILED", error,
                             "cpu", batch_start, scored_at, row["received_at_utc"],
                             scored_at if p else None, "CACHE_REPLAY_DEVELOPMENT", run_id))
        if offset and offset % 160 == 0:
            print(json.dumps({"run_id": run_id, "scored_units_so_far": done,
                              "failed_units_so_far": failed, "batches_so_far": batches}), flush=True)
    result = {"schema": "news_sentiment_v41_real_score_run_redacted_1",
              "run_id": run_id, "started_lock_at_utc": run_lock["locked_at_utc"],
              "completed_at_utc": now(), "config_sha256": run_lock["config_sha256"],
              "segments_sha256": run_lock["segments_sha256"], "model_revision": MODEL_ID,
              "device": "cpu", "article_versions": 230, "eligible_scoring_units": len(rows),
              "actual_inference_units": done, "batch_calls": batches, "failed_units": failed,
              "cache_replay_scores": done, "qualified_prospective_scores": 0,
              "elapsed_seconds": time.monotonic() - started,
              "no_return_labels_read": True}
    write(PUBLIC / "real_score_run_redacted.json", result)
    return result


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("smoke", "replay"))
    args = parser.parse_args()
    print(json.dumps(synthetic_smoke() if args.mode == "smoke" else score_frozen(), ensure_ascii=False))
