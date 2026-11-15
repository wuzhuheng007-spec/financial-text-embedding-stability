"""Freeze the exact v2.3 training-source, code and evaluator inputs before a full run.

Only the 16 saved NVDA/MU training documents are opened. The shared corpus
manifest is filtered before body paths are resolved; validation/test bodies and
return labels are never read. The result is a receipt, not a source review.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
from pathlib import Path
import platform
import re
import subprocess
from zoneinfo import ZoneInfo

from scripts.market_observer_financial_text_v22.run_preflight import (
    checked_json,
    checked_source_path,
    selected_training_sources,
)


ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "configs/market_observer_financial_text_v23_20261116.json"
MANIFEST = ROOT / "artifacts/market_observer_official_20261115/corpus_manifest.json"
PRIOR_PREFLIGHT = ROOT / "results/market_observer_financial_text_v22_20261116/preflight_r3_redacted.json"
PRIOR_RESULT_DIR = ROOT / "results/market_observer_financial_text_v22_20261116"
PRIOR_EVENT_INPUTS = ROOT / "artifacts/market_observer_financial_text_v22_20261116/event_inputs.private.jsonl"
PRIVATE_DIR = ROOT / "artifacts/market_observer_financial_text_v23_20261116"
PUBLIC_DIR = ROOT / "results/market_observer_financial_text_v23_20261116"
BASELINE = "77d86b300f92ed32df1149175e4c791568cf9dc6"
BRANCH = "codex/sync-research-progress-20261113"
OLD_IMMUTABLE = (
    "configs/market_observer_financial_text_v22_20261116.json",
    "scripts/market_observer_financial_text_v22/adapter.py",
    "scripts/market_observer_financial_text_v22/build.py",
    "scripts/market_observer_financial_text_v22/compare_retention.py",
    "scripts/market_observer_financial_text_v22/diagnose_postrun.py",
    "results/market_observer_financial_text_v22_20261116/build_redacted.json",
    "results/market_observer_financial_text_v22_20261116/retention_redacted.json",
    "results/market_observer_financial_text_v22_20261116/postrun_loss_diagnosis_redacted.json",
    "results/market_observer_financial_text_v22_20261116/loss_stage_redacted.json",
)
REQUIRED_NEW_MODULES = {"structure.py", "adapter.py", "evaluate.py", "build.py", "run_preflight.py"}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def git(*args: str) -> bytes:
    return subprocess.check_output(["git", *args], cwd=ROOT)


def tagged_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def checked_hash_inventory(config: dict, prior: dict) -> dict[str, str]:
    prior_hashes = prior["code_config_and_reference_sha256"]
    # The prior receipt is a closed 51-file inventory. v2.2 later gained a
    # read-only diagnosis script, so a fresh glob would add a file that was
    # correctly absent when the prior run was frozen.
    actual_prior = {}
    for rel in prior_hashes:
        path = (ROOT / rel).resolve()
        if not path.is_relative_to(ROOT.resolve()) or not path.is_file():
            raise RuntimeError(f"Frozen v2.2 input missing/outside repository: {rel}")
        actual_prior[rel] = sha(path.read_bytes())
    if actual_prior != prior_hashes:
        missing = sorted(set(prior_hashes) - set(actual_prior))
        changed = sorted(k for k in set(prior_hashes) & set(actual_prior) if prior_hashes[k] != actual_prior[k])
        raise RuntimeError(f"Frozen v2.2 input inventory differs: missing={missing}, changed={changed}")
    for rel in OLD_IMMUTABLE:
        path = ROOT / rel
        if not path.is_file() or path.read_bytes() != git("show", f"{BASELINE}:{rel}"):
            raise RuntimeError(f"Historical v2.2 file differs from the baseline: {rel}")
    for rel in ("build_redacted.json", "retention_redacted.json", "postrun_loss_diagnosis_redacted.json"):
        if not (PRIOR_RESULT_DIR / rel).is_file():
            raise FileNotFoundError(PRIOR_RESULT_DIR / rel)
    if sha(PRIOR_EVENT_INPUTS.read_bytes()) != checked_json(PRIOR_RESULT_DIR / "build_redacted.json")["private_event_inputs_sha256"]:
        raise RuntimeError("Saved v2.2 model-input version differs")
    new_code_dir = ROOT / "scripts/market_observer_financial_text_v23"
    new_code = sorted(new_code_dir.glob("*.py"))
    if not REQUIRED_NEW_MODULES.issubset({p.name for p in new_code}):
        raise RuntimeError("v2.3 production/parser/evaluator/preflight code is not complete")
    tests = sorted((ROOT / "tests").glob("test_market_observer_financial_text_v23*.py"))
    if len(tests) < 2:
        raise RuntimeError("Focused v2.3 structure and evaluator tests are missing")
    extra = [(ROOT / rel).resolve() for rel in config.get("runtime_dependency_paths", [])]
    if any(not p.is_relative_to(ROOT.resolve()) or not p.is_file() for p in extra):
        raise RuntimeError("A declared v2.3 runtime dependency is missing/outside the repository")
    current = [CONFIG, PRIOR_PREFLIGHT, PRIOR_EVENT_INPUTS]
    current += [PRIOR_RESULT_DIR / p for p in ("build_redacted.json", "retention_redacted.json",
                                                "postrun_loss_diagnosis_redacted.json", "loss_stage_redacted.json")]
    current += new_code + tests + extra
    if len(current) != len(set(current)):
        raise RuntimeError("Duplicate v2.3 input/code inventory path")
    return {**actual_prior, **{p.relative_to(ROOT).as_posix(): sha(p.read_bytes()) for p in current}}


def source_receipts(config: dict, prior: dict) -> list[dict]:
    manifest = checked_json(MANIFEST)
    selected = selected_training_sources(manifest, config["source"])
    old_by_id = {doc["document_id"]: doc for doc in prior["source_documents"]}
    if {doc["document_id"] for doc in selected} != set(old_by_id):
        raise RuntimeError("The 16 training document IDs differ from frozen v2.2 preflight")
    receipts = []
    for doc in selected:
        if doc.get("full_body_status") != "FULL_BODY" or doc.get("version_status") != "SEC_ARCHIVE_HASHED":
            raise RuntimeError("Selected saved document is not archived full body")
        raw = checked_source_path(doc["raw_body_path"], raw=True)
        body = checked_source_path(doc["body_path"], raw=False)
        raw_sha, body_sha = sha(raw.read_bytes()), sha(body.read_bytes())
        frozen = old_by_id[doc["document_id"]]
        if raw_sha != frozen["raw_body_sha256"] or body_sha != frozen["extracted_body_sha256"]:
            raise RuntimeError("Saved training source hash changed")
        receipts.append({"document_id": doc["document_id"], "event_group_id": doc["event_group_id"],
                         "announcement_date": doc["announcement_date"], "symbol": doc["symbol"],
                         "source_kind": doc["source_kind"], "raw_body_sha256": raw_sha,
                         "extracted_body_sha256": body_sha, "private_raw_path": str(raw),
                         "private_extracted_path": str(body)})
    if len(receipts) != 16 or len({d["event_group_id"] for d in receipts}) != 8:
        raise RuntimeError("Frozen source cardinality differs")
    return receipts


def run(run_id: str) -> dict:
    if not re.fullmatch(r"[a-z][a-z0-9_]{1,31}", run_id):
        raise ValueError("Use a short lowercase run_id")
    private_path = PRIVATE_DIR / f"preflight_{run_id}.private.json"
    public_path = PUBLIC_DIR / f"preflight_{run_id}_redacted.json"
    if private_path.exists() or public_path.exists():
        raise FileExistsError("Preflight is append-only; choose a fresh run_id")
    config = checked_json(CONFIG)
    if config["schema"] != "market_observer_financial_text_v23_plan_v1" or \
       config["frozen_at_utc"] is None or config["branch"] != BRANCH or config["baseline_commit"] != BASELINE or \
       config["adapter"]["max_characters_per_event"] != 20000 or \
       config["adapter"]["model_fit_calls"] != 0 or config["adapter"]["model_inference_calls"] != 0 or \
       config["source"]["new_downloads"] != 0:
        raise RuntimeError("v2.3 configuration has not been frozen or differs from the requested scope")
    if git("branch", "--show-current").decode().strip() != BRANCH or \
       subprocess.run(["git", "merge-base", "--is-ancestor", BASELINE, "HEAD"], cwd=ROOT, check=False).returncode:
        raise RuntimeError("Current branch does not descend from the known baseline")
    prior = checked_json(PRIOR_PREFLIGHT)
    hashes = checked_hash_inventory(config, prior)
    sources = source_receipts(config, prior)
    now = datetime.now(timezone.utc)
    workspace_status = git("status", "--porcelain=v1")
    common = {
        "schema": "financial_text_v23_preflight_v1", "run_id": run_id,
        "captured_at_utc": now.isoformat(),
        "captured_at_asia_shanghai": now.astimezone(ZoneInfo("Asia/Shanghai")).isoformat(),
        "git_head": git("rev-parse", "HEAD").decode().strip(), "branch": BRANCH,
        "tracked_diff_sha256": sha(git("diff", "--binary", "HEAD")),
        "staged_diff_sha256": sha(git("diff", "--cached", "--binary")),
        "workspace_status_sha256": sha(workspace_status),
        "workspace_status_entry_count": len(workspace_status.splitlines()),
        "code_config_and_reference_sha256": hashes,
        "source_document_count": len(sources),
        "earnings_exhibits": sum(d["source_kind"] == "ISSUER_EARNINGS" for d in sources),
        "matched_primary_filings": sum(d["source_kind"] == "SEC_FILINGS" for d in sources),
        "event_groups": len({d["event_group_id"] for d in sources}),
        "reference_candidates": prior["reference_candidates"],
        "same_team_source_review_status_counts": prior["same_team_source_review_status_counts"],
        "runtime": {"python": platform.python_version(), "beautifulsoup4": tagged_version("beautifulsoup4"),
                    "lxml": tagged_version("lxml"), "numpy": tagged_version("numpy")},
        "new_downloads": 0, "april_july_bodies_or_labels_read": False,
        "model_fit_calls": 0, "model_inference_calls": 0,
    }
    private = {**common, "source_documents": sources}
    public = {**common, "source_documents": [{k: v for k, v in d.items() if not k.startswith("private_")}
                                             for d in sources]}
    private_bytes = (json.dumps(private, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    public["private_preflight_sha256"] = sha(private_bytes)
    public_bytes = (json.dumps(public, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    PRIVATE_DIR.mkdir(parents=True, exist_ok=True)
    PUBLIC_DIR.mkdir(parents=True, exist_ok=True)
    with private_path.open("xb") as out:
        out.write(private_bytes)
    with public_path.open("xb") as out:
        out.write(public_bytes)
    return {"run_id": run_id, "public_receipt": str(public_path.relative_to(ROOT)),
            "public_sha256": sha(public_bytes), "source_documents": len(sources),
            "code_config_reference_files": len(hashes)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    print(json.dumps(run(parser.parse_args().run_id), ensure_ascii=False))
