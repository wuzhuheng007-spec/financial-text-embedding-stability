"""The web image is built from an allowlisted context. The portal's X stance view has to work from exactly the
files that list lets through, with numpy and scipy absent; otherwise the live panel can only say reading failed."""
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
FILES = [f"scripts/x_stance_v1/{n}.py" for n in ("__init__", "config", "store", "portal_read", "ledger", "batch", "provider", "cost", "sessions", "vocab", "snapshots", "stance")]
# what the web view imports (reading the page, and saving a keyword edit): standard library only
CODE = "\n".join([
    "import sys",
    "sys.path.insert(0, '.')",
    "sys.modules['numpy'] = None",
    "sys.modules['scipy'] = None",
    "from scripts.x_stance_v1 import config, portal_read",
    "v = portal_read.read_for_portal(config.load(), env={})",
    "print(v['has_data'], v['collection']['state'], len(v['settings']['topics']))",
])


def test_the_allowlist_carries_the_package_its_config_and_calendar():
    """The same image runs the web view and the scheduled X job, so the whole package is allowed in; the image has
    numpy but no scipy, and nothing in the package may need scipy."""
    allow = (REPO / "deploy" / "linux" / "Dockerfile.dockerignore").read_text(encoding="utf-8").splitlines()
    for line in ("!scripts/x_stance_v1/**", "!research/x_stance_v1/config/*.json", "!research/x_stance_v1/calendar/*"):
        assert line in allow
    docker = (REPO / "deploy" / "linux" / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY research/x_stance_v1/config /app/research/x_stance_v1/config" in docker and "COPY research/x_stance_v1/calendar /app/research/x_stance_v1/calendar" in docker
    for f in (REPO / "scripts" / "x_stance_v1").glob("*.py"):
        src = f.read_text(encoding="utf-8")
        assert "import scipy" not in src and "from scipy" not in src, f.name


def test_the_read_model_runs_from_those_files_alone(tmp_path):
    for f in FILES:
        (tmp_path / f).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(REPO / f, tmp_path / f)
    (tmp_path / "scripts" / "__init__.py").write_text("", encoding="utf-8")
    shutil.copytree(REPO / "research" / "x_stance_v1" / "config", tmp_path / "research" / "x_stance_v1" / "config")
    r = subprocess.run([sys.executable, "-I", "-c", CODE], cwd=tmp_path, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr[-400:]
    assert r.stdout.split() == ["False", "NOT_STARTED", "4"]
