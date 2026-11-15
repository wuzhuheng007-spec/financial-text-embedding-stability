"""Hash actual training-only v2.1 inputs/code before build or comparison."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
from pathlib import Path
import platform
import subprocess

from .build import select_training_documents


ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "configs/market_observer_financial_text_v21.json"
MANIFEST = ROOT / "artifacts/market_observer_official_20261115/corpus_manifest.json"
V2_PUBLIC = ROOT / "results/market_observer_financial_text_v2_20261115/retention_redacted.json"
V21_PRIVATE = ROOT / "artifacts/market_observer_financial_text_v21_20261115"
V21_PUBLIC = ROOT / "results/market_observer_financial_text_v21_20261115"
P3 = ROOT / "artifacts/market_observer_financial_text_v2_20261115/fact_cards"
FROZEN_V2_SHA256 = "2cdb00d715447b1850b1db6473308376943e21d05a2abbb1e5bcc62211681e0f"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def git(*args: str) -> bytes:
    return subprocess.check_output(["git", *args], cwd=ROOT)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("phase", choices=["build", "retention"])
    args = ap.parse_args()
    target = V21_PUBLIC / f"preflight_{args.phase}_redacted.json"
    if target.exists():
        raise FileExistsError(target)
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    if config["source_scope"]["announcement_date_start"] != "2025-04-01" or \
            config["source_scope"]["announcement_date_end"] != "2026-03-31" or \
            config["v2_input"]["model_adapter_max_characters"] != 20000:
        raise RuntimeError("v2.1 train scope or fixed adapter budget changed")
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    docs = select_training_documents(manifest, config)
    if len(docs) != 16 or sha(V2_PUBLIC.read_bytes()) != FROZEN_V2_SHA256:
        raise RuntimeError("training documents or frozen v2 receipt changed")
    files = [CONFIG, ROOT / "configs/market_observer_financial_text_v21_CHANGE.md", MANIFEST, V2_PUBLIC,
             ROOT / "configs/market_observer_official_text_v1.json",
             ROOT / "scripts/market_observer_official_v1/text_comparison.py",
             ROOT / "tests/test_market_observer_financial_text_v21.py",
             ROOT / "tests/test_market_observer_financial_text_v21_retention.py",
             ROOT / "tests/test_market_observer_financial_text_retention.py",
             ROOT / "scripts/market_observer_financial_text_v21/__init__.py",
             ROOT / "scripts/market_observer_financial_text_v21/parser.py",
             ROOT / "scripts/market_observer_financial_text_v21/adapter.py",
             ROOT / "scripts/market_observer_financial_text_v21/build.py",
             ROOT / "scripts/market_observer_financial_text_v21/compare_retention.py",
             ROOT / "scripts/market_observer_financial_text_v21/preflight.py",
             ROOT / "results/market_observer_official_repro_20261115/text_input_audit_redacted.json",
             ROOT / "artifacts/market_observer_official_text_audit_20261115/aggregate_rows.private.jsonl",
             P3 / "content_checklist_reviewed.private.json",
             P3 / "fact_review_v2.private.json",
             P3 / "fact_review_v4.private.json",
             P3 / "fact_candidates_v2.private.json"]
    if args.phase == "retention":
        files += [V21_PRIVATE / "blocks.private.jsonl",
                  V21_PRIVATE / "event_inputs.private.jsonl",
                  V21_PRIVATE / "documents.private.json",
                  V21_PUBLIC / "build_redacted.json"]
    hashes = {}
    for path in files:
        if not path.is_file():
            raise FileNotFoundError(path)
        hashes[path.relative_to(ROOT).as_posix()] = sha(path.read_bytes())
    sources = [{"document_id": d["document_id"], "symbol": d["symbol"],
                "announcement_date": d["announcement_date"], "source_kind": d["source_kind"],
                "raw_sha256": d["raw_body_sha256"], "extracted_sha256": d["body_sha256"]}
               for d in docs]
    status = git("status", "--porcelain=v1")
    receipt = {"schema": "financial_text_v21_preflight_redacted_v1",
               "phase": args.phase, "captured_at_utc": datetime.now(timezone.utc).isoformat(),
               "git_head": git("rev-parse", "HEAD").decode().strip(),
               "branch": git("branch", "--show-current").decode().strip(),
               "tracked_diff_sha256": sha(git("diff", "--binary")),
               "staged_diff_sha256": sha(git("diff", "--cached", "--binary")),
               "workspace_status_sha256": sha(status),
               "workspace_status_entry_count": len(status.splitlines()),
               "code_config_and_input_sha256": hashes,
               "source_documents": sources, "source_document_count": len(sources),
               "frozen_v2_retention_sha256": FROZEN_V2_SHA256,
               "runtime": {"python": platform.python_version(),
                           "beautifulsoup4": importlib.metadata.version("beautifulsoup4"),
                           "numpy": importlib.metadata.version("numpy")},
               "no_new_downloads": True, "no_validation_or_test_bodies_or_labels_opened": True,
               "model_fit_calls": 0}
    V21_PUBLIC.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"phase": args.phase, "source_documents": len(sources),
                      "receipt": target.relative_to(ROOT).as_posix(),
                      "sha256": sha(target.read_bytes())}))


if __name__ == "__main__":
    main()
