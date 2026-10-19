"""Offline diagnostics; intentionally makes no SEC HTTP request."""
from __future__ import annotations

import importlib.metadata
import os
import platform
import socket
import sys

from .config import Config


def inspect(config: Config, offline: bool = True) -> dict:
    packages = {}
    for name in ("PyYAML", "pytest", "setuptools", "numpy", "onnxruntime", "tokenizers", "huggingface_hub"):
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = None
    gpu = "unavailable"
    try:
        import subprocess
        p = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"],
                           capture_output=True, text=True, timeout=5, check=False)
        if p.returncode == 0:
            gpu = p.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    report = {
        "offline": offline,
        "os": platform.platform(),
        "python": sys.version.split()[0],
        "cpu_logical_count": os.cpu_count(),
        "gpu": gpu,
        "dependencies": packages,
        "env_present": {"SEC_USER_AGENT": bool(os.environ.get("SEC_USER_AGENT"))},
        "config_sha256": config.source_sha256,
        "config_first_ticker": config.ticker,
        "config_as_of": config.as_of.isoformat(),
        "network": "not tested by offline doctor" if offline else _network_status(),
    }
    return report


def _network_status() -> str:
    try:
        with socket.create_connection(("data.sec.gov", 443), timeout=5):
            return "data.sec.gov TCP 443 reachable; SEC HTTP access untested"
    except OSError as exc:
        return f"data.sec.gov TCP 443 unavailable: {type(exc).__name__}"

