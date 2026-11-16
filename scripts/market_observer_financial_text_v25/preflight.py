"""Freeze the eight saved training events and every v2.5 runtime dependency.

The reference and reviewed candidates are hashed but never passed into the
production parser or packer.  Private source paths remain in ignored artifacts.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from hashlib import sha256
import importlib.metadata
import json
from pathlib import Path
import platform
import subprocess
from zoneinfo import ZoneInfo

from scripts.market_observer_financial_text_v21.build import select_training_documents


ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "configs/market_observer_financial_text_v25_20261116.json"
MANIFEST = ROOT / "artifacts/market_observer_official_20261115/corpus_manifest.json"
V21_CONFIG = ROOT / "configs/market_observer_financial_text_v21.json"
PRIVATE = ROOT / "artifacts/market_observer_financial_text_v25_20261116"
PUBLIC = ROOT / "results/market_observer_financial_text_v25_20261116"
BASELINE = "75133b055f4238eff5af3a4ed16115c8b717a01a"
BRANCH = "codex/sync-research-progress-20261113"
CANDIDATES = ROOT / "artifacts/market_observer_financial_text_v2_20261115/fact_cards/fact_candidates_v2.private.json"
REVIEWS = ROOT / "artifacts/market_observer_financial_text_v2_20261115/fact_cards/fact_review_v4.private.json"
OLD_RETENTION = ROOT / "artifacts/market_observer_financial_text_v2_20261115/retention_comparison.private.json"
V24_REFERENCE = ROOT / "artifacts/market_observer_financial_text_v24_20261116/expected_relations.private.json"
V25_REFERENCE = PRIVATE / "reference_frozen_v1.private.json"


def sha(raw: bytes) -> str:
    return sha256(raw).hexdigest()


def canonical(obj: object) -> bytes:
    return (json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def git(*args: str) -> bytes:
    return subprocess.check_output(["git", *args], cwd=ROOT)


def version(package: str) -> str | None:
    try:
        return importlib.metadata.version(package)
    except importlib.metadata.PackageNotFoundError:
        return None


def source_rows(config: dict) -> list[dict]:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    legacy = json.loads(V21_CONFIG.read_text(encoding="utf-8"))
    scope = config["source"]
    old_scope = legacy["source_scope"]
    for key in ("symbols", "announcement_date_start", "announcement_date_end"):
        if scope[key] != old_scope[key]:
            raise RuntimeError("v2.5 source scope diverges from saved training selection")
    docs = select_training_documents(manifest, legacy)
    if len(docs) != 16 or len({d["event_group_id"] for d in docs}) != 8:
        raise RuntimeError("saved training source cardinality changed")
    for d in docs:
        if d["source_kind"] not in {"ISSUER_EARNINGS", "SEC_FILINGS"}:
            raise RuntimeError("unexpected source kind")
        if not scope["announcement_date_start"] <= d["announcement_date"] <= scope["announcement_date_end"]:
            raise RuntimeError("training range breach")
    counts = Counter(d["source_kind"] for d in docs)
    if counts != {"ISSUER_EARNINGS": 8, "SEC_FILINGS": 8}:
        raise RuntimeError("saved exhibit/filing count changed")
    return docs


def dependency_hashes() -> dict[str, str]:
    fixed = [CONFIG, MANIFEST, V21_CONFIG, CANDIDATES, REVIEWS, OLD_RETENTION,
             V24_REFERENCE, V25_REFERENCE,
             ROOT / "results/market_observer_financial_text_v25_20261116/reference_freeze_redacted.json",
             ROOT / "scripts/market_observer_financial_text_v24/__init__.py",
             ROOT / "scripts/market_observer_financial_text_v24/semantic.py",
             ROOT / "scripts/market_observer_financial_text_v23/__init__.py",
             ROOT / "scripts/market_observer_financial_text_v23/structure.py",
             ROOT / "scripts/market_observer_financial_text_v21/__init__.py",
             ROOT / "scripts/market_observer_financial_text_v21/adapter.py",
             ROOT / "scripts/market_observer_financial_text_v21/parser.py",
             ROOT / "scripts/market_observer_financial_text_v21/build.py",
             ROOT / "results/market_observer_financial_text_v24_20261116/freeze_frozen_v1_redacted.json"]
    current = sorted((ROOT / "scripts/market_observer_financial_text_v25").glob("*.py"))
    tests = sorted((ROOT / "tests").glob("test_market_observer_financial_text_v25*.py"))
    if not {"preflight.py", "adapter.py", "evaluate.py", "run.py"}.issubset({p.name for p in current}):
        raise RuntimeError("v2.5 runtime code incomplete")
    if not tests:
        raise RuntimeError("v2.5 counterexample tests absent")
    paths = fixed + current + tests
    if len(paths) != len(set(paths)) or any(not p.is_file() for p in paths):
        raise RuntimeError("duplicate or missing freeze dependency")
    return {p.relative_to(ROOT).as_posix(): sha(p.read_bytes()) for p in paths}


def freeze() -> dict:
    private_path = PRIVATE / "preflight_frozen_v1.private.json"
    public_path = PUBLIC / "preflight_frozen_v1_redacted.json"
    if private_path.exists() or public_path.exists():
        raise FileExistsError("frozen v2.5 preflight already exists")
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    if (config["schema"] != "financial_text_v25_expansion_plan_v1"
            or config["status"] != "FROZEN_BEFORE_FULL_RUN"
            or not config["frozen_at_utc"]
            or config["adapter"]["max_unicode_characters_per_event"] != 20000
            or config["source"]["new_downloads"] != 0):
        raise RuntimeError("v2.5 plan not frozen as requested")
    from .adapter import TOPIC_PRIORITY
    if tuple(config["selection"]["topic_order"]) != TOPIC_PRIORITY:
        raise RuntimeError("frozen topic selection differs from production adapter")
    prior_freeze = json.loads((ROOT / "results/market_observer_financial_text_v24_20261116/freeze_frozen_v1_redacted.json")
                              .read_text(encoding="utf-8"))
    for rel in ("scripts/market_observer_financial_text_v24/semantic.py",
                "scripts/market_observer_financial_text_v23/structure.py",
                "scripts/market_observer_financial_text_v21/parser.py",
                "artifacts/market_observer_official_20261115/corpus_manifest.json",
                "artifacts/market_observer_financial_text_v24_20261116/expected_relations.private.json"):
        if sha((ROOT / rel).read_bytes()) != prior_freeze["file_sha256"][rel]:
            raise RuntimeError("frozen v2.4 kernel or source reference changed: " + rel)
    if git("branch", "--show-current").decode().strip() != BRANCH:
        raise RuntimeError("wrong branch")
    if subprocess.run(["git", "merge-base", "--is-ancestor", BASELINE, "HEAD"],
                      cwd=ROOT, check=False).returncode:
        raise RuntimeError("current HEAD does not descend from v2.4 baseline")
    docs = source_rows(config)
    candidates = json.loads(CANDIDATES.read_text(encoding="utf-8"))["candidates"]
    reviews = json.loads(REVIEWS.read_text(encoding="utf-8"))["reviews"]
    if len(candidates) != 71 or len(reviews) != 71 or \
       {c["candidate_id"] for c in candidates} != {r["candidate_id"] for r in reviews}:
        raise RuntimeError("71-candidate reference changed")
    reviewed = Counter(r.get("status") or r.get("final_status") for r in reviews)
    if sorted(reviewed.values()) != [11, 60]:
        raise RuntimeError("60/11 source-review split changed")
    dependencies = dependency_hashes()
    now = datetime.now(timezone.utc)
    common = {
        "schema": "financial_text_v25_preflight_v1",
        "frozen_at_utc": now.isoformat(),
        "frozen_at_asia_shanghai": now.astimezone(ZoneInfo("Asia/Shanghai")).isoformat(),
        "baseline_commit": BASELINE,
        "git_head": git("rev-parse", "HEAD").decode().strip(),
        "branch": BRANCH,
        "tracked_diff_sha256": sha(git("diff", "--binary", "HEAD")),
        "workspace_status_sha256": sha(git("status", "--porcelain=v1")),
        "dependency_sha256": dependencies,
        "source_documents": [{"document_id": d["document_id"], "event_group_id": d["event_group_id"],
                              "announcement_date": d["announcement_date"], "symbol": d["symbol"],
                              "source_kind": d["source_kind"], "raw_body_sha256": d["raw_body_sha256"]}
                             for d in docs],
        "source_documents_count": len(docs),
        "event_groups_count": len({d["event_group_id"] for d in docs}),
        "candidate_count": len(candidates),
        "review_status_counts": dict(reviewed),
        "budget_unicode_characters_per_event": 20000,
        "runtime": {"python": platform.python_version(), "beautifulsoup4": version("beautifulsoup4")},
        "new_downloads": 0, "model_fit_calls": 0, "model_inference_calls": 0,
        "return_evaluation_calls": 0,
    }
    private = {**common, "private_source_rows": docs,
               "private_reference_paths": [str(CANDIDATES), str(REVIEWS), str(OLD_RETENTION), str(V24_REFERENCE)]}
    PRIVATE.mkdir(parents=True, exist_ok=True)
    PUBLIC.mkdir(parents=True, exist_ok=True)
    private_path.write_bytes(canonical(private))
    public_path.write_bytes(canonical(common))
    return common


if __name__ == "__main__":
    print(json.dumps(freeze(), ensure_ascii=False, sort_keys=True))
