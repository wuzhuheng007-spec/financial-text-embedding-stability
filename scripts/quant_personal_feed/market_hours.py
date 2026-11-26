"""When a moomoo US quote push is due, and the feed health verdict that follows from it.

The health check used to call the push stale whenever the last push was older than 300 s, so every night, weekend and
holiday (2026-11-26, Thanksgiving) read as a failure. A missing push is only a fault while a push is due:

* the market phase comes from the registered XNYS session file (holidays and early closes): regular hours, plus the
  extended sessions when the service enables them (QUANT_MOOMOO_EXTENDED_SESSIONS) and the subscription has delivered
  that session before (``session_capabilities``: the account's entitlement, observed, never assumed). Pre-market runs
  from 04:00 ET, after-hours until 4 h after the close (20:00 ET, or 17:00 ET on an early-close day). Overnight is off
  unless QUANT_MOOMOO_OVERNIGHT=1 and observed.
* the first push of a due period gets a bounded grace (QUANT_MOOMOO_FIRST_PUSH_GRACE_SECONDS), counted from the later of
  the period start and the moment the connection came up: WAITING_FIRST_PUSH ("等待首条推送") inside it, NO_FIRST_PUSH
  (a fault) after it.
* authentication failure, subscription failure and heartbeat timeout are faults at once, in any phase. A disconnect is
  a fault once it outlasts the reconnect grace, also while the market is closed (the next open would be missed).
* a date outside the calendar, an unregistered calendar or any error keeps the old, strict rule (UNKNOWN_SESSION).

``classify`` is pure: every input is passed in, so the replay tests run without a feed, a network or a clock."""
from __future__ import annotations

import csv
import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

CALENDAR = Path("research/x_stance_v1/calendar/sessions_xnys_20191101_20261231.csv")
CALENDAR_SHA256 = "b14e9f02fc19a50b9232f7340e1cc3ed074a17f91b5e645e5be0242176f1c0c0"
PRE_OPEN = timedelta(hours=5, minutes=30)          # 04:00 ET before a 09:30 ET open
AFTER_CLOSE = timedelta(hours=4)                   # 20:00 ET after 16:00; 17:00 ET after a 13:00 early close
UP_STATES = ("CONNECTED_DELAY_UNKNOWN", "CONNECTED_MARKET_CLOSED", "CONNECTED_REALTIME", "PARTIAL_SYMBOL_UNAVAILABLE")
DOWN_STATES = ("DISCONNECTED", "CONNECTING")
SUBSCRIBED = ("SUBSCRIBED", "RECEIVED")
EXTENDED_EVIDENCE = ("HAS_SOURCE_TIME", "SNAPSHOT_TIME_UNKNOWN")
HEALTHY = ("OK", "MARKET_CLOSED", "WAITING_FIRST_PUSH", "RECONNECTING", "NOT_DUE_SESSION_NOT_ENTITLED", "NO_SUBSCRIPTIONS")
LABEL_ZH = {
    "OK": "行情正常", "MARKET_CLOSED": "休市，不应有推送", "WAITING_FIRST_PUSH": "等待首条推送",
    "NO_FIRST_PUSH": "开市后宽限期内没有收到首条推送", "STALE_IN_SESSION": "交易时段内推送中断",
    "RECONNECTING": "正在重连", "DISCONNECTED": "连接断开超过重连宽限", "AUTH_FAILED": "鉴权失败",
    "SUBSCRIPTION_FAILED": "订阅失败", "HEARTBEAT_TIMEOUT": "心跳超时", "WORKER_STOPPED": "采集进程已停止",
    "NOT_DUE_SESSION_NOT_ENTITLED": "当前时段未开通或从未收到该时段行情，不应有推送",
    "NO_SUBSCRIPTIONS": "未选择订阅标的", "UNKNOWN_SESSION": "交易时段未知，按严格规则判断",
}


def load(root: Path) -> dict:
    p = root / CALENDAR
    raw = p.read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    rows = [(r["session_date"], datetime.fromisoformat(r["open_utc"]), datetime.fromisoformat(r["close_utc"]), r.get("session_type"))
            for r in csv.DictReader(raw.decode("utf-8").splitlines())]
    return {"rows": sorted(rows), "sha256": sha, "registered": sha == CALENDAR_SHA256, "path": str(CALENDAR)}


def parse(value) -> datetime | None:
    if not value:
        return None
    try:
        t = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def phase(cal: dict | None, now: datetime, *, extended: bool = True, overnight: bool = False) -> dict:
    """{"phase": REGULAR | PRE_MARKET | AFTER_HOURS | CLOSED | UNKNOWN, "push_expected": bool | None, "due_since": ...}"""
    rows = (cal or {}).get("rows") or []
    if not rows or not cal.get("registered") or now < rows[0][1] - PRE_OPEN or now > rows[-1][2] + AFTER_CLOSE:
        return {"phase": "UNKNOWN", "push_expected": None, "session": None, "calendar_sha256": (cal or {}).get("sha256")}
    for d, o, c, kind in rows:
        if o - PRE_OPEN <= now < c + AFTER_CLOSE:
            ph = "REGULAR" if o <= now < c else ("PRE_MARKET" if now < o else "AFTER_HOURS")
            exp = ph == "REGULAR" or extended
            due = (o - PRE_OPEN if extended else o) if ph != "AFTER_HOURS" or extended else None
            return {"phase": ph, "push_expected": exp, "session": d, "session_type": kind, "open_utc": o.isoformat(),
                    "close_utc": c.isoformat(), "due_since": due.isoformat() if (exp and due) else None, "calendar_sha256": cal["sha256"]}
    nxt = next((r for r in rows if r[1] > now), None)
    return {"phase": "CLOSED", "push_expected": bool(overnight), "session": None, "next_session": nxt[0] if nxt else None,
            "next_open_utc": nxt[1].isoformat() if nxt else None, "due_since": None, "calendar_sha256": cal["sha256"]}


def connected_since(events: list[tuple[str, str]]) -> datetime | None:
    """The time the connection last came up: the first up-state after the last down-state (events oldest first)."""
    since = None
    for state, at in events:
        if state in UP_STATES:
            since = since or parse(at)
        else:
            since = None
    return since


def classify(*, row: dict | None, events: list[tuple[str, str]], symbols: list[str], caps: dict[str, list[str]],
             cal: dict | None, now: datetime, push_stale_s: float, heartbeat_stale_s: float, first_push_grace_s: float,
             reconnect_grace_s: float, extended: bool = True, overnight: bool = False) -> dict:
    """The moomoo business verdict. row: provider_state; events: provider_state_events (oldest first); symbols:
    symbol_state.state per subscribed symbol; caps: session -> session_capabilities states."""
    row = row or {}
    state = row.get("state")
    hb = parse(row.get("heartbeat_at")); hb_age = (now - hb).total_seconds() if hb else None
    push = parse(row.get("last_push_at")); push_age = (now - push).total_seconds() if push else None
    ext_seen = {s for s, st in caps.items() if any(x in EXTENDED_EVIDENCE for x in st)}
    ext_ok = extended and {"PREMARKET", "AFTERHOURS"} <= ext_seen
    ovn_ok = overnight and "OVERNIGHT" in ext_seen
    ph = phase(cal, now, extended=ext_ok, overnight=ovn_ok)
    out = {"state": state, "heartbeat_age_seconds": round(hb_age, 1) if hb_age is not None else None,
           "business_age_seconds": round(push_age, 1) if push_age is not None else None, "last_push_at": row.get("last_push_at"),
           "market": ph, "extended_sessions": {"enabled": extended, "observed": sorted(ext_seen), "used": ext_ok, "overnight_used": ovn_ok}}

    def verdict(v, **kw):
        out.update(business=v, business_zh=LABEL_ZH[v], ok=v in HEALTHY, **kw)
        return out

    # faults at once, in any phase
    if row.get("worker_alive") == 0:
        return verdict("WORKER_STOPPED")
    if state == "AUTH_EXPIRED" or row.get("last_error_class") == "AUTHORIZATION":
        return verdict("AUTH_FAILED")
    if state == "RATE_LIMITED" or (symbols and not any(s in SUBSCRIBED or s == "CONNECTING" for s in symbols)):
        return verdict("SUBSCRIPTION_FAILED", unavailable_symbols=sum(1 for s in symbols if s == "UNAVAILABLE"))
    if hb_age is None or hb_age > heartbeat_stale_s:
        return verdict("HEARTBEAT_TIMEOUT")
    if state == "NO_SUBSCRIPTIONS":
        return verdict("NO_SUBSCRIPTIONS")
    if state in DOWN_STATES:
        start = None                                      # the first event of the trailing run of down states
        for st, at in reversed(events):
            if st not in DOWN_STATES:
                break
            start = parse(at)
        down_s = (now - start).total_seconds() if start else None
        out["down_seconds"] = round(down_s, 1) if down_s is not None else None
        return verdict("RECONNECTING" if down_s is not None and down_s <= reconnect_grace_s else "DISCONNECTED")
    # connected: is a push due, and has the first one arrived?
    if push_age is not None and push_age <= push_stale_s:
        return verdict("OK")
    if ph["push_expected"] is None:
        return verdict("UNKNOWN_SESSION")                # outside the calendar: the old, strict rule
    if ph["push_expected"] is False:
        if ph["phase"] in ("PRE_MARKET", "AFTER_HOURS"):
            return verdict("NOT_DUE_SESSION_NOT_ENTITLED")
        return verdict("MARKET_CLOSED")
    up = connected_since(events)
    due = parse(ph.get("due_since")) or now
    grace_from = max(due, up) if up else due
    out["first_push_grace"] = {"from_utc": grace_from.isoformat(), "seconds": first_push_grace_s, "connected_since_utc": up.isoformat() if up else None}
    if push is not None and push >= grace_from:
        return verdict("STALE_IN_SESSION")              # the period's first push came, then the pushes stopped
    if (now - grace_from).total_seconds() <= first_push_grace_s:
        return verdict("WAITING_FIRST_PUSH", grace_left_seconds=round(first_push_grace_s - (now - grace_from).total_seconds(), 1))
    return verdict("NO_FIRST_PUSH")
