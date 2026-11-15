"""Fail publication if reviewed staged files contain private Jin10 rows or secrets."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.jin10_mcp.engine import unwrap_mcp_payload


ROOT = Path(__file__).resolve().parents[1]
PRIVATE_ROOT = ROOT / "artifacts"
PUBLIC_ROOT = ROOT / "results/news_jin10_20261115"
SECRET_PATTERNS = (
    re.compile(r"hf_[A-Za-z0-9]{20,}"),
    re.compile(r"Bearer\s+[A-Za-z0-9._-]{24,}", re.I),
)


def staged_paths() -> list[Path]:
    result = subprocess.run(["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"],
                            cwd=ROOT, check=True, capture_output=True, text=True)
    return [ROOT / line for line in result.stdout.splitlines() if line]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture-dir", type=Path, required=True)
    parser.add_argument("--public-receipt", type=Path, required=True)
    args = parser.parse_args()
    captures = args.capture_dir.resolve()
    receipt = args.public_receipt.resolve()
    if not captures.is_relative_to(PRIVATE_ROOT) or not receipt.is_relative_to(PUBLIC_ROOT):
        raise ValueError("Capture and receipt locations are fixed")
    if receipt.exists():
        raise FileExistsError(receipt)
    flashes = json.loads((captures / "flash_captures.json").read_text(encoding="utf-8"))
    calendars = json.loads((captures / "calendar_captures.json").read_text(encoding="utf-8"))
    flash_bodies = [item.get("content") for capture in flashes
                    for item in unwrap_mcp_payload(capture["response"])[0]["data"]["items"]]
    calendar_rows = [item for capture in calendars
                     for item in unwrap_mcp_payload(capture["response"])[0]["data"]]
    paths = staged_paths()
    if not paths:
        raise ValueError("No staged files to review")
    bad_paths = [str(path.relative_to(ROOT)) for path in paths
                 if path.is_relative_to(PRIVATE_ROOT) or "local_only" in path.parts or
                 path.suffix.lower() in {".sqlite", ".db"}]
    body_leaks = row_leaks = pattern_hits = 0
    for path in paths:
        text = path.read_text(encoding="utf-8", errors="replace")
        body_leaks += sum(bool(body and len(body) >= 20 and body in text) for body in flash_bodies)
        row_leaks += sum(json.dumps(row, ensure_ascii=False, sort_keys=True) in text for row in calendar_rows)
        pattern_hits += sum(len(pattern.findall(text)) for pattern in SECRET_PATTERNS)
    if bad_paths or body_leaks or row_leaks or pattern_hits:
        raise AssertionError("Staged-file privacy check failed; inspect privately before publication")
    result = {
        "schema": "jin10_staged_privacy_review_v1",
        "staged_files_scanned": len(paths),
        "flash_bodies_compared": len(flash_bodies),
        "calendar_rows_compared": len(calendar_rows),
        "flash_body_matches": body_leaks,
        "complete_calendar_row_matches": row_leaks,
        "credential_pattern_hits": pattern_hits,
        "private_or_database_paths_staged": bad_paths,
        "result": "PASS",
    }
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                       encoding="utf-8", newline="\n")
    print(json.dumps({"result": "PASS", "staged_files_scanned": len(paths),
                      "flash_body_matches": body_leaks,
                      "complete_calendar_row_matches": row_leaks,
                      "credential_pattern_hits": pattern_hits}))


if __name__ == "__main__":
    main()
