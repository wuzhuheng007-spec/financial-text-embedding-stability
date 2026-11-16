"""Verify one official pinned Qwen candidate without exposing model weights."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from .analysis_v18_freeze import CONFIG, PRIVATE, PUBLIC
from .analysis_v17_batch import write

EXPECTED_SHARDS = {
    "model-00001-of-00004.safetensors": "a1333e6293854747c481288ea83b348226af178dd565c49b6f9495ba1966aba7",
    "model-00002-of-00004.safetensors": "f5d25a2772cb825164a2a2c0fb6d51a87e282abf21e4dd75bc5cfb3cd0ea6185",
    "model-00003-of-00004.safetensors": "8efdec4c1bc12317ae1a38dc42b595ce777738a64deea3fcb8a0a91381bcdfd5",
    "model-00004-of-00004.safetensors": "1a72d403cdf0c1ec3cb7f289f17b394a01e64394c2e9b3c0f94dbce3faf879bd",
}


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def verify() -> dict:
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    root = Path(r"E:\quant_news_v18_candidate_models") / config["candidate_revision"]
    names = ["config.json", "tokenizer.json", "model.safetensors.index.json", "LICENSE", *EXPECTED_SHARDS]
    missing = [name for name in names if not (root / name).is_file()]
    if missing:
        return {"verified": False, "error": "MODEL_FILES_MISSING", "missing": missing}
    hashes = {name: digest(root / name) for name in names}
    mismatches = [name for name, expected in EXPECTED_SHARDS.items() if hashes[name] != expected]
    if mismatches:
        return {"verified": False, "error": "WEIGHT_HASH_MISMATCH", "mismatches": mismatches}
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    private = {"verified_at_utc": now, "model_name": config["candidate_model"],
               "model_revision": config["candidate_revision"], "model_dir": str(root),
               "model_file_hashes": {name: hashes[name] for name in ("config.json", "tokenizer.json", "model.safetensors.index.json")},
               "weight_file_hashes": {name: hashes[name] for name in EXPECTED_SHARDS},
               "quantization": config["candidate_quantization"],
               "license": "Apache-2.0", "source": "https://huggingface.co/Qwen/Qwen2.5-7B-Instruct/tree/" + config["candidate_revision"]}
    write(PRIVATE / "candidate_model_runtime.json", private)
    write(PUBLIC / "candidate_model_manifest_redacted.json", {k: v for k, v in private.items() if k != "model_dir"})
    return {"verified": True, "weight_shards": len(EXPECTED_SHARDS), "revision": config["candidate_revision"]}


if __name__ == "__main__":
    print(json.dumps(verify(), ensure_ascii=False))
