"""Single foreground entrypoint for the Linux personal deployment.

Each container runs exactly one role in the foreground, so the container
runtime (restart policy, logs, health) supervises the real process:

  web          gunicorn portal (PostgreSQL accounts, owner-only personal pages)
  migrate      Django migrations, then exit
  feed         moomoo push + Jin10 collection (scripts.quant_personal_feed.cli run)
  research     Chinese tone scoring worker v42; it is also the only scheduler of
               the v43 daily snapshot and label-state refresh (no second task)
  daily-price  bounded v45 daily-bar / label worker
  health ROLE  business health JSON; exit 0 healthy, 1 unhealthy
  moomoo-auth  status | authorize --callback-port N  (server token store)

``manage.py run_worker`` only clears sessions; it is not a collector and is
not used here.
"""
from __future__ import annotations

import fcntl
import json
import os
import sqlite3
import sys
import time
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path("/app")
PORTAL = ROOT / "apps/quant_portal"
VAR = PORTAL / "var"
FEED_CONFIG = os.environ.get("QUANT_FEED_CONFIG", "configs/quant_portal_personal_feeds_linux_v1.json")
PRIVATE_DIRS = [VAR] + [ROOT / "artifacts" / name for name in (
    "news_sentiment_factor_v41_20261117", "news_sentiment_factor_v42_20261117",
    "news_sentiment_factor_v43_20261117", "news_sentiment_factor_v44_20261117",
    "news_sentiment_factor_v45_20261117", "news_sentiment_factor_v4_20261117",
    "market_observer_live")]
HEARTBEAT_STALE_SECONDS = int(os.environ.get("QUANT_HEARTBEAT_STALE_SECONDS", "180"))
JIN10_SUCCESS_STALE_SECONDS = int(os.environ.get("QUANT_JIN10_SUCCESS_STALE_SECONDS", "900"))
MOOMOO_PUSH_STALE_SECONDS = int(os.environ.get("QUANT_MOOMOO_PUSH_STALE_SECONDS", "300"))
RESEARCH_BACKLOG_STALE_SECONDS = int(os.environ.get("QUANT_RESEARCH_BACKLOG_STALE_SECONDS", "1800"))


def _age(value: str | None, now: datetime) -> float | None:
    if not value:
        return None
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return round((now - stamp).total_seconds(), 1)


def _prepare(require_postgres: bool, private_dirs: bool = True) -> None:
    os.chdir(ROOT)
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    # Only roles that mount the private volumes create their subdirectories;
    # migrate/health run on the read-only image filesystem.
    for path in PRIVATE_DIRS if private_dirs else ():
        path.mkdir(parents=True, exist_ok=True)
    if require_postgres and not os.environ.get("POSTGRES_HOST"):
        raise SystemExit("POSTGRES_HOST is required: Linux accounts live in PostgreSQL")
    token_file = os.environ.get("JIN10_MCP_TOKEN_FILE")
    if token_file and not os.environ.get("JIN10_MCP_TOKEN"):
        path = Path(token_file)
        if path.is_file():
            value = path.read_text(encoding="utf-8").strip()
            if value:
                # scripts.jin10_mcp_http reads this variable at call time.
                os.environ["JIN10_MCP_TOKEN"] = value


def _exec(argv: list[str], cwd: Path = ROOT) -> None:
    os.chdir(cwd)
    sys.stdout.flush()
    os.execvp(argv[0], argv)


def _feed_config() -> dict:
    return json.loads((ROOT / FEED_CONFIG).read_text(encoding="utf-8"))


def health_web() -> dict:
    from urllib.request import Request, urlopen
    # Django validates Host; probe with the first configured private host name.
    host = next((h.strip() for h in os.environ.get("ALLOWED_HOSTS", "").split(",") if h.strip()), "localhost")
    try:
        with urlopen(Request("http://127.0.0.1:8000/readyz/", headers={"Host": host}), timeout=4) as response:
            ok = response.status == 200
    except Exception as exc:  # noqa: BLE001 - report class only
        return {"role": "web", "healthy": False, "error_class": type(exc).__name__}
    return {"role": "web", "healthy": ok, "check": "readyz", "note": "page health only; not collector health"}


def _collector_lock_held() -> bool:
    lock = VAR / "personal_feed.lock"
    if not lock.exists():
        return False
    with lock.open("a+b") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return True
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        return False


def _moomoo_business(cache: Path, row: dict, now: datetime) -> dict:
    """moomoo push verdict from the session calendar, the subscription acknowledgements, the per-session entitlement
    observed so far and the connection history (scripts/quant_personal_feed/market_hours.py)."""
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from scripts.quant_personal_feed import market_hours as mh
    with closing(sqlite3.connect(f"file:{cache.as_posix()}?mode=ro", uri=True, timeout=3)) as con:
        events = [(s, a) for s, a in con.execute(
            "SELECT state, at_utc FROM provider_state_events WHERE provider='moomoo' ORDER BY id DESC LIMIT 200")][::-1]
        symbols = [s for (s,) in con.execute("SELECT state FROM symbol_state WHERE state<>'STOPPED'")]
        caps: dict[str, list[str]] = {}
        for session, state in con.execute("SELECT session, state FROM session_capabilities"):
            caps.setdefault(session, []).append(state)
    return mh.classify(
        row=row, events=events, symbols=symbols, caps=caps, cal=mh.load(ROOT), now=now,
        push_stale_s=MOOMOO_PUSH_STALE_SECONDS, heartbeat_stale_s=HEARTBEAT_STALE_SECONDS,
        first_push_grace_s=int(os.environ.get("QUANT_MOOMOO_FIRST_PUSH_GRACE_SECONDS", "600")),
        reconnect_grace_s=int(os.environ.get("QUANT_MOOMOO_RECONNECT_GRACE_SECONDS", "180")),
        extended=os.environ.get("QUANT_MOOMOO_EXTENDED_SESSIONS", "1") == "1",
        overnight=os.environ.get("QUANT_MOOMOO_OVERNIGHT", "0") == "1")


def health_feed() -> dict:
    now = datetime.now(timezone.utc)
    config = _feed_config()
    enabled = {"jin10": config["jin10"]["enabled"], "moomoo": config["moomoo"]["enabled"]}
    result = {"role": "feed", "process_lock_held": _collector_lock_held(), "providers": {}}
    cache = ROOT / config["cache_db"]
    rows = {}
    if cache.is_file():
        with closing(sqlite3.connect(f"file:{cache.as_posix()}?mode=ro", uri=True, timeout=3)) as con:
            con.row_factory = sqlite3.Row
            try:
                rows = {r["provider"]: dict(r) for r in con.execute("SELECT * FROM provider_state")}
            except sqlite3.Error:
                rows = {}
    healthy = result["process_lock_held"]
    for provider, on in enabled.items():
        row = rows.get(provider, {})
        item = {"enabled": on, "state": row.get("state")}
        if on and provider == "moomoo":
            try:
                item.update(_moomoo_business(cache, row, now))
                if not item["ok"]:
                    healthy = False
                result["providers"][provider] = item
                continue
            except Exception as exc:  # noqa: BLE001 - fall back to the old, strict rule below; class only
                item["session_rule_error_class"] = type(exc).__name__
        if on:
            heartbeat = _age(row.get("heartbeat_at"), now)
            if provider == "jin10":
                business = _age(row.get("last_success_at"), now)
                limit = JIN10_SUCCESS_STALE_SECONDS
            else:
                business = _age(row.get("last_push_at"), now)
                limit = MOOMOO_PUSH_STALE_SECONDS
            item.update(heartbeat_age_seconds=heartbeat, business_age_seconds=business,
                        business="OK" if business is not None and business <= limit else "STALE_OR_NONE")
            if heartbeat is None or heartbeat > HEARTBEAT_STALE_SECONDS or item["business"] != "OK":
                healthy = False
        result["providers"][provider] = item
    if not any(enabled.values()):
        result["mode"] = "NO_PROVIDER_ENABLED"
    result["supervisor"] = _supervisor_state("feed")
    if result["supervisor"].get("state") in ("HOLD", "BACKOFF", "RUNNING_HOLD", "RUNNING_DEGRADED"):
        healthy = False
    result["healthy"] = healthy
    return result


def health_research() -> dict:
    from scripts.news_sentiment_factor_v42.worker import status
    item = status()
    now = datetime.now(timezone.utc)
    business_age = _age(item.get("last_success_at_utc"), now)
    backlog = item.get("backlog") or 0
    business_ok = backlog == 0 or (business_age is not None and business_age <= RESEARCH_BACKLOG_STALE_SECONDS)
    alive = bool(item.get("process_alive_identity")) and (item.get("heartbeat_age_seconds") or 1e9) <= HEARTBEAT_STALE_SECONDS
    sup = _supervisor_state("research")
    return {"role": "research", "healthy": alive and business_ok and sup.get("state") in (None, "RUNNING"),
            "supervisor": sup, "state": item.get("state"),
            "process_alive_identity": item.get("process_alive_identity"),
            "heartbeat_age_seconds": item.get("heartbeat_age_seconds"), "backlog": backlog,
            "last_success_age_seconds": business_age,
            "daily_snapshot_state": item.get("daily_snapshot_state"),
            "price_label_states": item.get("price_label_states")}


def health_daily_price() -> dict:
    from scripts.news_sentiment_factor_v45.worker import status
    item = status()
    alive = bool(item.get("process_alive_identity")) and (item.get("heartbeat_age_seconds") or 1e9) <= HEARTBEAT_STALE_SECONDS
    sup = _supervisor_state("daily-price")
    return {"role": "daily-price", "healthy": alive and item.get("state") == "RUNNING" and sup.get("state") in (None, "RUNNING"),
            "supervisor": sup,
            "state": item.get("state"), "heartbeat_age_seconds": item.get("heartbeat_age_seconds"),
            "pause_reason": item.get("pause_reason"), "last_error_class": item.get("last_error_class"),
            "last_supplier_http_status": item.get("last_supplier_http_status"),
            "next_due_utc": item.get("next_due_utc"), "budget_used_today": item.get("budget_used_today")}


def _supervise(role: str) -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from supervisor import Supervisor, classify_daily, classify_feed, classify_research, policy_from_env
    policy = policy_from_env()
    if role == "feed":
        config = _feed_config()
        enabled = {"jin10": config["jin10"]["enabled"], "moomoo": config["moomoo"]["enabled"]}
        argv = [sys.executable, "-m", "scripts.quant_personal_feed.cli", "run", "--config", FEED_CONFIG]
        classify = lambda alive: classify_feed(ROOT / config["cache_db"], enabled, alive)  # noqa: E731
    elif role == "research":
        private = ROOT / "artifacts/news_sentiment_factor_v42_20261117"
        argv = [sys.executable, "-m", "scripts.news_sentiment_factor_v42.worker", "run"]
        classify = lambda alive: classify_research(private / "worker_state_private.json",  # noqa: E731
                                                   private / "worker_stop.flag", alive, policy.stale_heartbeat)
    else:
        private = ROOT / "artifacts/news_sentiment_factor_v45_20261117"
        argv = [sys.executable, "-m", "scripts.news_sentiment_factor_v45.worker", "run"]
        classify = lambda alive: classify_daily(private / "daily_worker_state_private.json",  # noqa: E731
                                                alive, policy.stale_heartbeat)
    return Supervisor(role, argv, classify, VAR / f"supervisor_{role}.json", policy).run()


def _supervisor_state(role: str) -> dict:
    path = VAR / f"supervisor_{role}.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def moomoo_auth(args: list[str]) -> int:
    from scripts.market_observer_v1.moomoo_webapi_oauth import authorize_quote_read, token_store
    store = token_store(ROOT / "artifacts/market_observer_live/moomoo_webapi_oauth.dpapi")
    if args[:1] == ["status"]:
        print(json.dumps(store.status()))
        return 0
    if args[:1] == ["authorize"]:
        port = int(args[args.index("--callback-port") + 1]) if "--callback-port" in args else 8790
        bind = args[args.index("--bind") + 1] if "--bind" in args else "0.0.0.0"
        print(json.dumps(authorize_quote_read(store, open_browser=False, callback_port=port, bind_host=bind)))
        return 0
    print("usage: moomoo-auth status | authorize [--callback-port N] [--bind 0.0.0.0|127.0.0.1]", file=sys.stderr)
    return 2


def main(argv: list[str]) -> int:
    role = argv[0] if argv else "web"
    if role == "web":
        _prepare(require_postgres=True)
        workers = os.environ.get("GUNICORN_WORKERS", "2")
        _exec(["gunicorn", "portal.wsgi:application", "--bind", "0.0.0.0:8000",
               "--workers", workers, "--threads", "2", "--timeout", "30",
               "--access-logfile", "-", "--error-logfile", "-"], cwd=PORTAL)
    if role == "migrate":
        _prepare(require_postgres=True, private_dirs=False)
        _exec(["python", "manage.py", "migrate", "--noinput"], cwd=PORTAL)
    if role == "manage":
        _prepare(require_postgres=True, private_dirs=False)
        _exec(["python", "manage.py", *argv[1:]], cwd=PORTAL)
    if role == "feed":
        _prepare(require_postgres=True)
        if os.environ.get("QUANT_OWNER_RESOLVER") != "django_orm":
            raise SystemExit("Linux feed requires QUANT_OWNER_RESOLVER=django_orm")
        return _supervise("feed")
    if role == "research":
        _prepare(require_postgres=False)
        return _supervise("research")
    if role == "daily-price":
        _prepare(require_postgres=False)
        return _supervise("daily-price")
    if role == "moomoo-auth":
        _prepare(require_postgres=False, private_dirs=False)
        return moomoo_auth(argv[1:])
    if role == "health":
        _prepare(require_postgres=False, private_dirs=False)
        target = argv[1] if len(argv) > 1 else "web"
        checks = {"web": health_web, "feed": health_feed, "research": health_research,
                  "daily-price": health_daily_price}
        if target not in checks:
            print(f"unknown health role {target}", file=sys.stderr)
            return 2
        started = time.monotonic()
        try:
            result = checks[target]()
        except Exception as exc:  # noqa: BLE001 - a crashed check is unhealthy
            result = {"role": target, "healthy": False, "error_class": type(exc).__name__}
        result["checked_at_utc"] = datetime.now(timezone.utc).isoformat()
        result["check_seconds"] = round(time.monotonic() - started, 3)
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result.get("healthy") else 1
    print(f"unknown role {role}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
