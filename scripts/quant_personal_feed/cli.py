"""Start, inspect or gracefully stop the local personal feed worker."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import sqlite3
import threading
import time
import traceback
from pathlib import Path

from . import incidents
from .collector import ROOT, owner_id, run_jin10, run_moomoo, under_root
from .session_poll import run_moomoo_snapshot
from .store import PersonalFeedStore, read_personal_cache, utc_now


def recover_snapshot_sqlite(config: dict, owner: int, stop: threading.Event) -> None:
    """Restart only a transient local-cache SQLite failure, with a fixed ceiling."""
    for failure in range(3):
        if stop.is_set():
            return
        try:
            run_moomoo_snapshot(config, owner, stop)
            return
        except sqlite3.OperationalError:
            if failure == 2:
                raise
            stop.wait(2 ** (failure + 1))


def recover_jin10_sqlite(config: dict, owner: int, stop: threading.Event) -> None:
    """Recover lock contention without restarting the independent quote feed.

    Retry only SQLITE_BUSY/LOCKED, with >=60s spacing. Non-lock database errors
    fail closed. Supplier budgets are persisted by run_jin10 before requests.
    """
    diagnostic = ROOT / "apps/quant_portal/var/personal_feed_sqlite_errors.log"
    failure = 0
    while not stop.is_set():
        if stop.is_set():
            return
        try:
            run_jin10(config, owner, stop)
            return
        except sqlite3.OperationalError as exc:
            diagnostic.parent.mkdir(parents=True, exist_ok=True)
            if diagnostic.exists() and diagnostic.stat().st_size > 1_000_000:
                diagnostic.replace(diagnostic.with_name(
                    diagnostic.name + "." + utc_now().replace(":", "-")))
            with diagnostic.open("a", encoding="utf-8") as handle:
                # Stack locations only: no SQL values, source news, or locals.
                handle.write(f"{utc_now()} jin10 SQLite error code="
                             f"{getattr(exc, 'sqlite_errorcode', None)}\n")
                for frame in traceback.extract_tb(exc.__traceback__):
                    handle.write(f"  {Path(frame.filename).name}:{frame.lineno} {frame.name}\n")
            code = getattr(exc, "sqlite_errorcode", None)
            locked = ((code is not None and (code & 255) in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED))
                      or str(exc).lower() in {"database is locked", "database table is locked"})
            if not locked:
                raise
            failure = min(failure + 1, 4)
            stop.wait(min(300, 60 * 2 ** (failure - 1)))


def load_config(path: Path) -> dict:
    path = path.resolve()
    if not path.is_relative_to(ROOT) or not path.is_file():
        raise ValueError("Feed config must be an existing repository file")
    config = json.loads(path.read_text(encoding="utf-8"))
    if config.get("schema") not in {"quant_portal_personal_feeds_v1", "quant_portal_personal_feeds_v11"}:
        raise ValueError("Unexpected personal feed config version")
    for key in ("portal_db", "cache_db", "mapping_receipt_path"):
        under_root(config[key])
    if not str(under_root(config["cache_db"])).startswith(str((ROOT / "apps/quant_portal/var").resolve())):
        raise ValueError("Private feed cache must stay under ignored personal var")
    if not isinstance(config["moomoo"].get("snapshot_enabled", True), bool):
        raise ValueError("moomoo.snapshot_enabled must be a boolean")
    return config


def lock_file(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+b")
    handle.seek(0)
    if handle.read(1) == b"":
        handle.write(b"0")
        handle.flush()
    handle.seek(0)
    try:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(),fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        raise RuntimeError("Personal feed collector already runs") from None
    return handle


def run(config_path: Path) -> None:
    config = load_config(config_path)
    var = (ROOT / "apps/quant_portal/var").resolve()
    lock = lock_file(var / "personal_feed.lock")
    stop_path = var / "personal_feed.stop"
    pid_path = var / "personal_feed.pid.json"
    stop_path.unlink(missing_ok=True)
    user_id = owner_id(config)
    store = PersonalFeedStore(under_root(config["cache_db"]),user_id)
    store.ensure_selection(config["moomoo"]["priority_symbols"])
    store.close()
    stop = threading.Event()

    def request_stop(_signum, _frame):
        stop.set()

    signal.signal(signal.SIGINT,request_stop)
    signal.signal(signal.SIGTERM,request_stop)
    receipt = {"pid":os.getpid(),"started_at_utc":utc_now(),
               "config_sha256":hashlib.sha256(config_path.read_bytes()).hexdigest(),
               "loopback_only":True,"trade_calls":0}
    pid_path.write_text(json.dumps(receipt),encoding="utf-8")
    threads = []

    # Each provider opens its own SQLite connection. A failed source does not
    # terminate the other source or the loopback portal.
    def launch(name, target):
        def wrapped():
            try:
                target(config,user_id,stop)
            except Exception as exc:
                db = PersonalFeedStore(under_root(config["cache_db"]),user_id)
                try:
                    incidents.record(exc, db, provider=name, phase="THREAD", recorded_state="DISCONNECTED")
                    db.state(name,"DISCONNECTED",error_class=type(exc).__name__)
                finally:
                    db.close()
            finally:
                db = PersonalFeedStore(under_root(config["cache_db"]),user_id)
                try:
                    db.provider_metric(name,worker_alive=0)
                finally:
                    db.close()
        thread = threading.Thread(target=wrapped,name=f"personal-{name}",daemon=True)
        thread.start()
        threads.append(thread)

    try:
        if config["moomoo"]["enabled"]:
            launch("moomoo",run_moomoo)
            if config["moomoo"].get("snapshot_enabled", True):
                launch("moomoo_snapshot",recover_snapshot_sqlite)
            else:
                # A previous HTTP 439 is a policy block. Keep this decision in
                # configuration so an unrelated SQLite error cannot erase it.
                db = PersonalFeedStore(under_root(config["cache_db"]),user_id)
                try:
                    db.state("moomoo_snapshot","SECURITY_POLICY_BLOCKED",
                             error_class="WEBAPI_SECURITY_POLICY_BLOCKED")
                    db.provider_metric("moomoo_snapshot",worker_alive=0)
                finally:
                    db.close()
        if config["jin10"]["enabled"]:
            launch("jin10",recover_jin10_sqlite)
        while not stop.is_set():
            if stop_path.exists():
                stop.set()
                break
            time.sleep(1)
        for thread in threads:
            thread.join(timeout=30)
    finally:
        pid_path.unlink(missing_ok=True)
        stop_path.unlink(missing_ok=True)
        lock.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action",choices=("run","status","stop"))
    parser.add_argument("--config",type=Path,default=ROOT / "configs/quant_portal_personal_feeds_v11_20261116.json")
    args = parser.parse_args()
    config = load_config(args.config)
    var = (ROOT / "apps/quant_portal/var").resolve()
    if args.action == "stop":
        (var / "personal_feed.stop").write_text("stop\n",encoding="ascii")
        print("STOP_REQUESTED")
        return
    if args.action == "status":
        cache = read_personal_cache(under_root(config["cache_db"]),owner_id(config),news_limit=1)
        print(json.dumps({"pid_file_present":(var/"personal_feed.pid.json").exists(),
                          "cache_state":cache["cache_state"],
                          "states":[{"provider":x["provider"],"state":x["state"],
                                     "heartbeat_at":x["heartbeat_at"],
                                     "last_success_at":x["last_success_at"],
                                     "last_push_at":x.get("last_push_at"),
                                     "last_quote_saved_at":x.get("last_quote_saved_at"),
                                     "duplicate_quote_packets":x.get("duplicate_quote_packets"),
                                     "business_attempts_today":x.get("business_attempts_today"),
                                     "business_calls_today":x["business_calls_today"],
                                     "business_failure_total":x.get("business_failure_total"),
                                     "handshake_attempts_total":x.get("handshake_attempts_total"),
                                     "worker_alive":x.get("worker_alive"),
                                     "coverage_gap":x.get("coverage_gap")}
                                    for x in cache["states"]],
                          "quote_symbols":[x["symbol"] for x in cache["quotes"]],
                          "desired_symbols":[x["symbol"] for x in cache.get("selection",[]) if x["desired"]],
                          "cached_news_items":cache["news_total"]},ensure_ascii=False))
        return
    run(args.config)


if __name__ == "__main__":
    main()
