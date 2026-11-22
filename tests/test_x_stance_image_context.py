"""The web image is built from an allowlisted context. The portal's X stance view has to work from exactly the
files that list lets through, with numpy and scipy absent; otherwise the live panel can only say reading failed."""
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
FILES = ["scripts/x_stance_v1/__init__.py", "scripts/x_stance_v1/config.py", "scripts/x_stance_v1/store.py", "scripts/x_stance_v1/portal_read.py"]
CODE = "\n".join([
    "import sys",
    "sys.path.insert(0, '.')",
    "sys.modules['numpy'] = None",
    "sys.modules['scipy'] = None",
    "from scripts.x_stance_v1 import config, portal_read",
    "v = portal_read.read_for_portal(config.load(), env={})",
    "print(v['has_data'], v['collection']['state'], len(v['settings']['topics']))",
])


def test_the_allowlist_carries_the_read_model_and_its_config_only():
    allow = (REPO / "deploy" / "linux" / "Dockerfile.dockerignore").read_text(encoding="utf-8").splitlines()
    for f in FILES:
        assert f"!{f}" in allow
    assert "!research/x_stance_v1/config/*.json" in allow
    assert "!scripts/x_stance_v1/**" not in allow                                  # the collector and the numpy code stay out of the web image
    docker = (REPO / "deploy" / "linux" / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY research/x_stance_v1/config /app/research/x_stance_v1/config" in docker


def test_the_read_model_runs_from_those_files_alone(tmp_path):
    for f in FILES:
        (tmp_path / f).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(REPO / f, tmp_path / f)
    (tmp_path / "scripts" / "__init__.py").write_text("", encoding="utf-8")
    shutil.copytree(REPO / "research" / "x_stance_v1" / "config", tmp_path / "research" / "x_stance_v1" / "config")
    r = subprocess.run([sys.executable, "-I", "-c", CODE], cwd=tmp_path, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr[-400:]
    assert r.stdout.split() == ["False", "NOT_STARTED", "4"]
