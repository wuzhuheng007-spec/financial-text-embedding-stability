"""Bounded in-container supervisor for feed, research and daily-price.

Docker's restart policy only reacts when PID 1 exits, and a failing
healthcheck does not restart anything. This supervisor is PID 1 and:

* restarts the child after a *recoverable* failure (child exit, or a business
  thread that stopped while the process stays alive, or a stale heartbeat),
  with exponential backoff and a per-hour restart cap;
* never restarts for authorization failures, HTTP 439 security-policy blocks,
  quota limits or explicit pause states. It records HOLD and waits for an
  operator, so a permanent error cannot become a restart storm;
* starts a new child only after the previous one has exited, so the child's
  own single-instance lock is never contended by the supervisor.

State: ``<var>/supervisor_<role>.json`` (read by ``entrypoint.py health``).
"""
from __future__ import annotations

import json
import os
import signal
import sqlite3
import subprocess
import sys
import time
from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

OK, RESTART, HOLD = "OK", "RESTART", "HOLD"
TERMINAL_STATES = {"AUTH_EXPIRED", "SECURITY_POLICY_BLOCKED"}
TERMINAL_ERRORS = {"OAuthError", "AUTHORIZATION", "HTTP_401", "HTTP_403", "HTTP_439",
                   "WEBAPI_SECURITY_POLICY_BLOCKED", "SUBSCRIPTION_QUOTA"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _age(value: str | None) -> float | None:
    if not value:
        return None
    stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - stamp).total_seconds()


@dataclass
class Policy:
    initial_backoff: float = 60.0
    max_backoff: float = 900.0
    max_restarts_per_hour: int = 6
    thread_dead_grace: float = 180.0
    stale_heartbeat: float = 300.0
    poll: float = 15.0
    stop_grace: float = 40.0


def classify_feed(cache_db: Path, enabled: dict[str, bool], alive: bool) -> tuple[str, str]:
    """Return (verdict, reason) from provider_state of enabled providers."""
    if not cache_db.is_file():
        return (OK, "cache not created yet") if alive else (RESTART, "exited before cache existed")
    with closing(sqlite3.connect(f"file:{cache_db.as_posix()}?mode=ro", uri=True, timeout=5)) as con:
        con.row_factory = sqlite3.Row
        rows = {r["provider"]: dict(r) for r in con.execute("SELECT * FROM provider_state")}
    dead = []
    for provider, on in enabled.items():
        if not on or provider not in rows:
            continue
        row = rows[provider]
        # The feed store names the column last_error_class; older synthetic
        # fixtures used error_class. Read either so quota/USER_STOP are seen.
        error = row.get("last_error_class") or row.get("error_class") or ""
        if row["state"] in TERMINAL_STATES or error in TERMINAL_ERRORS:
            return HOLD, f"{provider}:{row['state']}:{error}"
        if row["state"] == "REQUEST_BUDGET_PAUSED":
            continue  # local daily cap: the thread waits by itself
        if not row.get("worker_alive") and error != "USER_STOP":
            dead.append(f"{provider}:{row['state']}:{error}")
    if dead:
        return RESTART, "business thread stopped: " + ",".join(dead)
    return (OK, "providers running") if alive else (RESTART, "process exited")


def classify_research(state_file: Path, stop_flag: Path, alive: bool, stale: float) -> tuple[str, str]:
    if stop_flag.exists():
        return HOLD, "stop flag present"
    state = json.loads(state_file.read_text(encoding="utf-8")) if state_file.is_file() else {}
    if state.get("state") in {"ERROR_PAUSED", "DAILY_BUDGET_PAUSED"}:
        return HOLD, f"worker state {state.get('state')}"
    if alive:
        age = _age(state.get("heartbeat_at_utc"))
        if age is not None and age > stale:
            return RESTART, f"heartbeat stale {int(age)}s"
        return OK, "running"
    return RESTART, "process exited"


def classify_daily(state_file: Path, alive: bool, stale: float) -> tuple[str, str]:
    state = json.loads(state_file.read_text(encoding="utf-8")) if state_file.is_file() else {}
    status = state.get("last_supplier_http_status")
    if status in (401, 403, 429, 439) or state.get("pause_reason"):
        if state.get("pause_reason") not in (None, "WAIT_RELEASED_NONMISSING_N1_QUEUE"):
            return HOLD, f"pause {state.get('pause_reason')} http {status}"
        if status in (401, 403, 429, 439):
            return HOLD, f"supplier http {status}"
    if alive:
        age = _age(state.get("heartbeat_at_utc"))
        if age is not None and age > stale:
            return RESTART, f"heartbeat stale {int(age)}s"
        return OK, "running"
    return RESTART, "process exited"


@dataclass
class Supervisor:
    role: str
    argv: list[str]
    classify: callable
    state_path: Path
    policy: Policy = field(default_factory=Policy)
    child: subprocess.Popen | None = None
    restarts: list[float] = field(default_factory=list)
    backoff: float = 0.0
    stopping: bool = False
    record: dict = field(default_factory=dict)

    def write(self, state: str, reason: str, **extra) -> None:
        self.record = {"role": self.role, "state": state, "reason": reason, "updated_at_utc": utc_now(),
                       "child_pid": self.child.pid if self.child and self.child.poll() is None else None,
                       "restarts_last_hour": len([t for t in self.restarts if time.monotonic() - t < 3600]),
                       "next_backoff_s": self.backoff, **extra}
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.record), encoding="utf-8")
        tmp.replace(self.state_path)

    def start_child(self) -> None:
        self.child = subprocess.Popen(self.argv)
        self.started = time.monotonic()
        self.write("RUNNING", "child started")

    def stop_child(self) -> int | None:
        if not self.child or self.child.poll() is not None:
            return self.child.poll() if self.child else None
        self.child.send_signal(signal.SIGTERM)
        try:
            return self.child.wait(self.policy.stop_grace)
        except subprocess.TimeoutExpired:
            self.child.kill()
            return self.child.wait(10)

    def _signal(self, *_args) -> None:
        self.stopping = True

    def sleep(self, seconds: float) -> None:
        end = time.monotonic() + seconds
        while not self.stopping and time.monotonic() < end:
            time.sleep(min(0.2, max(0.0, end - time.monotonic())))

    def hold(self, reason: str, exit_code: int | None) -> int:
        self.write("HOLD", reason, last_exit_code=exit_code)
        while not self.stopping:
            self.sleep(self.policy.poll)
        return 0

    def run(self) -> int:
        import threading
        if os.name != "nt" and threading.current_thread() is threading.main_thread():
            signal.signal(signal.SIGTERM, self._signal)
            signal.signal(signal.SIGINT, self._signal)
        self.start_child()
        dead_since = None
        while not self.stopping:
            self.sleep(self.policy.poll)
            if self.stopping:
                break
            code = self.child.poll()
            alive = code is None
            try:
                verdict, reason = self.classify(alive)
            except Exception as exc:  # noqa: BLE001 - unreadable state is not a reason to restart-loop
                verdict, reason = (OK, f"classify error {type(exc).__name__}") if alive else (RESTART, "process exited")
            if alive:
                if verdict == RESTART:
                    dead_since = dead_since or time.monotonic()
                    if time.monotonic() - dead_since < self.policy.thread_dead_grace:
                        self.write("RUNNING_DEGRADED", reason)
                        continue
                    code = self.stop_child()
                elif verdict == HOLD:
                    # Keep the child (other providers may be fine); no restart.
                    self.write("RUNNING_HOLD", reason)
                    continue
                else:
                    dead_since = None
                    if time.monotonic() - self.started > 1800:
                        self.backoff = 0.0
                    self.write("RUNNING", reason)
                    continue
            dead_since = None
            if verdict == HOLD:
                return self.hold(reason, code)
            now = time.monotonic()
            self.restarts = [t for t in self.restarts if now - t < 3600]
            if len(self.restarts) >= self.policy.max_restarts_per_hour:
                return self.hold(f"restart limit reached ({reason})", code)
            self.backoff = min(self.policy.max_backoff, self.backoff * 2 if self.backoff else self.policy.initial_backoff)
            self.write("BACKOFF", reason, last_exit_code=code)
            self.sleep(self.backoff)
            if self.stopping:
                break
            self.restarts.append(time.monotonic())
            self.start_child()
        code = self.stop_child()
        self.write("STOPPED", "supervisor stopped", last_exit_code=code)
        return 0


def policy_from_env() -> Policy:
    p = Policy()
    for name in ("initial_backoff", "max_backoff", "thread_dead_grace", "stale_heartbeat", "poll", "stop_grace"):
        value = os.environ.get("SUPERVISOR_" + name.upper())
        if value:
            setattr(p, name, float(value))
    if os.environ.get("SUPERVISOR_MAX_RESTARTS_PER_HOUR"):
        p.max_restarts_per_hour = int(os.environ["SUPERVISOR_MAX_RESTARTS_PER_HOUR"])
    return p


if __name__ == "__main__":
    print("use deploy/linux/entrypoint.py", file=sys.stderr)
    raise SystemExit(2)
