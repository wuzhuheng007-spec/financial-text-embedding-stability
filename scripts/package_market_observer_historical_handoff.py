"""Package only the reviewed public historical-observer commit files for GPT."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PUBLICATION_COMMIT = "24826168f8048edea41de65eb91cfeca41d68f15"
ALLOWED_PREFIXES = ("configs/", "docs/news_research/", "reports/", "results/market_observer_historical_20261115/",
                    "scripts/market_observer_historical_v1/", "scripts/market_observer_v1/", "tests/")
PRIVATE_PATTERNS = (re.compile(rb"hf_[A-Za-z0-9]{20,}"),
                    re.compile(rb"Bearer\s+[A-Za-z0-9._-]{20,}", re.I),
                    re.compile(rb'"(?:access_token|refresh_token)"\s*:\s*"[^" ]{12,}"', re.I))


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build(out_dir: Path, zip_path: Path) -> dict:
    if out_dir.exists() or zip_path.exists():
        raise FileExistsError("handoff destination already exists; keep prior package intact")
    paths = subprocess.check_output(
        ["git", "diff-tree", "--no-commit-id", "--name-only", "-r", PUBLICATION_COMMIT],
        cwd=ROOT, text=True).splitlines()
    if not paths:
        raise ValueError("publication commit has no files")
    for name in paths:
        if name != "README.md" and not name.startswith(ALLOWED_PREFIXES):
            raise ValueError(f"unreviewed path in publication: {name}")
        if name.startswith("artifacts/") or any(tag in name.lower() for tag in (
                "local_only", ".sqlite", ".db", "credential", "token")):
            raise ValueError(f"private-looking path in publication: {name}")
    out_dir.mkdir(parents=True)
    manifest = []
    for name in paths:
        source = (ROOT / name).resolve()
        if not source.is_file() or not source.is_relative_to(ROOT.resolve()):
            raise ValueError(f"missing or escaping source: {name}")
        payload = source.read_bytes()
        if any(pattern.search(payload) for pattern in PRIVATE_PATTERNS):
            raise ValueError(f"credential-like content: {name}")
        target = out_dir / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        manifest.append({"path": name.replace("\\", "/"), "size": target.stat().st_size,
                         "sha256": sha(target)})
    readme = out_dir / "HANDOFF_README.md"
    readme.write_text(
        "# Historical market observer handoff\n\n"
        "Executed 2026-11-15, Asia/Shanghai. Read in this order:\n\n"
        "1. `reports/2026-11-15_market_observer_historical_v1.zh.md` or `.en.md`.\n"
        "2. `results/market_observer_historical_20261115/plan_execution.csv`.\n"
        "3. Price, news, alignment, v3 model and contamination redacted receipts in that results folder.\n"
        "4. Frozen config, then code and focused synthetic tests.\n\n"
        "The three pilot securities have real historical daily bars and a retrospective A/B price baseline. "
        "News-augmented C, formal M5, independent test and trading are NOT_RUN or not established. "
        "The June-July interval was previously exposed. Raw market/news data, credentials and private row predictions are excluded.\n\n"
        f"Main Git publication: {PUBLICATION_COMMIT}. This local package includes the final updated P7 ledger.\n",
        encoding="utf-8")
    manifest.append({"path": "HANDOFF_README.md", "size": readme.stat().st_size,
                     "sha256": sha(readme)})
    index = out_dir / "MANIFEST_SHA256.json"
    index.write_text(json.dumps({"schema": "historical_handoff_manifest_v1", "files": manifest},
                                indent=2) + "\n", encoding="utf-8")
    with zipfile.ZipFile(zip_path, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(out_dir.rglob("*")):
            if path.is_file():
                archive.write(path, f"{out_dir.name}/{path.relative_to(out_dir).as_posix()}")
    with zipfile.ZipFile(zip_path) as archive:
        bad = archive.testzip()
        if bad is not None:
            raise ValueError(f"ZIP CRC failure: {bad}")
        count = len(archive.namelist())
    return {"folder": str(out_dir), "zip": str(zip_path), "zip_sha256": sha(zip_path),
            "included_files": count, "crc": "PASS"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--zip", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.out_dir.resolve(), args.zip.resolve()), ensure_ascii=False))


if __name__ == "__main__":
    main()
