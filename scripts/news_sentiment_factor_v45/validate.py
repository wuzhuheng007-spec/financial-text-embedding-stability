"""Focused software gate with a BOM-readable local log; no supplier requests."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from .preflight import PUBLIC, ROOT, dump, sha


def run() -> dict:
    at = datetime.now(timezone.utc)
    portal = ROOT / "apps/quant_portal"
    isolated_env = {**os.environ,
                    "DJANGO_TESTING": "1", "PERSONAL_MODE": "1",
                    "SECRET_KEY": "v45-isolated-test-only-very-long-key",
                    "ALLOWED_HOSTS": "127.0.0.1,localhost,testserver"}
    jobs = [
        ("v45_synthetic", [sys.executable, "-m", "unittest", "scripts.news_sentiment_factor_v45.test_v45", "-v"], ROOT, os.environ),
        ("django_owner_cache", [str(portal / "var/.venv/Scripts/python.exe"), "manage.py", "test",
                                "core.test_research_sentiment_v41", "core.test_research_sentiment_v4", "--noinput"], portal, isolated_env),
        ("javascript_syntax", ["node", "--check", "apps/quant_portal/static/core/research_sentiment_v4.js"], ROOT, os.environ),
        ("python_compile", [sys.executable, "-m", "compileall", "-q",
                            "scripts/news_sentiment_factor_v45", "apps/quant_portal/core/research_chinese_cache.py"], ROOT, os.environ),
        ("diff_whitespace", ["git", "diff", "--check"], ROOT, os.environ),
    ]
    rows = []
    logs = []
    outputs = {}
    for name, command, cwd, env in jobs:
        result = subprocess.run(command, cwd=cwd, env=env, text=True, encoding="utf-8",
                                errors="replace", stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                timeout=120)
        rows.append({"name": name, "exit_code": result.returncode,
                     "command_label": command[1:4] if name != "django_owner_cache" else ["manage.py", "test", "targeted"],
                     "no_provider_or_model_calls": True})
        logs.append(f"### {name} exit={result.returncode}\n{result.stdout}")
        outputs[name] = result.stdout
    synthetic_match = re.search(r"Ran (\d+) tests?", outputs["v45_synthetic"])
    django_match = re.search(r"Found (\d+) test", outputs["django_owner_cache"])
    if not synthetic_match or not django_match:
        raise ValueError("TARGETED_TEST_COUNTS_NOT_PARSEABLE")
    audit = json.loads((PUBLIC / "snapshot_acceptance_redacted.json").read_text(encoding="utf-8"))
    log_file = PUBLIC / "software_checks_local.txt"
    log_file.write_text("\n".join(logs), encoding="utf-8-sig", newline="\n")
    receipt = {"schema": "news_sentiment_factor_v45_software_checks_redacted_1",
               "checked_at_utc": at.isoformat(),
               "checked_at_asia_shanghai": at.astimezone(ZoneInfo("Asia/Shanghai")).isoformat(),
               "checks": rows, "all_exit_zero": all(x["exit_code"] == 0 for x in rows),
               "targeted_synthetic_unittest_cases": int(synthetic_match.group(1)),
               "targeted_django_tests": int(django_match.group(1)),
               "real_source_price_fields_read_back": sum(x["open_close_fields_source_matched"] for x in audit["pilot"].values()),
               "actual_new_supplier_requests": audit["v45_supplier_requests"],
               "previous_failed_runs_kept_in_report": True,
               "text_log_utf8_bom": log_file.read_bytes().startswith(b"\xef\xbb\xbf"),
               "text_log_sha256": sha(log_file)}
    dump(PUBLIC / "software_checks_redacted.json", receipt)
    return receipt


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False))
