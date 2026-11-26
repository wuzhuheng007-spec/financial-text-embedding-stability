"""Evidence for the first due quote period after a feed release: which confirmed subscriptions received a valid
pre-market quote in [start, end), when, and what was left out. Read-only; prints no symbol and no price.

Rules (2026-11-26 audit):
* every time is parsed to UTC before it is compared; SQL only pre-filters with a margin, so a sub-second record in the
  first second of the window (09:00:00.123Z sorts before "09:00:00Z" as text) is still counted;
* the window is half-open [start, end);
* a valid pre-market quote: session PREMARKET from the push channel, a provider source time inside [start, end), and that
  source time falls in the pre-market phase of the registered XNYS calendar. Old snapshots (source time before start),
  missing source times and records whose session cannot be confirmed are listed apart and never count as first quotes;
* subscriptions are shown as S1..Sn (sorted, never named); the mapping is not printed;
* the gap is measured from start, across any reconnect, to the first valid quote (or to end): a reconnect restarts the
  health grace but not this clock.

CLI (inside the feed container): python -m scripts.quant_personal_feed.first_quote_evidence START [END]"""
from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import market_hours as mh

UTC = timezone.utc
CONFIRMED = ("SUBSCRIBED", "RECEIVED")
VALID_CHANNELS = ("WS_QUOTE",)


def utc(value) -> datetime | None:
    if value is None or value == "":
        return None
    try:
        t = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return (t if t.tzinfo else t.replace(tzinfo=UTC)).astimezone(UTC)


def iso(t: datetime | None) -> str | None:
    return t.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z") if t else None


def in_window(t: datetime | None, start: datetime, end: datetime) -> bool:
    return t is not None and start <= t < end


def labels(symbols: list[str]) -> tuple[dict[str, str], str]:
    order = sorted(set(symbols))
    m = {s: f"S{i + 1}" for i, s in enumerate(order)}
    return m, hashlib.sha256(json.dumps(order).encode()).hexdigest()[:16]


def match_key(symbol: str, salt: bytes | None) -> str | None:
    """Cross-window matching key: HMAC-SHA256 under a salt that stays on the observing machine. Without the salt a key
    cannot be tied to a symbol (a plain hash of a ticker could be brute-forced; this cannot)."""
    return hmac.new(salt, symbol.encode(), hashlib.sha256).hexdigest()[:16] if salt else None


def classify_row(row: dict, start: datetime, end: datetime, cal: dict | None) -> str:
    """VALID_PREMARKET | OLD_SNAPSHOT | SOURCE_TIME_MISSING | SESSION_UNCONFIRMED | OTHER_SESSION | SOURCE_AFTER_WINDOW"""
    src = utc(row.get("source_at"))
    if row.get("session") != "PREMARKET":
        return "OTHER_SESSION"
    if src is None:
        return "SOURCE_TIME_MISSING"
    if src < start:
        return "OLD_SNAPSHOT"
    if src >= end:
        return "SOURCE_AFTER_WINDOW"
    if row.get("source_channel") not in VALID_CHANNELS or "CONFLICT" in str(row.get("date_basis") or ""):
        return "SESSION_UNCONFIRMED"
    if cal is None or mh.phase(cal, src)["phase"] != "PRE_MARKET":
        return "SESSION_UNCONFIRMED"
    return "VALID_PREMARKET"


def summarize(*, session_rows: list[dict], subscriptions: list[str], start: datetime, end: datetime, cal: dict | None,
              events: list[tuple[str, str]] = (), parent_rows: list[dict] = (), key_salt: bytes | None = None) -> dict:
    """session_rows: session_quotes rows (symbol, session, source_at, received_at, source_channel, date_basis).
    With key_salt, every confirmed subscription carries a match key, and valid quotes of symbols that are no longer
    confirmed are kept by key (so a subscription dropped during the window can still be traced)."""
    lab, map_sha = labels(subscriptions)
    per = {lab[s]: {"first_valid": None, "excluded": {}} for s in subscriptions}
    if key_salt:
        for s in subscriptions:
            per[lab[s]]["key"] = match_key(s, key_salt)
    unconfirmed_valid: dict[str, str] = {}
    excluded_total: dict[str, int] = {}
    first_any = None
    rows = sorted((r for r in session_rows if in_window(utc(r.get("received_at")), start, end)),
                  key=lambda r: (utc(r["received_at"]), str(r.get("source_at"))))
    outside_subs = 0
    for r in rows:
        k = classify_row(r, start, end, cal)
        name = lab.get(r.get("symbol"))
        if name is None:
            outside_subs += 1
            if key_salt and k == "VALID_PREMARKET":
                unconfirmed_valid.setdefault(match_key(r["symbol"], key_salt), iso(utc(r["received_at"])))
            continue
        slot = per[name]
        if k == "VALID_PREMARKET":
            if slot["first_valid"] is None:
                rec, src = utc(r["received_at"]), utc(r["source_at"])
                slot["first_valid"] = {"received_at_utc": iso(rec), "source_at_utc": iso(src), "session": r["session"],
                                       "source_channel": r["source_channel"], "receive_minus_source_ms": round((rec - src).total_seconds() * 1000, 1),
                                       "seconds_after_start": round((rec - start).total_seconds(), 3)}
                first_any = min(first_any, rec) if first_any else rec
        else:
            slot["excluded"][k] = slot["excluded"].get(k, 0) + 1
            excluded_total[k] = excluded_total.get(k, 0) + 1
    got = sorted(n for n, v in per.items() if v["first_valid"])
    ev = [(s, iso(utc(a))) for s, a in events if in_window(utc(a), start - timedelta(minutes=5), end)]
    reconnects = [e for e in ev if e[0] in ("DISCONNECTED", "CONNECTING") and utc(e[1]) >= start]
    gap_end = first_any or end
    return {
        "window_utc": [iso(start), iso(end)], "window_rule": "[start, end), UTC, parsed per record",
        "subscriptions_confirmed": len(subscriptions), "label_map_sha256_16": map_sha,
        "subscriptions_with_valid_premarket_quote": len(got), "labels_with_valid_quote": got,
        "labels_without_valid_quote": sorted(set(per) - set(got)),
        "result": ("ALL_SUBSCRIPTIONS_RECEIVED" if got and len(got) == len(per) else
                   "PARTIAL" if got else "NO_VALID_PREMARKET_QUOTE"),
        "first_valid_any_utc": iso(first_any),
        "no_valid_quote_from_start_seconds": round((gap_end - start).total_seconds(), 3),
        "no_valid_quote_still_open": first_any is None,
        "per_subscription": per, "excluded_records": excluded_total,
        "records_in_window": len(rows), "records_for_unconfirmed_symbols": outside_subs,
        "parent_quote_rows_in_window": sum(1 for r in parent_rows if in_window(utc(r.get("received_at")), start, end)),
        "state_events": ev, "reconnects_after_start": len(reconnects),
        "keyed": bool(key_salt), "valid_quote_first_by_unconfirmed_key": unconfirmed_valid,
    }


def collect(db_path: str, start: datetime, end: datetime, root: Path = Path("/app"), key_salt: bytes | None = None) -> dict:
    d = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True); d.row_factory = sqlite3.Row
    lo, hi = (start - timedelta(seconds=2)).strftime("%Y-%m-%dT%H:%M:%S"), (end + timedelta(seconds=2)).strftime("%Y-%m-%dT%H:%M:%S")
    sess = [dict(r) for r in d.execute("""SELECT symbol, session, source_at, received_at, source_channel, date_basis
                                           FROM session_quotes WHERE received_at>=? AND received_at<?""", (lo, hi))]
    parent = [dict(r) for r in d.execute("SELECT received_at FROM quotes WHERE received_at>=? AND received_at<?", (lo, hi))]
    subs = [r["symbol"] for r in d.execute("SELECT symbol, state FROM symbol_state") if r["state"] in CONFIRMED]
    events = [(r["state"], r["at_utc"]) for r in d.execute("SELECT state, at_utc FROM provider_state_events WHERE provider='moomoo' ORDER BY id")]
    sym_events = sorted((r["state"], r["updated_at"]) for r in d.execute("SELECT state, updated_at FROM symbol_state"))
    try:
        cal = mh.load(root)
    except OSError:
        cal = None
    out = summarize(session_rows=sess, subscriptions=subs, start=start, end=end, cal=cal, events=events, parent_rows=parent, key_salt=key_salt)
    out["subscription_state_updates"] = [(s, iso(utc(a))) for s, a in sym_events]
    out["calendar_registered"] = bool(cal and cal.get("registered"))
    return out


def main(argv: list[str]) -> None:
    start = utc(argv[0]); end = utc(argv[1]) if len(argv) > 1 else datetime.now(UTC)
    root = Path("/app")
    cfg = json.loads((root / "configs/quant_portal_personal_feeds_linux_v1.json").read_text(encoding="utf-8"))
    out = collect(str(root / cfg["cache_db"]), start, end, root)
    h = subprocess.run([sys.executable, str(root / "deploy/linux/entrypoint.py"), "health", "feed"], capture_output=True, text=True)
    try:
        m = json.loads(h.stdout)["providers"]["moomoo"]
        out["health_now"] = {k: m.get(k) for k in ("business", "business_zh", "ok", "state", "heartbeat_age_seconds", "first_push_grace", "grace_left_seconds", "down_seconds")}
        out["health_now"]["phase"] = m["market"]["phase"]
    except (ValueError, KeyError) as exc:
        out["health_now"] = {"error_class": type(exc).__name__}
    out["checked_at_utc"] = iso(datetime.now(UTC))
    print(json.dumps(out, ensure_ascii=False, indent=1, default=str))


if __name__ == "__main__":
    main(sys.argv[1:])
