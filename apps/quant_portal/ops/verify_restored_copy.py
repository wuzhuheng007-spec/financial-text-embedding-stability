"""Boot a private backup on an isolated app copy and port; never replace the live DB."""

from __future__ import annotations

import argparse
import hashlib
import http.cookiejar
import json
import os
import re
import secrets
import shutil
import socket
import sqlite3
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo


APP_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = APP_ROOT.parents[1]
BACKUPS = APP_ROOT / "backups"
RECEIPT = REPO_ROOT / "results" / "quant_portal_personal_acceptance_20261116" / "restore_boot_redacted.json"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def snapshot(db_path: Path) -> dict:
    with closing(sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)) as db:
        table_count = db.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
        integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
        selects = {
            "accounts": "SELECT id, username, password, is_active FROM auth_user ORDER BY id",
            "watchlist": "SELECT id, user_id, symbol, display_name, asset_type FROM core_watchlistitem ORDER BY id",
            "preferences": "SELECT id, user_id, display_timezone FROM core_userpreferences ORDER BY id",
            "fact_cards": "SELECT id, candidate_id, issuer, fiscal_period, metric, value_text FROM core_publicfactcard ORDER BY id",
        }
        rows = {name: db.execute(sql).fetchall() for name, sql in selects.items()}
        migration_0005 = bool(db.execute(
            "SELECT 1 FROM django_migrations WHERE app='core' AND name LIKE '0005%'"
        ).fetchone())
    return {
        "table_count": table_count,
        "integrity": integrity,
        "counts": {name: len(items) for name, items in rows.items()},
        "row_digests": {
            name: hashlib.sha256(json.dumps(items, ensure_ascii=False).encode("utf-8")).hexdigest()
            for name, items in rows.items()
        },
        "migration_0005_applied": migration_0005,
        "rows": rows,
    }


def free_loopback_port() -> int:
    with closing(socket.socket()) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def http_code(url: str) -> int:
    with urllib.request.urlopen(url, timeout=2) as response:
        response.read(64)
        return response.status


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("backup", type=Path)
    parser.add_argument("--receipt-name", choices=("restore_boot_redacted.json", "restore_boot_final_redacted.json"),
                        default="restore_boot_redacted.json")
    args = parser.parse_args()
    receipt_path = RECEIPT.with_name(args.receipt_name)
    source = args.backup.resolve(strict=True)
    if BACKUPS.resolve() not in source.parents or source.suffix != ".sqlite3":
        raise SystemExit("Accepts only an app-private .sqlite3 backup")
    private_receipt = source.with_suffix(".receipt.json")
    if not private_receipt.is_file():
        raise SystemExit("Matching private backup receipt is absent")
    expected = json.loads(private_receipt.read_text(encoding="utf-8"))["sha256"]
    if sha256(source) != expected:
        raise SystemExit("Private backup hash mismatch")
    before = snapshot(source)
    if before["integrity"] != "ok":
        raise SystemExit("Private backup integrity check failed")

    python = APP_ROOT / "var" / ".venv" / "Scripts" / "python.exe"
    if not python.is_file():
        raise SystemExit("Pinned personal Python environment is absent")
    RECEIPT.parent.mkdir(parents=True, exist_ok=True)
    receipt = {
        "schema": "quant_portal_restore_boot_redacted_v1",
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "checked_at_shanghai": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
        "backup_private_repo_relative_path": "apps/quant_portal/backups/" + source.name,
        "backup_sha256": expected,
        "backup_integrity": before["integrity"],
        "pre_restore_counts": before["counts"],
        "live_database_modified": False,
        "result": "STARTED",
        "key_code_sha256": {
            name: sha256(APP_ROOT / name)
            for name in (
                "core/management/commands/bootstrap_personal.py", "core/views.py",
                "static/core/portal.css", "templates/core/dashboard.html",
            )
        },
    }
    try:
        with tempfile.TemporaryDirectory(prefix="portal-boot-probe-", dir=BACKUPS) as scratch_name:
            scratch = Path(scratch_name)
            app = scratch / "app"
            app.mkdir()
            for name in ("core", "portal", "templates", "static"):
                shutil.copytree(APP_ROOT / name, app / name, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            shutil.copy2(APP_ROOT / "manage.py", app / "manage.py")
            (app / "var").mkdir()
            restored = app / "var" / "dev.sqlite3"
            shutil.copy2(source, restored)
            port = free_loopback_port()
            env = os.environ.copy()
            for key in ("POSTGRES_HOST", "POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_PORT"):
                env.pop(key, None)
            env.update({
                "SECRET_KEY": secrets.token_urlsafe(48),
                "DEBUG": "1", "DJANGO_TESTING": "1", "PERSONAL_MODE": "1",
                "PUBLIC_MARKET_DATA_ENABLED": "0", "ALLOWED_HOSTS": "127.0.0.1,localhost",
                "CSRF_TRUSTED_ORIGINS": f"http://127.0.0.1:{port}",
            })
            migrated = subprocess.run(
                [str(python), "manage.py", "migrate", "--noinput"],
                cwd=app, env=env, capture_output=True, timeout=120,
            )
            if migrated.returncode:
                raise RuntimeError("scratch_migration_failed")
            receipt["scratch_migration"] = "PASS"
            facts_path = REPO_ROOT / "results" / "market_observer_financial_text_v2_20261115" / "fact_cards_v4_redacted.json"
            imported = subprocess.run(
                [str(python), "manage.py", "bootstrap_personal", "--facts-path", str(facts_path)],
                cwd=app, env=env, capture_output=True, timeout=120,
            )
            if imported.returncode:
                raise RuntimeError("scratch_bootstrap_failed")
            receipt["scratch_bootstrap"] = "PASS"
            log_path = scratch / "server.log"
            with log_path.open("wb") as server_log:
                proc = subprocess.Popen(
                    [str(python), "manage.py", "runserver", f"127.0.0.1:{port}", "--noreload"],
                    cwd=app, env=env, stdout=server_log, stderr=subprocess.STDOUT,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                try:
                    deadline = time.monotonic() + 20
                    while time.monotonic() < deadline:
                        if proc.poll() is not None:
                            raise RuntimeError("scratch_server_exited")
                        try:
                            ready = http_code(f"http://127.0.0.1:{port}/readyz/")
                            break
                        except (urllib.error.URLError, TimeoutError):
                            time.sleep(0.25)
                    else:
                        raise RuntimeError("scratch_server_ready_timeout")
                    login = http_code(f"http://127.0.0.1:{port}/accounts/login/")
                    css = http_code(f"http://127.0.0.1:{port}/static/core/portal.css")
                    after = snapshot(restored)
                    if after["integrity"] != "ok":
                        raise RuntimeError("restored_rows_changed")
                    for name in ("accounts", "watchlist", "preferences"):
                        if after["row_digests"][name] != before["row_digests"][name]:
                            raise RuntimeError("restored_user_state_changed")
                    prior_facts = {row[0]: row for row in before["rows"]["fact_cards"]}
                    after_facts = {row[0]: row for row in after["rows"]["fact_cards"]}
                    if (len(prior_facts) != 11 or len(after_facts) != 14 or
                            any(after_facts.get(key) != value for key, value in prior_facts.items())):
                        raise RuntimeError("restored_fact_cards_changed_or_missing_guidance")
                    with closing(sqlite3.connect(f"file:{restored}?mode=ro", uri=True)) as db:
                        kinds = dict(db.execute(
                            "SELECT fact_kind, COUNT(*) FROM core_publicfactcard GROUP BY fact_kind"
                        ).fetchall())
                    if kinds.get("GUIDANCE") != 2 or sum(kinds.values()) != 14:
                        raise RuntimeError("restored_guidance_count_wrong")
                    probe_email = "restore-probe-" + secrets.token_hex(8) + "@local.invalid"
                    probe_password = secrets.token_urlsafe(32)
                    probe_env = dict(env, RESTORE_PROBE_EMAIL=probe_email,
                                     RESTORE_PROBE_PASSWORD=probe_password)
                    created = subprocess.run(
                        [str(python), "manage.py", "shell", "-c",
                         "import os; from django.contrib.auth import get_user_model; "
                         "from core.models import UserPreferences; "
                         "u=get_user_model().objects.create_user(username=os.environ['RESTORE_PROBE_EMAIL'], "
                         "email=os.environ['RESTORE_PROBE_EMAIL'], password=os.environ['RESTORE_PROBE_PASSWORD']); "
                         "UserPreferences.objects.create(user=u)"],
                        cwd=app, env=probe_env, capture_output=True, timeout=30,
                    )
                    if created.returncode:
                        raise RuntimeError("scratch_probe_account_creation_failed")
                    opener = urllib.request.build_opener(
                        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
                    )
                    login_url = f"http://127.0.0.1:{port}/accounts/login/"
                    with opener.open(login_url, timeout=2) as response:
                        login_html = response.read().decode("utf-8")
                    csrf = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', login_html)
                    if not csrf:
                        raise RuntimeError("scratch_login_csrf_missing")
                    post = urllib.parse.urlencode({
                        "username": probe_email, "password": probe_password,
                        "csrfmiddlewaretoken": csrf.group(1),
                    }).encode("utf-8")
                    with opener.open(urllib.request.Request(
                        login_url, data=post, headers={"Referer": login_url}
                    ), timeout=3) as response:
                        dashboard_html = response.read().decode("utf-8")
                        dashboard_code = response.status
                        dashboard_url = response.geturl()
                    with closing(sqlite3.connect(f"file:{restored}?mode=ro", uri=True)) as db:
                        ranges = db.execute(
                            "SELECT range_lower_text, range_upper_text FROM core_publicfactcard "
                            "WHERE fact_kind='GUIDANCE' ORDER BY id"
                        ).fetchall()
                    guidance_rendered = (
                        dashboard_code == 200 and dashboard_url == f"http://127.0.0.1:{port}/"
                        and "历史公告中的未来指引" in dashboard_html
                        and all(lower in dashboard_html and upper in dashboard_html
                                for lower, upper in ranges)
                    )
                    if not guidance_rendered:
                        raise RuntimeError("scratch_guidance_dashboard_not_rendered")
                    receipt.update({
                        "isolated_bind": "127.0.0.1", "isolated_port": port,
                        "ready_http": ready, "login_http": login, "css_http": css,
                        "scratch_integrity": after["integrity"],
                        "scratch_table_count": after["table_count"],
                        "post_restore_counts": after["counts"],
                        "account_watchlist_preferences_facts_preserved": True,
                        "legacy_fact_card_count_preserved": len(prior_facts),
                        "new_eligible_fact_card_count": len(after_facts) - len(prior_facts),
                        "guidance_card_count": kinds["GUIDANCE"],
                        "scratch_only_test_account_created": True,
                        "authenticated_dashboard_http": dashboard_code,
                        "guidance_bounds_rendered": guidance_rendered,
                        "migration_0005_applied": after["migration_0005_applied"],
                        "result": "PASS",
                    })
                finally:
                    proc.terminate()
                    try:
                        proc.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait(timeout=5)
                    receipt["isolated_process_stopped"] = proc.poll() is not None
    except Exception as exc:
        receipt["result"] = "FAIL"
        receipt["error_category"] = str(exc)
        raise
    finally:
        receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"result": receipt["result"], "restore_receipt": str(receipt_path.relative_to(REPO_ROOT)),
                      "scratch_table_count": receipt["scratch_table_count"], "migration_0005_applied": receipt["migration_0005_applied"]}))


if __name__ == "__main__":
    main()
