"""Freeze the exact v2.4 small-table regression dependencies before scoring.

This records hashes only. Source bodies, reviewed cell values, and private
paths are never copied into the public receipt.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
from importlib import metadata
import json
from pathlib import Path
import platform
import subprocess
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
PRIVATE = ROOT / "artifacts/market_observer_financial_text_v24_20261116"
PUBLIC = ROOT / "results/market_observer_financial_text_v24_20261116"
CONFIG = ROOT / "configs/market_observer_financial_text_v24_20261116.json"
DEPENDENCIES = (
    "scripts/market_observer_financial_text_v24/__init__.py",
    "scripts/market_observer_financial_text_v24/semantic.py",
    "scripts/market_observer_financial_text_v24/evaluate.py",
    "scripts/market_observer_financial_text_v23/__init__.py",
    "scripts/market_observer_financial_text_v23/structure.py",
    "scripts/market_observer_financial_text_v21/__init__.py",
    "scripts/market_observer_financial_text_v21/parser.py",
    "scripts/freeze_market_observer_financial_text_v24.py",
    "configs/market_observer_financial_text_v24_20261116.json",
    "results/market_observer_financial_text_v24_20261116/reference_freeze_redacted.json",
    "artifacts/market_observer_financial_text_v24_20261116/selection_manifest.private.json",
    "artifacts/market_observer_financial_text_v24_20261116/expected_relations.private.json",
    "artifacts/market_observer_financial_text_v24_20261116/freeze_hashes.private.json",
    "artifacts/market_observer_official_20261115/corpus_manifest.json",
)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def freeze(run_id: str) -> dict:
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    if config.get("status") != "FROZEN_BEFORE_REAL_REGRESSION":
        raise ValueError("freeze the plan/config before creating a run receipt")
    selection = json.loads((PRIVATE / "selection_manifest.private.json").read_text(encoding="utf-8"))
    expected = json.loads((PRIVATE / "expected_relations.private.json").read_text(encoding="utf-8"))
    reference_freeze = json.loads((PUBLIC / "reference_freeze_redacted.json").read_text(encoding="utf-8"))
    if sha(PRIVATE / "selection_manifest.private.json") != reference_freeze["private_selection_manifest_sha256"]:
        raise ValueError("pre-code selection freeze changed")
    if sha(PRIVATE / "expected_relations.private.json") != reference_freeze["private_expected_relations_sha256"]:
        raise ValueError("pre-code expected-relation freeze changed")
    if sha(PRIVATE / "freeze_hashes.private.json") != reference_freeze["private_freeze_receipt_sha256"]:
        raise ValueError("private source-freeze receipt changed")
    corpus_manifest = ROOT / "artifacts/market_observer_official_20261115/corpus_manifest.json"
    if sha(corpus_manifest) != selection["source_manifest_sha256"]:
        raise ValueError("source corpus manifest changed")
    tables = selection["selected_tables"]
    if len(tables) != 4 or len(expected["expected_tables"]) != 4:
        raise ValueError("the preselected four-table reference changed")
    if any(not ("2025-04-01" <= t["announcement_date"] <= "2026-03-31") for t in tables):
        raise ValueError("out-of-window document")
    source_hashes = {}
    for table in tables:
        path = Path(table["raw_body_path_local_only"])
        if sha(path) != table["raw_body_sha256"]:
            raise ValueError("source bytes changed: " + table["document_id"])
        source_hashes[table["document_id"]] = table["raw_body_sha256"]
        public_table = next((x for x in reference_freeze["source_tables"]
                             if x["document_id"] == table["document_id"]), None)
        if public_table is None or public_table["raw_body_sha256"] != table["raw_body_sha256"]:
            raise ValueError("source list changed from pre-code freeze")
        snippet = table["snippet"]
        snippet_path = Path(snippet["path"])
        if not snippet_path.is_absolute():
            snippet_path = ROOT / snippet_path
        if sha(snippet_path) != snippet["sha256"]:
            raise ValueError("saved table snippet changed")
    paths = list(DEPENDENCIES)
    paths.extend(sorted(p.relative_to(ROOT).as_posix() for p in PRIVATE.glob("*.private.html")))
    paths.extend(sorted(p.relative_to(ROOT).as_posix() for p in (ROOT / "tests").glob("test_market_observer_financial_text_v24*.py")))
    if len(paths) != len(set(paths)) or not any(x.startswith("tests/") for x in paths):
        raise ValueError("dependency list missing tests or contains duplicates")
    for name in paths:
        if not (ROOT / name).is_file():
            raise FileNotFoundError(name)
    now = datetime.now(timezone.utc)
    receipt = {
        "schema": "financial_text_v24_freeze_v1",
        "run_id": run_id,
        "frozen_at_utc": now.isoformat(),
        "frozen_at_asia_shanghai": now.astimezone(ZoneInfo("Asia/Shanghai")).isoformat(),
        "file_sha256": {name: sha(ROOT / name) for name in paths},
        "source_raw_sha256": source_hashes,
        "runtime": {
            "python": platform.python_version(),
            "beautifulsoup4": metadata.version("beautifulsoup4"),
            "numpy": metadata.version("numpy"),
            "git_head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        },
        "expected_tables": 4,
        "expected_value_cells": 138,
        "source_scope": "2025-04-01_to_2026-03-31_saved_NVDA_MU_training_only",
        "independent_gold": False,
        "evaluation_runs_on_this_receipt": 0,
    }
    target = PUBLIC / f"freeze_{run_id}_redacted.json"
    with target.open("x", encoding="utf-8", newline="\n") as out:
        json.dump(receipt, out, ensure_ascii=False, indent=2)
        out.write("\n")
    return {"receipt": str(target), "sha256": sha(target), "files": len(paths)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", default="frozen_v1")
    args = parser.parse_args()
    print(json.dumps(freeze(args.run_id), ensure_ascii=False))
