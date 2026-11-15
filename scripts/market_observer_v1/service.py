"""Bounded, read-only observation loop with honest feed and coverage states.

No provider implementation is bundled here. A live PUSH source must first be
verified against actual entitlements and injected by a separate adapter.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from .store import Store, digest, require_utc, utc_now


class PushSource(Protocol):
    provider_name: str

    def connect(self) -> None: ...
    def next_message(self, timeout_seconds: float) -> dict[str, Any] | None: ...
    def close(self) -> None: ...


class AuthorizationError(RuntimeError):
    """401/403 or absent quote permission: stop rather than retry."""


class QuotaError(RuntimeError):
    """Provider quota or rate budget reached: stop and preserve gap."""


def load_config(path: str | Path) -> dict[str, Any]:
    config = json.loads(Path(path).read_text(encoding="utf-8"))
    required = {"schema", "db_path", "dashboard_path", "feed_mode", "heartbeat_seconds",
                "max_disk_mib", "max_events_per_session", "max_reconnect_attempts",
                "reconnect_initial_seconds", "reconnect_max_seconds"}
    missing = required - config.keys()
    if missing:
        raise ValueError(f"observer config missing {sorted(missing)}")
    if config["schema"] != "market_observer_service_config_v1":
        raise ValueError("unknown observer service configuration schema")
    if config["feed_mode"] not in {"NO_FEED", "LIVE_PUSH"}:
        raise ValueError("feed_mode must be NO_FEED or LIVE_PUSH")
    if config["feed_mode"] == "LIVE_PUSH" and not config.get("quote_push_verified"):
        raise ValueError("LIVE_PUSH requires verified quote push capability")
    if config.get("news_poll_interval_seconds") is not None and not config.get("news_quota_confirmed"):
        raise ValueError("news polling requires verified quota and retention terms")
    if config.get("news_quota_confirmed") and not config.get("max_provider_requests_per_session"):
        raise ValueError("confirmed news polling still requires a positive request budget")
    for name in ("heartbeat_seconds", "max_disk_mib", "max_events_per_session",
                 "max_reconnect_attempts", "reconnect_initial_seconds", "reconnect_max_seconds"):
        if not isinstance(config[name], (int, float)) or config[name] <= 0:
            raise ValueError(f"{name} must be positive")
    if config.get("max_runtime_minutes") is not None and config["max_runtime_minutes"] <= 0:
        raise ValueError("max_runtime_minutes must be null or positive")
    return config


def private_output_path(value: str | Path, repo_root: Path) -> Path:
    """Resolve and confine runtime files to the ignored project artifacts tree."""
    root = (repo_root / "artifacts").resolve()
    target = (repo_root / value).resolve() if not Path(value).is_absolute() else Path(value).resolve()
    if not target.is_relative_to(root):
        raise ValueError("runtime DB and dashboard must stay within ignored artifacts/")
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def database_bytes(db_path: Path) -> int:
    return sum(p.stat().st_size for p in (db_path, Path(str(db_path) + "-wal"),
                                         Path(str(db_path) + "-shm")) if p.exists())


def backoff_seconds(attempt: int, initial: float, cap: float) -> float:
    return min(cap, initial * (2 ** min(attempt, 20)))


def interruptible_backoff(store: Store, session_id: str, *, delay_seconds: float,
                          heartbeat_seconds: float) -> bool:
    """Wait the full bounded backoff while honoring durable stop requests.

    Return True when a stop was requested before the retry deadline.
    """
    deadline = time.monotonic() + delay_seconds
    while True:
        if store.heartbeat(session_id, "PROVIDER_DISCONNECTED"):
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(remaining, heartbeat_seconds))


def process_message(store: Store, message: dict[str, Any], provider_name: str) -> tuple[str, int]:
    """Persist a single *already acquired* push message and its complete marker."""
    kind = message.get("kind")
    if kind == "QUOTE":
        quote = message["quote"]
        quote_id, created = store.insert_quote(
            provider=provider_name, instrument_id=quote["instrument_id"],
            kind=quote["kind"], provider_time=quote["provider_time"],
            received_at=quote["received_at"], source_timezone=quote["source_timezone"],
            market_session=quote["market_session"], delay_status=quote["delay_status"],
            payload=quote["payload"])
        if message.get("complete_marker"):
            store.set_checkpoint(provider_name, "quotes", complete_cursor=message["complete_marker"],
                                 latest_seen_at=quote["provider_time"])
        return ("QUOTE_CREATED" if created else "QUOTE_DUPLICATE"), quote_id
    if kind == "NEWS":
        item = message["news"]
        news_id, created = store.insert_news(provider=provider_name, **item)
        if message.get("complete_marker"):
            store.set_checkpoint(provider_name, "news", complete_cursor=message["complete_marker"],
                                 latest_seen_at=item.get("provider_time"))
        return ("NEWS_VERSION_CREATED" if created else "NEWS_DUPLICATE"), news_id
    if kind == "GAP":
        gap = message["gap"]
        gap_id = store.record_gap(provider_name, gap["stream_key"], gap["reason"],
                                  started_at=gap.get("started_at"), ended_at=gap.get("ended_at"),
                                  detail=gap.get("detail"))
        return "GAP_RECORDED", gap_id
    raise ValueError(f"unknown provider message kind {kind!r}")


def _write_dashboard(store: Store, dashboard_path: Path) -> None:
    from .dashboard import render_dashboard
    html = render_dashboard(store.dashboard_snapshot(include_private_text=True))
    temporary = dashboard_path.with_name(dashboard_path.name + ".tmp")
    temporary.write_text(html, encoding="utf-8")
    os.replace(temporary, dashboard_path)


def run_service(*, config: dict[str, Any], repo_root: Path, session_id: str,
                source: PushSource | None = None, max_cycles: int | None = None) -> str:
    """Run until stop, cap, or explicit provider failure; no hidden retries.

    `max_cycles` is only a focused test hook. CLI never uses it for a live run.
    """
    db_path = private_output_path(config["db_path"], repo_root)
    dashboard_path = private_output_path(config["dashboard_path"], repo_root)
    if config["feed_mode"] == "LIVE_PUSH" and source is None:
        raise ValueError("no verified live push adapter is installed")
    if config["feed_mode"] == "NO_FEED" and source is not None:
        raise ValueError("NO_FEED must not call a provider adapter")
    with Store(db_path) as store:
        store.seed_candidate_instruments(config.get("observation_pool", []))
        store.create_session(session_id, mode=config["feed_mode"], pid=os.getpid(),
                             config_hash=digest(config))
        status = "NO_AUTHORIZED_LIVE_FEED" if source is None else "CONNECTING"
        started = time.monotonic()
        cycles = 0
        events = 0
        reconnects = 0
        connected = False
        gap_started: str | None = None
        try:
            while True:
                if store.heartbeat(session_id, status):
                    status = "STOPPED_BY_USER"
                    break
                _write_dashboard(store, dashboard_path)
                if database_bytes(db_path) >= config["max_disk_mib"] * 1024 * 1024:
                    store.record_gap(source.provider_name if source else "LOCAL", "storage",
                                     "DISK_CAP_REACHED")
                    status = "STOPPED_DISK_CAP"
                    break
                if events >= config["max_events_per_session"]:
                    status = "STOPPED_EVENT_BUDGET"
                    break
                if config.get("max_runtime_minutes") is not None and (
                    time.monotonic() - started >= 60 * config["max_runtime_minutes"]
                ):
                    status = "STOPPED_RUNTIME_CAP"
                    break
                if max_cycles is not None and cycles >= max_cycles:
                    status = "STOPPED_TEST_CYCLES"
                    break
                cycles += 1
                if source is None:
                    time.sleep(config["heartbeat_seconds"])
                    continue
                try:
                    if not connected:
                        source.connect()
                        connected = True
                        reconnects = 0
                        if gap_started:
                            store.record_gap(source.provider_name, "push", "DISCONNECT_RECOVERED",
                                             started_at=gap_started, ended_at=utc_now(),
                                             coverage_status="RECOVERY_UNVERIFIED")
                            gap_started = None
                        status = "CONNECTED_WAITING_FOR_DATA"
                    message = source.next_message(float(config["heartbeat_seconds"]))
                    if message is not None:
                        process_message(store, message, source.provider_name)
                        events += 1
                        status = "CONNECTED_DATA_RECEIVED"
                except (AuthorizationError, QuotaError) as exc:
                    gap_started = gap_started or utc_now()
                    store.record_gap(source.provider_name, "push", type(exc).__name__,
                                     started_at=gap_started, detail={"error_type": type(exc).__name__})
                    status = "STOPPED_AUTHORIZATION_OR_QUOTA"
                    break
                except Exception as exc:
                    connected = False
                    source.close()
                    gap_started = gap_started or utc_now()
                    status = "PROVIDER_DISCONNECTED"
                    reconnects += 1
                    if reconnects > config["max_reconnect_attempts"]:
                        store.record_gap(source.provider_name, "push", "RECONNECT_BUDGET_EXHAUSTED",
                                         started_at=gap_started,
                                         detail={"error_type": type(exc).__name__, "attempts": reconnects})
                        status = "STOPPED_DISCONNECTED"
                        break
                    delay = backoff_seconds(reconnects - 1,
                                            config["reconnect_initial_seconds"],
                                            config["reconnect_max_seconds"])
                    if interruptible_backoff(store, session_id, delay_seconds=delay,
                                             heartbeat_seconds=float(config["heartbeat_seconds"])):
                        status = "STOPPED_BY_USER"
                        break
        finally:
            if source is not None:
                source.close()
            if gap_started and status not in {"STOPPED_DISCONNECTED", "STOPPED_AUTHORIZATION_OR_QUOTA"}:
                store.record_gap(source.provider_name if source else "LOCAL", "push",
                                 "UNRESOLVED_DISCONNECT", started_at=gap_started)
            store.end_session(session_id, status)
            _write_dashboard(store, dashboard_path)
        return status
