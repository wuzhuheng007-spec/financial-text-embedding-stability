"""Focused checks for public-only shortfacts v2 handoff packaging."""

from __future__ import annotations

import importlib.util
import json
import zipfile
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/news_shortfacts_v2/package_handoff.py"
spec = importlib.util.spec_from_file_location("shortfacts_v2_handoff", SCRIPT)
assert spec and spec.loader
handoff = importlib.util.module_from_spec(spec)
spec.loader.exec_module(handoff)


def test_rejects_private_path_even_if_allowlisted(tmp_path: Path, monkeypatch) -> None:
    private = tmp_path / "artifacts/private.txt"
    private.parent.mkdir()
    private.write_text("private", encoding="utf-8")
    monkeypatch.setattr(handoff, "ALLOWLIST", ("artifacts/private.txt",))
    with pytest.raises(ValueError, match="Disallowed handoff path"):
        handoff.source_files(tmp_path)


def test_rejects_credentials_and_private_absolute_path() -> None:
    with pytest.raises(ValueError, match="Potential credential"):
        handoff.screen_text("report.md", b"hf_" + b"ABCDEFGHIJKLMNOPQRSTUVWXYZ123456", [])
    with pytest.raises(ValueError, match="Potential credential"):
        handoff.screen_text("report.md", b"C:\\Users\\Someone\\private\\file.txt", [])


def test_rejects_full_private_passage_or_response() -> None:
    private = "Official evidence paragraph with multiple numerical facts and confidential full text."
    with pytest.raises(ValueError, match="Full private passage"):
        handoff.screen_text("report.md", private.encode(), [private])


def test_build_checks_zip_crc_member_hashes_and_no_overwrite(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    result = repo / handoff.RESULT
    result.mkdir(parents=True)
    (repo / "public.md").write_text("Short aggregate result", encoding="utf-8")
    (result / "new_run_summary_redacted.json").write_text(json.dumps({"counts": {
        "attempted_tasks": 1, "completed_response_tasks": 1,
        "strict_raw_json_valid_tasks": 1, "fence_adapted_tasks": 0,
        "fully_correct_facts": 1, "reference_facts": 14,
    }}), encoding="utf-8")
    (result / "retrospective_summary_redacted.json").write_text(json.dumps({"counts": {
        "strict_raw_json_valid_tasks": 1, "fence_adapted_tasks": 5,
        "fully_correct_facts": 0, "reference_facts": 14,
    }}), encoding="utf-8")
    (result / "run_receipt_redacted.json").write_text(json.dumps({"run_status": "STOP_AFTER_ONE"}), encoding="utf-8")
    monkeypatch.setattr(handoff, "ALLOWLIST", (
        "public.md",
        f"{handoff.RESULT}/new_run_summary_redacted.json",
        f"{handoff.RESULT}/retrospective_summary_redacted.json",
        f"{handoff.RESULT}/run_receipt_redacted.json",
    ))
    out = tmp_path / "handoffs"
    receipt = handoff.build(repo, out)
    assert receipt["zip_crc"] == "PASS" and receipt["member_hashes"] == "PASS"
    with zipfile.ZipFile(out / "news_shortfacts_v2_20261115.zip") as zf:
        assert set(zf.namelist()) == set(handoff.ALLOWLIST) | {"README.md", "MANIFEST.json"}
        manifest = json.loads(zf.read("MANIFEST.json"))
        assert len(manifest["files"]) == 5
    with pytest.raises(FileExistsError, match="Handoff already exists"):
        handoff.build(repo, out)
