"""feed_personal.ps1 profiles and the Jin10-only Windows config (no supplier calls).

The PowerShell tests copy the real script into a throw-away repository whose
``scripts.quant_personal_feed.cli`` is a stand-in honouring the same pid-file and
stop-file contract, so real Start-Process / Win32_Process checks run without
touching the production checkout, its cache or any supplier.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
FULL = "configs/quant_portal_personal_feeds_v11_20261116.json"
JIN10_ONLY = "configs/quant_portal_personal_feeds_jin10_only_v12_20261118.json"
LEGACY = "configs/quant_portal_personal_feeds_v1.json"


def test_jin10_only_config_differs_from_full_only_in_moomoo_enabled() -> None:
    full = json.loads((ROOT / FULL).read_text(encoding="utf-8"))
    only = json.loads((ROOT / JIN10_ONLY).read_text(encoding="utf-8"))
    assert only["moomoo"]["enabled"] is False and only["jin10"]["enabled"] is True
    assert only["moomoo"]["snapshot_enabled"] is False
    full.pop("_comment", None); only.pop("_comment", None)
    full["moomoo"].pop("enabled"); only["moomoo"].pop("enabled")
    assert full == only  # same cache_db, owner, retention, Jin10 budget


def test_cli_accepts_config_and_run_launches_only_jin10(tmp_path: Path) -> None:
    from scripts.quant_personal_feed import cli
    config = cli.load_config(ROOT / JIN10_ONLY)
    launched: list[str] = []

    class FakeStore:
        def __init__(self, *a, **k): pass
        def ensure_selection(self, *a): pass
        def close(self): pass
        def state(self, *a, **k): pass
        def provider_metric(self, *a, **k): pass

    def target(name):
        def run(cfg, user, stop):
            launched.append(name)
        return run

    # cli.run with its var directory redirected to tmp and every provider target replaced.
    with patch.object(cli, "owner_id", lambda cfg: 1), \
         patch.object(cli, "PersonalFeedStore", FakeStore), \
         patch.object(cli, "run_moomoo", target("moomoo")), \
         patch.object(cli, "recover_jin10_sqlite", target("jin10")), \
         patch.object(cli, "recover_snapshot_sqlite", target("moomoo_snapshot")), \
         patch.object(cli.signal, "signal", lambda *a: None), \
         patch.object(cli, "ROOT", tmp_path):
        (tmp_path / "apps/quant_portal").mkdir(parents=True)
        stopper = threading.Timer(1.0, lambda: (tmp_path / "apps/quant_portal/var/personal_feed.stop").write_text("stop"))
        stopper.start()
        with patch.object(cli, "load_config", lambda p: config):
            cli.run(ROOT / JIN10_ONLY)
        stopper.cancel()
    assert launched == ["jin10"]


FAKE_CLI = r'''
import argparse, json, os, sys, time
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
var = ROOT / "apps/quant_portal/var"
p = argparse.ArgumentParser(); p.add_argument("action"); p.add_argument("--config")
a = p.parse_args()
var.mkdir(parents=True, exist_ok=True)
if a.action == "run":
    (var / "personal_feed.stop").unlink(missing_ok=True)
    (var / "personal_feed.pid.json").write_text(json.dumps({"pid": os.getpid()}))
    while not (var / "personal_feed.stop").exists():
        time.sleep(0.2)
    (var / "personal_feed.pid.json").unlink(missing_ok=True); (var / "personal_feed.stop").unlink(missing_ok=True)
elif a.action == "stop":
    (var / "personal_feed.stop").write_text("stop"); print("STOP_REQUESTED")
else:
    print(json.dumps({"fake_status_config": Path(a.config).name}))
'''


@pytest.fixture()
def fake_repo(tmp_path: Path):
    if os.name != "nt":
        pytest.skip("feed_personal.ps1 is the Windows entry point")
    repo = tmp_path / "repo"
    (repo / "apps/quant_portal/ops").mkdir(parents=True)
    shutil.copy(ROOT / "apps/quant_portal/ops/feed_personal.ps1", repo / "apps/quant_portal/ops/feed_personal.ps1")
    for rel in (FULL, JIN10_ONLY, LEGACY):
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text("{}")
    pkg = repo / "scripts/quant_personal_feed"
    pkg.mkdir(parents=True)
    (repo / "scripts/__init__.py").write_text(""); (pkg / "__init__.py").write_text("")
    (pkg / "cli.py").write_text(FAKE_CLI)
    env = {**os.environ, "PATH": str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"]}

    log = tmp_path / "ps_out.txt"

    def ps(*args: str) -> tuple[int, str]:
        # Output goes to a file, not a pipe: Start-Process children inherit the
        # parent's handles, so a pipe would stay open while the collector runs.
        with open(log, "wb") as handle:
            p = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                                str(repo / "apps/quant_portal/ops/feed_personal.ps1"), *args],
                               stdin=subprocess.DEVNULL, stdout=handle, stderr=subprocess.STDOUT,
                               timeout=120, env=env)
        return p.returncode, log.read_bytes().decode("utf-8", "replace").strip()

    yield repo, ps
    ps("stop")


def _cmdline(pid: int) -> str:
    out = subprocess.run(["powershell", "-NoProfile", "-Command",
                          f"(Get-CimInstance Win32_Process -Filter 'ProcessId={pid}').CommandLine"],
                         capture_output=True, text=True, timeout=60).stdout
    return out.strip()


def _pid(text: str) -> int:
    return int(text.split("pid=")[1].split()[0].rstrip(";"))


def test_default_profile_is_unchanged_full(fake_repo) -> None:
    repo, ps = fake_repo
    code, out = ps("start")
    assert code == 0 and out.startswith("FEED_STARTED") and "profile=full" in out
    assert FULL.split("/")[1] in _cmdline(_pid(out))
    code, out = ps("status")
    assert out.splitlines()[0].startswith("FEED_RUNNING ") and "profile=full" in out
    assert '"fake_status_config": "quant_portal_personal_feeds_v11_20261116.json"' in out
    code, out = ps("stop")
    assert code == 0 and out.endswith("FEED_STOPPED profile=full")


def test_switch_full_to_jin10_only_never_runs_two_writers(fake_repo) -> None:
    repo, ps = fake_repo
    code, out = ps("start"); first = _pid(out)
    code, out = ps("start", "-FeedProfile", "jin10_only")
    assert code == 3 and out.startswith("FEED_RUNNING_OTHER_PROFILE") and "profile=full" in out
    code, out = ps("status")
    assert _pid(out) == first  # still the one original writer
    code, out = ps("stop")  # no profile needed: stop follows the running one
    assert out.endswith("FEED_STOPPED profile=full")
    code, out = ps("start", "-FeedProfile", "jin10_only")
    assert code == 0 and "profile=jin10_only" in out
    assert "jin10_only_v12_20261118.json" in _cmdline(_pid(out))
    code, out = ps("status")
    assert "profile=jin10_only" in out and '"fake_status_config": "quant_portal_personal_feeds_jin10_only_v12_20261118.json"' in out
    code, out = ps("start", "-FeedProfile", "jin10_only")
    assert code == 0 and out.startswith("FEED_ALREADY_RUNNING")
    code, out = ps("stop")
    assert out.endswith("FEED_STOPPED profile=jin10_only")
    code, out = ps("start")  # rollback R5: back to the full profile
    assert code == 0 and "profile=full" in out


def test_legacy_v1_is_recognised_but_not_startable(fake_repo) -> None:
    repo, ps = fake_repo
    code, out = ps("start", "-FeedProfile", "legacy_v1")
    assert code != 0 and "legacy_v1" in out  # ValidateSet rejects it
    var = repo / "apps/quant_portal/var"; var.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen([sys.executable, "-m", "scripts.quant_personal_feed.cli", "run", "--config",
                             str(repo / LEGACY)], cwd=repo)
    try:
        for _ in range(50):
            if (var / "personal_feed.pid.json").exists(): break
            time.sleep(0.1)
        code, out = ps("status")
        assert "profile=legacy_v1" in out
        code, out = ps("start", "-FeedProfile", "jin10_only")
        assert code == 3 and "profile=legacy_v1" in out
    finally:
        ps("stop"); proc.wait(30)


def test_unknown_config_process_is_untracked(fake_repo) -> None:
    repo, ps = fake_repo
    var = repo / "apps/quant_portal/var"; var.mkdir(parents=True, exist_ok=True)
    other = repo / "configs/some_other.json"; other.write_text("{}")
    proc = subprocess.Popen([sys.executable, "-m", "scripts.quant_personal_feed.cli", "run", "--config", str(other)], cwd=repo)
    try:
        for _ in range(50):
            if (var / "personal_feed.pid.json").exists(): break
            time.sleep(0.1)
        code, out = ps("status")
        assert out.splitlines()[0] == "FEED_STOPPED_OR_UNTRACKED"
    finally:
        (var / "personal_feed.stop").write_text("stop"); proc.wait(30)
