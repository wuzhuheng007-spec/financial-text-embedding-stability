"""Local GPU worker for the news judgement (scripts.quant_news_v1.stance). Runs on the owner's machine with a
checkpoint that is already there; downloads nothing, opens no network connection, never sees the server's stores.

    python -m scripts.quant_news_v1.stance_gpu --model DIR --work FILE --results FILE [--receipt FILE]

Reads the hashed work file, asks the model once per (article, target), retries a failed answer once, and writes the
raw answers with start/finish times plus a manifest carrying the identity of what was really loaded. The server
validates every raw answer again at import; the worker's own validation is only for the receipt.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import time
from collections import Counter
from pathlib import Path

from . import stance

ADAPTER = "hf-simple-prefill-stop-v1"          # the X worker's simple adapter: the turn starts with "{" and reading stops at its closing brace
MAX_NEW_TOKENS = 256


def run(model_dir: Path, work: Path, results: Path, receipt: Path | None = None) -> dict:
    from scripts.x_stance_v1 import local_gpu
    man_in = json.loads(Path(str(work) + ".manifest.json").read_text(encoding="utf-8"))
    if hashlib.sha256(work.read_bytes()).hexdigest() != man_in["sha256"]:
        raise SystemExit("work file does not match its manifest")
    if man_in["prompt_sha256"] != stance.prompt_hash() or man_in["targets_sha256"] != stance.targets_hash():
        raise SystemExit("work file was made with another prompt or target list")
    t0 = time.perf_counter()
    facts = local_gpu.checkpoint_facts(model_dir)
    clf = local_gpu.HFClassifier(model_dir, max_new_tokens=MAX_NEW_TOKENS, seed=42, simple_contract=True)
    t1 = time.perf_counter()
    wsha = hashlib.sha256("|".join(w["sha256"] or "" for w in facts["weights"]).encode()).hexdigest()
    model = {"model_id": facts["repo"], "revision": f"hf:{facts['hf_snapshot_commit']}|safetensors-sha256-of-list:{wsha}|tokenizer:{facts['tokenizer_json_sha256'][:16]}",
             "adapter": ADAPTER, "generation": {"do_sample": False, "seed": 42, "max_new_tokens": MAX_NEW_TOKENS, "dtype": "float16", "batch_size": 1},
             "chat_template_sha256": facts["chat_template_sha256"], "engine": f"transformers {clf.versions['transformers']} / torch {clf.versions['torch']}", "device": clf.versions.get("device")}
    payload = stance.identity_payload(model)
    out_lines, outcomes, errors, two = [], Counter(), Counter(), 0
    started = stance.utcnow()
    for line in work.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        x = json.loads(line)
        prompt = stance.fill(x["item"])
        t_a, raw, lab, attempts = stance.utcnow(), None, None, 0
        while attempts < stance.MAX_ATTEMPTS:
            attempts += 1
            raw = clf(prompt)
            lab = stance.validate(raw, item=x["item"])
            if lab["status"] not in stance.FAILED:
                break
            errors[lab["status"]] += 1
        two += attempts > 1
        outcomes[lab["label"] or lab["status"]] += 1
        out_lines.append(json.dumps({"upstream": x["upstream"], "upstream_id": x["upstream_id"], "version": x["version"], "content_sha256": x["content_sha256"],
                                     "target_id": x["item"]["target_id"], "found_by": x["found_by"], "raw_output": raw, "attempts": attempts,
                                     "started_at": t_a, "completed_at": stance.utcnow()}, ensure_ascii=False, sort_keys=True))
    t2 = time.perf_counter()
    results.write_text("".join(s + "\n" for s in out_lines), encoding="utf-8")
    man = {"work_sha256": man_in["sha256"], "sha256": hashlib.sha256(results.read_bytes()).hexdigest(), "items": len(out_lines), "prompt_version": stance.PROMPT_VERSION,
           "prompt_sha256": stance.prompt_hash(), "targets_sha256": stance.targets_hash(), "identity_payload": payload, "identity": stance.identity_hash(payload),
           "started_at": started, "finished_at": stance.utcnow()}
    Path(str(results) + ".manifest.json").write_text(json.dumps(man, indent=1, ensure_ascii=False), encoding="utf-8")
    calls = clf.calls
    rec = {**{k: man[k] for k in ("work_sha256", "sha256", "items", "prompt_version", "identity", "started_at", "finished_at")}, "articles": man_in.get("articles"),
           "model_id": model["model_id"], "revision": model["revision"], "decoding": "greedy, do_sample=false, seed 42, max 256 new tokens, batch 1",
           "stages_seconds": {"hash_and_load": round(t1 - t0, 1), "inference": round(t2 - t1, 1)}, "items_per_minute_inference_only": round(60 * len(out_lines) / (t2 - t1), 1) if out_lines else None,
           "model_calls": len(calls), "items_with_two_attempts": two, "attempt_errors": dict(errors), "answers_cut_by_the_output_limit": sum(1 for c in calls if c["hit_output_limit"]),
           "prompt_tokens": {"min": min(c["prompt_tokens"] for c in calls), "max": max(c["prompt_tokens"] for c in calls)} if calls else None,
           "outcomes_by_the_local_validator": dict(outcomes), "gpu": local_gpu.gpu_facts(), "libraries": clf.versions,
           "note": "outcomes here are the worker's own check; the server validates again at import and its result is the one stored"}
    if receipt:
        receipt.write_text(json.dumps(rec, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return rec


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--model", type=Path, required=True)
    ap.add_argument("--work", type=Path, required=True)
    ap.add_argument("--results", type=Path, required=True)
    ap.add_argument("--receipt", type=Path)
    a = ap.parse_args(argv)
    print(json.dumps(run(a.model, a.work, a.results, a.receipt), indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
