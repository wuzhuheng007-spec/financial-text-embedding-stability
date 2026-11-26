"""PR #4 Linux acceptance runner, round 3.

    python deploy/linux/verify_matrix.py --state-dir <dir> --out <results_dir> [--only GROUP,...]

Every row records, separately:
  command_status  OK | FAILED | TIMEOUT       did the command run and exit as expected
  assertion       PASS | FAIL | BLOCKED | NOT_RUN   was the business claim proven
  required        a required FAIL makes the whole run exit 1; BLOCKED is listed, not failed
Results are written after every row (CSV + JSON). Test stacks are always stopped
in ``finally`` (volumes kept). Missing state (test.env, pw.env, accounts, seed
data, model volume) is recreated or reported by the PRE group; nothing depends
on files that cannot be rebuilt.

Isolation: projects quant-pr4-test / quant-pr4-restore, loopback ports only,
test feed configs never enable a supplier loop with credentials, test feed has
no egress network, daily-price is not started. Current working-tree code runs
on the existing images through compose.devmount.yaml (no rebuild).
"""
from __future__ import annotations

import argparse
import base64
import csv
import http.cookiejar
import json
import os
import re
import secrets
import shutil
import socket
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

REPO = Path.cwd()
APP, RESEARCH = "quant-personal-app:pr4test", "quant-personal-research:pr4test"
TEST_CFG = "configs/quant_portal_personal_feeds_linux_test_v1.json"
NOTOKEN_CFG = "configs/quant_portal_personal_feeds_linux_test_moomoo_notoken_v1.json"
MODEL_VOLUME = "quant_pr4test_model_v41"
FIELDS = ["id", "group", "category", "check", "command", "exit_code", "expected_exit", "command_status",
          "assertion", "required", "seconds", "config", "input_type", "log", "detail"]


class Runner:
    def __init__(self, state: Path, out: Path, tag: str = "r3"):
        self.state, self.out, self.tag = state, out, tag
        self.logs = out / f"logs_{tag}"
        self.logs.mkdir(parents=True, exist_ok=True)
        self.rows: list[dict] = []
        self.secrets: list[str] = []
        self.env_extra: dict[str, str] = {}

    # ---- plumbing -------------------------------------------------------------------
    def compose(self, project="quant-pr4-test", profiles=()):
        cmd = ["docker", "compose", "-p", project, "-f", "deploy/linux/compose.yaml",
               "-f", "deploy/linux/compose.test.yaml", "-f", "deploy/linux/compose.devmount.yaml",
               "--env-file", str(self.state / "test.env")]
        for p in profiles:
            cmd += ["--profile", p]
        return cmd

    def redact(self, text: str) -> str:
        for value in self.secrets:
            if value:
                text = text.replace(value, "<redacted>")
        text = re.sub(r"(sessionid|csrftoken)=[^;\s]+", r"\1=<redacted>", text)
        return "\n".join(line.rstrip() for line in text.splitlines()).rstrip("\n") + "\n"

    def save(self) -> None:
        with open(self.out / f"verification_matrix_{self.tag}.csv", "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=FIELDS, lineterminator="\n")
            w.writeheader()
            for r in self.rows:
                w.writerow({k: (json.dumps(r[k], ensure_ascii=False) if isinstance(r.get(k), (dict, list)) else r.get(k))
                            for k in FIELDS})
        (self.out / f"verification_matrix_{self.tag}.json").write_text(
            json.dumps(self.rows, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")

    def add(self, cid, group, category, title, *, command="", exit_code=None, expect=0, assertion, required=True,
            seconds=None, config="", input_type="", detail=None, output="") -> dict:
        status = "TIMEOUT" if exit_code == "TIMEOUT" else ("OK" if exit_code == expect or exit_code is None else "FAILED")
        log = self.logs / f"{cid}.log"
        log.write_text(self.redact(f"$ {command}\n# exit={exit_code} expected={expect} seconds={seconds}\n"
                                   f"# assertion={assertion}\n{json.dumps(detail, ensure_ascii=False, indent=1, default=str) if detail is not None else ''}\n{output}"),
                       encoding="utf-8", newline="\n")
        row = {"id": cid, "group": group, "category": category, "check": title, "command": self.redact(command).strip(),
               "exit_code": exit_code, "expected_exit": expect, "command_status": status, "assertion": assertion,
               "required": required, "seconds": seconds, "config": config, "input_type": input_type,
               "log": f"logs_{self.tag}/{cid}.log", "detail": detail}
        self.rows.append(row)
        self.save()
        print(f"{cid:10} {status:7} {assertion:8} {title}", flush=True)
        return row

    def sh(self, argv, *, env=None, timeout=900, stdin=None, stdout_file=None):
        started = time.monotonic()
        merged = {**os.environ, **self.env_extra, **(env or {})}
        try:
            if stdout_file:
                with open(stdout_file, "wb") as fh:
                    proc = subprocess.run(argv, cwd=REPO, env=merged, stdin=stdin, stdout=fh,
                                          stderr=subprocess.PIPE, timeout=timeout)
                return proc.returncode, proc.stderr.decode("utf-8", "replace"), round(time.monotonic() - started, 2)
            proc = subprocess.run(argv, cwd=REPO, env=merged, stdin=stdin, capture_output=True, text=True,
                                  encoding="utf-8", errors="replace", timeout=timeout)
            return proc.returncode, proc.stdout + proc.stderr, round(time.monotonic() - started, 2)
        except subprocess.TimeoutExpired:
            return "TIMEOUT", "", round(time.monotonic() - started, 2)

    def run(self, cid, group, category, title, argv, *, expect=0, assert_fn=None, required=True, config="",
            input_type="", env=None, timeout=900):
        code, out, secs = self.sh(argv, env=env, timeout=timeout)
        if code != expect:
            assertion, detail = "FAIL", {"reason": "unexpected exit code"}
        elif assert_fn:
            try:
                assertion, detail = assert_fn(out)
            except Exception as exc:  # noqa: BLE001
                assertion, detail = "FAIL", {"assert_error": f"{type(exc).__name__}: {exc}"}
        else:
            assertion, detail = "PASS", None
        return self.add(cid, group, category, title, command=" ".join(argv), exit_code=code, expect=expect,
                        assertion=assertion, required=required, seconds=secs, config=config,
                        input_type=input_type, detail=detail, output=out)

    def json_lines(self, text):
        return [json.loads(l) for l in text.splitlines() if l.startswith("{")]

    def container(self, svc, project="quant-pr4-test"):
        return f"{project}-{svc}-1"

    def inspect(self, name, fmt):
        return subprocess.run(["docker", "inspect", "-f", fmt, name], capture_output=True, text=True).stdout.strip()

    def health(self, svc):
        code, out, _ = self.sh(self.compose() + ["exec", "-T", svc, "python", "/app/deploy/linux/entrypoint.py", "health", svc],
                               timeout=60)
        js = self.json_lines(out)
        return code, (js[-1] if js else {})

    def wait_for(self, predicate, limit, step=1.0):
        end = time.monotonic() + limit
        while time.monotonic() < end:
            value = predicate()
            if value:
                return value
            time.sleep(step)
        return None

    # ---- PRE: rebuildable preconditions -----------------------------------------------
    def pre(self):
        free = shutil.disk_usage(str(REPO)).free
        self.add("PRE-disk", "PRE", "PRECONDITION", "host free disk >= 5 GiB (no image rebuild this round)",
                 assertion="PASS" if free >= 5 * 2**30 else "FAIL", detail={"free_gib": round(free / 2**30, 2)})
        if free < 5 * 2**30:
            raise SystemExit("not enough disk")
        code, out, secs = self.sh(["docker", "info", "--format", "{{.OSType}} {{.ServerVersion}}"], timeout=30)
        self.add("PRE-engine", "PRE", "PRECONDITION", "Docker Linux engine reachable", command="docker info",
                 exit_code=code, assertion="PASS" if code == 0 and out.startswith("linux") else "FAIL",
                 seconds=secs, detail={"engine": out.strip()})
        ids = {img: self.inspect(img, "{{.Id}}") for img in (APP, RESEARCH)}
        self.add("PRE-images", "PRE", "PRECONDITION", "test images present (identity recorded)",
                 assertion="PASS" if all(ids.values()) else "FAIL", detail={"image_ids": ids,
                 "code": "working tree bind-mounted via compose.devmount.yaml",
                 "git_head": subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip(),
                 "git_dirty": bool(subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True).stdout.strip())})
        self.state.mkdir(parents=True, exist_ok=True)
        env_file, pw_file = self.state / "test.env", self.state / "pw.env"
        recreated = False
        if not env_file.exists() or not pw_file.exists():
            # Without the original passwords the old test volumes are unusable: reset TEST volumes only.
            for project in ("quant-pr4-test", "quant-pr4-restore"):
                subprocess.run(["docker", "compose", "-p", project, "-f", "deploy/linux/compose.yaml", "down", "-v"],
                               cwd=REPO, capture_output=True, env=dict(os.environ, POSTGRES_PASSWORD="x", SECRET_KEY="x" * 50,
                               ALLOWED_HOSTS="x", CSRF_TRUSTED_ORIGINS="x", SITE_DOMAIN="x", MOOMOO_TOKEN_KEY_PATH=str(env_file)))
            sec = self.state / "secrets"
            sec.mkdir(exist_ok=True)
            (sec / "moomoo_token_key").write_text(base64.b64encode(secrets.token_bytes(32)).decode())
            env_file.write_text("\n".join([
                "COMPOSE_PROJECT_NAME=quant-pr4-test", "POSTGRES_DB=quant_test", "POSTGRES_USER=quant_test",
                f"POSTGRES_PASSWORD={secrets.token_urlsafe(24)}", f"SECRET_KEY={secrets.token_urlsafe(48)}",
                "SITE_DOMAIN=localhost", "CADDY_TLS=tls internal", "ALLOWED_HOSTS=localhost",
                "CSRF_TRUSTED_ORIGINS=https://localhost:18443", "HTTPS_PORT=18443",
                f"MOOMOO_TOKEN_KEY_PATH={(sec / 'moomoo_token_key').as_posix()}", f"MODEL_VOLUME={MODEL_VOLUME}",
                f"APP_IMAGE={APP}", f"FEED_IMAGE={APP}", f"RESEARCH_IMAGE={RESEARCH}", "RESEARCH_THREADS=2", ""]), encoding="utf-8")
            pw_file.write_text(f"TEST_OWNER_PASSWORD={secrets.token_urlsafe(18)}\nTEST_OTHER_PASSWORD={secrets.token_urlsafe(18)}\n")
            recreated = True
        env = dict(l.split("=", 1) for l in env_file.read_text().splitlines() if "=" in l)
        self.pw = dict(l.split("=", 1) for l in pw_file.read_text().splitlines() if "=" in l)
        self.secrets = [env.get("POSTGRES_PASSWORD"), env.get("SECRET_KEY"), *self.pw.values()]
        self.add("PRE-state", "PRE", "PRECONDITION", "test env and passwords available (recreated if missing)",
                 assertion="PASS", detail={"recreated": recreated, "state_dir": "scratch (not in Git)"})
        manifest = REPO / "results/news_sentiment_factor_v41_20261117/input_manifest_redacted.json"
        code, out, secs = self.sh(["docker", "run", "--rm", "--network", "none", "-v", f"{MODEL_VOLUME}:/m:ro",
                                   "-v", f"{manifest.as_posix()}:/manifest.json:ro", "--entrypoint", "python", APP, "-c",
                                   "import json,hashlib;m=json.load(open('/manifest.json'))['model_files'];"
                                   "bad=[n for n,v in m.items() if hashlib.sha256(open('/m/'+n,'rb').read()).hexdigest()!=v['sha256']];"
                                   "print(json.dumps({'files':len(m),'mismatch':bad}))"], timeout=120)
        js = self.json_lines(out)
        self.model_ok = code == 0 and js and not js[-1]["mismatch"]
        self.add("PRE-model", "PRE", "PRECONDITION", "model volume present and hash-checked",
                 command=f"docker run ... {MODEL_VOLUME} sha256 vs input_manifest", exit_code=code,
                 assertion="PASS" if self.model_ok else "BLOCKED", required=False, seconds=secs,
                 detail=js[-1] if js else {"hint": "populate the volume from a controlled model copy"})
        code, out, secs = self.sh(self.compose() + ["up", "-d", "--no-build", "--wait", "db", "migrate", "web", "proxy",
                                                    "feed", "research"], timeout=600)
        self.add("PRE-up", "PRE", "PRECONDITION", "isolated stack up with current code (devmount)",
                 command="compose up -d --wait db migrate web proxy feed research", exit_code=code,
                 assertion="PASS" if code == 0 else "FAIL", seconds=secs, config=TEST_CFG, input_type="test volumes", output=out)
        acc = f"{(REPO / 'deploy/linux/acceptance_synthetic.py').as_posix()}:/accept/a.py:ro"
        self.acc = acc
        self.run("PRE-users", "PRE", "PRECONDITION", "test accounts exist (idempotent create)",
                 self.compose() + ["run", "--rm", "--no-deps", "-e", "TEST_OWNER_PASSWORD", "-e", "TEST_OTHER_PASSWORD",
                                   "-v", acc, "--entrypoint", "python", "web", "/accept/a.py", "create-users"],
                 env=self.pw, assert_fn=lambda o: (("PASS" if len(self.json_lines(o)[-1]) == 2 else "FAIL"), self.json_lines(o)[-1]),
                 input_type="synthetic accounts")
        self.run("PRE-seed", "PRE", "PRECONDITION", "synthetic seed data present (idempotent)",
                 self.compose() + ["run", "--rm", "--no-deps", "-v", acc, "--entrypoint", "python", "feed", "/accept/a.py", "seed-feed"],
                 assert_fn=lambda o: (("PASS" if self.json_lines(o)[-1]["counts"]["news_versions"] >= 4 else "FAIL"),
                                      self.json_lines(o)[-1]["counts"]), input_type="marked SYNTHETIC rows")

    # ---- CFG: compose without Jin10; auth port only on loopback ------------------------
    def cfg(self):
        base = ["docker", "compose", "-f", "deploy/linux/compose.yaml", "--env-file", str(self.state / "test.env")]
        def no_jin10(out):
            cfg = json.loads(out)
            feed = cfg["services"]["feed"]
            auth = cfg["services"]["moomoo-auth"]
            ok = ("jin10_mcp_token" not in json.dumps(feed.get("secrets", [])) and "JIN10_MCP_TOKEN_FILE" not in feed["environment"]
                  and all(p.get("host_ip") == "127.0.0.1" for p in auth["ports"]) and "jin10_mcp_token" not in cfg.get("secrets", {}))
            return ("PASS" if ok else "FAIL"), {"feed_secrets": feed.get("secrets"), "auth_ports": auth["ports"],
                                                "top_level_secrets": sorted(cfg.get("secrets", {}))}
        env = {"JIN10_MCP_TOKEN_PATH": ""}
        self.run("CFG-1", "CFG", "SYNTHETIC", "moomoo-only compose renders without any Jin10 secret; auth port loopback-only",
                 base + ["--profile", "auth", "config", "--format", "json"], env=env, assert_fn=no_jin10, config="compose.yaml")
        self.run("CFG-2", "CFG", "SYNTHETIC", "Jin10 overlay refuses to render without an explicit token path",
                 base + ["-f", "deploy/linux/compose.jin10.yaml", "config", "--quiet"], expect=1, env=env,
                 config="compose.yaml + compose.jin10.yaml")

    # ---- S2/S8 with real assertions -----------------------------------------------------
    def settings_checks(self):
        expected = {"DEBUG": False, "PERSONAL_MODE": True, "TRUSTED_REVERSE_PROXY": True, "PUBLIC_MARKET_DATA_ENABLED": False,
                    "SECURE_SSL_REDIRECT": True, "SESSION_COOKIE_SECURE": True, "CSRF_COOKIE_SECURE": True,
                    "db": "django.db.backends.postgresql"}
        def check(out):
            got = self.json_lines(out)[-1]
            diff = {k: got.get(k) for k in expected if got.get(k) != expected[k]}
            return ("PASS" if not diff else "FAIL"), {"observed": got, "mismatches": diff}
        self.run("S2", "S2", "SYNTHETIC", "production flags inside web container (asserted)",
                 self.compose() + ["exec", "-T", "web", "python", "-c",
                                   "import os,sys,json;sys.path.insert(0,'/app/apps/quant_portal');os.environ.setdefault('DJANGO_SETTINGS_MODULE','portal.settings');"
                                   "import django;django.setup();from django.conf import settings as s;"
                                   "print(json.dumps({k:getattr(s,k) for k in ['DEBUG','PERSONAL_MODE','TRUSTED_REVERSE_PROXY','PUBLIC_MARKET_DATA_ENABLED','SECURE_SSL_REDIRECT','SESSION_COOKIE_SECURE','CSRF_COOKIE_SECURE']}|{'db':s.DATABASES['default']['ENGINE']}))"],
                 assert_fn=check, config="compose app-env", input_type="runtime environment")
        def blocked(out):
            got = self.json_lines(out)[-1]
            return ("BLOCKED" if got.get("credential_state") == "NOT_INSTALLED" else "FAIL"), {
                "status": got, "meaning": "status query works; real supplier connectivity NOT tested (no Linux authorization)"}
        self.run("S8", "S8", "REAL_SUPPLIER", "moomoo Linux credential status -> real supplier test",
                 self.compose() + ["run", "--rm", "--no-deps", "feed", "moomoo-auth", "status"], assert_fn=blocked,
                 required=False, config="MOOMOO_TOKEN_STORE=server", input_type="no real credential installed")

    # ---- REC: recovery timing measured from before the fault until health confirmed -----
    def rec(self):
        env = {"SUPERVISOR_POLL": "2", "SUPERVISOR_INITIAL_BACKOFF": "5", "SUPERVISOR_STALE_HEARTBEAT": "300"}
        self.env_extra.update(env)
        self.sh(self.compose() + ["up", "-d", "--no-build", "--wait", "research"], timeout=300)
        name = self.container("research")
        rc0 = self.inspect(name, "{{.RestartCount}}")
        t0 = time.monotonic()
        code, out, _ = self.sh(["docker", "exec", name, "python", "-c",
                                "import os,signal\nfor p in os.listdir('/proc'):\n if p.isdigit() and p not in ('1', str(os.getpid())):\n"
                                "  c=open(f'/proc/{p}/cmdline','rb').read().replace(b'\\0',b' ')\n"
                                "  if b'news_sentiment_factor_v42.worker run' in c: os.kill(int(p),signal.SIGKILL); print('killed',p)"])
        def healthy_again():
            c, h = self.health("research")
            sup = h.get("supervisor", {})
            return h if c == 0 and sup.get("state") == "RUNNING" and sup.get("restarts_last_hour", 0) >= 1 else None
        h = self.wait_for(healthy_again, 180, 1)
        total = round(time.monotonic() - t0, 1)
        self.add("REC-1", "REC", "SYNTHETIC", "research child SIGKILL -> supervisor backoff restart -> healthy",
                 command="docker exec research kill -9 <v42 child>; poll health research", exit_code=code,
                 assertion="PASS" if h and self.inspect(name, "{{.RestartCount}}") == rc0 else "FAIL",
                 seconds=total, config="SUPERVISOR_INITIAL_BACKOFF=5 SUPERVISOR_POLL=2",
                 detail={"total_seconds_signal_to_healthy": total, "includes": "poll 2 s + backoff 5 s + model reload",
                         "container_restart_count_before_after": [rc0, self.inspect(name, "{{.RestartCount}}")],
                         "supervisor": (h or {}).get("supervisor")}, output=out)
        name = self.container("web")
        rc0 = self.inspect(name, "{{.RestartCount}}")
        t0 = time.monotonic()
        self.sh(["docker", "exec", name, "python", "-c", "import os,signal;os.kill(1,signal.SIGTERM)"])
        restarted = self.wait_for(lambda: self.inspect(name, "{{.RestartCount}}") != rc0, 60, 0.5)
        t_restart = round(time.monotonic() - t0, 1)
        ok = self.wait_for(lambda: self.inspect(name, "{{.State.Health.Status}}") == "healthy", 120, 1)
        total = round(time.monotonic() - t0, 1)
        self.add("REC-2", "REC", "SYNTHETIC", "web PID 1 SIGTERM -> Docker restart policy -> healthcheck healthy",
                 command="docker exec web kill -TERM 1; poll RestartCount and Health.Status",
                 assertion="PASS" if restarted and ok else "FAIL", seconds=total,
                 detail={"signal_to_restart_s": t_restart, "signal_to_docker_healthy_s": total,
                         "note": "healthy needs the next healthcheck (interval 30 s)",
                         "correction": "round-2 S9 reported 3.2 s measured from 5 s after the signal and excluded that fixed wait; "
                                       "it is kept as the original observation and superseded by this row"})
        name = self.container("research")
        rc0 = self.inspect(name, "{{.RestartCount}}")
        self.sh(["docker", "kill", "-s", "KILL", name])
        time.sleep(20)
        status = self.inspect(name, "{{.State.Status}}")
        engine = self.sh(["docker", "version", "--format", "{{.Server.Version}}"])[1].strip()
        self.add("REC-3", "REC", "SYNTHETIC", "observation: docker kill -s KILL and restart policy",
                 command="docker kill -s KILL research; wait 20 s", assertion="PASS", required=False, seconds=20,
                 detail={"state_after_20s": status, "restart_count": [rc0, self.inspect(name, "{{.RestartCount}}")],
                         "scope": f"observed only on Docker Desktop with Engine {engine}; not a general rule"})
        self.sh(self.compose() + ["up", "-d", "--no-build", "--wait", "research"], timeout=300)

    # ---- HOLD: business thread stops for an authorization error; no restart storm --------
    def hold(self):
        env = {"TEST_FEED_CONFIG": NOTOKEN_CFG, "SUPERVISOR_POLL": "2", "SUPERVISOR_THREAD_DEAD_GRACE": "5",
               "SUPERVISOR_INITIAL_BACKOFF": "5"}
        code, out, secs = self.sh(self.compose() + ["up", "-d", "--no-build", "feed"], env=env, timeout=300)
        name = self.container("feed")
        t0 = time.monotonic()
        def held():
            c, h = self.health("feed")
            return h if h.get("supervisor", {}).get("state") in ("RUNNING_HOLD", "HOLD") else None
        h = self.wait_for(held, 120, 2)
        time.sleep(60)  # observe: no restart storm
        c2, h2 = self.health("feed")
        sup = h2.get("supervisor", {})
        rc = self.inspect(name, "{{.RestartCount}}")
        ok = bool(h) and sup.get("restarts_last_hour") == 0 and rc == "0" and c2 == 1 and \
            self.inspect(name, "{{.State.Status}}") == "running"
        self.add("HOLD-1", "HOLD", "SYNTHETIC", "moomoo thread stops on missing authorization -> HOLD, unhealthy, no restarts",
                 command="feed with moomoo enabled, no token, no egress; observe 60 s after HOLD", exit_code=code,
                 assertion="PASS" if ok else "FAIL", seconds=round(time.monotonic() - t0, 1), config=NOTOKEN_CFG,
                 input_type="no credential; token check fails before any network call",
                 detail={"first_hold": (h or {}).get("supervisor"), "after_60s": sup, "health_exit": c2,
                         "provider": h2.get("providers"), "docker_restart_count": rc,
                         "note": "Docker healthcheck=unhealthy does not restart anything; the supervisor intentionally holds"})
        self.sh(self.compose() + ["up", "-d", "--no-build", "--wait", "feed"], timeout=300)

    # ---- DBU: database briefly unavailable ---------------------------------------------
    def dbu(self):
        web = self.container("web")
        rc0 = self.inspect(web, "{{.RestartCount}}")
        t0 = time.monotonic()
        self.sh(self.compose() + ["stop", "db"], timeout=120)
        c_down, _ = self.health("web")
        time.sleep(20)
        down_state = self.inspect(web, "{{.State.Health.Status}}")
        self.sh(self.compose() + ["start", "db"], timeout=120)
        back = self.wait_for(lambda: self.health("web")[0] == 0, 120, 1)
        total = round(time.monotonic() - t0, 1)
        self.add("DBU-1", "DBU", "SYNTHETIC", "PostgreSQL stopped ~20 s: readyz fails, no restart, recovers by reconnect",
                 command="compose stop db; wait 20 s; compose start db; poll health web",
                 assertion="PASS" if c_down == 1 and back and self.inspect(web, "{{.RestartCount}}") == rc0 else "FAIL",
                 seconds=total, detail={"health_exit_while_down": c_down, "docker_health_while_down": down_state,
                                        "web_restart_count": [rc0, self.inspect(web, "{{.RestartCount}}")]})
        # SQLite: hold an exclusive lock on the research queue while new synthetic work arrives
        self.sh(self.compose() + ["run", "--rm", "--no-deps", "-v", self.acc, "--entrypoint", "python", "feed",
                                  "/accept/a.py", "seed-bulk", "5"], timeout=120)
        locker = subprocess.Popen(self.compose() + ["run", "--rm", "--no-deps", "--entrypoint", "python", "research", "-c",
                                  "import sqlite3,time;c=sqlite3.connect('/app/artifacts/news_sentiment_factor_v42_20261117/queue.sqlite3',isolation_level=None);"
                                  "c.execute('BEGIN EXCLUSIVE');print('locked',flush=True);time.sleep(25);c.execute('COMMIT');print('released',flush=True)"],
                                  cwd=REPO, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                  env=dict(os.environ, **self.env_extra))
        t0 = time.monotonic()
        lock_out = locker.communicate(timeout=120)[0]
        drained = self.wait_for(lambda: (lambda h: h if h[1].get("backlog") == 0 and h[0] == 0 else None)(self.health("research")), 240, 3)
        total = round(time.monotonic() - t0, 1)
        _, h = self.health("research")
        self.add("DBU-2", "DBU", "SYNTHETIC", "SQLite queue exclusively locked 25 s during new work -> backlog drains after release",
                 command="seed-bulk 5; BEGIN EXCLUSIVE on queue for 25 s; poll health research",
                 assertion="PASS" if drained else "FAIL", seconds=total, input_type="5 marked synthetic items",
                 detail={"locker": lock_out.strip().splitlines()[-2:], "after": {k: h.get(k) for k in ("state", "backlog", "supervisor")}})

    # ---- BKP: pg_dump / pg_restore with full logging ------------------------------------
    def bkp(self):
        dump = self.state / "pg_r3.dump"
        cmd = self.compose() + ["exec", "-T", "db", "sh", "-c", 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc']
        code, err, secs = self.sh(cmd, stdout_file=dump)
        self.add("B1", "BKP", "SYNTHETIC", "pg_dump of test database (custom format)", command=" ".join(cmd) + f" > {dump.name}",
                 exit_code=code, assertion="PASS" if code == 0 and dump.stat().st_size > 1000 else "FAIL", seconds=secs,
                 config="test project", input_type="test PostgreSQL", detail={"bytes": dump.stat().st_size}, output=err)
        renv = {"HTTPS_PORT": "18444", "CSRF_TRUSTED_ORIGINS": "https://localhost:18444"}
        self.sh(self.compose("quant-pr4-restore") + ["down", "-v"], env=renv)
        self.sh(self.compose("quant-pr4-restore") + ["up", "-d", "--no-build", "--wait", "db"], env=renv, timeout=300)
        cmd = self.compose("quant-pr4-restore") + ["exec", "-T", "db", "sh", "-c",
                                                   'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --no-owner --exit-on-error']
        with open(dump, "rb") as fh:
            code, out, secs = self.sh(cmd, env=renv, stdin=fh)
        count_cmd = self.compose("quant-pr4-restore") + ["exec", "-T", "db", "sh", "-c",
                                                         'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc "select count(*) from auth_user"']
        c2, users, _ = self.sh(count_cmd, env=renv)
        self.add("B7", "BKP", "SYNTHETIC", "pg_restore into separate restore project; users counted",
                 command=" ".join(cmd) + f" < {dump.name}", exit_code=code,
                 assertion="PASS" if code == 0 and users.strip().splitlines()[-1] == "2" else "FAIL", seconds=secs,
                 config="quant-pr4-restore", input_type="pg_r3.dump", detail={"restored_auth_users": users.strip()}, output=out)
        self.sh(self.compose("quant-pr4-restore") + ["down"], env=renv)

    # ---- OAUTH: callback path through a published loopback port (mock provider) -------
    def oauth(self):
        script = f"{(REPO / 'deploy/linux/oauth_mock_check.py').as_posix()}:/x/oauth_mock_check.py:ro"
        env = {"OAUTH_CALLBACK_PORT": "8790"}
        for case in ("ok", "bad_state", "provider_error", "redirect_mismatch", "bind_loopback_only"):
            bind = "127.0.0.1" if case == "bind_loopback_only" else "0.0.0.0"
            mock_case = "ok" if case == "bind_loopback_only" else case
            argv = self.compose(profiles=("auth",)) + ["run", "--rm", "--service-ports", "-v", script, "--entrypoint", "python",
                                                       "moomoo-auth", "/x/oauth_mock_check.py", "serve", "--case", mock_case,
                                                       "--port", "8790", "--bind", bind]
            started = time.monotonic()
            proc = subprocess.Popen(argv, cwd=REPO, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                                    env={**os.environ, **self.env_extra, **env})
            consent, browser = None, {}
            for _ in range(200):
                line = proc.stdout.readline()
                if not line:
                    break
                if line.startswith("{"):
                    consent = json.loads(line)
                    break
            if consent and consent.get("event") == "consent":
                url = "http://127.0.0.1:8790/callback?" + consent["callback_query"]
                deadline = time.monotonic() + (8 if case == "bind_loopback_only" else 30)
                while time.monotonic() < deadline:
                    try:
                        with urllib.request.urlopen(url, timeout=5) as r:
                            browser = {"http": r.status}
                        break
                    except urllib.error.HTTPError as e:
                        browser = {"http": e.code}
                        break
                    except (urllib.error.URLError, ConnectionError, OSError) as e:
                        browser = {"error": type(e).__name__}
                        time.sleep(0.5)
            # The loopback-only case is expected to end by the client's own 60 s callback timeout.
            rest = proc.communicate(timeout=150)[0]
            results = [json.loads(l) for l in rest.splitlines() if l.startswith("{")]
            result = results[-1] if results else {}
            time.sleep(1)
            with socket.socket() as s:
                released = s.connect_ex(("127.0.0.1", 8790)) != 0
            leftovers = subprocess.run(["docker", "ps", "-q", "--filter", "name=quant-pr4-test-moomoo-auth"],
                                       capture_output=True, text=True).stdout.strip()
            if case == "bind_loopback_only":
                ok = ("http" not in browser and proc.returncode != 0 and released and not leftovers
                      and "not received" in (result.get("client_error") or ""))
            else:
                ok = (proc.returncode == 0 and result.get("ok") and consent["redirect_uri"] == "http://127.0.0.1:8790/callback"
                      and released and not leftovers)
            self.add(f"OAUTH-{case}", "OAUTH", "MOCK_PROVIDER", f"callback via host 127.0.0.1:8790 -> container ({case})",
                     command=" ".join(argv), exit_code=proc.returncode, expect=proc.returncode,
                     assertion="PASS" if ok else "FAIL", seconds=round(time.monotonic() - started, 1),
                     config="compose --profile auth; ports 127.0.0.1:8790:8790", input_type="mock provider in container",
                     detail={"consent": consent, "browser": browser, "result": result, "port_released": released,
                             "container_left": bool(leftovers),
                             "meaning": "plumbing only; moomoo authorization itself NOT tested"})

    # ---- LOAD: scoring and backup at the same time --------------------------------------
    def load(self, n=200):
        if not self.model_ok:
            self.add("LOAD-1", "LOAD", "REAL_MODEL_SYNTHETIC_INPUT", "parallel scoring + backup", assertion="BLOCKED",
                     required=False, detail={"reason": "model volume missing"})
            return
        copy = os.environ.get("LOAD_BACKUP_SOURCE")
        if not copy or not Path(copy).is_file():
            self.add("LOAD-1", "LOAD", "REAL_MODEL_SYNTHETIC_INPUT", "parallel scoring + backup", assertion="NOT_RUN",
                     required=False, detail={"reason": "set LOAD_BACKUP_SOURCE to an isolated SQLite copy"})
            return
        vol = "quant_pr4test_p2load"
        subprocess.run(["docker", "volume", "create", vol], capture_output=True)
        self.sh(["docker", "run", "--rm", "--network", "none", "--user", "0", "-v", f"{vol}:/w", "-v", f"{Path(copy).parent.as_posix()}:/src:ro",
                 "--entrypoint", "sh", APP, "-c", f"rm -f /w/*; cp /src/{Path(copy).name} /w/big.sqlite3 && chown -R 10001:10001 /w"], timeout=300)
        self.sh(self.compose() + ["run", "--rm", "--no-deps", "-v", self.acc, "--entrypoint", "python", "feed",
                                  "/accept/a.py", "seed-bulk", str(n)], timeout=300)
        samples, t0 = [], time.monotonic()
        backup = subprocess.Popen(["docker", "run", "--rm", "--name", "quant-pr4-load-backup", "--network", "none", "-v", f"{vol}:/w",
                                   "--entrypoint", "python", APP, "-c",
                                   "import sqlite3,time;t=time.time();s=sqlite3.connect('/w/big.sqlite3');d=sqlite3.connect('/w/out.sqlite3');"
                                   "s.backup(d);d.close();print('backup_seconds',round(time.time()-t,1))"],
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        backlog_series = []
        while time.monotonic() - t0 < 900:
            # One container per call: docker stats fails entirely if any named container is gone.
            stats = "".join(subprocess.run(["docker", "stats", "--no-stream", "--format", "{{.Name}}|{{.CPUPerc}}|{{.MemUsage}}", c],
                                           capture_output=True, text=True).stdout
                            for c in (self.container("research"), "quant-pr4-load-backup"))
            samples.append({"t": round(time.monotonic() - t0, 1), "stats": stats.strip().splitlines()})
            _, h = self.health("research")
            backlog_series.append((round(time.monotonic() - t0, 1), h.get("backlog")))
            seen_work = any((b or 0) > 0 for _, b in backlog_series)
            if seen_work and h.get("backlog") == 0 and backup.poll() is not None:
                break
        backup_out = backup.communicate()[0]
        def parse(name):
            cpu, mem = [], []
            for s in samples:
                for line in s["stats"]:
                    if line.startswith(name):
                        _, c, m = line.split("|")
                        cpu.append(float(c.strip("%")))
                        v, unit = re.match(r"([\d.]+)\s*(\w+)", m).groups()
                        mem.append(float(v) * {"KiB": 1 / 1024, "MiB": 1, "GiB": 1024, "B": 1 / 2**20}.get(unit, 1))
            return {"cpu_pct_peak": max(cpu, default=None), "cpu_pct_mean": round(sum(cpu) / len(cpu), 1) if cpu else None,
                    "mem_mib_peak": round(max(mem, default=0), 1), "samples": len(cpu)}
        research_name = self.container("research")
        detail = {"load": f"{n} marked synthetic Chinese items + one online backup of a {round(Path(copy).stat().st_size / 2**20, 1)} MiB feed copy",
                  "first_backlog_seen_s": next((t for t, b in backlog_series if (b or 0) > 0), None),
                  "scoring_drain_seconds": (lambda first: next((t for t, b in backlog_series if first is not None and t > first and b == 0), None))(
                      next((t for t, b in backlog_series if (b or 0) > 0), None)),
                  "peak_backlog": max((b or 0) for _, b in backlog_series),
                  "backlog_series": backlog_series[::max(1, len(backlog_series) // 20)],
                  "research": parse(research_name), "backup": parse("quant-pr4-load-backup"), "backup_output": backup_out.strip(),
                  "host": {"cpus": os.cpu_count()}, "sampling": "docker stats ~1-2 s; peak values are sampled, not exact",
                  "limit": "one test on Docker Desktop (WSL2, 32 logical CPUs, 15.2 GiB VM); not a capacity proof for a 4 vCPU server"}
        ok = (detail["scoring_drain_seconds"] and detail["peak_backlog"] > 0 and "backup_seconds" in backup_out
              and detail["research"]["samples"] > 0)
        self.add("LOAD-1", "LOAD", "REAL_MODEL_SYNTHETIC_INPUT", "parallel scoring + SQLite backup: CPU, peak memory, time, backlog",
                 command=f"seed-bulk {n}; docker run backup; sample docker stats", assertion="PASS" if ok else "FAIL",
                 required=False, seconds=round(time.monotonic() - t0, 1), input_type="synthetic text, real model", detail=detail)
        subprocess.run(["docker", "volume", "rm", vol], capture_output=True)

    def stop(self):
        for project, env in (("quant-pr4-test", {}), ("quant-pr4-restore", {"HTTPS_PORT": "18444"})):
            self.sh(self.compose(project) + ["down"], env=env, timeout=300)
        left = subprocess.run(["docker", "ps", "-q", "--filter", "name=quant-pr4"], capture_output=True, text=True).stdout.strip()
        self.add("Z-stop", "Z", "SYNTHETIC", "all test containers stopped (volumes kept)", assertion="PASS" if not left else "FAIL",
                 detail={"remaining_containers": left.split()})


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state-dir", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--only", default="PRE,CFG,S2,S8,REC,HOLD,DBU,BKP,OAUTH,LOAD")
    ap.add_argument("--tag", default="r3")
    args = ap.parse_args()
    r = Runner(args.state_dir, args.out, args.tag)
    groups = args.only.split(",")
    try:
        r.pre()
        for name, fn in (("CFG", r.cfg), ("S2", r.settings_checks), ("REC", r.rec), ("HOLD", r.hold), ("DBU", r.dbu),
                         ("BKP", r.bkp), ("OAUTH", r.oauth), ("LOAD", r.load)):
            if name in groups:
                try:
                    fn()
                except Exception as exc:  # noqa: BLE001 - record and continue to cleanup
                    r.add(f"{name}-ERROR", name, "RUNNER", f"group {name} raised", assertion="FAIL",
                          detail={"error": f"{type(exc).__name__}: {exc}"})
    finally:
        r.stop()
    summary = {"rows": len(r.rows),
               "required_fail": [x["id"] for x in r.rows if x["required"] and x["assertion"] == "FAIL"],
               "blocked": [x["id"] for x in r.rows if x["assertion"] == "BLOCKED"],
               "not_run": [x["id"] for x in r.rows if x["assertion"] == "NOT_RUN"],
               "optional_fail": [x["id"] for x in r.rows if not x["required"] and x["assertion"] == "FAIL"]}
    (args.out / f"verification_summary_{args.tag}.json").write_text(json.dumps(summary, indent=1) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(summary))
    return 1 if summary["required_fail"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
