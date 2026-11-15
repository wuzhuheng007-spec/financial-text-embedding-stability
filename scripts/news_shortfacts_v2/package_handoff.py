"""Build a small, public-only shortfacts v2 handoff folder and ZIP.

Only the exact files in ALLOWLIST can enter the package. The script refuses
existing output rather than replacing a previous handoff.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
RESULT = "results/news_shortfacts_v2_20261115"
ALLOWLIST = (
    "reports/2026-11-15_news_shortfacts_v2.en.md",
    "reports/2026-11-15_news_shortfacts_v2.zh.md",
    "docs/news_research/README.md",
    "docs/news_research/STATUS.json",
    "docs/news_research/CHANGELOG.md",
    "scripts/news_shortfacts_v2/prompt.txt",
    "scripts/news_shortfacts_v2/output.schema.json",
    "scripts/news_shortfacts_v2/run.py",
    "scripts/news_shortfacts_v2/evaluate.py",
    "scripts/news_shortfacts_v2/public_receipts.py",
    "scripts/news_shortfacts_v2/package_handoff.py",
    "tests/test_news_shortfacts_v2_runner.py",
    "tests/test_news_shortfacts_v2_evaluate.py",
    "tests/test_news_shortfacts_v2_public_receipts.py",
    "tests/test_news_shortfacts_v2_handoff.py",
    f"{RESULT}/baseline_readback.json",
    f"{RESULT}/contract_receipt.json",
    f"{RESULT}/offline_diagnosis_receipt.json",
    f"{RESULT}/retrospective_summary_redacted.json",
    f"{RESULT}/pre_run_freeze_receipt.json",
    f"{RESULT}/run_receipt_redacted.json",
    f"{RESULT}/new_run_summary_redacted.json",
    f"{RESULT}/comparison_redacted.json",
    f"{RESULT}/focused_tests.log",
    f"{RESULT}/public_receipts_tests_final.log",
    f"{RESULT}/handoff_tests_final.log",
    f"{RESULT}/docs_validation.log",
    f"{RESULT}/plan_execution.csv",
)
FORBIDDEN_PATH_PARTS = {"artifacts", "local_only", "raw_responses", "model_cache", "venv"}
SECRET_PATTERNS = (
    re.compile(r"hf_[A-Za-z0-9]{20,}"),
    re.compile(r"(?i)authorization\s*:\s*bearer\s+[^\s<>{}]+"),
    re.compile(r"(?i)(?:password|secret|api[_-]?key|token)\s*[=:]\s*['\"]?[A-Za-z0-9_\-]{20,}"),
    re.compile(r"(?i)C:\\Users\\[^\\\s]+\\"),
)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def source_files(repo: Path) -> list[tuple[str, Path]]:
    found = []
    for relative in ALLOWLIST:
        rel = Path(relative)
        if (rel.is_absolute() or ".." in rel.parts or
                FORBIDDEN_PATH_PARTS.intersection(part.casefold() for part in rel.parts)):
            raise ValueError(f"Disallowed handoff path: {relative}")
        path = repo / rel
        if not path.is_file() or not path.resolve().is_relative_to(repo.resolve()):
            raise FileNotFoundError(f"Missing or external allowlisted file: {relative}")
        found.append((relative, path))
    if len({relative for relative, _ in found}) != len(found):
        raise ValueError("Duplicate allowlist path")
    return found


def private_fragments(repo: Path) -> list[str]:
    """Full private passages and full old/new responses must never be copied."""
    roots = [repo / "artifacts/news_shortfacts_20261115/inputs",
             repo / "artifacts/news_shortfacts_20261115/run1/raw_responses",
             repo / "artifacts/news_shortfacts_v2_20261115/run1/raw_responses"]
    fragments = []
    for directory in roots:
        if directory.is_dir():
            for path in directory.glob("*.txt"):
                value = path.read_text(encoding="utf-8").strip()
                if len(value) >= 80:
                    fragments.append(value)
                fragments.extend(line.strip() for line in value.splitlines()
                                 if len(line.strip()) >= 80)
    return fragments


def screen_text(relative: str, data: bytes, fragments: list[str]) -> None:
    try:
        content = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"Non-UTF-8 file in text-only handoff: {relative}") from exc
    for pattern in SECRET_PATTERNS:
        if pattern.search(content):
            raise ValueError(f"Potential credential or private absolute path in {relative}")
    if any(fragment in content for fragment in fragments):
        raise ValueError(f"Full private passage or response found in {relative}")


def readme(payload: dict[str, bytes]) -> bytes:
    new = json.loads(payload[f"{RESULT}/new_run_summary_redacted.json"])
    old = json.loads(payload[f"{RESULT}/retrospective_summary_redacted.json"])
    run = json.loads(payload[f"{RESULT}/run_receipt_redacted.json"])
    n, o = new["counts"], old["counts"]
    status = run.get("run_status", run.get("status", "SEE_RUN_RECEIPT"))
    body = f"""# Shortfacts v2 development handoff

Execution date: 2026-11-15, Asia/Shanghai. This is a sanitized copy of a
six-passage, pinned-model development retest, not an independent gold set or
evidence of general extraction or financial validity.

## Read in order

1. `reports/2026-11-15_news_shortfacts_v2.en.md` or `.zh.md` for findings and limits.
2. `{RESULT}/comparison_redacted.json` for same-rule old/new comparison.
3. `{RESULT}/plan_execution.csv` for plan versus actual work.
4. `{RESULT}/contract_receipt.json` and `pre_run_freeze_receipt.json` for
   input, prompt, schema, reference, evaluator, and model identities.
5. `{RESULT}/retrospective_summary_redacted.json`,
   `new_run_summary_redacted.json`, and `run_receipt_redacted.json` for
   disaggregated format, field, and generation outcomes.
6. `scripts/news_shortfacts_v2/` and `tests/test_news_shortfacts_v2_*.py`
   for the reproducible software contract and focused synthetic tests.

## What actually ran

- New run status: `{status}`; attempted {n['attempted_tasks']}/6 tasks and saved
  {n['completed_response_tasks']}/6 responses, with no automatic retries.
- Old responses under corrected reference: {o['strict_raw_json_valid_tasks']}/6
  strict raw JSON; {o['fence_adapted_tasks']}/6 exact-fence adaptations;
  {o['fully_correct_facts']}/{o['reference_facts']} fully correct development facts.
- New responses under the same corrected reference:
  {n['strict_raw_json_valid_tasks']}/6 strict raw JSON;
  {n['fence_adapted_tasks']}/6 exact-fence adaptations;
  {n['fully_correct_facts']}/{n['reference_facts']} fully correct development facts.
- Historical financial admission, returns, prediction and backtesting remain
  `NOT_RUN`. The same executor prepared the reference and content review.

Only code, public source links, redacted aggregate receipts, and reports are
included. Complete official passages, raw model responses, private reference
facts and review decisions, weights, databases, and credentials remain local.
`MANIFEST.json` lists the SHA-256 and size of every packaged file; the builder
also reopens the ZIP to check CRC and each member hash.
"""
    return body.encode("utf-8")


def build(repo: Path, output_parent: Path) -> dict:
    files = source_files(repo)
    fragments = private_fragments(repo)
    payload = [(relative, path.read_bytes()) for relative, path in files]
    snapshot = dict(payload)
    for relative, data in payload:
        screen_text(relative, data, fragments)
    handoff_readme = readme(snapshot)
    screen_text("README.md", handoff_readme, fragments)
    for relative, path in files:
        if path.read_bytes() != snapshot[relative]:
            raise ValueError(f"Source changed during packaging check: {relative}")
    folder = output_parent / "news_shortfacts_v2_20261115"
    archive = output_parent / "news_shortfacts_v2_20261115.zip"
    validation = output_parent / "news_shortfacts_v2_20261115_validation.json"
    if any(path.exists() for path in (folder, archive, validation)):
        raise FileExistsError("Handoff already exists; previous result will not be replaced")
    output_parent.mkdir(parents=True, exist_ok=True)
    folder.mkdir()
    payload.append(("README.md", handoff_readme))
    manifest = {"schema": "shortfacts_v2_handoff_manifest_v1",
                "created_at_utc": datetime.now(timezone.utc).isoformat(),
                "execution_date_timezone": "2026-11-15 Asia/Shanghai",
                "source_git_branch": "codex/sync-research-progress-20261113",
                "files": [{"path": name, "sha256": sha256(data), "bytes": len(data)}
                          for name, data in payload]}
    manifest_bytes = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    payload.append(("MANIFEST.json", manifest_bytes))
    for relative, data in payload:
        target = folder / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED,
                         compresslevel=6) as zf:
        for relative, data in payload:
            zf.writestr(relative, data)
    with zipfile.ZipFile(archive, "r") as zf:
        if zf.testzip() is not None:
            raise ValueError("ZIP CRC failure")
        if set(zf.namelist()) != {name for name, _ in payload}:
            raise ValueError("ZIP member set differs from allowlist")
        for relative, data in payload:
            if sha256(zf.read(relative)) != sha256(data):
                raise ValueError(f"ZIP hash mismatch: {relative}")
    result = {"schema": "shortfacts_v2_handoff_validation_v1",
              "folder": str(folder), "zip": str(archive),
              "zip_sha256": sha256(archive.read_bytes()),
              "zip_crc": "PASS", "member_hashes": "PASS",
              "allowlist_member_count": len(files),
              "zip_member_count": len(payload),
              "full_private_passage_response_scan": "PASS",
              "credential_and_private_path_scan": "PASS",
              "manifest_sha256": sha256(manifest_bytes)}
    validation.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                          encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=REPO)
    parser.add_argument("--output-parent", type=Path,
                        default=REPO.parent / "quant/handoffs")
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    files = source_files(args.repo)
    fragments = private_fragments(args.repo)
    for relative, path in files:
        screen_text(relative, path.read_bytes(), fragments)
    if args.check_only:
        print(json.dumps({"status": "CHECK_ONLY_PASS", "allowlist_files": len(files)}))
    else:
        print(json.dumps(build(args.repo, args.output_parent), ensure_ascii=False))


if __name__ == "__main__":
    main()
