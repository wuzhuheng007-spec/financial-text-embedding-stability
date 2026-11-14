"""Model-free LMFE character and pinned-Qwen token callback compatibility gate.

No model class, model weights, or model forward call is loaded here. The frozen
wire Schema remains the independent source validator, not the LMFE parser.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path

from jsonschema import Draft202012Validator

# LMFE's official builder creates a new config, so these documented environment
# controls must be set before the builder is called. Zero disables its implicit
# 20-element array cap; the high whitespace cap is beyond the fixed 1024-token
# generation budget and does not change which JSON objects are representable.
os.environ["LMFE_MAX_JSON_ARRAY_LENGTH"] = "0"
os.environ["LMFE_MAX_CONSECUTIVE_WHITESPACES"] = str(sys.maxsize)
os.environ["LMFE_STRICT_JSON_FIELD_ORDER"] = "false"
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"

import torch  # noqa: E402 - tensor input only, no model loading
from transformers import AutoTokenizer  # noqa: E402
from lmformatenforcer import JsonSchemaParser  # noqa: E402
from lmformatenforcer.characterlevelparser import CharacterLevelParserConfig  # noqa: E402
from lmformatenforcer.integrations.transformers import (  # noqa: E402
    build_token_enforcer_tokenizer_data,
    build_transformers_prefix_allowed_tokens_fn,
)
from lmformatenforcer.tokenenforcer import TokenEnforcerTokenizerData  # noqa: E402

from check_lmfe_backend import encode_case, replay, sha256  # noqa: E402


PINNED_TOKENIZER_FILES = {
    "tokenizer.json": "c0382117ea329cdf097041132f6d735924b697924d6f6fc3945713e96ce87539",
    "tokenizer_config.json": "5b5d4f65d0acd3b2d56a35b56d374a36cbc1c8fa5cf3b3febbbfabf22f359583",
    "generation_config.json": "ea35dfb6fc5051b01114f9b995820d55dab01ed33ee490f6378b442af82c09f9",
}
MODEL_REVISION = "aa8e72537993ba99e69dfaafa59ed015b17504d1"


def new_callback(tokenizer_data: TokenEnforcerTokenizerData, schema: dict):
    parser = JsonSchemaParser(schema)
    callback = build_transformers_prefix_allowed_tokens_fn(tokenizer_data, parser)
    effective = callback.token_enforcer.root_parser.config
    if (effective.max_json_array_length != 0
            or effective.max_consecutive_whitespaces != sys.maxsize
            or effective.force_json_field_order
            or effective.alphabet != tokenizer_data.tokenizer_alphabet):
        raise AssertionError("EFFECTIVE_BACKEND_CONFIG_NOT_FROZEN")
    return callback


def allowed(callback, ids: list[int]) -> tuple[set[int], bool]:
    result = callback(0, torch.tensor(ids, dtype=torch.long))
    parser = callback.token_enforcer.prefix_states[tuple(ids)].parser
    return set(result), bool(parser.can_end())


def replay_tokens(name: str, text: str, tokenizer, data, schema: dict,
                  eos_ids: list[int], prompt_ids: list[int], max_wall: float) -> dict:
    started = time.monotonic()
    callback = new_callback(data, schema)
    ids = tokenizer.encode(text, add_special_tokens=False)
    decoded = tokenizer.decode(ids, skip_special_tokens=False,
                               clean_up_tokenization_spaces=False)
    result = {"name": name, "input_tokens": len(ids),
              "decoded_text_matches_input": decoded == text,
              "multicharacter_tokens": sum(len(tokenizer.decode([token],
                    skip_special_tokens=False, clean_up_tokenization_spaces=False)) > 1
                    for token in ids),
              "prompt_tokens": len(prompt_ids), "first_disallowed_token_index": None,
              "empty_allowed_set": False, "premature_eos": False,
              "eos_at_end": False, "generation_prefix_verified": 0}
    prefix = list(prompt_ids)
    for index, token in enumerate(ids):
        if time.monotonic() - started > max_wall:
            result["stopped_for_wall_limit"] = True
            break
        choices, can_end = allowed(callback, prefix)
        if not choices:
            result["empty_allowed_set"] = True
            result["first_disallowed_token_index"] = index
            break
        if not can_end and any(eos in choices for eos in eos_ids):
            result["premature_eos"] = True
            result["first_disallowed_token_index"] = index
            break
        if token not in choices:
            result["first_disallowed_token_index"] = index
            break
        prefix.append(token)
        result["generation_prefix_verified"] += 1
    else:
        choices, can_end = allowed(callback, prefix)
        result["eos_at_end"] = bool(can_end and all(eos in choices for eos in eos_ids))
        result["empty_allowed_set"] = not bool(choices)
    rejected = result["first_disallowed_token_index"]
    if rejected is not None:
        prior = tokenizer.decode(ids[:rejected], skip_special_tokens=False,
                                 clean_up_tokenization_spaces=False)
        expected_next = tokenizer.decode(ids[:rejected + 1], skip_special_tokens=False,
                                         clean_up_tokenization_spaces=False)
        choices, can_end = allowed(callback, prompt_ids + ids[:rejected])
        result["synthetic_witness"] = {
            "token_id": ids[rejected],
            "token_alone_decoded": tokenizer.decode([ids[rejected]],
                skip_special_tokens=False, clean_up_tokenization_spaces=False),
            "prefix_tail": prior[-80:], "would_be_tail": expected_next[-80:],
            "allowed_token_count": len(choices), "state_can_end": can_end,
            "eos_allowed": [eos for eos in eos_ids if eos in choices],
            "allowed_character_count": len(callback.token_enforcer.prefix_states[
                tuple(prompt_ids + ids[:rejected])].parser.get_allowed_characters()),
        }
    result["elapsed_seconds"] = round(time.monotonic() - started, 3)
    return result


def main() -> None:
    ap = argparse.ArgumentParser()
    for name in ("schema", "source-schema", "sentinels", "supplemental", "tokenizer", "out"):
        ap.add_argument("--" + name, type=Path, required=True)
    ap.add_argument("--max-wall-per-token-case", type=float, default=45.0)
    ap.add_argument("--token-case", action="append")
    ap.add_argument("--skip-char", action="store_true")
    args = ap.parse_args()
    if args.out.exists():
        raise FileExistsError(args.out)
    if args.tokenizer.name != MODEL_REVISION:
        raise ValueError("TOKENIZER_REVISION_PATH_MISMATCH")
    file_hashes = {name: sha256(args.tokenizer / name) for name in PINNED_TOKENIZER_FILES}
    if file_hashes != PINNED_TOKENIZER_FILES:
        raise ValueError("TOKENIZER_FILE_HASH_MISMATCH")
    schema = json.loads(args.schema.read_text(encoding="utf-8"))
    source = json.loads(args.source_schema.read_text(encoding="utf-8"))
    validator = Draft202012Validator(source)
    cases = (json.loads(args.sentinels.read_text(encoding="utf-8"))
             + json.loads(args.supplemental.read_text(encoding="utf-8")))
    tokenizer = AutoTokenizer.from_pretrained(str(args.tokenizer), local_files_only=True,
                                              trust_remote_code=False)
    raw_data = build_token_enforcer_tokenizer_data(tokenizer)
    eos_ids = json.loads((args.tokenizer / "generation_config.json").read_text(
        encoding="utf-8"))["eos_token_id"]
    if eos_ids != [151645, 151643]:
        raise ValueError("EFFECTIVE_GENERATION_EOS_CHANGED")
    data = TokenEnforcerTokenizerData(raw_data.regular_tokens, raw_data.decoder,
                                      eos_ids, False, len(tokenizer))
    char_config = CharacterLevelParserConfig(
        alphabet=data.tokenizer_alphabet, max_json_array_length=0,
        max_consecutive_whitespaces=sys.maxsize, force_json_field_order=False)
    char_rows = []
    for case in ([] if args.skip_char else cases):
        result = replay(schema, case["object"], char_config,
                        case.get("serialization", "compact"))
        char_rows.append({"name": case["name"], "source_accepts": validator.is_valid(case["object"]),
                          "expected_valid": case["expected_valid"], **result})
    by_name = {case["name"]: case for case in cases}
    synthetic_messages = [
        {"role": "system", "content": "Return a JSON object."},
        {"role": "user", "content": "Synthetic article: Acme announced a product."},
    ]
    prompt_ids = tokenizer.apply_chat_template(synthetic_messages, tokenize=True,
                                               add_generation_prompt=True)
    selected = ["root_NO_IDENTIFIABLE_EVENT_NO", "valid_company_event",
                "unicode_quotes_backslash_newline", "unbounded_article_evidence_21",
                "no_market_wrong_direction"]
    if args.token_case:
        selected = args.token_case
    token_rows = []
    for name in selected:
        case = by_name[name]
        mode = case.get("serialization", "compact")
        text = encode_case(case["object"], mode)
        row = replay_tokens(name, text, tokenizer, data, schema, eos_ids, prompt_ids,
                            args.max_wall_per_token_case)
        row["source_accepts"] = validator.is_valid(case["object"])
        token_rows.append(row)
    empty_callback = new_callback(data, schema)
    empty_choices, empty_can_end = allowed(empty_callback, [])
    second_messages = [
        {"role": "system", "content": "Return a JSON object."},
        {"role": "user", "content": "Different synthetic article: Beta reported a launch."},
    ]
    second_prompt = tokenizer.apply_chat_template(second_messages, tokenize=True,
                                                  add_generation_prompt=True)
    first_callback = new_callback(data, schema)
    second_callback = new_callback(data, schema)
    first_choices, first_can_end = allowed(first_callback, prompt_ids)
    second_choices, second_can_end = allowed(second_callback, second_prompt)
    callback_checks = {
        "empty_prompt_allowed_nonempty": bool(empty_choices),
        "empty_prompt_can_end": empty_can_end,
        "empty_prompt_eos_allowed": any(x in empty_choices for x in eos_ids),
        "two_distinct_prompt_lengths": len(prompt_ids) != len(second_prompt),
        "independent_article_callback_objects": first_callback is not second_callback,
        "article_one_prefix_state_count": len(first_callback.token_enforcer.prefix_states),
        "article_two_prefix_state_count": len(second_callback.token_enforcer.prefix_states),
        "article_one_initial_allowed_nonempty": bool(first_choices),
        "article_two_initial_allowed_nonempty": bool(second_choices),
        "article_one_initial_can_end": first_can_end,
        "article_two_initial_can_end": second_can_end,
        "eos_disallowed_at_both_starts": (not any(x in first_choices for x in eos_ids)
                                          and not any(x in second_choices for x in eos_ids)),
    }
    result = {
        "schema": "lmfe_tokenizer_gate_v1", "model_calls": 0,
        "backend_version": "0.11.3", "compiler_schema_sha256": sha256(args.schema),
        "source_schema_sha256": sha256(args.source_schema),
        "tokenizer_revision": MODEL_REVISION, "tokenizer_file_sha256": file_hashes,
        "tokenizer_length": len(tokenizer),
        "tokenizer_alphabet_length": len(data.tokenizer_alphabet),
        "effective_eos_ids": eos_ids,
        "backend_effective_config": {"max_json_array_length": 0,
                                     "max_consecutive_whitespaces": sys.maxsize,
                                     "force_json_field_order": False,
                                     "alphabet_from_tokenizer": True},
        "char_rows": char_rows, "token_rows": token_rows,
        "callback_checks": callback_checks,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
    char_mismatch = [r["name"] for r in char_rows if r["source_accepts"] != r["backend_accepts"]]
    print(json.dumps({"char_cases": len(char_rows), "char_mismatches": char_mismatch,
                      "token_rows": [{"name": r["name"], "verified": r["generation_prefix_verified"],
                                      "total": r["input_tokens"], "disallowed": r["first_disallowed_token_index"],
                                      "eos_at_end": r["eos_at_end"]} for r in token_rows],
                      "callback_checks": callback_checks}))


if __name__ == "__main__":
    main()
