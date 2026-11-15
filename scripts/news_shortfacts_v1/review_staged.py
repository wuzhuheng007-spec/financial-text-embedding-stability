"""Reject staged short-fact passages, raw Qwen outputs, weights or credentials."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PRIVATE = ROOT / "artifacts/news_shortfacts_20261115"
SECRET_PATTERNS = (
    re.compile(rb"hf_[A-Za-z0-9]{20,}"),
    re.compile(rb"Bearer\s+[A-Za-z0-9._-]{24,}", re.IGNORECASE),
)
FORBIDDEN_SUFFIXES = {".sqlite", ".db", ".safetensors", ".bin", ".pt", ".pth"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to((ROOT / "results/news_shortfacts_20261115").resolve()):
        parser.error("Receipt must stay under the new public results directory")
    if output.exists():
        raise FileExistsError(output)
    result = subprocess.run(["git", "diff", "--cached", "--name-only", "--diff-filter=ACM", "-z"],
                            cwd=ROOT, check=True, capture_output=True)
    names = [name.decode("utf-8") for name in result.stdout.split(b"\0") if name]
    if not names:
        raise ValueError("No reviewed staged files")
    passages = [p.read_bytes().strip() for p in sorted((PRIVATE / "inputs").glob("[abc][12].txt"))]
    responses = [p.read_bytes().strip() for p in sorted((PRIVATE / "run1/raw_responses").glob("*.txt"))]
    if len(passages) != 6 or len(responses) != 6:
        raise ValueError("Expected frozen six passages and six raw responses")
    private_paths = credential_hits = passage_matches = response_matches = 0
    for name in names:
        relative = Path(name)
        if (name.startswith(("artifacts/", "local_only/")) or "local_only" in relative.parts or
                relative.suffix.lower() in FORBIDDEN_SUFFIXES):
            private_paths += 1
        blob = subprocess.run(["git", "show", f":{name}"], cwd=ROOT,
                              capture_output=True, check=True).stdout
        credential_hits += sum(len(pattern.findall(blob)) for pattern in SECRET_PATTERNS)
        passage_matches += sum(bool(passage and passage in blob) for passage in passages)
        response_matches += sum(bool(response and response in blob) for response in responses)
    if any((private_paths, credential_hits, passage_matches, response_matches)):
        raise AssertionError("Staged privacy boundary failed; inspect private files locally")
    receipt = {"schema": "shortfacts_staged_privacy_receipt_v1", "staged_files_scanned": len(names),
               "private_passages_compared": len(passages), "raw_responses_compared": len(responses),
               "private_or_weight_paths_staged": 0, "credential_pattern_hits": 0,
               "full_passage_matches": 0, "full_raw_response_matches": 0, "result": "PASS"}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n",
                      encoding="utf-8", newline="\n")
    print(json.dumps(receipt, ensure_ascii=False))


if __name__ == "__main__":
    main()
