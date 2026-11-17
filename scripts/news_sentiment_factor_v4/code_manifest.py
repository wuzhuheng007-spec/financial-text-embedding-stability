"""Hash actual v4 implementation and public inputs without exposing private data."""
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .preflight import ROOT, PUBLIC, save, sha

FILES = [
    "configs/news_sentiment_factor_v4_20261117.json",
    "scripts/news_sentiment_factor_v4/preflight.py",
    "scripts/news_sentiment_factor_v4/probe.py",
    "scripts/news_sentiment_factor_v4/store.py",
    "scripts/news_sentiment_factor_v4/score.py",
    "scripts/news_sentiment_factor_v4/observe.py",
    "scripts/news_sentiment_factor_v4/freeze_review.py",
    "scripts/news_sentiment_factor_v4/finalize.py",
    "scripts/news_sentiment_factor_v4/migrate_response_time.py",
    "scripts/news_sentiment_factor_v4/code_manifest.py",
    "apps/quant_portal/core/views.py",
    "apps/quant_portal/core/urls.py",
    "apps/quant_portal/templates/core/dashboard.html",
    "apps/quant_portal/static/core/research_sentiment_v4.js",
    "apps/quant_portal/static/core/portal.css",
    "tests/test_news_sentiment_factor_v4.py",
    "tests/news_sentiment_v4_model_smoke.py",
    "apps/quant_portal/core/test_research_sentiment_v4.py",
    "results/news_sentiment_factor_v4_20261117/preflight_redacted.json",
    "results/news_sentiment_factor_v4_20261117/actual_summary_redacted.json",
]


def main():
    now = datetime.now(timezone.utc)
    result = {"schema": "news_sentiment_factor_v4_code_manifest_1",
              "generated_at_utc": now.isoformat(),
              "generated_at_asia_shanghai": now.astimezone(ZoneInfo("Asia/Shanghai")).isoformat(),
              "head_before_v4_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
              "files_sha256": {name: sha(ROOT / name) for name in FILES},
              "working_tree_diff_sha256_before_manifest_write": __import__("hashlib").sha256(
                  subprocess.check_output(["git", "diff", "HEAD", "--binary"], cwd=ROOT)).hexdigest(),
              "old_v3_artifacts_reused_as_data": False}
    save(PUBLIC / "code_manifest_redacted.json", result)
    return result


if __name__ == "__main__":
    print(json.dumps(main(), ensure_ascii=False))
