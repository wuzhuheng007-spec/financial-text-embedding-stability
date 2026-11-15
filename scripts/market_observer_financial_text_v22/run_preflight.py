"""Freeze v2.2 code and saved training inputs before the one real content run.

This tool reads only saved NVDA/MU training-period source files selected from
the manifest. It does not open validation/test bodies, return labels, model
weights, accounts, or a network connection. Run after code/tests are frozen.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
from pathlib import Path
import platform
import subprocess
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "configs/market_observer_financial_text_v22_20261116.json"
MANIFEST = ROOT / "artifacts/market_observer_official_20261115/corpus_manifest.json"
SOURCE_ROOT = (ROOT / "artifacts/market_observer_official_20261115").resolve()
QUANT_PROBE_ROOT = (ROOT.parent / "quant/artifacts/historical_news_probe_20261115").resolve()
V21_PRIVATE = ROOT / "artifacts/market_observer_financial_text_v21_20261115"
V21_PUBLIC = ROOT / "results/market_observer_financial_text_v21_20261115"
V2_PRIVATE = ROOT / "artifacts/market_observer_financial_text_v2_20261115"
V2_PUBLIC = ROOT / "results/market_observer_financial_text_v2_20261115"
OUT_PRIVATE = ROOT / "artifacts/market_observer_financial_text_v22_20261116/preflight_r3.private.json"
OUT_PUBLIC = ROOT / "results/market_observer_financial_text_v22_20261116/preflight_r3_redacted.json"
EXPECTED_BASELINE = "cb5ced8d206daba1eb99c018151f1cbbd7ba8e27"
EXPECTED_BRANCH = "codex/sync-research-progress-20261113"
FROZEN_V2_RETENTION_SHA256 = "2cdb00d715447b1850b1db6473308376943e21d05a2abbb1e5bcc62211681e0f"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def git(*args: str) -> bytes:
    return subprocess.check_output(["git", *args], cwd=ROOT)


def checked_json(path: Path) -> dict:
    if not path.is_file():
        raise FileNotFoundError(path)
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"Expected JSON object: {path}")
    return value


def checked_source_path(value: str, *, raw: bool) -> Path:
    path = Path(value).resolve()
    allowed = (SOURCE_ROOT, QUANT_PROBE_ROOT) if raw else (SOURCE_ROOT,)
    if not any(path.is_relative_to(root) for root in allowed) or not path.is_file():
        raise RuntimeError("A selected saved source is absent or outside the official-source cache")
    return path


def selected_training_sources(manifest: dict, scope: dict) -> list[dict]:
    allowed_symbols = set(scope["symbols"])
    start, end = scope["announcement_date_start"], scope["announcement_date_end"]
    if allowed_symbols != {"NVDA", "MU"} or (start, end) != ("2025-04-01", "2026-03-31"):
        raise RuntimeError("Training source scope differs from frozen v2.2 plan")
    docs = manifest.get("documents")
    if not isinstance(docs, list):
        raise RuntimeError("Missing saved source-manifest documents")
    exhibits = [doc for doc in docs if doc.get("source_kind") == "ISSUER_EARNINGS"
                and doc.get("symbol") in allowed_symbols and start <= doc.get("announcement_date", "") <= end]
    if len(exhibits) != scope["expected_earnings_exhibits"]:
        raise RuntimeError("Earnings exhibit count differs from frozen plan")
    groups = {doc["event_group_id"] for doc in exhibits}
    if len(groups) != scope["expected_events"]:
        raise RuntimeError("Earnings accession/event grouping differs from frozen plan")
    filings = [doc for doc in docs if doc.get("source_kind") == "SEC_FILINGS"
               and doc.get("event_group_id") in groups and doc.get("symbol") in allowed_symbols
               and start <= doc.get("announcement_date", "") <= end]
    if len(filings) != scope["expected_matched_primary_filings"] or \
            {doc["event_group_id"] for doc in filings} != groups:
        raise RuntimeError("Matched primary filing count or grouping differs from frozen plan")
    selected = sorted(exhibits + filings,
                      key=lambda d: (d["announcement_date"], d["symbol"], d["event_group_id"],
                                     0 if d["source_kind"] == "ISSUER_EARNINGS" else 1, d["document_id"]))
    if len({doc["document_id"] for doc in selected}) != len(selected):
        raise RuntimeError("Duplicate selected training document ID")
    return selected


def input_paths() -> list[Path]:
    """Enumerate actual code and reference inputs; v2.2 files must exist now."""
    required = [
        CONFIG, MANIFEST,
        ROOT / "configs/market_observer_financial_text_v2.json",
        ROOT / "configs/market_observer_financial_text_v21.json",
        ROOT / "configs/market_observer_official_text_v1.json",
        ROOT / "configs/market_observer_financial_text_v21_CHANGE.md",
        ROOT / "scripts/market_observer_official_v1/text_comparison.py",
        ROOT / "scripts/market_observer_historical_v1/direction_baseline.py",
        ROOT / "scripts/market_observer_historical_v1/price_baseline.py",
        ROOT / "results/market_observer_official_repro_20261115/text_input_audit_redacted.json",
        ROOT / "artifacts/market_observer_official_text_audit_20261115/aggregate_rows.private.jsonl",
        V21_PRIVATE / "blocks.private.jsonl",
        V21_PRIVATE / "event_inputs.private.jsonl",
        V21_PRIVATE / "documents.private.json",
        V21_PRIVATE / "retention_comparison.private.json",
        V21_PUBLIC / "build_redacted.json",
        V21_PUBLIC / "retention_redacted.json",
        V2_PUBLIC / "retention_redacted.json",
        V2_PRIVATE / "fact_cards/content_checklist_reviewed.private.json",
        V2_PRIVATE / "fact_cards/fact_candidates_v2.private.json",
        V2_PRIVATE / "fact_cards/fact_review_v2.private.json",
        V2_PRIVATE / "fact_cards/fact_review_v4.private.json",
    ]
    required += sorted((ROOT / "scripts/market_observer_financial_text_v2").glob("*.py"))
    required += sorted((ROOT / "scripts/market_observer_financial_text_v21").glob("*.py"))
    v22_code = sorted((ROOT / "scripts/market_observer_financial_text_v22").glob("*.py"))
    required += v22_code
    tests = sorted((ROOT / "tests").glob("test_market_observer_financial_text_v22*.py"))
    required += tests
    if not {"adapter.py", "build.py", "run_preflight.py"}.issubset({p.name for p in v22_code}):
        raise RuntimeError("v2.2 builder, adapter or this preflight code is missing")
    if not tests:
        raise RuntimeError("v2.2 synthetic test code must be frozen before preflight")
    missing = [p for p in required if not p.is_file()]
    if missing:
        raise FileNotFoundError(missing[0])
    if len(set(required)) != len(required):
        raise RuntimeError("Duplicate code/input inventory path")
    return required


def verify_saved_versions(hashes: dict[str, str]) -> tuple[dict, dict]:
    prior = checked_json(V21_PUBLIC / "retention_redacted.json")
    built = checked_json(V21_PUBLIC / "build_redacted.json")
    prior_inputs = prior["input_sha256"]
    expected = {
        "manifest": MANIFEST,
        "old_config": ROOT / "configs/market_observer_official_text_v1.json",
        "prior_text_audit": ROOT / "results/market_observer_official_repro_20261115/text_input_audit_redacted.json",
        "frozen_reviewed_checklist": V2_PRIVATE / "fact_cards/content_checklist_reviewed.private.json",
        "fact_review_v4": V2_PRIVATE / "fact_cards/fact_review_v4.private.json",
        "fact_review_v2": V2_PRIVATE / "fact_cards/fact_review_v2.private.json",
        "old_aggregate_metadata_only": ROOT / "artifacts/market_observer_official_text_audit_20261115/aggregate_rows.private.jsonl",
        "fact_candidates_v2": V2_PRIVATE / "fact_cards/fact_candidates_v2.private.json",
        "v21_blocks": V21_PRIVATE / "blocks.private.jsonl",
        "v21_event_inputs": V21_PRIVATE / "event_inputs.private.jsonl",
        "v21_documents": V21_PRIVATE / "documents.private.json",
        "v21_build": V21_PUBLIC / "build_redacted.json",
    }
    for key, path in expected.items():
        actual = hashes[path.relative_to(ROOT).as_posix()]
        if actual != prior_inputs[key]:
            raise RuntimeError(f"Saved v2.1 comparison input version changed: {key}")
    if hashes[V2_PUBLIC.joinpath("retention_redacted.json").relative_to(ROOT).as_posix()] != FROZEN_V2_RETENTION_SHA256:
        raise RuntimeError("Frozen v2 retention receipt changed")
    if built["source_manifest_sha256"] != hashes[MANIFEST.relative_to(ROOT).as_posix()] or \
            built["config_sha256"] != hashes[(ROOT / "configs/market_observer_financial_text_v21.json").relative_to(ROOT).as_posix()]:
        raise RuntimeError("Saved v2.1 build has mismatched manifest/config")
    if any(built["private_output_hashes"][name] != hashes[(V21_PRIVATE / path).relative_to(ROOT).as_posix()]
           for name, path in (("blocks", "blocks.private.jsonl"), ("event_inputs", "event_inputs.private.jsonl"),
                              ("documents", "documents.private.json"))):
        raise RuntimeError("Saved v2.1 output hash differs from build receipt")
    if prior["denominators"]["reviewed_candidate_facts"] != 71:
        raise RuntimeError("Frozen reference denominator changed")
    return prior, built


def run() -> dict:
    if OUT_PRIVATE.exists() or OUT_PUBLIC.exists():
        raise FileExistsError("Refusing to overwrite a v2.2 preflight receipt")
    config = checked_json(CONFIG)
    if config["schema"] != "market_observer_financial_text_v22_plan_v1" or \
            config["branch"] != EXPECTED_BRANCH or config["baseline_commit"] != EXPECTED_BASELINE or \
            config["input_adapter"]["max_characters_per_event"] != 20000 or \
            config["input_adapter"]["model_fit_calls"] != 0 or \
            config["input_adapter"]["model_inference_calls"] != 0:
        raise RuntimeError("v2.2 frozen configuration contract changed")
    if git("branch", "--show-current").decode().strip() != EXPECTED_BRANCH or \
            subprocess.run(["git", "merge-base", "--is-ancestor", EXPECTED_BASELINE, "HEAD"],
                           cwd=ROOT, check=False).returncode != 0:
        raise RuntimeError("Current branch no longer descends from the known baseline")
    manifest = checked_json(MANIFEST)
    docs = selected_training_sources(manifest, config["source"])
    code_input_hashes = {p.relative_to(ROOT).as_posix(): sha(p.read_bytes()) for p in input_paths()}
    prior, built = verify_saved_versions(code_input_hashes)
    sources = []
    for doc in docs:
        if doc.get("full_body_status") != "FULL_BODY" or doc.get("version_status") != "SEC_ARCHIVE_HASHED":
            raise RuntimeError("Selected source is not a saved full archived body")
        raw = checked_source_path(doc["raw_body_path"], raw=True)
        body = checked_source_path(doc["body_path"], raw=False)
        raw_sha, body_sha = sha(raw.read_bytes()), sha(body.read_bytes())
        if raw_sha != doc["raw_body_sha256"] or body_sha != doc["body_sha256"]:
            raise RuntimeError("Saved training source byte hash changed")
        sources.append({"document_id": doc["document_id"], "event_group_id": doc["event_group_id"],
                        "announcement_date": doc["announcement_date"], "symbol": doc["symbol"],
                        "source_kind": doc["source_kind"], "raw_body_sha256": raw_sha,
                        "extracted_body_sha256": body_sha,
                        "private_raw_path": str(raw), "private_extracted_path": str(body)})
    if len(sources) != 16 or len({s["event_group_id"] for s in sources}) != 8:
        raise RuntimeError("Training source count changed")
    review = checked_json(V2_PRIVATE / "fact_cards/fact_review_v4.private.json")
    candidate = checked_json(V2_PRIVATE / "fact_cards/fact_candidates_v2.private.json")
    if len(review["reviews"]) != 71 or len(candidate["candidates"]) != 71:
        raise RuntimeError("Candidate/review reference count changed")
    status_counts = Counter(row["status"] for row in review["reviews"])
    if status_counts != {"CODEX_REVIEW_SOURCE_SUPPORTED": 60, "CODEX_REVIEW_AMBIGUOUS": 11}:
        raise RuntimeError(f"Frozen reference source-status counts changed: {dict(status_counts)}")
    now = datetime.now(timezone.utc)
    status = git("status", "--porcelain=v1")
    common = {
        "schema": "financial_text_v22_preflight_v1",
        "captured_at_utc": now.isoformat(),
        "captured_at_asia_shanghai": now.astimezone(ZoneInfo("Asia/Shanghai")).isoformat(),
        "git_head": git("rev-parse", "HEAD").decode().strip(),
        "branch": EXPECTED_BRANCH,
        "tracked_diff_sha256": sha(git("diff", "--binary", "HEAD")),
        "staged_diff_sha256": sha(git("diff", "--cached", "--binary")),
        "workspace_status_sha256": sha(status),
        "workspace_status_entry_count": len(status.splitlines()),
        "code_config_and_reference_sha256": code_input_hashes,
        "source_document_count": len(sources),
        "earnings_exhibits": sum(s["source_kind"] == "ISSUER_EARNINGS" for s in sources),
        "matched_primary_filings": sum(s["source_kind"] == "SEC_FILINGS" for s in sources),
        "event_groups": len({s["event_group_id"] for s in sources}),
        "reference_candidates": len(candidate["candidates"]),
        "same_team_source_review_status_counts": dict(status_counts),
        "frozen_v21_strict_selected_complete": prior["reviewed_fact_retention"]["v21_selected"].get("COMPLETE", 0),
        "frozen_v21_build_block_count": built["block_count"],
        "runtime": {"python": platform.python_version(),
                    "beautifulsoup4": importlib.metadata.version("beautifulsoup4"),
                    "numpy": importlib.metadata.version("numpy")},
        "new_downloads": 0,
        "april_july_bodies_or_labels_read": False,
        "model_fit_calls": 0,
        "model_inference_calls": 0,
    }
    private = {**common, "source_documents": sources}
    public = {**common, "source_documents": [{k: v for k, v in item.items() if not k.startswith("private_")}
                                             for item in sources]}
    private_bytes = (json.dumps(private, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    public["private_preflight_sha256"] = sha(private_bytes)
    public_bytes = (json.dumps(public, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    OUT_PRIVATE.parent.mkdir(parents=True, exist_ok=True)
    OUT_PUBLIC.parent.mkdir(parents=True, exist_ok=True)
    with OUT_PRIVATE.open("xb") as fh:
        fh.write(private_bytes)
    with OUT_PUBLIC.open("xb") as fh:
        fh.write(public_bytes)
    return {"private_path": str(OUT_PRIVATE.relative_to(ROOT)),
            "public_path": str(OUT_PUBLIC.relative_to(ROOT)),
            "public_sha256": sha(public_bytes), "source_documents": len(sources),
            "reference_candidates": len(candidate["candidates"])}


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False))
