"""Post-run local model-file integrity receipt without exposing private paths."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from .analysis_v16 import RUNTIME
from .collector import ROOT

FILES = ("model-00001-of-00002.safetensors", "model-00002-of-00002.safetensors",
         "model.safetensors.index.json", "config.json", "tokenizer.json")


def run() -> dict:
    runtime = json.loads(RUNTIME.read_text(encoding="utf-8"))
    model = Path(runtime["model_dir"])
    prior = json.loads((ROOT / "results/quant_portal_personal_v18_1_20261117/model_files_postrun_redacted.json")
                       .read_text(encoding="utf-8"))
    prior_hashes = {x["file"]: x["sha256"] for x in prior["files"]}
    rows = []
    for name in FILES:
        path = model / name
        with path.open("rb") as handle:
            digest = hashlib.file_digest(handle, "sha256").hexdigest()
        rows.append({"file": name, "bytes": path.stat().st_size, "sha256": digest,
                     "matches_previous_postrun_hash": digest == prior_hashes[name]})
    result = {"schema": "news_extraction_v182_postrun_model_hashes_1",
              "checked_at_utc": datetime.now(timezone.utc).isoformat(),
              "model": "Qwen/Qwen2.5-3B-Instruct",
              "revision": "aa8e72537993ba99e69dfaafa59ed015b17504d1",
              "all_match_previous_postrun_hashes": all(x["matches_previous_postrun_hash"] for x in rows),
              "files": rows,
              "limitation": "Weights were hashed after generation, not before each call; this does not prove remote origin or exclude mid-run mutation."}
    target = ROOT / "results/news_extraction_v182_20261117/model_files_postrun_redacted.json"
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"files": len(rows), "all_match": result["all_match_previous_postrun_hashes"]}


if __name__ == "__main__":
    print(json.dumps(run()))
