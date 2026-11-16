"""One isolated model invocation. Supervisor enforces load and generation timeouts."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

from .analysis_contract_v18 import messages
from .generate_v15b import EXPECTED_CONFIG, EXPECTED_REVISION, EXPECTED_TOKENIZER


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--response", type=Path, required=True)
    parser.add_argument("--preflight", action="store_true")
    args = parser.parse_args()
    req = json.loads(args.request.read_text(encoding="utf-8"))
    model_dir = Path(os.environ["QUANT_V18_MODEL_DIR"])
    if model_dir.name != req["model_revision"]:
        raise RuntimeError("REVISION_DIRECTORY_MISMATCH")
    if req["model_kind"] == "baseline":
        if model_dir.name != EXPECTED_REVISION:
            raise RuntimeError("BASELINE_REVISION_MISMATCH")
        for name, expected in (("config.json", EXPECTED_CONFIG), ("tokenizer.json", EXPECTED_TOKENIZER)):
            if hashlib.sha256((model_dir / name).read_bytes()).hexdigest() != expected:
                raise RuntimeError("BASELINE_FILE_HASH_MISMATCH")
    else:
        for name, expected in req["model_file_hashes"].items():
            if hashlib.sha256((model_dir / name).read_bytes()).hexdigest() != expected:
                raise RuntimeError("CANDIDATE_FILE_HASH_MISMATCH")
    for key in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "HF_DATASETS_OFFLINE"):
        os.environ[key] = "1"
    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
    tokenizer = AutoTokenizer.from_pretrained(str(model_dir), local_files_only=True, trust_remote_code=False)
    prompt = tokenizer.apply_chat_template(messages(req["stage"], req["payload"]),
                                           tokenize=False, add_generation_prompt=True)
    ids = tokenizer(prompt, return_tensors="pt", add_special_tokens=False).input_ids
    if int(ids.shape[1]) > req["max_input_tokens"]:
        args.response.write_text(json.dumps({"error": "INPUT_TOKEN_BUDGET", "input_tokens": int(ids.shape[1])}), encoding="utf-8")
        return
    if args.preflight:
        args.response.write_text(json.dumps({"input_tokens": int(ids.shape[1]), "within_budget": True}), encoding="utf-8")
        return
    import torch
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA_UNAVAILABLE")
    torch.manual_seed(req["seed"])
    torch.cuda.manual_seed_all(req["seed"])
    options = {"local_files_only": True, "trust_remote_code": False,
               "torch_dtype": torch.float16, "attn_implementation": "eager"}
    if req["model_kind"] == "candidate":
        options["quantization_config"] = BitsAndBytesConfig(
            load_in_4bit=True, bnb_4bit_quant_type="nf4", bnb_4bit_compute_dtype=torch.float16,
            bnb_4bit_use_double_quant=False)
        options["device_map"] = {"": 0}
    model = AutoModelForCausalLM.from_pretrained(str(model_dir), **options)
    if req["model_kind"] == "baseline":
        model = model.to("cuda")
    model.eval()
    args.request.with_suffix(".loaded").write_text(str(int(ids.shape[1])), encoding="ascii")
    torch.cuda.reset_peak_memory_stats()
    with torch.inference_mode():
        generated = model.generate(ids.to("cuda"), do_sample=False, num_beams=1,
            max_new_tokens=req["max_new_tokens"], pad_token_id=tokenizer.eos_token_id,
            use_cache=True)[0][ids.shape[1]:]
    torch.cuda.synchronize()
    end_token = int(generated[-1]) if generated.shape[0] else None
    args.response.write_text(json.dumps({
        "raw": tokenizer.decode(generated, skip_special_tokens=True, clean_up_tokenization_spaces=False),
        "input_tokens": int(ids.shape[1]), "generated_tokens": int(generated.shape[0]),
        "stop_reason": "EOS_TOKEN" if end_token == tokenizer.eos_token_id else
            "MAX_NEW_TOKENS" if int(generated.shape[0]) >= req["max_new_tokens"] else "OTHER_TOKEN",
        "device": "cuda", "dtype": "float16", "quantization": req.get("quantization"),
        "peak_cuda_bytes": torch.cuda.max_memory_allocated(),
    }, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
