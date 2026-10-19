"""Run bookkeeping. No secret values are written to disk."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import importlib.metadata
import hashlib
import json
import platform
import subprocess
import sys
import uuid

from .config import Config


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def git_state(root: Path) -> dict:
    def run(*args: str) -> str | None:
        p = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=False)
        return p.stdout.strip() if p.returncode == 0 else None
    return {"head": run("rev-parse", "HEAD"), "status_porcelain": run("status", "--porcelain")}


class Run:
    def __init__(self, config: Config, kind: str):
        self.config = config
        self.path = config.artifacts / "runs" / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8])
        self.path.mkdir(parents=True, exist_ok=False)
        self.manifest = {
            "schema_version": 1,
            "kind": kind,
            "status": "RUNNING",
            "started_utc": datetime.now(timezone.utc).isoformat(),
            "config_path": str(config.source),
            "config_sha256": config.source_sha256,
            "as_of": config.as_of.isoformat(),
            "first_ticker": config.ticker,
            "python": sys.version.split()[0],
            "dependencies": {
                name: _installed_version(name)
                for name in ("PyYAML", "numpy", "onnxruntime", "tokenizers", "huggingface_hub")
            },
            "platform": platform.platform(),
            "git": git_state(config.root),
            "source_files_sha256": {
                str(p.relative_to(config.root)).replace("\\", "/"): sha256(p.read_bytes())
                for p in sorted((config.root / "src" / "finembed").glob("*.py"))
            },
            "inputs": [],
            "outputs": [],
        }
        self.save()

    def log(self, event: str, **fields: object) -> None:
        record = {"time_utc": datetime.now(timezone.utc).isoformat(), "event": event, **fields}
        with (self.path / "events.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")

    def save(self) -> None:
        write_json(self.path / "manifest.json", self.manifest)

    def input(self, path: Path, source_url: str | None = None) -> None:
        self.manifest["inputs"].append({"path": str(path), "sha256": sha256(path.read_bytes()), "source_url": source_url})
        self.save()

    def output(self, path: Path) -> None:
        self.manifest["outputs"].append({"path": str(path), "sha256": sha256(path.read_bytes()), "bytes": path.stat().st_size})
        self.save()

    def finish(self, status: str, reason: str | None = None) -> None:
        self.manifest["status"] = status
        self.manifest["finished_utc"] = datetime.now(timezone.utc).isoformat()
        if reason:
            self.manifest["reason"] = reason
        self.log("run_finished", status=status, reason=reason)
        self.save()


def _installed_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None

