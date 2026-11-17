"""Package a strict allowlist of public v4.1 files, never private news or weights."""
from __future__ import annotations

import hashlib
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from .freeze import ROOT


RESULT = "results/news_sentiment_factor_v41_20261117"
CODE = [
    "scripts/news_sentiment_factor_v41/__init__.py",
    "scripts/news_sentiment_factor_v41/freeze.py",
    "scripts/news_sentiment_factor_v41/prepare.py",
    "scripts/news_sentiment_factor_v41/prepare_v2.py",
    "scripts/news_sentiment_factor_v41/score.py",
    "scripts/news_sentiment_factor_v41/review.py",
    "scripts/news_sentiment_factor_v41/association_audit.py",
    "scripts/news_sentiment_factor_v41/worker.py",
    "scripts/news_sentiment_factor_v41/candidates.py",
    "scripts/news_sentiment_factor_v41/handoff.py",
    "scripts/quant_personal_feed/event_rules.py",
    "apps/quant_portal/core/research_chinese_cache.py",
    "apps/quant_portal/core/test_research_sentiment_v41.py",
    "apps/quant_portal/core/views.py",
    "apps/quant_portal/static/core/research_sentiment_v4.js",
    "apps/quant_portal/templates/core/dashboard.html",
    "tests/test_news_sentiment_factor_v41.py",
]
CONFIG = [
    "configs/news_sentiment_factor_v41_20261117.json",
    "configs/news_sentiment_factor_v41_20261117_r2.json",
    "configs/news_sentiment_factor_v41_future_eval_20261117.json",
]
REPORT = [
    "reports/2026-11-17_news_sentiment_factor_v41.zh.md",
    "reports/2026-11-17_news_sentiment_factor_v41.en.md",
    "docs/news_research/wrds_ravenpack_dj_access_inquiry_v41.en.txt",
    f"{RESULT}/plan_execution.csv",
]
RECEIPTS = [
    "implementation_revisions_redacted.json",
    "input_manifest_redacted.json",
    "segment_manifest_redacted.json",
    "segment_manifest_r2_redacted.json",
    "source_review_frozen_redacted.json",
    "synthetic_smoke_redacted.json",
    "run_lock_synthetic_smoke_redacted.json",
    "run_lock_real_replay_redacted.json",
    "real_score_run_redacted.json",
    "source_review_comparison_redacted.json",
    "association_audit_redacted.json",
    "candidate_coverage_redacted.json",
    "worker_observation_redacted.json",
    "browser_acceptance_redacted.json",
    "evidence_index_redacted.json",
    "prospective_score_observation_redacted.json",
    "evidence_index_r2_redacted.json",
]
PUBLIC_FILES = CODE + CONFIG + REPORT + [f"{RESULT}/{name}" for name in RECEIPTS]


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def public_bytes(relative: str) -> bytes:
    if Path(relative).is_absolute() or ".." in Path(relative).parts:
        raise ValueError("UNSAFE_MEMBER_PATH")
    lowered = relative.lower()
    if any(x in lowered for x in ("artifacts/", ".sqlite", ".safetensors", ".env", "credential", "secret", "private.json")):
        raise ValueError("PRIVATE_MEMBER_PATH:" + relative)
    data = (ROOT / relative).read_bytes()
    data.decode("utf-8-sig")
    return data


def evidence_index() -> dict:
    target = ROOT / RESULT / "evidence_index_r2_redacted.json"
    if target.exists():
        raise FileExistsError(target)
    rows = []
    for name in RECEIPTS:
        if name == target.name:
            continue
        relative = f"{RESULT}/{name}"
        raw = public_bytes(relative)
        rows.append({"path": relative, "sha256": digest(raw), "bytes": len(raw)})
    result = {
        "schema": "news_sentiment_factor_v41_evidence_index_redacted_2",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "scope": "PUBLIC_REDACTED_RECEIPTS_ONLY",
        "immutable_replay_reference": "run_lock_real_replay_redacted.json",
        "historical_english_v4": "SEPARATE_UNCHANGED",
        "private_inputs_scores_weights_credentials_and_news_text": "EXCLUDED",
        "rows": rows,
    }
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def package() -> dict:
    destination = ROOT.parent / "quant" / "handoffs" / "news_sentiment_factor_v41_20261117.zip"
    if destination.exists():
        raise FileExistsError(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    members = []
    for relative in PUBLIC_FILES:
        raw = public_bytes(relative)
        members.append({"path": relative, "bytes": len(raw), "sha256": digest(raw), "data": raw})
    readme = (
        "# Chinese news tone v4.1 handoff\n\n"
        "Separate from frozen English v4. Start with the bilingual reports and plan_execution.csv.\n"
        "This ZIP contains code, configuration and redacted evidence only. The local model weights,\n"
        "licensed/private news, raw per-unit scores, databases and credentials are deliberately absent.\n"
        "The replay scored 1,101 units; the completed bounded observation scored 0 new versions.\n"
        "A later continuous run scored 1 new unreviewed Jin10 version without a company link.\n"
        "No return model, IC, prediction or trade was run.\n"
    ).encode("utf-8")
    members.append({"path": "HANDOFF_README.md", "bytes": len(readme), "sha256": digest(readme), "data": readme})
    manifest = {"schema": "news_sentiment_factor_v41_handoff_members_1",
                "created_at_utc": datetime.now(timezone.utc).isoformat(),
                "members": [{k: x[k] for k in ("path", "bytes", "sha256")} for x in members]}
    manifest_bytes = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    with zipfile.ZipFile(destination, "x", compression=zipfile.ZIP_DEFLATED) as out:
        for item in members:
            out.writestr(item["path"], item["data"])
        out.writestr("members.sha256.json", manifest_bytes)
    with zipfile.ZipFile(destination) as archive:
        if archive.testzip() is not None:
            raise ValueError("ZIP_CRC_FAILURE")
        for item in members:
            body = archive.read(item["path"])
            if digest(body) != item["sha256"]:
                raise ValueError("MEMBER_HASH_MISMATCH:" + item["path"])
            body.decode("utf-8-sig")
        if "新闻情绪" not in archive.read("reports/2026-11-17_news_sentiment_factor_v41.zh.md").decode("utf-8"):
            raise ValueError("CHINESE_UTF8_CHECK_FAILED")
    return {"path": str(destination), "sha256": digest(destination.read_bytes()),
            "members": len(members) + 1, "crc": "PASS", "member_hashes": "PASS",
            "text_encoding": "UTF8_PASS", "private_content_excluded_by_allowlist": True}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("index", "package"))
    args = parser.parse_args()
    print(json.dumps(evidence_index() if args.action == "index" else package(), ensure_ascii=False))
