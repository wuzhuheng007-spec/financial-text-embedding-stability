"""Private market-observer control: start, status, stop, render, replay-jin10."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from .replay import replay_jin10
from .moomoo_probe_import import import_private_projection
from .moomoo_webapi_oauth import LocalTokenStore, authorize_quote_read
from .moomoo_quota_probe import probe_subscription_quota
from .moomoo_ws import MoomooWebSocketPush, validate_live_config
from .service import _view_context, load_config, private_output_path, run_service
from .store import Store


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = REPO_ROOT / "configs" / "market_observer_v1.example.json"
MOOMOO_TOKEN_FILE = REPO_ROOT / "artifacts" / "market_observer_live" / "moomoo_webapi_oauth.dpapi"


@contextmanager
def file_lock(path: Path) -> Iterator[None]:
    """OS lock survives a process crash; lock file itself is never deleted."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise RuntimeError("market observer already running or launch in progress") from exc
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise RuntimeError("market observer already running or launch in progress") from exc
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)


def _paths(config_path: Path) -> tuple[dict, Path, Path, Path]:
    config = load_config(config_path)
    db = private_output_path(config["db_path"], REPO_ROOT)
    dashboard = private_output_path(config["dashboard_path"], REPO_ROOT)
    lock = db.parent / "observer.lock"
    return config, db, dashboard, lock


def _running(session: dict | None, heartbeat_seconds: float) -> bool:
    if not session or session["ended_at"] is not None:
        return False
    try:
        heartbeat = datetime.fromisoformat(session["heartbeat_at"].replace("Z", "+00:00"))
        age = (datetime.now(timezone.utc) - heartbeat).total_seconds()
        return age <= max(30, heartbeat_seconds * 3)
    except (TypeError, ValueError):
        return False


def cmd_status(config_path: Path) -> dict:
    config, db, dashboard, _ = _paths(config_path)
    live = config.get("live_push") or {}
    push_receipt = None
    if live.get("status_path"):
        path = private_output_path(live["status_path"], REPO_ROOT)
        if path.is_file():
            push_receipt = json.loads(path.read_text(encoding="utf-8"))
    with Store(db) as store:
        session = store.latest_session()
        active = bool(_running(session, config["heartbeat_seconds"])
                      and session and session["mode"] == "LIVE_PUSH"
                      and session["status"].startswith("CONNECTED_")
                      and push_receipt and push_receipt.get("connection_state", "").startswith("CONNECTED_"))
        gap_count = store.connection.execute("SELECT COUNT(*) FROM collection_gaps").fetchone()[0]
        quote_count = store.connection.execute("SELECT COUNT(*) FROM quotes_or_bars").fetchone()[0]
        probe_count = store.connection.execute("SELECT COUNT(*) FROM probe_observations").fetchone()[0]
        verified_count = store.connection.execute("SELECT COUNT(*) FROM instrument_resolutions").fetchone()[0]
        news_count = store.connection.execute("SELECT COUNT(*) FROM news_items").fetchone()[0]
        return {"session_id": session["session_id"] if session else None,
                "state": session["status"] if session else "NOT_STARTED",
                "running_heartbeat_recent": _running(session, config["heartbeat_seconds"]),
                "feed_mode": config["feed_mode"],
                "live_push_adapter_installed": True,
                "webapi_oauth": LocalTokenStore(MOOMOO_TOKEN_FILE).status(),
                "webapi_push": {"connection_state": (push_receipt or {}).get("connection_state", "NOT_TESTED"),
                                "receipt_time_utc": (push_receipt or {}).get("observed_at_utc"),
                                "active_subscriptions": (push_receipt or {}).get("enabled_subscriptions", []) if active else [],
                                "last_acknowledged_subscriptions": (push_receipt or {}).get("accepted_subscriptions_this_session", []),
                                "skipped_subscriptions": (push_receipt or {}).get("skipped_subscriptions", [])},
                "quote_rows_local": quote_count, "quarantined_probe_snapshots_local": probe_count,
                "official_instrument_resolutions_local": verified_count,
                "news_versions_local": news_count,
                "coverage_gaps_local": gap_count, "dashboard_private_path": str(dashboard),
                "provider_calls_this_command": 0}


def cmd_start(config_path: Path) -> dict:
    config, db, _, lock = _paths(config_path)
    if config["feed_mode"] == "LIVE_PUSH":
        validate_live_config(config.get("live_push", {}), repo_root=REPO_ROOT)
        if LocalTokenStore(MOOMOO_TOKEN_FILE).status()["credential_state"] != "QUOTE_READ_AUTHORIZED":
            raise RuntimeError("separate moomoo WebAPI quote:read browser authorization required")
        with Store(db) as store:
            missing = [code for code in {r["instrument_id"] for r in config["live_push"]["subscriptions"]}
                       if not (row := store.connection.execute(
                           "SELECT identity_status FROM instruments WHERE instrument_id=?", (code,)).fetchone())
                       or row["identity_status"] != "VERIFIED"]
            if missing:
                raise RuntimeError("officially mapped instruments are not imported into the private observer database")
    launch_lock = lock.with_name("observer_launch.lock")
    with file_lock(launch_lock):
        try:
            with file_lock(lock):
                pass
        except RuntimeError:
            raise RuntimeError("market observer service already holds the single-instance lock")
        session_id = str(uuid.uuid4())
        log_path = db.parent / "service.log"
        child_args = [sys.executable, "-m", "scripts.market_observer_v1.cli", "serve",
                      "--config", str(config_path.resolve()), "--session-id", session_id]
        child_env = os.environ.copy()
        # Offline service has no reason to inherit provider credentials.
        child_env.pop("JIN10_MCP_TOKEN", None)
        with log_path.open("ab") as log:
            flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            child = subprocess.Popen(child_args, cwd=REPO_ROOT, env=child_env,
                                     stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                     creationflags=flags,
                                     start_new_session=os.name != "nt")
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if child.poll() is not None:
                raise RuntimeError(f"observer child exited during startup (code {child.returncode}); inspect private service log")
            with Store(db) as store:
                row = store.connection.execute("SELECT status FROM sessions WHERE session_id=?",
                                               (session_id,)).fetchone()
                if row:
                    return {"session_id": session_id, "pid": child.pid,
                            "state": row["status"], "feed_mode": config["feed_mode"],
                            "provider_connected": row["status"].startswith("CONNECTED_"),
                            "note": ("Provider connection must be checked with status" if config["feed_mode"] == "LIVE_PUSH"
                                     else "Service is running without an authorized live feed")}
            time.sleep(0.1)
        raise RuntimeError("observer child did not establish a session within five seconds; inspect private service log")


def cmd_stop(config_path: Path) -> dict:
    config, db, _, _ = _paths(config_path)
    with Store(db) as store:
        session = store.latest_session()
        if not session or session["ended_at"] is not None:
            return {"state": "NOT_RUNNING", "provider_calls": 0}
        store.request_stop(session["session_id"])
        session_id = session["session_id"]
    deadline = time.monotonic() + max(10, config["heartbeat_seconds"] * 3)
    while time.monotonic() < deadline:
        with Store(db) as store:
            current = store.connection.execute("SELECT ended_at,status FROM sessions WHERE session_id=?",
                                               (session_id,)).fetchone()
            if current and current["ended_at"]:
                return {"session_id": session_id, "state": current["status"],
                        "stop_confirmed": True, "provider_calls": 0}
        time.sleep(0.2)
    return {"session_id": session_id, "state": "STOP_REQUESTED_UNCONFIRMED",
            "stop_confirmed": False, "provider_calls": 0}


def cmd_render(config_path: Path) -> dict:
    config, db, dashboard, _ = _paths(config_path)
    from .dashboard import render_dashboard
    with Store(db) as store:
        store.seed_candidate_instruments(config.get("observation_pool", []))
        html = render_dashboard(store.dashboard_snapshot(include_private_text=True),
                                **_view_context(config, REPO_ROOT))
    dashboard.write_text(html, encoding="utf-8")
    return {"dashboard_private_path": str(dashboard), "provider_calls": 0}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("start", "status", "stop", "render", "seed", "serve"):
        p = sub.add_parser(name)
        p.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
        if name == "serve":
            p.add_argument("--session-id", required=True)
    replay = sub.add_parser("replay-jin10")
    replay.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    replay.add_argument("--source-db", type=Path,
                        default=Path("artifacts/jin10_20261115/jin10_v2.sqlite"))
    replay.add_argument("--limit", type=int, default=100)
    probe = sub.add_parser("import-moomoo-probe")
    probe.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    probe.add_argument("--projection", type=Path, required=True)
    sub.add_parser("moomoo-oauth-login", help="Open separate quote:read-only WebAPI consent in local browser")
    sub.add_parser("moomoo-oauth-status", help="Show only non-secret local OAuth status")
    sub.add_parser("moomoo-quota-probe", help="One read-only WebAPI subscription quota GET; no retry")
    args = parser.parse_args(argv)
    try:
        if args.command == "start":
            receipt = cmd_start(args.config)
        elif args.command == "moomoo-oauth-login":
            receipt = authorize_quote_read(LocalTokenStore(MOOMOO_TOKEN_FILE))
        elif args.command == "moomoo-oauth-status":
            receipt = LocalTokenStore(MOOMOO_TOKEN_FILE).status()
        elif args.command == "moomoo-quota-probe":
            receipt = probe_subscription_quota(
                LocalTokenStore(MOOMOO_TOKEN_FILE),
                REPO_ROOT / "artifacts/market_observer_live/quota_probe_20261115.json")
        elif args.command == "status":
            receipt = cmd_status(args.config)
        elif args.command == "stop":
            receipt = cmd_stop(args.config)
        elif args.command == "render":
            receipt = cmd_render(args.config)
        elif args.command == "seed":
            config, db, _, _ = _paths(args.config)
            with Store(db) as store:
                inserted = store.seed_candidate_instruments(config.get("observation_pool", []))
            receipt = {"candidate_rows_inserted": inserted,
                       "identity_status": "PENDING_OFFICIAL_LOOKUP", "provider_calls": 0}
        elif args.command == "replay-jin10":
            _, db, _, _ = _paths(args.config)
            source_db = private_output_path(args.source_db, REPO_ROOT)
            receipt = replay_jin10(source_db, db, limit=args.limit)
        elif args.command == "import-moomoo-probe":
            config, db, _, _ = _paths(args.config)
            receipt = import_private_projection(args.projection, db, config["observation_pool"])
        else:
            config, db, _, lock = _paths(args.config)
            with file_lock(lock):
                source = None
                if config["feed_mode"] == "LIVE_PUSH":
                    live = config.get("live_push", {})
                    validate_live_config(live, repo_root=REPO_ROOT)
                    status_path = private_output_path(
                        live.get("status_path", "artifacts/market_observer_live/push_status.json"), REPO_ROOT)
                    source = MoomooWebSocketPush(
                        live=live, token_store=LocalTokenStore(MOOMOO_TOKEN_FILE),
                        status_path=status_path)
                final_state = run_service(config=config, repo_root=REPO_ROOT,
                                          session_id=args.session_id, source=source)
            receipt = {"session_id": args.session_id, "state": final_state,
                       "provider_calls": 0}
        print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))
        return 0
    except Exception as exc:
        print(json.dumps({"error_type": type(exc).__name__, "error": str(exc),
                          "provider_calls_this_command": 0}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
