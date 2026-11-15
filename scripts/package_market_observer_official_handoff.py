"""Package the reviewed public official-corpus commit for a GPT handoff."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ALLOWED = (
    "README.md", "configs/market_observer_official_", "docs/news_research/README.md",
    "docs/news_research/STATUS.json", "docs/news_research/CHANGELOG.md",
    "reports/2026-11-15_market_observer_official_v1.",
    "results/market_observer_official_20261115/",
    "results/market_observer_historical_official_20261115/",
    "scripts/market_observer_official_v1/",
    "scripts/market_observer_historical_v1/audit_price_time.py",
    "scripts/package_market_observer_official_handoff.py",
    "tests/test_market_observer_official_",
)
PRIVATE_NAMES = ("artifacts/", "local_only", "private", ".sqlite", ".db", "credential", "token")
PRIVATE_CONTENT = (
    re.compile(rb"hf_[A-Za-z0-9]{20,}"),
    re.compile(rb"Bearer\s+[A-Za-z0-9._-]{20,}", re.I),
    re.compile(rb'"(?:access_token|refresh_token|password)"\s*:\s*"[^" ]{8,}"', re.I),
    re.compile(rb"C:[/\\]Users[/\\][^/\\\s]+[/\\]", re.I),
)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def git_bytes(commit: str, name: str) -> bytes:
    return subprocess.check_output(["git", "show", f"{commit}:{name}"], cwd=ROOT)


def build(base: str, commit: str, out_dir: Path, zip_path: Path) -> dict:
    if out_dir.exists() or zip_path.exists():
        raise FileExistsError("destination exists; earlier handoff is preserved")
    full_commit = subprocess.check_output(["git", "rev-parse", commit], cwd=ROOT,
                                          text=True).strip()
    full_base = subprocess.check_output(["git", "rev-parse", base], cwd=ROOT,
                                        text=True).strip()
    paths = subprocess.check_output(
        ["git", "diff", "--name-only", full_base, full_commit],
        cwd=ROOT, text=True).splitlines()
    if not paths:
        raise ValueError("commit contains no handoff files")
    for name in paths:
        lower = name.lower()
        if not any(name == prefix or name.startswith(prefix) for prefix in ALLOWED):
            raise ValueError(f"unreviewed path: {name}")
        if any(term in lower for term in PRIVATE_NAMES):
            raise ValueError(f"private-looking path: {name}")
    out_dir.mkdir(parents=True)
    records = []
    for name in paths:
        payload = git_bytes(full_commit, name)
        if any(pattern.search(payload) for pattern in PRIVATE_CONTENT):
            raise ValueError(f"credential or personal path pattern in {name}")
        target = out_dir / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
        records.append({"path": name, "bytes": len(payload), "sha256": sha(payload)})
    readme = (
        "# Official announcement development handoff\n\n"
        "Executed 2026-11-15, Asia/Shanghai. Read the Chinese or English official corpus "
        "report first, then `results/market_observer_official_20261115/plan_execution.csv`, "
        "the redacted corpus/price/text receipts, frozen configs and code.\n\n"
        "This is a retrospective announcement-date-proxy development comparison on "
        "reused train/validation data. Strict historical availability remains unproved; "
        "June-July 2026 was previously exposed and is not re-scored here. "
        "No formal M5, live prediction, model fine-tuning or trading result is claimed.\n\n"
        "Raw SEC/FOMC documents, private prices and predictions, source account material "
        "and credentials are absent.\n\n"
        f"Git branch range: {full_base}..{full_commit}.\n"
    ).encode("utf-8")
    (out_dir / "HANDOFF_README.md").write_bytes(readme)
    records.append({"path": "HANDOFF_README.md", "bytes": len(readme), "sha256": sha(readme)})
    manifest = json.dumps({"schema": "official_handoff_manifest_v1", "commit": full_commit,
                           "files": records}, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"
    (out_dir / "MANIFEST_SHA256.json").write_bytes(manifest)
    with zipfile.ZipFile(zip_path, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(out_dir.rglob("*")):
            if path.is_file():
                archive.write(path, f"{out_dir.name}/{path.relative_to(out_dir).as_posix()}")
    with zipfile.ZipFile(zip_path) as archive:
        bad = archive.testzip()
        if bad is not None:
            raise ValueError(f"ZIP CRC failed: {bad}")
    return {"folder": str(out_dir), "zip": str(zip_path), "zip_sha256": sha(zip_path.read_bytes()),
            "files": len(records) + 1, "crc": "PASS", "base": full_base,
            "commit": full_commit}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--zip", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.base, args.commit, args.out_dir.resolve(), args.zip.resolve()),
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
