"""One isolated pinned-model stage call; caller terminates process on timeout."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

from .analysis_contract_v17 import messages
from .generate_v15b import EXPECTED_CONFIG, EXPECTED_REVISION, EXPECTED_TOKENIZER


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--request", type=Path, required=True)
    p.add_argument("--response", type=Path, required=True)
    p.add_argument("--preflight", action="store_true")
    a = p.parse_args()
    req = json.loads(a.request.read_text(encoding="utf-8"))
    model_dir = Path(os.environ["QUANT_V15_MODEL_DIR"])
    if model_dir.name != EXPECTED_REVISION or req["model_revision"] != EXPECTED_REVISION:
        raise RuntimeError("PINNED_REVISION_MISMATCH")
    for name, expected in (("config.json", EXPECTED_CONFIG), ("tokenizer.json", EXPECTED_TOKENIZER)):
        if hashlib.sha256((model_dir / name).read_bytes()).hexdigest() != expected:
            raise RuntimeError("MODEL_FILE_HASH_MISMATCH")
    for name in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "HF_DATASETS_OFFLINE"):
        os.environ[name] = "1"
    from transformers import AutoModelForCausalLM, AutoTokenizer
    tokenizer = AutoTokenizer.from_pretrained(str(model_dir), local_files_only=True, trust_remote_code=False)
    prompt = tokenizer.apply_chat_template(messages(req["stage"], req["payload"]),
                                           tokenize=False, add_generation_prompt=True)
    ids = tokenizer(prompt, return_tensors="pt", add_special_tokens=False).input_ids
    if ids.shape[1] > req["max_input_tokens"]:
        a.response.write_text(json.dumps({"error": "INPUT_TOKEN_BUDGET", "input_tokens": int(ids.shape[1])}), encoding="utf-8")
        return
    if a.preflight:
        a.response.write_text(json.dumps({"input_tokens": int(ids.shape[1]), "within_budget": True}), encoding="utf-8")
        return
    import torch
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA_UNAVAILABLE")
    torch.manual_seed(req["seed"])
    torch.cuda.manual_seed_all(req["seed"])
    model = AutoModelForCausalLM.from_pretrained(str(model_dir), local_files_only=True,
        trust_remote_code=False, torch_dtype=torch.float16, attn_implementation="eager").to("cuda")
    model.eval()
    a.request.with_suffix(".loaded").write_text(str(int(ids.shape[1])), encoding="ascii")
    with torch.inference_mode():
        generated = model.generate(ids.to("cuda"), do_sample=False, num_beams=1,
            max_new_tokens=req["max_new_tokens"], pad_token_id=tokenizer.eos_token_id,
            use_cache=True)[0][ids.shape[1]:]
    torch.cuda.synchronize()
    end_token = int(generated[-1]) if generated.shape[0] else None
    a.response.write_text(json.dumps({
        "raw": tokenizer.decode(generated, skip_special_tokens=True, clean_up_tokenization_spaces=False),
        "input_tokens": int(ids.shape[1]), "generated_tokens": int(generated.shape[0]),
        "stop_reason": "EOS_TOKEN" if end_token == tokenizer.eos_token_id else
            "MAX_NEW_TOKENS" if int(generated.shape[0]) >= req["max_new_tokens"] else "OTHER_TOKEN",
        "device": "cuda", "dtype": "float16",
    }, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
