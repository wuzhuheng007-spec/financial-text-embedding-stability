"""End to end, synthetic input: the real pipeline builds a store from made-up posts and labels (the `built` fixture of
test_x_stance_day_view), the real read model reads it, and the page renderer runs on that payload in node
(tests/x_stance_ui.cjs, the X_STANCE_UI_PAYLOAD case). Nothing here touches a provider, a model or real posts."""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from scripts.x_stance_v1 import config as C, portal_read
from test_x_stance_day_view import built  # noqa: F401  (pytest fixture)

REPO = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_page_renders_a_pipeline_built_payload(built, tmp_path):  # noqa: F811
    payload = portal_read.read_for_portal(C.load(), built, env={})
    assert payload["has_data"] and any(r["topic_id"] == "qqq" for r in payload["daily"])
    p, rep = tmp_path / "payload.json", tmp_path / "report.json"
    p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    env = {**os.environ, "X_STANCE_UI_PAYLOAD": str(p), "X_STANCE_UI_REPORT": str(rep)}
    r = subprocess.run(["node", "--test", "--test-name-pattern=pipeline-built", str(REPO / "tests" / "x_stance_ui.cjs")], env=env, capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-2000:]
    checks = json.loads(rep.read_text(encoding="utf-8"))["checks"]
    assert checks and all(checks.values()), checks
