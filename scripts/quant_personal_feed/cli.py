"""Start, inspect or gracefully stop the local personal feed worker."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import threading
import time
from pathlib import Path

from .collector import ROOT, owner_id, run_jin10, run_moomoo, under_root
from .store import PersonalFeedStore, read_personal_cache, utc_now


def load_config(path: Path) -> dict:
    path = path.resolve()
    if not path.is_relative_to(ROOT) or not path.is_file():
        raise ValueError("Feed config must be an existing repository file")
    config = json.loads(path.read_text(encoding="utf-8"))
    if config.get("schema") != "quant_portal_personal_feeds_v1":
        raise ValueError("Unexpected personal feed config version")
    for key in ("portal_db", "cache_db", "mapping_receipt_path"):
        under_root(config[key])
    if not str(under_root(config["cache_db"])).startswith(str((ROOT / "apps/quant_portal/var").resolve())):
        raise ValueError("Private feed cache must stay under ignored personal var")
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
                    db.state(name,"DISCONNECTED",error_class=type(exc).__name__)
                finally:
                    db.close()
        thread = threading.Thread(target=wrapped,name=f"personal-{name}",daemon=True)
        thread.start()
        threads.append(thread)

    try:
        if config["moomoo"]["enabled"]:
            launch("moomoo",run_moomoo)
        if config["jin10"]["enabled"]:
            launch("jin10",run_jin10)
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
    parser.add_argument("--config",type=Path,default=ROOT / "configs/quant_portal_personal_feeds_v1.json")
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
                                     "business_calls_today":x["business_calls_today"]}
                                    for x in cache["states"]],
                          "quote_symbols":[x["symbol"] for x in cache["quotes"]],
                          "cached_news_items":cache["news_total"]},ensure_ascii=False))
        return
    run(args.config)


if __name__ == "__main__":
    main()
