"""Model-free character-level compatibility check for a pinned LMFE candidate.

This file never loads model weights or changes the frozen wire validator.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import warnings
from pathlib import Path

from jsonschema import Draft202012Validator
from lmformatenforcer import JsonSchemaParser
from lmformatenforcer.characterlevelparser import CharacterLevelParserConfig


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def encode_case(obj: dict, mode: str) -> str:
    if mode == "compact":
        return json.dumps(obj, ensure_ascii=True, separators=(",", ":"))
    if mode == "raw_unicode":
        return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    if mode == "spaced":
        return json.dumps(obj, ensure_ascii=True)
    if mode == "pretty_long_indent":
        return json.dumps(obj, ensure_ascii=True, indent=14)
    raise ValueError(f"unknown synthetic serialization mode: {mode}")


def replay(schema: dict, obj: dict, config: CharacterLevelParserConfig,
           mode: str = "compact") -> dict:
    encoded = encode_case(obj, mode)
    start = time.monotonic()
    with warnings.catch_warnings(record=True) as seen:
        warnings.simplefilter("always")
        parser = JsonSchemaParser(schema, config=config)
        for index, char in enumerate(encoded):
            allowed = parser.get_allowed_characters()
            if not allowed:
                return {"backend_accepts": False, "rejection_index": index,
                        "rejection_kind": "EMPTY_ALLOWED_CHARACTERS",
                        "duration_seconds": round(time.monotonic() - start, 3),
                        "warnings": [str(w.message) for w in seen]}
            if char not in allowed:
                return {"backend_accepts": False, "rejection_index": index,
                        "rejection_kind": "CHARACTER_NOT_ALLOWED",
                        "duration_seconds": round(time.monotonic() - start, 3),
                        "warnings": [str(w.message) for w in seen]}
            parser = parser.add_character(char)
        return {"backend_accepts": bool(parser.can_end()),
                "rejection_index": None,
                "rejection_kind": None if parser.can_end() else "CANNOT_END",
                "duration_seconds": round(time.monotonic() - start, 3),
                "warnings": [str(w.message) for w in seen]}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--schema", type=Path, required=True)
    ap.add_argument("--source-schema", type=Path, required=True)
    ap.add_argument("--sentinels", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--case", action="append")
    ap.add_argument("--config-mode", choices=("default", "no_implicit_caps"),
                    default="default")
    args = ap.parse_args()
    if args.out.exists():
        raise FileExistsError(args.out)
    schema = json.loads(args.schema.read_text(encoding="utf-8"))
    source = json.loads(args.source_schema.read_text(encoding="utf-8"))
    sentinels = json.loads(args.sentinels.read_text(encoding="utf-8"))
    validator = Draft202012Validator(source)
    config = (CharacterLevelParserConfig() if args.config_mode == "default" else
              CharacterLevelParserConfig(max_json_array_length=0,
                                         max_consecutive_whitespaces=sys.maxsize))
    rows = []
    for item in sentinels:
        if args.case and item["name"] not in args.case:
            continue
        source_accepts = validator.is_valid(item["object"])
        mode = item.get("serialization", "compact")
        result = replay(schema, item["object"], config, mode)
        rows.append({"name": item["name"], "expected_valid": item["expected_valid"],
                     "source_accepts": source_accepts, "serialization": mode, **result})
    public = {"schema": "lmfe_char_gate_probe_v1", "model_calls": 0,
              "compiled_schema_sha256": sha256(args.schema),
              "source_schema_sha256": sha256(args.source_schema),
              "sentinels_sha256": sha256(args.sentinels),
              "config_mode": args.config_mode,
              "backend_config": {"max_json_array_length": config.max_json_array_length,
                                 "max_consecutive_whitespaces": config.max_consecutive_whitespaces,
                                 "force_json_field_order": config.force_json_field_order,
                                 "alphabet_length": len(config.alphabet)},
              "rows": rows}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(public, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"cases": len(rows), "mismatches": [row["name"] for row in rows
                                      if row["source_accepts"] != row["backend_accepts"]],
                      "warnings": sum(bool(row["warnings"]) for row in rows)}))


if __name__ == "__main__":
    main()
