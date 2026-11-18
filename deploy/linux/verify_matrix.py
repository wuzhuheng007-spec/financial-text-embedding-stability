"""Re-run the PR #4 Linux acceptance checks and record each one.

Run from the repository root on the Docker host:
  python deploy/linux/verify_matrix.py <scratch_dir_with_test.env_and_pw.env> <results_dir>

For every check it records command, exit code, expected exit code, duration,
configuration, input type and a log file. Isolation rules:
- Compose projects quant-pr4-test / quant-pr4-restore only; loopback 18443/18444
- test feed config disables both supplier loops; daily-price is not started
- passwords come from the scratch pw.env and are never written to a log
- categories: SYNTHETIC (synthetic data through real code), REAL_MODEL_SYNTHETIC_INPUT,
  LOCAL_RUNTIME_READONLY (status of the live Windows install), REAL_SUPPLIER
"""
from __future__ import annotations

import csv
import http.cookiejar
import json
import os
import re
import ssl
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

REPO = Path.cwd()
SCRATCH = Path(sys.argv[1])
OUT = Path(sys.argv[2])
LOGS = OUT / "logs"
LOGS.mkdir(parents=True, exist_ok=True)
ENV_FILE = SCRATCH / "test.env"
PW = dict(line.split("=", 1) for line in (SCRATCH / "pw.env").read_text().split() if "=" in line)
PROD = Path(r"C:\Users\ROG\Documents\ChatGPT\financial-text-embedding-stability-sync-20261113")
ACCEPT = f"{(REPO / 'deploy/linux/acceptance_synthetic.py').as_posix()}:/accept/a.py:ro"
TEST_CFG = "configs/quant_portal_personal_feeds_linux_test_v1.json"
APP, RESEARCH = "quant-personal-app:pr4test", "quant-personal-research:pr4test"
SECRET_VALUES = [v for v in PW.values() if v] + [
    line.split("=", 1)[1] for line in ENV_FILE.read_text().splitlines()
    if line.startswith(("POSTGRES_PASSWORD=", "SECRET_KEY="))]
ROWS: list[dict] = []


def compose(project: str = "quant-pr4-test", port: str = "18443") -> list[str]:
    return ["docker", "compose", "-p", project, "-f", "deploy/linux/compose.yaml",
            "-f", "deploy/linux/compose.test.yaml", "--env-file", str(ENV_FILE)]


def redact(text: str) -> str:
    for value in SECRET_VALUES:
        text = text.replace(value, "<redacted>")
    text = re.sub(r"(sessionid|csrftoken)=[^;\s]+", r"\1=<redacted>", text)
    return "\n".join(line.rstrip() for line in text.splitlines()).rstrip("\n") + "\n"


def run(cid, category, title, argv, *, expect=0, config="", input_type="", env=None, cwd=REPO,
        timeout=900, note=""):
    started = time.monotonic()
    merged = dict(os.environ, **(env or {}))
    try:
        proc = subprocess.run(argv, cwd=cwd, env=merged, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout)
        code, output = proc.returncode, proc.stdout + proc.stderr
    except subprocess.TimeoutExpired as exc:
        code, output = "TIMEOUT", f"timeout after {timeout}s\n{exc.stdout or ''}"
    seconds = round(time.monotonic() - started, 1)
    log = LOGS / f"{cid}.log"
    shown = " ".join(argv)
    log.write_text(redact(f"$ {shown}\n# exit={code} expected={expect} seconds={seconds}\n{output}"),
                   encoding="utf-8", newline="\n")
    row = {"id": cid, "category": category, "check": title, "command": redact(shown), "exit_code": code,
           "expected_exit": expect, "pass": code == expect, "seconds": seconds, "config": config,
           "input_type": input_type, "log": f"logs/{cid}.log", "note": note}
    ROWS.append(row)
    print(f"{cid} exit={code} expect={expect} {seconds}s {title}", flush=True)
    return code, output


def record(cid, category, title, ok, detail, *, config="", input_type="", note=""):
    log = LOGS / f"{cid}.log"
    log.write_text(redact(json.dumps(detail, ensure_ascii=False, indent=1)) + "\n", encoding="utf-8", newline="\n")
    ROWS.append({"id": cid, "category": category, "check": title, "command": "(python, see log)",
                 "exit_code": 0 if ok else 1, "expected_exit": 0, "pass": ok, "seconds": "",
                 "config": config, "input_type": input_type, "log": f"logs/{cid}.log", "note": note})
    print(f"{cid} {'PASS' if ok else 'FAIL'} {title}", flush=True)


def https_session(base: str, user: str, password: str):
    context = ssl._create_unverified_context()  # Caddy local CA, test only
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=context),
                                         urllib.request.HTTPCookieProcessor(jar))

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None
    raw = urllib.request.build_opener(urllib.request.HTTPSHandler(context=context),
                                      urllib.request.HTTPCookieProcessor(jar), NoRedirect)

    def status(url, opener_=raw, data=None, headers=None):
        try:
            with opener_.open(urllib.request.Request(url, data=data, headers=headers or {}), timeout=15) as r:
                return r.status, r.read(), dict(r.headers)
        except urllib.error.HTTPError as e:
            return e.code, e.read(), dict(e.headers)
    if user:
        _, body, _ = status(base + "/accounts/login/", opener)
        token = re.search(rb'name="csrfmiddlewaretoken" value="([^"]+)"', body).group(1).decode()
        form = urllib.parse.urlencode({"csrfmiddlewaretoken": token, "username": user, "password": password}).encode()
        code, _, hdr = status(base + "/accounts/login/", raw, form, {"Referer": base + "/accounts/login/"})
        return status, code, hdr.get("Location")
    return status, None, None


def http_checks(cid, base, label):
    anon, _, _ = https_session(base, "", "")
    owner, owner_login, owner_loc = https_session(base, "owner@local.invalid", PW["TEST_OWNER_PASSWORD"])
    other, other_login, _ = https_session(base, "other@local.invalid", PW["TEST_OTHER_PASSWORD"])
    detail = {"anon": {p: anon(base + p)[0] for p in ("/", "/personal-feed.json", "/readyz/")},
              "owner_login": [owner_login, owner_loc],
              "owner": {p: owner(base + p)[0] for p in ("/", "/settings/", "/personal-feed.json", "/research-sentiment.json")},
              "other_login": other_login,
              "other": {p: other(base + p)[0] for p in ("/personal-feed.json", "/research-sentiment.json")}}
    code, body, headers = owner(base + "/personal-feed.json")
    feed = json.loads(body)
    detail["owner_feed"] = {"news_total": feed.get("news_total"), "quotes": len(feed.get("quotes", []))}
    page = owner(base + "/")[1].decode("utf-8", "replace")
    statics = sorted(set(re.findall(r'(/static/[^"\s]+)', page)))
    detail["static"] = {s: anon(base + s)[0] for s in statics}
    detail["headers_login_page"] = {k: v for k, v in anon(base + "/accounts/login/")[2].items()
                                    if k.lower() in ("strict-transport-security", "x-frame-options", "referrer-policy")}
    ok = (detail["anon"]["/"] == 302 and detail["anon"]["/personal-feed.json"] == 302 and owner_login == 302
          and all(v == 200 for v in detail["owner"].values()) and all(v == 404 for v in detail["other"].values())
          and statics and all(v == 200 for v in detail["static"].values()))
    record(cid, "SYNTHETIC", f"HTTPS login, owner isolation, static ({label})", ok, detail,
           config="compose.yaml+compose.test.yaml; Caddy tls internal; DEBUG=0 PERSONAL_MODE=1",
           input_type="synthetic test accounts")
    return detail


def inspect(project="quant-pr4-test"):
    _, out = run(f"inspect_{project}_{len(ROWS)}", "SYNTHETIC", f"store counts ({project})",
                 compose(project) + ["run", "--rm", "--no-deps", "-v", ACCEPT, "--entrypoint", "python",
                                     "research", "/accept/a.py", "inspect"],
                 config=TEST_CFG, input_type="test volumes")
    return json.loads([line for line in out.splitlines() if line.startswith("{")][-1])


def health(cid, service, project="quant-pr4-test", expect=0, note=""):
    return run(cid, "SYNTHETIC", f"business health {service}",
               compose(project) + ["exec", "-T", service, "python", "/app/deploy/linux/entrypoint.py", "health", service],
               expect=expect, config=TEST_CFG, input_type="test volumes", note=note)


def restart_count(container):
    return int(subprocess.check_output(["docker", "inspect", "-f", "{{.RestartCount}}", container], text=True).strip())


def wait_healthy(container, limit=240):
    end = time.monotonic() + limit
    while time.monotonic() < end:
        state = subprocess.run(["docker", "inspect", "-f", "{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{end}}", container],
                               capture_output=True, text=True).stdout.strip()
        if state == "running healthy":
            return state, round(limit - (end - time.monotonic()), 1)
        time.sleep(3)
    return state, None


def main():
    # --- Windows host, software only -------------------------------------------------
    run("W1", "SYNTHETIC", "Windows pytest: token store, owner resolver, feed",
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests/test_server_token_store.py",
         "tests/test_personal_feed_push_batching.py", "tests/test_quant_personal_feed.py",
         "tests/test_personal_feed_lock_recovery.py"], config="Windows system Python 3.11", input_type="synthetic fixtures")
    run("W2", "SYNTHETIC", "Windows production settings rules (portal venv)",
        [str(PROD / "apps/quant_portal/var/.venv/Scripts/python.exe"), "-m", "unittest", "tests.test_personal_production_settings"],
        config="portal venv Django 5.2.19", input_type="synthetic env values")
    # --- Linux images ----------------------------------------------------------------
    run("L1", "SYNTHETIC", "image private-file scan (expect no matches)",
        ["docker", "run", "--rm", "--network", "none", "--entrypoint", "sh", APP, "-c",
         "find /app -xdev \\( -name '*.sqlite3*' -o -name '*.db' -o -name '.env' -o -name '*.dpapi' -o -name '*.sealed' "
         "-o -name '*private*' -o -name '*.key' -o -name 'personal_invite.txt' -o -name 'local_secret.txt' \\) -print | "
         "tee /dev/stderr | wc -l | grep -qx 0"], config=APP, input_type="built image")
    run("L2", "SYNTHETIC", "Linux pytest suite in app image",
        ["docker", "run", "--rm", "-e", "HOME=/tmp", "-v", f"{REPO}:/src:ro", "--entrypoint", "sh", APP, "-c",
         "mkdir -p /tmp/s && cd /src && tar --exclude=.git --exclude=__pycache__ --exclude=.pytest_cache -cf - "
         "apps/quant_portal scripts configs results tests deploy pyproject.toml | tar -xf - -C /tmp/s && cd /tmp/s && "
         "pip install --user -q pytest==9.1.1 >/dev/null 2>&1 && python -m pytest -q -p no:cacheprovider "
         "tests/test_server_token_store.py tests/test_quant_personal_feed.py tests/test_personal_feed_push_batching.py "
         "tests/test_personal_feed_lock_recovery.py tests/test_personal_feed_query_index.py tests/test_personal_feed_quote_prune.py "
         "tests/test_personal_production_settings.py tests/test_quant_personal_sessions_v13.py tests/test_quant_personal_timeline_v14.py "
         "&& python -m unittest scripts.news_sentiment_factor_v45.test_v45"],
        config=APP + " (network used only for pip install pytest)", input_type="synthetic fixtures")
    run("L3", "SYNTHETIC", "v43 daily window/snapshot/label harness (13 checks)",
        ["docker", "run", "--rm", "--network", "none", "--read-only", "--tmpfs", "/tmp:size=64m", "-w", "/app",
         "--entrypoint", "python", RESEARCH, "-m", "scripts.news_sentiment_factor_v43.verify_daily"],
        config=RESEARCH + " network none, read-only root", input_type="synthetic temp DBs")
    run("L4", "SYNTHETIC", "compose config validation", compose() + ["config", "--quiet"],
        config="compose.yaml+compose.test.yaml", input_type="test.env (scratch)")
    # --- isolated stack ---------------------------------------------------------------
    run("S1", "SYNTHETIC", "stack up (db, migrate, web, proxy, feed, research)",
        compose() + ["up", "-d", "--no-build", "--wait", "db", "migrate", "web", "proxy", "feed", "research"],
        config=TEST_CFG + "; daily-price not started", input_type="test volumes", timeout=600)
    run("S2", "SYNTHETIC", "settings inside web container",
        compose() + ["exec", "-T", "web", "python", "-c",
                     "import os,sys,json;sys.path.insert(0,'/app/apps/quant_portal');os.environ.setdefault('DJANGO_SETTINGS_MODULE','portal.settings');"
                     "import django;django.setup();from django.conf import settings as s;"
                     "print(json.dumps({k:getattr(s,k) for k in ['DEBUG','PERSONAL_MODE','TRUSTED_REVERSE_PROXY','PUBLIC_MARKET_DATA_ENABLED','SECURE_SSL_REDIRECT','SESSION_COOKIE_SECURE','CSRF_COOKIE_SECURE']}|{'db':s.DATABASES['default']['ENGINE']}))"],
        config="compose.yaml app-env", input_type="runtime environment")
    run("S3", "SYNTHETIC", "manage.py check --deploy",
        compose() + ["exec", "-T", "-w", "/app/apps/quant_portal", "web", "python", "manage.py", "check", "--deploy"],
        config="production flags", input_type="runtime environment", note="2 HSTS advisory warnings expected")
    for cid, svc in (("S4a", "web"), ("S4b", "feed"), ("S4c", "research")):
        health(cid, svc, note="feed: NO_PROVIDER_ENABLED; supplier business not exercised" if svc == "feed" else "")
    http_checks("S5", "https://localhost:18443", "test project")
    run("S6", "SYNTHETIC", "second feed collector refused (single instance)",
        compose() + ["run", "--rm", "--no-deps", "feed"], expect=1, config=TEST_CFG, input_type="test volumes")
    run("S7", "SYNTHETIC", "seed synthetic news/quotes again (dedup)",
        compose() + ["run", "--rm", "--no-deps", "-v", ACCEPT, "--entrypoint", "python", "feed", "/accept/a.py", "seed-feed"],
        config=TEST_CFG + "; QUANT_OWNER_RESOLVER=django_orm", input_type="marked SYNTHETIC rows")
    before = inspect()
    run("S8", "REAL_SUPPLIER", "moomoo server credential status in container",
        compose() + ["run", "--rm", "--no-deps", "feed", "moomoo-auth", "status"], config="MOOMOO_TOKEN_STORE=server",
        input_type="no real credential installed", note="NOT_INSTALLED means real supplier test is BLOCKED")
    # --- exception recovery -----------------------------------------------------------
    recovery = {}
    for svc, sig in (("web", "SIGTERM"), ("feed", "SIGTERM"), ("research", "SIGINT")):
        name = f"quant-pr4-test-{svc}-1"
        count = restart_count(name)
        subprocess.run(["docker", "exec", name, "python", "-c", f"import os,signal;os.kill(1,signal.{sig})"],
                       capture_output=True)
        time.sleep(5)
        state, seconds = wait_healthy(name)
        recovery[svc] = {"signal_to_pid1": sig, "restart_count_before": count,
                         "restart_count_after": restart_count(name), "state": state, "healthy_after_s": seconds}
    name = "quant-pr4-test-research-1"
    count = restart_count(name)
    subprocess.run(["docker", "kill", "-s", "KILL", name], capture_output=True)
    time.sleep(8)
    status = subprocess.run(["docker", "inspect", "-f", "{{.State.Status}}", name], capture_output=True, text=True).stdout.strip()
    recovery["research_docker_kill_SIGKILL"] = {"restart_count_before": count, "restart_count_after": restart_count(name),
                                               "state_after_8s": status}
    if status != "running":
        subprocess.run(compose() + ["up", "-d", "--no-build", "research"], capture_output=True)
    recovery["research_after_kill"] = dict(zip(("state", "healthy_after_s"), wait_healthy(name)))
    ok = all(recovery[s]["restart_count_after"] == recovery[s]["restart_count_before"] + 1 and recovery[s]["state"] == "running healthy"
             for s in ("web", "feed", "research"))
    record("S9", "SYNTHETIC", "process exit -> restart: unless-stopped -> healthy", ok, recovery,
           config="restart: unless-stopped", input_type="signal to PID 1 / docker kill",
           note="docker kill is treated as a manual stop by Docker; see log")
    after_signal = inspect()
    run("S10", "SYNTHETIC", "stack down (volumes kept)", compose() + ["down"], config="test project", input_type="-")
    run("S11", "SYNTHETIC", "stack up again", compose() + ["up", "-d", "--no-build", "--wait", "db", "migrate", "web", "proxy", "feed", "research"],
        config=TEST_CFG, input_type="test volumes", timeout=600)
    after_restart = inspect()
    same = {k: before[k] == after_restart[k] for k in before if k != "queue_worker_runs"}
    record("S12", "SYNTHETIC", "persistence across signals and down/up", all(same.values()),
           {"before": before, "after_signals": after_signal, "after_down_up": after_restart, "equal_except_worker_runs": same},
           config="named volumes", input_type="test volumes")
    # --- backup and restore into another project --------------------------------------
    backup = SCRATCH / "backup_matrix"
    backup.mkdir(exist_ok=True)
    with open(backup / "pg.dump", "wb") as fh:
        code = subprocess.run(compose() + ["exec", "-T", "db", "sh", "-c", 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc'],
                              stdout=fh).returncode
    ROWS.append({"id": "B1", "category": "SYNTHETIC", "check": "pg_dump test database", "command": "docker compose exec db pg_dump -Fc > pg.dump",
                 "exit_code": code, "expected_exit": 0, "pass": code == 0, "seconds": "", "config": "test project",
                 "input_type": "test PostgreSQL", "log": "", "note": f"{(backup / 'pg.dump').stat().st_size} bytes"})
    for cid, src, name in (("B2", "/app/apps/quant_portal/var/personal_feeds.sqlite3", "feed"),
                           ("B3", "/app/artifacts/news_sentiment_factor_v42_20261117/queue.sqlite3", "queue"),
                           ("B4", "/app/artifacts/news_sentiment_factor_v41_20261117/chinese_tone.sqlite3", "score")):
        run(cid, "SYNTHETIC", f"SQLite online backup {name}",
            compose() + ["run", "--rm", "--no-deps", "-v", f"{backup}:/backup", "-v", ACCEPT, "--entrypoint", "python",
                         "research", "/accept/a.py", "sqlite-backup", src, f"/backup/{name}.sqlite3"],
            config="sqlite3 backup API", input_type="test volumes")
    renv = {"HTTPS_PORT": "18444", "CSRF_TRUSTED_ORIGINS": "https://localhost:18444"}
    run("B5", "SYNTHETIC", "reset restore project volumes (test only)", compose("quant-pr4-restore") + ["down", "-v"],
        env=renv, config="quant-pr4-restore", input_type="-")
    run("B6", "SYNTHETIC", "restore project db up", compose("quant-pr4-restore") + ["up", "-d", "--no-build", "--wait", "db"],
        env=renv, config="quant-pr4-restore", input_type="-")
    with open(backup / "pg.dump", "rb") as fh:
        code = subprocess.run(compose("quant-pr4-restore") + ["exec", "-T", "db", "sh", "-c",
                              'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --no-owner --exit-on-error'],
                              stdin=fh, env=dict(os.environ, **renv), capture_output=True).returncode
    ROWS.append({"id": "B7", "category": "SYNTHETIC", "check": "pg_restore into restore project", "command": "docker compose -p quant-pr4-restore exec db pg_restore < pg.dump",
                 "exit_code": code, "expected_exit": 0, "pass": code == 0, "seconds": "", "config": "quant-pr4-restore",
                 "input_type": "pg.dump of test DB", "log": "", "note": ""})
    run("B8", "SYNTHETIC", "copy SQLite backups into restore volumes",
        compose("quant-pr4-restore") + ["run", "--rm", "--no-deps", "-v", f"{backup}:/backup:ro", "--entrypoint", "sh", "research", "-c",
                                        "mkdir -p /app/artifacts/news_sentiment_factor_v42_20261117 /app/artifacts/news_sentiment_factor_v41_20261117 && "
                                        "cp /backup/feed.sqlite3 /app/apps/quant_portal/var/personal_feeds.sqlite3 && "
                                        "cp /backup/queue.sqlite3 /app/artifacts/news_sentiment_factor_v42_20261117/queue.sqlite3 && "
                                        "cp /backup/score.sqlite3 /app/artifacts/news_sentiment_factor_v41_20261117/chinese_tone.sqlite3"],
        env=renv, config="quant-pr4-restore", input_type="SQLite backups")
    run("B9", "SYNTHETIC", "restore project web/proxy up",
        compose("quant-pr4-restore") + ["up", "-d", "--no-build", "--wait", "migrate", "web", "proxy"],
        env=renv, config="quant-pr4-restore", input_type="restored volumes", timeout=600)
    restored = inspect("quant-pr4-restore")
    same = {k: after_restart[k] == restored[k] for k in after_restart}
    record("B10", "SYNTHETIC", "restored stores equal source", all(same.values()), {"source": after_restart, "restored": restored, "equal": same},
           config="quant-pr4-restore", input_type="restored volumes")
    http_checks("B11", "https://localhost:18444", "restore project")
    # --- read-only status of the live Windows install ---------------------------------
    ps = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File"]
    run("P1", "LOCAL_RUNTIME_READONLY", "Windows portal status", ps + ["apps/quant_portal/ops/local.ps1", "status"], cwd=PROD,
        config="production dir, loopback 8765", input_type="live local install")
    run("P2", "LOCAL_RUNTIME_READONLY", "Windows feed status (Jin10 + moomoo push in one process)",
        ps + ["apps/quant_portal/ops/feed_personal.ps1", "status"], cwd=PROD,
        config="configs/quant_portal_personal_feeds_v11_20261116.json", input_type="live local install")
    run("P3", "LOCAL_RUNTIME_READONLY", "Windows v42 research worker status",
        [str(PROD / "artifacts/news_sentiment_factor_v3_20261117/.venv/Scripts/python.exe"), "-m",
         "scripts.news_sentiment_factor_v42.worker", "status"], cwd=PROD, config="isolated v3 venv", input_type="live local install")
    run("P4", "LOCAL_RUNTIME_READONLY", "Windows v45 daily worker status",
        [sys.executable, "-m", "scripts.news_sentiment_factor_v45.worker", "status"], cwd=PROD,
        config="system Python", input_type="live local install")
    run("P5", "LOCAL_RUNTIME_READONLY", "Windows analysis queue status (must stay paused)",
        ps + ["apps/quant_portal/ops/analysis_personal.ps1", "status"], cwd=PROD,
        config="v16 analysis", input_type="live local install")
    # --- stop test stacks --------------------------------------------------------------
    run("Z1", "SYNTHETIC", "stop restore project", compose("quant-pr4-restore") + ["down"], env=renv, config="-", input_type="-")
    run("Z2", "SYNTHETIC", "stop test project", compose() + ["down"], config="-", input_type="-")
    fields = ["id", "category", "check", "command", "exit_code", "expected_exit", "pass", "seconds", "config", "input_type", "log", "note"]
    with open(OUT / "verification_matrix.csv", "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(ROWS)
    print(json.dumps({"total": len(ROWS), "pass": sum(1 for r in ROWS if r["pass"]),
                      "fail": [r["id"] for r in ROWS if not r["pass"]]}))


if __name__ == "__main__":
    main()
