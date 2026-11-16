"""Owner-bound, read-only timeline from saved quote and news cache.

The points are observed supplier quotes, not trades or complete intraday bars.
No provider request, price interpolation, or inferred exchange trading date occurs.
"""
from __future__ import annotations

import json
import re
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .event_rules import classify

CONFIG = Path(__file__).resolve().parents[2] / "configs/quant_portal_personal_timeline_v14_20261116.json"
SYMBOL_RE = re.compile(r"[A-Z0-9.]{1,16}\Z")
MACRO_CATEGORIES = {"MACRO_RATES", "GEO_ENERGY"}


def parse_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return result.astimezone(timezone.utc) if result.tzinfo else None


def iso_utc(value: datetime) -> str:
    # Keep the stored timestamp's full precision in the API contract. Rounding
    # to milliseconds made exact cache readback and first-receipt checks fail.
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def load_config() -> dict:
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    if config["schema"] != "quant_portal_personal_timeline_v14":
        raise ValueError("Unexpected timeline config")
    return config


def _selected_points(rows: list[sqlite3.Row], *, start: datetime, end: datetime,
                     gap_seconds: int, max_points: int,
                     interruption_times: list[datetime] | None = None) -> tuple[list[dict], dict]:
    # A late packet can carry an older source time. Select the last received
    # version of each (session, source time), then sort by source time.
    latest: dict[tuple[str, str], sqlite3.Row] = {}
    invalid = 0
    for row in rows:
        source = parse_utc(row["source_at"])
        if source is None or not start <= source <= end:
            invalid += 1
            continue
        try:
            price = Decimal(row["price_text"])
        except (InvalidOperation, TypeError):
            invalid += 1
            continue
        if not price.is_finite() or price <= 0:
            invalid += 1
            continue
        key = (row["session"], iso_utc(source))
        prior = latest.get(key)
        received = parse_utc(row["received_at"]) or datetime.min.replace(tzinfo=timezone.utc)
        prior_received = (parse_utc(prior["received_at"]) or
                          datetime.min.replace(tzinfo=timezone.utc)) if prior else None
        if prior is None or (received, row["id"]) > (prior_received, prior["id"]):
            latest[key] = row
    ordered = sorted(latest.values(), key=lambda x: (parse_utc(x["source_at"]), x["session"]))
    points: list[dict] = []
    segment = -1
    last_source = last_received = None
    last_session = None
    interruption_times = interruption_times or []
    for row in ordered:
        source = parse_utc(row["source_at"])
        received = parse_utc(row["received_at"])
        interrupted = (received is not None and last_received is not None and
                       any(min(last_received,received) < cut <= max(last_received,received)
                           for cut in interruption_times))
        gap = (last_source is None or row["session"] != last_session or interrupted or
               (source - last_source).total_seconds() > gap_seconds or
               (received is not None and last_received is not None and
                abs((received - last_received).total_seconds()) > gap_seconds))
        if gap:
            segment += 1
        points.append({"session": row["session"], "source_at": iso_utc(source),
                       "received_at": iso_utc(received) if received else None,
                       "price_text": row["price_text"], "currency": row["currency"],
                       "source_channel": row["source_channel"], "delay_status": row["delay_status"],
                       "segment": segment, "raw_evidence_present": bool(row["raw_present"])})
        last_source, last_received, last_session = source, received, row["session"]
    total = len(points)
    coverage_segments: list[dict] = []
    for point in points:
        if not coverage_segments or coverage_segments[-1]["segment"] != point["segment"]:
            coverage_segments.append({"segment": point["segment"], "session": point["session"],
                                      "first_source_at": point["source_at"],
                                      "last_source_at": point["source_at"], "point_count": 1})
        else:
            coverage_segments[-1]["last_source_at"] = point["source_at"]
            coverage_segments[-1]["point_count"] += 1
    if total > max_points:
        # Preserve at least the boundaries of every displayed segment before
        # filling the remaining budget by source-time order. Every displayed
        # point remains an original saved quote, never an interpolated value.
        mandatory = set()
        for index, point in enumerate(points):
            if index == 0 or point["segment"] != points[index-1]["segment"]:
                mandatory.add(index)
            if index == total-1 or point["segment"] != points[index+1]["segment"]:
                mandatory.add(index)
        if len(mandatory) >= max_points:
            kept = sorted(mandatory)
            indices = sorted({kept[round(i*(len(kept)-1)/(max_points-1))]
                              for i in range(max_points)})
        else:
            free = max_points-len(mandatory)
            evenly = {round(i*(total-1)/(free-1)) for i in range(free)} if free > 1 else {total//2}
            indices = sorted(mandatory | evenly)
        points = [points[i] for i in indices]
    return points, {"valid_unique_points": total, "returned_points": len(points),
                    "sampled": total > max_points, "invalid_or_out_of_window_rows": invalid,
                    "segment_count_before_sampling": segment + 1,
                    "coverage_segments": coverage_segments}


def _related_news(db: sqlite3.Connection, owner_id: int, symbol: str,
                  start: datetime, end: datetime, max_items: int) -> tuple[list[dict], dict]:
    rows = db.execute("""SELECT i.item_key,i.first_received_at,i.latest_version,
      v.title,v.content,v.source_url,v.published_raw,v.published_at_utc,v.received_at,v.version
      FROM news_items i JOIN news_versions v ON v.item_key=i.item_key
      WHERE i.owner_id=? AND v.version=i.latest_version
      ORDER BY i.latest_received_at DESC LIMIT 500""", (owner_id,)).fetchall()
    selected = []
    related_in_window = 0
    unknown_published_time = 0
    for row in rows:
        item = dict(row)
        try:
            labels = classify(item)
        except (ValueError, TypeError, KeyError, re.error):
            continue
        direct = next((x for x in labels["entities"] if x["id"] == symbol), None)
        macro = next((x for x in labels["categories"] if x["id"] in MACRO_CATEGORIES), None)
        if direct:
            reason = f"直接提及 {direct['label']}；匹配短语：{direct['phrase']}"
            kind = "DIRECT_ENTITY_PHRASE"
        elif macro:
            reason = f"宏观主题：{macro['label']}；匹配短语：{macro['phrase']}"
            kind = "SHARED_MACRO_THEME"
        else:
            continue
        published = parse_utc(item["published_at_utc"])
        first_received = parse_utc(item["first_received_at"])
        # A missing/ambiguous supplier time can appear in the list by local
        # receipt time, but it can never become a chart marker.
        in_window = (start <= published <= end) if published else (
            first_received is not None and start <= first_received <= end)
        if not in_window:
            continue
        related_in_window += 1
        if published is None:
            unknown_published_time += 1
        item["published_at_utc"] = iso_utc(published) if published else None
        item["first_received_at"] = iso_utc(first_received) if first_received else None
        item["association_reason"] = reason
        item["association_kind"] = kind
        item["event_group_id"] = labels["event_group_id"]
        item["rule_version"] = labels["rule_version"]
        item["content_preview"] = item.pop("content")[:240]
        selected.append(item)
    selected.sort(key=lambda x: (x["published_at_utc"] or x["first_received_at"] or ""), reverse=True)
    selected = selected[:max_items]
    if selected:
        keys = [x["item_key"] for x in selected]
        placeholders = ",".join("?" for _ in keys)
        versions = db.execute(f"""SELECT item_key,version,title,content,published_at_utc,received_at
          FROM news_versions WHERE owner_id=? AND item_key IN ({placeholders})
          ORDER BY item_key,version DESC""", (owner_id, *keys)).fetchall()
        by_key: dict[str, list[dict]] = {}
        for version in versions:
            by_key.setdefault(version["item_key"], []).append({
                "version": version["version"], "title": version["title"],
                "content_preview": version["content"][:240],
                "published_at_utc": version["published_at_utc"],
                "received_at": version["received_at"]})
        for item in selected:
            item["versions"] = by_key.get(item["item_key"], [])
    return selected, {"related_in_window": related_in_window,
                      "returned_news": len(selected),
                      "unknown_supplier_published_time": unknown_published_time,
                      "news_scan_limit": 500}


def read_timeline(path: Path, owner_id: int, *, symbol: str, session: str = "ALL",
                  range_key: str = "2h", end_at: str | None = None,
                  now: datetime | None = None) -> dict:
    config = load_config()
    if not SYMBOL_RE.fullmatch(symbol) or symbol not in {"TQQQ", "QQQ", "SOXX", "SPY", "NVDA", "MU"}:
        raise ValueError("Unverified chart symbol")
    if session not in {"ALL", *config["sessions"]} or range_key not in config["ranges_seconds"]:
        raise ValueError("Invalid chart session or range")
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    end = parse_utc(end_at) if end_at else now
    if end is None or end > now + timedelta(seconds=5) or end < now - timedelta(days=7):
        raise ValueError("Invalid chart end time")
    duration = timedelta(seconds=config["ranges_seconds"][range_key])
    start = end - duration
    response = {"schema": config["schema"], "symbol": symbol, "session": session,
                "range": range_key, "window_start_utc": iso_utc(start),
                "window_end_utc": iso_utc(end), "generated_at_utc": iso_utc(now),
                "points": [], "news": [], "coverage": [],
                "cache_state": "NOT_STARTED", "quote_kind": "SAVED_SUPPLIER_QUOTE_NOT_TRADE_OR_BAR",
                "gap_seconds": config["segment_gap_seconds"]}
    if not path.is_file():
        return response
    uri = path.resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True, timeout=5)) as db:
        db.row_factory = sqlite3.Row
        binding = db.execute("SELECT owner_id FROM owner_binding WHERE singleton=1").fetchone()
        if binding is None or binding["owner_id"] != owner_id:
            response["cache_state"] = "OWNER_MISMATCH"
            return response
        response["cache_state"] = "READY"
        response["coverage"] = [dict(row) for row in db.execute("""
          SELECT q.session,COUNT(*) AS saved_rows,
            SUM(q.source_at IS NOT NULL) AS source_time_known_rows,
            MIN(q.source_at) AS first_source_at,MAX(q.source_at) AS last_source_at,
            SUM(CASE WHEN r.sha256 IS NOT NULL THEN 1 ELSE 0 END) AS raw_evidence_available_rows
          FROM session_quotes q LEFT JOIN session_raw_responses r ON r.sha256=q.raw_sha256
          WHERE q.owner_id=? AND q.symbol=? GROUP BY q.session ORDER BY q.session""",
          (owner_id,symbol)).fetchall()]
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        transitions = [dict(row) for row in db.execute("""
          SELECT state,at_utc FROM provider_state_events
          WHERE provider='moomoo' AND at_utc>=? AND at_utc<=? ORDER BY at_utc""",
          (iso_utc(start-timedelta(seconds=1)),iso_utc(end+timedelta(seconds=1))))] if "provider_state_events" in tables else []
        interruption_times = [parsed for row in transitions
                              if row["state"] in {"CONNECTING", "DISCONNECTED", "AUTH_EXPIRED", "RATE_LIMITED"}
                              if (parsed := parse_utc(row["at_utc"])) is not None]
        response["connection_transitions"] = transitions
        sql = """SELECT q.id,q.session,q.price_text,q.currency,q.source_at,q.received_at,
          q.source_channel,q.delay_status,(r.sha256 IS NOT NULL) AS raw_present
          FROM session_quotes q LEFT JOIN session_raw_responses r ON r.sha256=q.raw_sha256
          WHERE q.owner_id=? AND q.symbol=? AND q.source_at IS NOT NULL
            AND q.source_at>=? AND q.source_at<=?"""
        # Wider SQL bounds include both whole-second and millisecond ISO forms;
        # the exact inclusive boundary is enforced after parsing above.
        args: list[object] = [owner_id,symbol,
                              iso_utc(start-timedelta(seconds=1)),
                              iso_utc(end+timedelta(seconds=1))]
        if session != "ALL":
            sql += " AND q.session=?"
            args.append(session)
        sql += " ORDER BY q.source_at DESC,q.id DESC LIMIT ?"
        args.append(config["max_scan_rows_per_response"] + 1)
        scanned = db.execute(sql, args).fetchall()
        truncated = len(scanned) > config["max_scan_rows_per_response"]
        if truncated:
            scanned = scanned[:config["max_scan_rows_per_response"]]
        points, metrics = _selected_points(scanned, start=start, end=end,
            gap_seconds=config["segment_gap_seconds"],
            max_points=config["max_points_per_response"],
            interruption_times=interruption_times)
        response.update({"points": points, "point_metrics": metrics,
                         "scan_truncated": truncated,
                         "sample_limit": config["max_points_per_response"]})
        response["news"], response["news_metrics"] = _related_news(db, owner_id,
            symbol, start, end, config["max_news_items_per_response"])
    return response
