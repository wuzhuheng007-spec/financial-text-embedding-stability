"""Local GPU candidates for the stance model: adapter and bench.

This is an in-process adapter for checkpoints that are ALREADY on this machine (transformers + CUDA). It downloads
nothing and starts no server. Each candidate gets its own inference identity built from what is really loaded:
snapshot commit, SHA-256 of the weight files, tokenizer and chat template hashes, library versions, dtype.

The output is not grammar-constrained here (unlike the llama.cpp worker), so the adapter appends a short format
instruction to the prompt; that appendix is part of the adapter version and therefore of the identity. Results of
this adapter are never interchangeable with the server worker's.

  python -m scripts.x_stance_v1.local_gpu inventory
  python -m scripts.x_stance_v1.local_gpu bench --model DIR --materials FILE --out FILE [--batch 8]
  python -m scripts.x_stance_v1.local_gpu work --model DIR --work FILE --results FILE     (off-server queue worker)

A bench on hand-written materials is an engineering diagnostic. It is not an accuracy figure for real posts.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import platform
import subprocess
import sys
import time
from pathlib import Path

from . import config as cfgmod, offload, stance, store

ADAPTER_VERSION = "x-stance-adapter-hf-json-appendix-prefill-stop-v2-20261123"
# v1 (appendix only) is retired. On the 48 development texts the 1.5B checkpoint wrapped every answer in a markdown
# code fence, so 48 of 48 failed the strict validator; the 3B checkpoint did not (2 of 48 were not valid JSON).
# Both receipts are kept as *_adapter_v1.json. A comparison in which one candidate fails on formatting alone says
# nothing about its judgement, so v2 starts the assistant turn with "{" and stops reading at the brace that closes
# that object - a decoding rule fixed before generation, like a stop sequence. The text inside the object is never
# edited or repaired.
RETIRED_ADAPTERS = {"x-stance-adapter-hf-json-appendix-v1-20261123": "1.5B: every answer inside a markdown code fence, 0 of 48 valid; 3B: 46 of 48 valid JSON"}
PREFILL = "{"
APPENDIX = ('\nAnswer with ONE JSON object and nothing else, with exactly these keys: '
            '"is_relevant" (true or false), "quality_status" ("OK", "IRRELEVANT", "CONTEXT_MISSING", "CONFLICTING", "LANGUAGE_UNSUPPORTED", "IMAGE_OR_LINK_ONLY" or "TRUNCATED"), '
            '"stance" ("bullish", "bearish", "neutral", or null when quality_status is not "OK"), '
            '"evidence_text" (the exact phrase copied from the post, or null when quality_status is not "OK").')
MODEL_KEYS = ("is_relevant", "quality_status", "stance", "evidence_text")


def _sha(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 22), b""):
            h.update(chunk)
    return h.hexdigest()


def gpu_facts() -> dict:
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total,memory.used,memory.free,driver_version", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=20).stdout.strip().splitlines()[0]
        name, total, used, free, drv = [x.strip() for x in out.split(",")]
        return {"name": name, "memory_total_mib": int(total), "memory_used_mib": int(used), "memory_free_mib": int(free), "driver": drv, "read_with": "nvidia-smi"}
    except Exception as ex:  # noqa: BLE001
        return {"error": type(ex).__name__}


def checkpoint_facts(model_dir: Path, *, hash_weights: bool = True) -> dict:
    """What is actually in the directory that will be loaded."""
    d = model_dir.resolve()
    weights = sorted(p for p in d.iterdir() if p.suffix == ".safetensors")
    cfg = json.loads((d / "config.json").read_text(encoding="utf-8"))
    tok = json.loads((d / "tokenizer_config.json").read_text(encoding="utf-8"))
    tpl = tok.get("chat_template") or ""
    return {"directory_name": d.name, "hf_snapshot_commit": d.name if len(d.name) == 40 else None, "repo": d.parent.parent.name.replace("models--", "").replace("--", "/"),
            "weights": [{"file": p.name, "bytes": p.stat().st_size, "sha256": _sha(p) if hash_weights else None} for p in weights],
            "tokenizer_json_sha256": _sha(d / "tokenizer.json"), "chat_template_sha256": store.sha(tpl) if tpl else None,
            "architecture": cfg.get("architectures"), "hidden_size": cfg.get("hidden_size"), "layers": cfg.get("num_hidden_layers"), "torch_dtype_in_config": cfg.get("torch_dtype")}


def candidate_config(base: dict, facts: dict, versions: dict, *, dtype: str, max_new_tokens: int | None = None) -> dict:
    """The classifier block for this checkpoint; everything in it comes from `facts` and the libraries in use."""
    c = copy.deepcopy(base)
    g = c["classifier"]["generation"]
    wsha = store.sha("|".join(w["sha256"] or "" for w in facts["weights"]))
    c["classifier"].update(
        model_id=facts["repo"], immutable_revision=f"hf:{facts['hf_snapshot_commit']}|safetensors-sha256-of-list:{wsha}|tokenizer:{facts['tokenizer_json_sha256'][:16]}",
        adapter_version=ADAPTER_VERSION, temperature=0,
        generation={"temperature": 0, "do_sample": False, "seed": g["seed"], "max_output_tokens": max_new_tokens or g["max_output_tokens"], "dtype": dtype, "batch_size": 1,
                    "padding_side": "left", "appendix_sha256": store.sha(APPENDIX), "assistant_prefill": PREFILL, "stop": "at the brace closing the first object"},
        runtime={"engine": f"transformers {versions['transformers']} / torch {versions['torch']}", "image": None, "build": versions.get("cuda"),
                 "chat_template_sha256": facts["chat_template_sha256"], "device": versions.get("device")})
    return c


def first_object(text: str) -> str | None:
    """The text up to and including the brace that closes the object opened by the first character. None if it
    never closes. Braces inside JSON strings are not counted."""
    depth, in_str, esc = 0, False, False
    for i, ch in enumerate(text):
        if in_str:
            if esc:
                esc = False
            elif ch == chr(92):
                esc = True
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[:i + 1]
    return None


class HFClassifier:
    """callable(prompt, ids) -> raw JSON text, same contract as the server worker's adapter."""

    wants_ids = True

    def __init__(self, model_dir: Path, *, dtype: str = "float16", max_new_tokens: int = 96, seed: int = 42):
        import torch
        import transformers
        from transformers import AutoModelForCausalLM, AutoTokenizer
        self.torch, self.calls, self.max_new_tokens = torch, [], max_new_tokens
        torch.manual_seed(seed)
        self.tok = AutoTokenizer.from_pretrained(str(model_dir), local_files_only=True)
        self.tok.padding_side = "left"
        t0 = time.monotonic()
        self.model = AutoModelForCausalLM.from_pretrained(str(model_dir), local_files_only=True, torch_dtype=getattr(torch, dtype)).to("cuda")
        self.model.eval()
        self.load_seconds = round(time.monotonic() - t0, 2)
        self.versions = {"torch": torch.__version__, "transformers": transformers.__version__, "cuda": torch.version.cuda, "device": torch.cuda.get_device_name(0), "python": platform.python_version()}
        self.template_sha = store.sha(self.tok.chat_template or "")

    def props(self) -> dict:
        return {"chat_template_sha256": self.template_sha, "engine": self.versions, "load_seconds": self.load_seconds}

    def _encode(self, prompts: list[str]):
        texts = [self.tok.apply_chat_template([{"role": "user", "content": p + APPENDIX}], tokenize=False, add_generation_prompt=True) + PREFILL for p in prompts]
        return self.tok(texts, return_tensors="pt", padding=True).to("cuda")

    def generate(self, prompts: list[str]) -> list[dict]:
        torch = self.torch
        enc = self._encode(prompts)
        torch.cuda.reset_peak_memory_stats()
        t0 = time.monotonic()
        with torch.no_grad():
            out = self.model.generate(**enc, max_new_tokens=self.max_new_tokens, do_sample=False, pad_token_id=self.tok.pad_token_id or self.tok.eos_token_id)
        torch.cuda.synchronize()
        dt = time.monotonic() - t0
        res = []
        for i in range(len(prompts)):
            new = out[i][enc["input_ids"].shape[1]:]
            n_new = int((new != (self.tok.pad_token_id or self.tok.eos_token_id)).sum())
            full = PREFILL + self.tok.decode(new, skip_special_tokens=True)
            obj = first_object(full)
            res.append({"text": obj if obj is not None else full.strip(), "generated_after_object": (len(full) - len(obj)) if obj is not None else None, "object_closed": obj is not None,
                        "prompt_tokens": int(enc["attention_mask"][i].sum()), "completion_tokens": n_new,
                        "hit_output_limit": obj is None and n_new >= self.max_new_tokens, "seconds_for_batch": round(dt, 3), "batch": len(prompts),
                        "vram_peak_mib": round(torch.cuda.max_memory_allocated() / 2**20)})
        return res

    @staticmethod
    def wrap(text: str, ids: dict | None, cut: bool) -> str:
        """The model's four fields plus the ids of the request. Anything that is not exactly one JSON object with
        exactly those four keys is passed through untouched and fails validation; nothing is repaired."""
        if cut:
            return ""
        try:
            o = json.loads(text)
        except ValueError:
            return text
        if ids is None or not isinstance(o, dict) or set(o) != set(MODEL_KEYS):
            return text
        return json.dumps({"post_id": str(ids["post_id"]), "topic_id": ids["topic_id"], "target_id": ids["target_id"], **o, "reason_short": None}, ensure_ascii=False, sort_keys=True)

    def __call__(self, prompt: str, ids: dict | None = None) -> str:
        r = self.generate([prompt])[0]
        self.calls.append(r)
        return self.wrap(r["text"], ids, r["hit_output_limit"])


def _materials(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").split("\n") if x.strip()]


def bench(model_dir: Path, materials: Path, out: Path, *, batch: int = 8, hash_weights: bool = True) -> dict:
    """Every item once, one at a time (labels, latency), then the same items in small batches (throughput only)."""
    base = cfgmod.load()
    items = _materials(materials)
    facts = checkpoint_facts(model_dir, hash_weights=hash_weights)
    gpu_before = gpu_facts()
    clf = HFClassifier(model_dir, max_new_tokens=base["classifier"]["generation"]["max_output_tokens"], seed=base["classifier"]["generation"]["seed"])
    cfg = candidate_config(base, facts, clf.versions, dtype="float16")
    tpl = stance.PROMPTS[cfg["classifier"]["prompt_version"]]
    prompt = lambda it: tpl.format(topic_id="qqq", target_id="QQQ", post_id=it["id"], kind=it["kind"], parent_available=False, text=it["text"])
    warm = clf.generate([prompt(items[0])])[0]                       # warm-up, not counted
    rows, t0 = [], time.monotonic()
    for it in items:
        started = store.utcnow()
        raw = clf(prompt(it), {"post_id": it["id"], "topic_id": "qqq", "target_id": "QQQ"})
        call = clf.calls[-1]
        lab = stance.validate_output(raw, post={"post_id": it["id"], "author_text": it["text"]}, topic_id="qqq", target_id="QQQ")
        got = lab["label"] or ("IRRELEVANT" if lab["quality_status"] == "IRRELEVANT" else "FORMAT_FAILURE" if lab["quality_status"] in ("JSON_INVALID", "EVIDENCE_NOT_IN_TEXT") else "UNJUDGED")
        rows.append({**{k: it[k] for k in ("id", "lang", "kind", "category", "intent")}, "chars": len(it["text"]), "label": lab["label"], "quality_status": lab["quality_status"], "outcome": got,
                     "matches_intent": got == it["intent"], "raw_model_text": call["text"], "seconds": call["seconds_for_batch"], "prompt_tokens": call["prompt_tokens"],
                     "completion_tokens": call["completion_tokens"], "hit_output_limit": call["hit_output_limit"], "vram_peak_mib": call["vram_peak_mib"],
                     "inference_started_at_utc": started, "inference_completed_at_utc": store.utcnow()})
    single_seconds = time.monotonic() - t0
    batches, tb = [], time.monotonic()
    for i in range(0, len(items), batch):
        res = clf.generate([prompt(it) for it in items[i:i + batch]])
        batches.append({"items": len(res), "seconds": res[0]["seconds_for_batch"], "vram_peak_mib": res[0]["vram_peak_mib"],
                        "same_text_as_single": sum(1 for r, row in zip(res, rows[i:i + batch]) if r["text"] == row["raw_model_text"])})
    batch_seconds = time.monotonic() - tb
    sec = sorted(r["seconds"] for r in rows)
    cats = {}
    for r in rows:
        c = cats.setdefault(r["category"], {"n": 0, "matches_intent": 0, "outcomes": {}})
        c["n"] += 1; c["matches_intent"] += int(r["matches_intent"]); c["outcomes"][r["outcome"]] = c["outcomes"].get(r["outcome"], 0) + 1
    matrix = {}
    for r in rows:
        matrix.setdefault(r["intent"], {}).setdefault(r["outcome"], 0)
        matrix[r["intent"]][r["outcome"]] += 1
    res = {"kind": "LOCAL_GPU_BENCH_ON_HAND_WRITTEN_DEVELOPMENT_MATERIALS", "not": "not real posts, not human-labelled, not an accuracy estimate",
           "materials": {"file": materials.name, "sha256": _sha(materials), "items": len(items)},
           "candidate": {"checkpoint": facts, "inference_identity": stance.identity_hash(cfg), "identity": stance.inference_identity(cfg), "adapter_version": ADAPTER_VERSION},
           "hardware": {"gpu_before_load": gpu_before, "gpu_after_run": gpu_facts(), "platform": platform.platform()}, "libraries": clf.versions,
           "load_seconds": clf.load_seconds, "warmup": {"seconds": warm["seconds_for_batch"], "counted": False},
           "single": {"items": len(rows), "seconds_total": round(single_seconds, 2), "seconds_median": sec[len(sec) // 2], "seconds_max": sec[-1], "items_per_minute": round(60 * len(rows) / single_seconds, 1),
                      "prompt_tokens": sum(r["prompt_tokens"] for r in rows), "completion_tokens": sum(r["completion_tokens"] for r in rows),
                      "vram_peak_mib": max(r["vram_peak_mib"] for r in rows), "hit_output_limit": sum(r["hit_output_limit"] for r in rows)},
           "batched": {"batch_size": batch, "batches": batches, "seconds_total": round(batch_seconds, 2), "items_per_minute": round(60 * len(items) / batch_seconds, 1),
                       "vram_peak_mib": max(b["vram_peak_mib"] for b in batches), "texts_identical_to_single": sum(b["same_text_as_single"] for b in batches),
                       "note": "throughput only; batched answers are not used as labels because padding can change a greedy decode"},
           "outcomes": {"by_quality_status": {q: sum(1 for r in rows if r["quality_status"] == q) for q in sorted({r["quality_status"] for r in rows})},
                        "format_failures": sum(1 for r in rows if r["outcome"] == "FORMAT_FAILURE"),
                        "matches_design_intent": sum(r["matches_intent"] for r in rows), "of": len(rows), "by_category": cats, "intent_by_outcome": matrix,
                        "by_language": {lg: {"n": sum(1 for r in rows if r["lang"] == lg), "matches_intent": sum(r["matches_intent"] for r in rows if r["lang"] == lg)} for lg in ("en", "zh")},
                        "bull_bear_swaps": sum(1 for r in rows if {r["intent"], r["outcome"]} == {"bullish", "bearish"}),
                        "directional_intent_excluded_as_irrelevant": sum(1 for r in rows if r["intent"] in ("bullish", "bearish", "neutral") and r["outcome"] == "IRRELEVANT")},
           "rows": rows}
    out.write_text(json.dumps(res, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    return {k: res[k] for k in ("load_seconds", "single", "outcomes")} | {"inference_identity": res["candidate"]["inference_identity"], "batched_items_per_minute": res["batched"]["items_per_minute"]}


def work(model_dir: Path, work_file: Path, results: Path) -> dict:
    """Off-server queue worker. The work file names the identity it wants; this refuses unless the loaded
    checkpoint, template, libraries and parameters give exactly that identity."""
    base = cfgmod.load()
    facts = checkpoint_facts(model_dir)
    clf = HFClassifier(model_dir, max_new_tokens=base["classifier"]["generation"]["max_output_tokens"], seed=base["classifier"]["generation"]["seed"])
    cfg = candidate_config(base, facts, clf.versions, dtype="float16")

    class Appended:                                                 # the work file carries the bare prompt; the adapter adds its appendix itself
        wants_ids, props = True, clf.props

        def __call__(self, prompt, ids=None):
            return clf(prompt, ids)
    return offload.write_results(work_file, results, Appended(), cfg, worker={"gpu": gpu_facts(), "checkpoint": {k: facts[k] for k in ("repo", "hf_snapshot_commit")}})


def inventory(roots: list[Path]) -> dict:
    found = []
    for root in roots:
        if not root.is_dir():
            continue
        for snap in root.glob("models--*/snapshots/*"):
            if (snap / "config.json").exists() and any(p.suffix == ".safetensors" for p in snap.iterdir()):
                found.append({"path": str(snap), **checkpoint_facts(snap, hash_weights=False)})
    libs = {}
    try:
        import torch, transformers
        libs = {"torch": torch.__version__, "transformers": transformers.__version__, "cuda_available": torch.cuda.is_available(), "cuda": torch.version.cuda}
    except Exception as ex:  # noqa: BLE001
        libs = {"error": type(ex).__name__}
    return {"read_at_utc": store.utcnow(), "gpu": gpu_facts(), "python": sys.executable, "libraries": libs, "checkpoints": found}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="x_stance_v1.local_gpu")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("inventory"); s.add_argument("--root", action="append", default=[])
    s = sub.add_parser("bench"); s.add_argument("--model", required=True); s.add_argument("--materials", required=True); s.add_argument("--out", required=True); s.add_argument("--batch", type=int, default=8)
    s = sub.add_parser("work"); s.add_argument("--model", required=True); s.add_argument("--work", required=True); s.add_argument("--results", required=True)
    s = sub.add_parser("config"); s.add_argument("--model", required=True); s.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    os.environ.setdefault("HF_HUB_OFFLINE", "1"); os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")       # nothing is fetched
    if a.cmd == "inventory":
        out = inventory([Path(r) for r in a.root])
    elif a.cmd == "bench":
        out = bench(Path(a.model), Path(a.materials), Path(a.out), batch=a.batch)
    elif a.cmd == "config":                                      # the complete candidate configuration for this checkpoint, for the server-side export / import
        base = cfgmod.load()
        facts = checkpoint_facts(Path(a.model))
        clf = HFClassifier(Path(a.model), max_new_tokens=base["classifier"]["generation"]["max_output_tokens"], seed=base["classifier"]["generation"]["seed"])
        cfg = candidate_config(base, facts, clf.versions, dtype="float16")
        cfg["classifier"]["classification_quality"] = "UNVALIDATED"
        Path(a.out).write_text(json.dumps(cfg, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
        from . import stance as _st
        out = {"written": a.out, "inference_identity": _st.identity_hash(cfg), "model_id": cfg["classifier"]["model_id"], "revision": cfg["classifier"]["immutable_revision"],
               "server_reference_identity": _st.identity_hash(base), "note": "a different identity from the server reference; its labels are never mixed with it"}
    else:
        out = work(Path(a.model), Path(a.work), Path(a.results))
    print(json.dumps(out, indent=1, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
