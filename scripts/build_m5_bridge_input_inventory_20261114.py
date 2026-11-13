"""Read-only provenance audit for the five-firm saved-vector bridge diagnosis."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

SCRIPT = Path(__file__).resolve()
WORKTREE = SCRIPT.parents[1]
PUBLISH = WORKTREE.parent / "financial-text-embedding-stability-publish"
QUANT = WORKTREE.parent / "quant"
TICKERS = {"AAPL", "MSFT", "AMZN", "GOOGL", "META"}
POOLS = ("equal_chunks", "body_token_weighted")
SPECS = {
    "round4": (
        "artifacts/round4/20261031-small-chunk-real5-run-v1",
        "698942ddc8a7b395108a3ce2b72deab0f370ed3cd9de2bca827b693632182d5e",
        "reports/2026-10-31_small_chunk_probe_descriptive_results.md",
        "3308783aab453467cf86fc7fbe0a61c59d74da98227e4b227f15634910db42fb",
    ),
    "round6": (
        "artifacts/round6/20261031-s3-real5-run-v1",
        "eab4abec64f65d9f9d48589eba20c59edc5c75f4d6ee2567db6765e58dfd1f47",
        "reports/2026-10-31_boundary_shift_probe_descriptive_results.md",
        "793050a0a8ed200b10ba022cc2efa88cf00372fbdbca03850f1b3a2202f4cf5f",
    ),
}


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def insist(ok: bool, message: str) -> None:
    if not ok:
        raise ValueError(message)


def bound_path(raw: str) -> Path:
    path = Path(raw)
    return path if path.is_absolute() else PUBLISH / path


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=WORKTREE, text=True).strip()


def audit_run(name: str) -> tuple[dict, dict, dict]:
    run_rel, manifest_expected, report_rel, report_expected = SPECS[name]
    root = PUBLISH / run_rel
    manifest_file = root / "manifest.json"
    manifest_sha = sha(manifest_file)
    insist(manifest_sha == manifest_expected, f"{name} manifest hash")
    m = json.loads(manifest_file.read_text(encoding="utf-8"))
    insist(m["status"] == "COMPLETE" and m["evidence_class"] == "REAL_MODEL",
           f"{name} run status")
    insist(m["logical_jobs_completed"] == 25 and m["company_vectors_completed"] == 50,
           f"{name} run counts")
    metric_file = root / m["metric_file"]["path"]
    metric_sha = sha(metric_file)
    insist(metric_sha == m["metric_file"]["sha256"], f"{name} metrics hash")
    report_sha = sha(PUBLISH / report_rel)
    insist(report_sha == report_expected, f"{name} historical source report hash")
    worktree_report_sha = sha(WORKTREE / report_rel)
    source_bytes = (PUBLISH / report_rel).read_bytes()
    worktree_bytes = (WORKTREE / report_rel).read_bytes()
    normalized_equal = (
        source_bytes.replace(b"\r\n", b"\n")
        == worktree_bytes.replace(b"\r\n", b"\n")
    )
    insist(normalized_equal, f"{name} worktree report content differs")
    report = (PUBLISH / report_rel).read_text(encoding="utf-8")
    for value in (manifest_sha, metric_sha, m["candidate_sha256"],
                  m["decision_sha256"], m["input_manifest_sha256"]):
        insist(value in report, f"{name} report binding")
    bindings = {}
    for label, path_key, hash_key in (
        ("candidate", "candidate_path", "candidate_sha256"),
        ("decision", "decision_path", "decision_sha256"),
        ("preflight_receipt", "receipt_path", "receipt_sha256"),
    ):
        actual = sha(bound_path(m[path_key]))
        insist(actual == m[hash_key], f"{name} {label} hash")
        bindings[label] = {"sha256": actual, "verified": True}
    vectors = []
    files = {}
    for job in m["jobs"]:
        insist(job["status"] == "COMPLETE" and job["ticker"] in TICKERS,
               f"{name} vector job")
        setting = str(job["body_tokens_per_chunk"] if name == "round4"
                      else job["boundary_shift"])
        for pool in POOLS:
            meta = job["files"][pool]
            path = (root / meta["path"]).resolve()
            insist(path.is_relative_to(root.resolve()), f"{name} vector path")
            actual = sha(path)
            insist(actual == meta["sha256"], f"{name} vector hash")
            a = np.load(path, allow_pickle=False)
            insist(list(a.shape) == meta["shape"] == [384], f"{name} shape")
            insist(str(a.dtype) == meta["dtype"] == "float64", f"{name} dtype")
            insist(bool(np.isfinite(a).all()) and bool(np.linalg.norm(a) > 0),
                   f"{name} finite/nonzero")
            vectors.append({
                "run": name, "setting": setting, "ticker": job["ticker"],
                "pooling": pool, "path": f"{run_rel}/{meta['path']}",
                "sha256": actual, "shape": [384], "dtype": "float64",
                "finite": True, "nonzero": True,
            })
            files[(setting, job["ticker"], pool)] = path
        chunk_meta = job["files"]["chunks"]
        chunk_path = (root / chunk_meta["path"]).resolve()
        insist(chunk_path.is_relative_to(root.resolve()), f"{name} chunk path")
        files[(setting, job["ticker"], "chunks")] = chunk_path
    insist(len(vectors) == 50 and len(files) == 75, f"{name} vector grid")
    expected_settings = ({"118", "123", "128", "133", "138"} if name == "round4"
                         else {"-10", "-5", "0", "5", "10"})
    actual_settings = {row["setting"] for row in vectors}
    insist(actual_settings == expected_settings, f"{name} setting grid")
    insist({row["pooling"] for row in vectors} == set(POOLS), f"{name} pooling grid")
    if name == "round6":
        insist(m["reference_body_tokens"] == 128
               and m["boundary_shifts"] == [-10, -5, 0, 5, 10]
               and m["pooling"] == list(POOLS), "round6 setting declaration")
    return {
        "manifest": {"path": f"{run_rel}/manifest.json", "sha256": manifest_sha},
        "metrics": {"path": f"{run_rel}/{m['metric_file']['path']}", "sha256": metric_sha},
        "historical_report": {"path": report_rel, "source_sha256": report_sha,
                              "current_worktree_sha256": worktree_report_sha,
                              "source_and_worktree_bytes_equal": worktree_report_sha == report_sha,
                              "source_and_worktree_text_equal_after_newline_normalization":
                                  normalized_equal},
        "bindings": bindings,
        "input_manifest_declared_sha256": m["input_manifest_sha256"],
        "metric_contract_declared_sha256": m["metric_contract_sha256"],
        "input_versions": m["input_versions"],
        "model_identity": m["model_identity"],
        "settings": sorted(actual_settings, key=int),
        "pooling": list(POOLS),
        "jobs": 25, "company_vectors_checked": 50, "vectors": vectors,
    }, m, files


def audit_text_and_model(a: dict, b: dict) -> dict:
    insist(a["input_versions"] == b["input_versions"], "input versions differ")
    insist(a["input_manifest_sha256"] == b["input_manifest_sha256"],
           "input manifest declarations differ")
    insist(a["model_identity"] == b["model_identity"], "model identity differs")
    insist(a["corpus_index_path"] == b["corpus_index_path"], "corpus path differs")
    corpus_path = Path(a["corpus_index_path"])
    insist(corpus_path.is_relative_to(QUANT), "unexpected corpus location")
    corpus = json.loads(corpus_path.read_text(encoding="utf-8"))
    entries = {e["ticker"]: e for e in corpus["entries"]}
    insist(set(entries) == TICKERS, "corpus company set")
    texts = []
    for version in a["input_versions"]:
        entry = entries[version["ticker"]]
        text_sha = sha(Path(entry["item1_path"]))
        insist(text_sha == entry["item1_sha256"] == version["item1_sha256"],
               "Item 1 text hash")
        insist(entry["accession"] == version["accession"], "accession")
        texts.append({"ticker": version["ticker"], "accession": version["accession"],
                      "item1_sha256": text_sha, "source_text_verified": True})
    model_root = PUBLISH / ".cache/models/all-MiniLM-L6-v2-1110a243"
    model_files = []
    for rel, expected in a["model_identity"]["model_file_sha256"].items():
        actual = sha(model_root / rel)
        insist(actual == expected, f"model file {rel}")
        model_files.append({"file": rel, "sha256": actual, "verified": True})
    config_sha = sha(PUBLISH / "configs/smoke.yaml")
    insist(config_sha == a["model_identity"]["reference_config_sha256"],
           "model config")
    return {
        "corpus_index_sha256": sha(corpus_path), "text_versions": texts,
        "model_files": model_files, "reference_config_sha256": config_sha,
        "common_input_manifest_declared_sha256": a["input_manifest_sha256"],
        "same_inputs_model_and_config": True,
    }


def audit_compatibility(r4: dict, r6: dict, f4: dict, f6: dict) -> dict:
    base = PUBLISH / "artifacts/round6"
    vector_file = base / "s3_delta0_round4_compatibility_v1.json"
    metric_file = base / "s3_delta0_round4_metric_compatibility_v1.json"
    receipt = json.loads(vector_file.read_text(encoding="utf-8"))
    metric = json.loads(metric_file.read_text(encoding="utf-8"))
    insist(receipt["status"] == "PASS_READBACK_COMPARISON"
           and metric["status"] == "PASS", "historical compatibility status")
    insist(receipt["round4_manifest_sha256"] == r4["manifest"]["sha256"]
           and receipt["s3_manifest_sha256"] == r6["manifest"]["sha256"]
           and receipt["round4_metrics_sha256"] == r4["metrics"]["sha256"]
           and receipt["s3_metrics_sha256"] == r6["metrics"]["sha256"],
           "compatibility receipt bindings")
    insist(len(receipt["comparisons"]) == 15, "compatibility comparison count")
    for row in receipt["comparisons"]:
        left = f4[("128", row["ticker"], row["kind"])]
        right = f6[("0", row["ticker"], row["kind"])]
        insist(sha(left) == row["round4_file_sha256"]
               and sha(right) == row["s3_file_sha256"], "compatibility file hash")
        insist(bool(np.array_equal(np.load(left, allow_pickle=False),
                                   np.load(right, allow_pickle=False))),
               "delta zero vector mismatch")
        insist(row["array_equal"] and row["max_absolute_difference"] == 0,
               "historical vector receipt row")
    for pool in POOLS:
        insist(all(metric["arms"][pool].values()), "historical metric compatibility")
    return {
        "vector_receipt_sha256": sha(vector_file),
        "metric_receipt_sha256": sha(metric_file),
        "delta0_vs_round4_128_saved_arrays_equal": 15,
        "historical_metric_compatibility_status": "PASS",
        "scope": "readback compatibility, not independent observations or a new model run",
    }


def audit_m5_gate() -> dict:
    config_file = WORKTREE / "configs/m5_protocol_company_proxy_conditional_20261113.json"
    proposal = json.loads(config_file.read_text(encoding="utf-8"))
    safe_file = PUBLISH / "artifacts/m5_protocol_joint_diagnostic_20261113_v1/m5_protocol_joint_diagnostic_summary_safe.json"
    safe = json.loads(safe_file.read_text(encoding="utf-8"))
    insist(proposal["status"] == "PROPOSED"
           and proposal["researcher_accepted"] is False
           and proposal["formal_financial_experiment_status"] == "NOT_RUN",
           "formal M5 proposal status")
    insist(proposal["current_missing_evidence"]["approved_prediction_observation_count"] is None,
           "unexpected approved observation count")
    insist(safe["H_time_valid_cik_primary_admitted_count"] is None
           and safe["R_formal_admitted_count"] is None
           and safe["financial_labels_created"] == 0
           and safe["prediction_evaluations"] == 0, "formal M5 output gate")
    insist(all(scenario["formal_event_mask"] is None
               for scenario in safe["scenarios"].values()), "formal event mask gate")
    return {
        "proposal_sha256": sha(config_file), "proposal_status": "PROPOSED",
        "researcher_accepted": False, "formal_financial_experiment_status": "NOT_RUN",
        "approved_prediction_observation_count": None,
        "joint_diagnostic_summary_sha256": sha(safe_file),
        "H_time_valid_cik_primary_admitted_count": None,
        "R_formal_admitted_count": None, "financial_labels_created": 0,
        "prediction_evaluations": 0, "formal_event_mask": None,
        "scope": "engineering preparation only; no formal M5 financial execution",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    git_status = git("status", "--porcelain")
    r4, m4, f4 = audit_run("round4")
    r6, m6, f6 = audit_run("round6")
    texts = audit_text_and_model(m4, m6)
    compat = audit_compatibility(r4, r6, f4, f6)
    result = {
        "schema": "m5_bridge_input_inventory_v1",
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "status": "PASS_SAVED_INPUTS_READBACK",
        "scope": "real saved-vector engineering diagnosis; no new inference or financial computation",
        "worktree_git": {"head": git("rev-parse", "HEAD"),
                         "branch": git("branch", "--show-current"),
                         "status_porcelain_before_output": git_status},
        "audit_script_sha256": sha(SCRIPT),
        "runs": {"round4": r4, "round6": r6},
        "text_and_model": texts,
        "historical_delta_zero_compatibility": compat,
        "formal_m5_gate": audit_m5_gate(),
        "command": "python scripts/build_m5_bridge_input_inventory_20261114.py --output artifacts/m5_bridge_20261114_0238/input_inventory.json",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
    print(json.dumps({"status": result["status"], "round4_vectors": len(r4["vectors"]),
                      "round6_vectors": len(r6["vectors"]),
                      "delta_zero_equal_arrays": compat["delta0_vs_round4_128_saved_arrays_equal"],
                      "output": str(args.output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
