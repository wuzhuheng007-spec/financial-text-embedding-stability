"""One isolated offline v1.8.2 model call; preflight never loads weights."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

from .analysis_contract_v182 import messages
from .generate_v15b import EXPECTED_CONFIG, EXPECTED_REVISION, EXPECTED_TOKENIZER


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--response", type=Path, required=True)
    parser.add_argument("--preflight", action="store_true")
    args = parser.parse_args()
    request = json.loads(args.request.read_text(encoding="utf-8"))
    model_dir = Path(os.environ["QUANT_V182_MODEL_DIR"])
    if request["model_revision"] != EXPECTED_REVISION or model_dir.name != EXPECTED_REVISION:
        raise RuntimeError("MODEL_REVISION_MISMATCH")
    for name, expected in (("config.json", EXPECTED_CONFIG), ("tokenizer.json", EXPECTED_TOKENIZER)):
        if hashlib.sha256((model_dir / name).read_bytes()).hexdigest() != expected:
            raise RuntimeError("MODEL_FILE_HASH_MISMATCH")
    for key in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "HF_DATASETS_OFFLINE"):
        os.environ[key] = "1"
    from transformers import AutoModelForCausalLM, AutoTokenizer
    tokenizer = AutoTokenizer.from_pretrained(str(model_dir), local_files_only=True, trust_remote_code=False)
    prompt = tokenizer.apply_chat_template(messages(request["payload"]), tokenize=False, add_generation_prompt=True)
    ids = tokenizer(prompt, return_tensors="pt", add_special_tokens=False).input_ids
    input_tokens = int(ids.shape[1])
    if input_tokens > request["max_input_tokens"]:
        args.response.write_text(json.dumps({"error": "INPUT_TOKEN_BUDGET", "input_tokens": input_tokens}), encoding="utf-8")
        return
    if args.preflight:
        args.response.write_text(json.dumps({"input_tokens": input_tokens, "within_budget": True}), encoding="utf-8")
        return
    import torch
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA_UNAVAILABLE")
    torch.manual_seed(request["seed"])
    torch.cuda.manual_seed_all(request["seed"])
    model = AutoModelForCausalLM.from_pretrained(str(model_dir), local_files_only=True,
                                                trust_remote_code=False, torch_dtype=torch.float16,
                                                attn_implementation="eager").to("cuda")
    model.eval()
    args.request.with_suffix(".loaded").write_text(str(input_tokens), encoding="ascii")
    torch.cuda.reset_peak_memory_stats()
    with torch.inference_mode():
        output = model.generate(ids.to("cuda"), do_sample=False, num_beams=1,
                                max_new_tokens=request["max_new_tokens"],
                                pad_token_id=tokenizer.eos_token_id, use_cache=True)[0][input_tokens:]
    torch.cuda.synchronize()
    end_token = int(output[-1]) if output.shape[0] else None
    args.response.write_text(json.dumps({
        "raw": tokenizer.decode(output, skip_special_tokens=True, clean_up_tokenization_spaces=False),
        "input_tokens": input_tokens, "generated_tokens": int(output.shape[0]),
        "stop_reason": "EOS_TOKEN" if end_token == tokenizer.eos_token_id else
                       "MAX_NEW_TOKENS" if int(output.shape[0]) >= request["max_new_tokens"] else "OTHER_TOKEN",
        "device": "cuda", "dtype": "float16", "attention": "eager",
        "peak_cuda_bytes": torch.cuda.max_memory_allocated(),
    }, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
