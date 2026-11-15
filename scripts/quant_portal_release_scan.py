"""Location-only scan for release review; never emit matched values."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path


PATTERNS = {
    "hf_token_shape": re.compile(rb"hf_[A-Za-z0-9]{20,}"),
    "cloud_access_key_shape": re.compile(rb"(?:AKIA|ASIA)[A-Z0-9]{16}"),
    "private_home_path": re.compile(rb"(?:[A-Za-z]:[\\/]Users[\\/][^\\/\r\n]+[\\/]|/Users/[^/\r\n]+/)", re.I),
    "credential_assignment_shape": re.compile(
        rb"(?i)(?:bearer[_-]?token|api[_-]?key|client[_-]?secret|password)\s*[=:]\s*['\"]?[A-Za-z0-9_./+-]{20,}"
    ),
}
SENSITIVE_NAMES = re.compile(
    r"(?i)(?:^|[/\\])(?:\.env$|[^/\\]*\.(?:sqlite3?|db|pem|key)$|"
    r"(?:oauth|token|credentials?)(?:[_.-][^/\\]*)?$)"
)
MAX_BLOB_BYTES = 5_000_000
PRIVATE_DIRS = {"var", ".venv", "backups", "load_results", "staticfiles", "__pycache__"}
INTENDED_RELEASE_ROOTS = (
    Path("apps/quant_portal"),
    Path("docs/quant_portal"),
    Path("docs/deployment/quant_portal"),
    Path("results/quant_portal_20261116"),
)
INTENDED_RELEASE_FILES = (
    Path("configs/quant_portal_release_v1.json"),
    Path("reports/2026-11-16_quant_portal_personal.en.md"),
    Path("reports/2026-11-16_quant_portal_personal.zh.md"),
    Path("scripts/quant_portal_release_scan.py"),
)


def git(*args: str) -> bytes:
    return subprocess.check_output(["git", *args], stderr=subprocess.DEVNULL)


def scan_blob(blob: bytes) -> list[str]:
    return [name for name, pattern in PATTERNS.items() if pattern.search(blob)]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--include-worktree", action="store_true")
    args = parser.parse_args()

    findings: list[dict[str, str]] = []
    seen: set[str] = set()
    skipped_large = 0
    history = git("rev-list", "--objects", "--all").decode("utf-8", "replace").splitlines()
    with subprocess.Popen(
        ["git", "cat-file", "--batch"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    ) as batch:
        assert batch.stdin and batch.stdout
        for line in history:
            object_id, _, path = line.partition(" ")
            if not path or object_id in seen:
                continue
            seen.add(object_id)
            batch.stdin.write((object_id + "\n").encode("ascii"))
            batch.stdin.flush()
            header = batch.stdout.readline().decode("ascii", "replace").split()
            if len(header) != 3:
                continue
            object_type, size = header[1], int(header[2])
            blob = batch.stdout.read(size)
            batch.stdout.read(1)  # terminating newline
            if object_type != "blob":
                continue
            if size > MAX_BLOB_BYTES:
                skipped_large += 1
                continue
            for category in scan_blob(blob):
                findings.append({"scope": "reachable_history", "path": path, "blob_id": object_id[:12], "category": category})
            if SENSITIVE_NAMES.search(path):
                findings.append({"scope": "reachable_history", "path": path, "blob_id": object_id[:12], "category": "sensitive_filename_review"})

    if args.include_worktree:
        paths = git("ls-files", "-z").decode("utf-8", "replace").split("\0")
        paths += [
            str(p).replace("\\", "/")
            for root in INTENDED_RELEASE_ROOTS
            for p in root.rglob("*")
            if p.is_file() and not PRIVATE_DIRS.intersection(p.parts)
            and p.name != ".env" and (not p.name.startswith(".env.") or p.name == ".env.example")
        ]
        paths += [str(p).replace("\\", "/") for p in INTENDED_RELEASE_FILES]
        for path in sorted(set(paths)):
            if not path:
                continue
            file = Path(path)
            if not file.is_file():
                continue
            if file.stat().st_size > MAX_BLOB_BYTES:
                skipped_large += 1
                continue
            try:
                blob = file.read_bytes()
            except OSError:
                continue
            for category in scan_blob(blob):
                findings.append({"scope": "worktree", "path": path, "category": category})
            if SENSITIVE_NAMES.search(path):
                findings.append({"scope": "worktree", "path": path, "category": "sensitive_filename_review"})

    receipt = {
        "schema_version": "location_only_release_scan_v1",
        "scope": "reachable Git objects and optionally tracked plus app worktree files",
        "values_emitted": False,
        "unique_history_objects_seen": len(seen),
        "large_blobs_skipped": skipped_large,
        "findings": findings,
        "note": "Pattern matches are review leads, not verified credential leaks; this scan cannot prove the absence of secrets.",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"findings": len(findings), "large_blobs_skipped": skipped_large, "output": str(args.output)}))


if __name__ == "__main__":
    main()
