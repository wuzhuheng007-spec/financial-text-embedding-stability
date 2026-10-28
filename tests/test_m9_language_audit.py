"""Focused checks for the M9 delivery language scan."""

from pathlib import Path
import subprocess

import pytest

from scripts.m9_language_audit import added_lines_since_head, audit, scan_text


def test_scan_flags_non_english_script_with_location() -> None:
    issues = scan_text("English heading\n\u4e2d\u6587", "review.html")
    assert len(issues) == 2
    assert issues[0]["path"] == "review.html"
    assert issues[0]["line"] == 2
    assert issues[0]["column"] == 1
    assert issues[0]["script"] == "Han"


def test_math_symbols_and_latin_names_are_not_script_failures() -> None:
    assert scan_text("Spearman ρ = 0.5; José; x²; 10–Q", "note.md") == []


def test_audit_scans_only_selected_delivery_and_skips_report(tmp_path: Path) -> None:
    new = tmp_path / "new"
    old = tmp_path / "old"
    new.mkdir()
    old.mkdir()
    (new / "page.html").write_text("Researcher review", encoding="utf-8")
    (old / "historical.md").write_text("\u5386\u53f2\u8bb0\u5f55", encoding="utf-8")
    report = new / "audit.json"
    report.write_text("\u4e2d\u6587", encoding="utf-8")
    result = audit([new], report, ["Old historical record kept outside scope"])
    assert result["character_issue_count"] == 0
    assert [Path(item["path"]).name for item in result["files_checked"]] == ["page.html"]
    assert result["preserved_historical_or_source_exceptions"]


def test_missing_selected_path_fails(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        audit([tmp_path / "missing"], tmp_path / "report.json")


def test_revised_legacy_scan_reads_only_added_lines(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    legacy = tmp_path / "TASKS.md"
    legacy.write_text("# Historical\n\u5386\u53f2\u8bb0\u5f55\n", encoding="utf-8")
    subprocess.run(["git", "add", "TASKS.md"], cwd=tmp_path, check=True)
    subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.org",
                    "commit", "-qm", "Historical baseline"], cwd=tmp_path, check=True)
    legacy.write_text(legacy.read_text(encoding="utf-8") + "M9 review pending.\n",
                      encoding="utf-8")
    assert added_lines_since_head(legacy, tmp_path) == "M9 review pending."
    authored = tmp_path / "new.md"
    authored.write_text("English delivery", encoding="utf-8")
    result = audit([authored], tmp_path / "report.json",
                   revised_paths=[legacy], repository_root=tmp_path)
    assert result["character_issue_count"] == 0
    assert result["revised_legacy_files_added_lines_checked"][0]["added_lines_checked"] == 1
