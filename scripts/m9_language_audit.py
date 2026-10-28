"""Scan explicitly selected M9 delivery files for non-English scripts.

This is a character-level aid to contextual language review. It does not decide
whether a Latin-script sentence is English and must not rewrite SEC source text.
Run from the repository root with explicit --path values.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess


TEXT_SUFFIXES = {".css", ".csv", ".html", ".js", ".json", ".md", ".py",
                 ".svg", ".tsv", ".txt"}
SCRIPT_RANGES = {
    "Arabic": ((0x0600, 0x06FF),),
    "Cyrillic": ((0x0400, 0x052F),),
    "Devanagari": ((0x0900, 0x097F),),
    "Han": ((0x3400, 0x9FFF), (0xF900, 0xFAFF), (0x20000, 0x2FA1F)),
    "Hangul": ((0x1100, 0x11FF), (0xAC00, 0xD7AF)),
    "Hebrew": ((0x0590, 0x05FF),),
    "Hiragana": ((0x3040, 0x309F),),
    "Katakana": ((0x30A0, 0x30FF),),
    "Thai": ((0x0E00, 0x0E7F),),
}


def non_english_script(character: str) -> str | None:
    """Return a script label for a character that warrants contextual review."""
    code = ord(character)
    for script, ranges in SCRIPT_RANGES.items():
        if any(low <= code <= high for low, high in ranges):
            return script
    return None


def scan_text(value: str, display_path: str) -> list[dict]:
    """Locate characters without deciding whether quoted source text is allowed."""
    issues = []
    for line_number, line in enumerate(value.splitlines(), 1):
        for column, character in enumerate(line, 1):
            script = non_english_script(character)
            if script:
                issues.append({
                    "path": display_path,
                    "line": line_number,
                    "column": column,
                    "script": script,
                    "code_point": f"U+{ord(character):04X}",
                    "context": line[max(0, column - 41):column + 39],
                })
    return issues


def selected_files(paths: list[Path], report_path: Path) -> list[Path]:
    """Expand only caller-selected files and supported text types."""
    files = set()
    for path in paths:
        if not path.exists():
            raise FileNotFoundError(f"Selected delivery path does not exist: {path}")
        candidates = path.rglob("*") if path.is_dir() else (path,)
        for candidate in candidates:
            if candidate.is_file() and candidate.suffix.lower() in TEXT_SUFFIXES:
                if candidate.resolve() != report_path.resolve():
                    files.add(candidate.resolve())
    return sorted(files)


def added_lines_since_head(path: Path, repository_root: Path) -> str:
    """Read only additions to a tracked legacy file, preserving old prose."""
    relative = path.resolve().relative_to(repository_root.resolve())
    result = subprocess.run(
        ["git", "diff", "--no-ext-diff", "--unified=0", "HEAD", "--", str(relative)],
        cwd=repository_root, capture_output=True, text=True, encoding="utf-8", check=True,
    )
    return "\n".join(line[1:] for line in result.stdout.splitlines()
                     if line.startswith("+") and not line.startswith("+++"))


def audit(paths: list[Path], report_path: Path, exceptions: list[str] | None = None,
          revised_paths: list[Path] | None = None,
          repository_root: Path | None = None) -> dict:
    """Return a reproducible scan report for new delivery paths only."""
    files = selected_files(paths, report_path)
    if not files:
        raise ValueError("No selected text delivery files were found")
    records = []
    issues = []
    for path in files:
        data = path.read_bytes()
        try:
            value = data.decode("utf-8")
        except UnicodeDecodeError as error:
            issues.append({"path": str(path), "type": "INVALID_UTF8", "detail": str(error)})
            continue
        records.append({"path": str(path), "bytes": len(data),
                        "sha256": hashlib.sha256(data).hexdigest()})
        issues.extend(scan_text(value, str(path)))
    revised_records = []
    for path in revised_paths or []:
        if not path.is_file():
            raise FileNotFoundError(f"Selected revised file does not exist: {path}")
        if repository_root is None:
            raise ValueError("repository_root is required for revised paths")
        additions = added_lines_since_head(path, repository_root)
        data = path.read_bytes()
        revised_records.append({"path": str(path.resolve()), "bytes": len(data),
                                "sha256": hashlib.sha256(data).hexdigest(),
                                "added_lines_checked": len(additions.splitlines())})
        issues.extend(scan_text(additions, str(path.resolve()) + " (added lines since HEAD)"))
    return {
        "schema_version": 1,
        "scope": "Explicitly selected new or revised M9 delivery files only",
        "files_checked": records,
        "revised_legacy_files_added_lines_checked": revised_records,
        "character_issues": issues,
        "character_issue_count": len(issues),
        "status": "NEEDS_CONTEXTUAL_REVIEW" if issues else "NO_NON_ENGLISH_SCRIPT_FOUND",
        "preserved_historical_or_source_exceptions": exceptions or [],
        "limitations": [
            "This scan does not identify non-English Latin-script prose.",
            "A source quotation is not automatically an authored-language defect.",
            "Only newly added lines are scanned in a selected revised legacy file.",
            "Contextual inspection of visible pages, descriptions, and generated messages is still required.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", action="append", required=True, type=Path,
                        help="A new or revised delivery file or directory; repeat as needed")
    parser.add_argument("--revised-path", action="append", default=[], type=Path,
                        help="A tracked legacy file; scan only lines added since HEAD")
    parser.add_argument("--repository-root", type=Path, default=Path.cwd(),
                        help="Git repository root for --revised-path")
    parser.add_argument("--report", required=True, type=Path,
                        help="Write the English JSON character-scan report here")
    parser.add_argument("--exception", action="append", default=[],
                        help="Describe an immutable historical or quoted source exclusion")
    args = parser.parse_args()
    result = audit(args.path, args.report, args.exception,
                   args.revised_path, args.repository_root)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Checked {len(result['files_checked'])} files; "
          f"found {result['character_issue_count']} characters for contextual review.")
    print(f"Report: {args.report}")


if __name__ == "__main__":
    main()
