"""Two-security, queue-driven regular daily-bar collector. Private raw bytes only.

The collector never uses a portal request as a trigger. Its durable reservation
ledger counts failures and crashes against the local UTC-day request ceiling.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from scripts.market_observer_v1.moomoo_webapi_oauth import LocalTokenStore
from scripts.news_sentiment_factor_v43.daily import regular_session_close, session_day
from scripts.news_sentiment_factor_v44.labels import DB, QUEUE, digest, init as init_old, parsed_utc
from scripts.news_sentiment_factor_v44.price_pilot import ENDPOINT, TOKEN
from .metadata import PILOT, init as init_metadata, record_daily_currency_evidence
from .preflight import CONFIG, PRIVATE, sha

NY = ZoneInfo("America/New_York")
UTC = timezone.utc
VERSION = "v45_daily_price_1"
RAW = PRIVATE / "daily_raw"


def now() -> datetime:
    return datetime.now(UTC)


def cfg() -> dict:
    return json.loads(CONFIG.read_text(encoding="utf-8"))


def init(db: Path = DB) -> None:
    init_old(db)
    init_metadata(db)
    with closing(sqlite3.connect(db, timeout=20)) as con, con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS v45_supplier_requests(
          request_id TEXT PRIMARY KEY, reserved_at_utc TEXT NOT NULL,
          utc_day TEXT NOT NULL, symbol TEXT NOT NULL, trade_date TEXT,
          kind TEXT NOT NULL, request_url TEXT NOT NULL,
          config_sha256 TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS v45_supplier_results(
          request_id TEXT PRIMARY KEY, completed_at_utc TEXT NOT NULL,
          http_status INTEGER, ret_code INTEGER, provider_error_code TEXT,
          error_class TEXT, response_sha256 TEXT, response_bytes INTEGER NOT NULL,
          raw_private_path TEXT, item_count INTEGER);
        CREATE TABLE IF NOT EXISTS v45_price_observations(
          evidence_id TEXT PRIMARY KEY, request_id TEXT NOT NULL,
          trade_date TEXT NOT NULL, source_time_key_ms INTEGER NOT NULL,
          source_time_zone_minutes INTEGER NOT NULL,
          fetched_at_utc TEXT NOT NULL, parsed_at_utc TEXT NOT NULL,
          qualified_at_utc TEXT NOT NULL, response_sha256 TEXT NOT NULL,
          request_params_json TEXT NOT NULL,
          provider_completion_flag TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS v45_action_coverage(
          action_evidence_id TEXT PRIMARY KEY,rehab_request_id TEXT NOT NULL,
          dividend_request_id TEXT NOT NULL,scope_from_date TEXT NOT NULL,
          scope_through_date TEXT NOT NULL,rehab_count INTEGER NOT NULL,
          dividend_count INTEGER NOT NULL,pagination_state TEXT NOT NULL,
          interpretation TEXT NOT NULL);
        """)
        for table in ("v45_supplier_requests", "v45_supplier_results",
                      "v45_price_observations", "v45_action_coverage"):
            con.execute(f"CREATE TRIGGER IF NOT EXISTS {table}_no_update BEFORE UPDATE ON {table} "
                        "BEGIN SELECT RAISE(ABORT,'APPEND_ONLY'); END")
            con.execute(f"CREATE TRIGGER IF NOT EXISTS {table}_no_delete BEFORE DELETE ON {table} "
                        "BEGIN SELECT RAISE(ABORT,'APPEND_ONLY'); END")


def _completed_session(day: str, at: datetime, wait_minutes: int = 45) -> bool:
    d = date.fromisoformat(day)
    return session_day(d) and at >= regular_session_close(d) + timedelta(minutes=wait_minutes)


def queue_requirements(queue: Path = QUEUE, at: datetime | None = None) -> tuple[list[dict], list[dict]]:
    """Only released, witnessed, nonmissing-N1 real signals create demand."""
    at = at or now()
    if not queue.exists():
        return [], []
    with closing(sqlite3.connect(f"file:{queue.as_posix()}?mode=ro", uri=True)) as con:
        con.row_factory = sqlite3.Row
        rows = con.execute("""SELECT q.*, c.n1, s.kind,r.signal_available_at_utc,
          w.release_commit_observed_at_utc FROM daily_label_queue q
          JOIN daily_snapshot_company c ON c.snapshot_id=q.snapshot_id AND c.entity_id=q.entity_id
          JOIN daily_snapshots s USING(snapshot_id)
          JOIN daily_snapshot_releases r USING(snapshot_id)
          JOIN daily_snapshot_publication_witness w USING(snapshot_id)
          WHERE q.security_id IN ('SEC:NVDA:US','SEC:MU:US')
          ORDER BY q.entry_session_ny,q.security_id,q.exit_session_ny""").fetchall()
    valid = []
    excluded = []
    for row in rows:
        r = dict(row)
        reason = None
        if r["kind"] not in ("ON_TIME_SCHEDULED", "LATE_SCHEDULED"):
            reason = "NOT_REAL_SCHEDULED"
        elif r["n1"] is None:
            reason = "N1_MISSING"
        elif not r["entry_session_ny"] or not r["exit_session_ny"] or not r["entry_at_utc"] or not r["exit_at_utc"]:
            reason = "MISSING_SESSION_TIMES"
        elif parsed_utc(r["signal_available_at_utc"]) < parsed_utc(r["release_commit_observed_at_utc"]):
            reason = "NOT_RELEASED_AT_SIGNAL_TIME"
        elif parsed_utc(r["signal_available_at_utc"]) >= parsed_utc(r["entry_at_utc"]):
            reason = "MISSED_ENTRY"
        elif parsed_utc(r["signal_available_at_utc"]) > at:
            reason = "SIGNAL_NOT_YET_AVAILABLE"
        if reason:
            excluded.append({"entity_id": r["entity_id"], "reason": reason})
        else:
            valid.append(r)
    return valid, excluded


def _reserve(con: sqlite3.Connection, symbol: str, day: str | None,
             kind: str, url: str, at: datetime) -> str | None:
    limit = cfg()["budgets"]["ongoing_supplier_requests_per_utc_day"]
    con.execute("BEGIN IMMEDIATE")
    count = con.execute("SELECT COUNT(*) FROM v45_supplier_requests WHERE utc_day=?",
                        (at.date().isoformat(),)).fetchone()[0]
    if count >= limit:
        con.rollback()
        return None
    rid = digest([at.isoformat(), symbol, day, kind, url, count, VERSION])
    con.execute("INSERT INTO v45_supplier_requests VALUES(?,?,?,?,?,?,?,?)",
                (rid, at.isoformat(), at.date().isoformat(), symbol, day, kind, url, sha(CONFIG)))
    con.commit()
    return rid


def _request(con: sqlite3.Connection, symbol: str, day: str | None,
             kind: str, url: str, at: datetime) -> dict:
    blocked = con.execute("""SELECT s.http_status,s.ret_code,r.utc_day FROM v45_supplier_requests r
      JOIN v45_supplier_results s USING(request_id)
      WHERE r.symbol=? AND r.kind=? AND (s.http_status IN (401,403,429,439)
        OR s.ret_code IN (-11,-12))
      ORDER BY r.reserved_at_utc DESC LIMIT 1""", (symbol, kind)).fetchone()
    if blocked and (blocked[0] in (401, 403, 439) or blocked[1] == -12
                    or ((blocked[0] == 429 or blocked[1] == -11)
                        and blocked[2] == at.date().isoformat())):
        return {"kind": kind, "symbol": symbol, "date": day,
                "state": "ROUTE_PAUSED", "http_status": blocked[0], "ret_code": blocked[1],
                "error_class": "PREVIOUS_AUTH_SECURITY_OR_DAILY_RATE_STOP"}
    rid = _reserve(con, symbol, day, kind, url, at)
    if rid is None:
        return {"kind": kind, "symbol": symbol, "date": day, "state": "DAILY_LOCAL_BUDGET_PAUSED"}
    body = b""
    code = None
    error_class = None
    token = None
    try:
        token = LocalTokenStore(TOKEN).access_token()
        req = Request(url, headers={"Authorization": "Bearer " + token, "Accept": "application/json"})
        with urlopen(req, timeout=15) as response:
            code = response.status
            body = response.read(2_000_001)
    except HTTPError as exc:
        code = exc.code
        error_class = "HTTPError"
        body = exc.read(200_001)
    except Exception as exc:
        error_class = type(exc).__name__
    finally:
        token = None
    completed = now().isoformat()
    raw_hash = hashlib.sha256(body).hexdigest() if body else None
    raw_path = None
    if body:
        RAW.mkdir(parents=True, exist_ok=True)
        raw_path = RAW / f"{rid}.json"
        raw_path.write_bytes(body)
    ret_code = None
    provider_error = None
    payload = None
    items = None
    if len(body) > 2_000_000:
        error_class = "RESPONSE_BYTE_CAP"
    else:
        try:
            payload = json.loads(body) if body else None
            if isinstance(payload, dict):
                ret_code = payload.get("ret_code")
                err = payload.get("error")
                provider_error = str(err.get("code")) if isinstance(err, dict) and err.get("code") is not None else None
                data = payload.get("data") or {}
                key = {"history": "kline_list", "rehab": "rehabs", "dividends": "dividend_list"}[kind]
                value = data.get(key) if isinstance(data, dict) else None
                items = len(value) if isinstance(value, list) else None
        except ValueError:
            error_class = "NON_JSON_RESPONSE"
    con.execute("INSERT INTO v45_supplier_results VALUES(?,?,?,?,?,?,?,?,?,?)",
                (rid, completed, code, ret_code, provider_error, error_class,
                 raw_hash, len(body), str(raw_path) if raw_path else None, items))
    con.commit()
    state = ("SUCCESS" if code == 200 and ret_code == 0 and error_class is None else
             "ROUTE_PAUSED" if code in (401, 403, 429, 439) or ret_code in (-11, -12) else
             "FAILED")
    return {"request_id": rid, "kind": kind, "symbol": symbol, "date": day,
            "state": state, "http_status": code, "ret_code": ret_code,
            "provider_error_code": provider_error, "error_class": error_class,
            "response_sha256": raw_hash, "completed_at_utc": completed,
            "payload": payload if state == "SUCCESS" else None}


def _import_price(con: sqlite3.Connection, result: dict, symbol: str, day: str,
                  url: str) -> int:
    payload = result["payload"]
    if (payload.get("pagination") or {}).get("has_more"):
        raise ValueError("DAILY_BAR_PAGINATION_INCOMPLETE")
    bars = payload.get("data", {}).get("kline_list")
    if not isinstance(bars, list) or len(bars) != 1:
        raise ValueError("EXPECTED_ONE_DAILY_BAR")
    bar = bars[0]
    basic_files = list(PILOT.glob("BATCH_basicinfo_*.json"))
    if len(basic_files) != 1:
        raise ValueError("ARCHIVED_BASICINFO_NOT_UNIQUE")
    basic_path = basic_files[0]
    basic = json.loads(basic_path.read_text(encoding="utf-8"))
    match = [item for item in basic.get("data", {}).get("basic_list", [])
             if item.get("code") == f"US.{symbol}"]
    if len(match) != 1 or not bar.get("name") or \
       str(bar["name"]).casefold() != str(match[0].get("name", "")).casefold():
        raise ValueError("DAILY_BAR_SECURITY_NAME_MISMATCH_WITH_ARCHIVED_BASICINFO")
    raw_date = str(bar.get("date", ""))
    if raw_date.replace("-", "") != day.replace("-", ""):
        raise ValueError("DAILY_BAR_DATE_MISMATCH")
    stamp = datetime.fromtimestamp(int(bar["time_key"]) / 1000, UTC)
    if stamp.astimezone(NY).date().isoformat() != day:
        raise ValueError("DAILY_BAR_SOURCE_TIME_DATE_MISMATCH")
    offset = int(bar["time_zone"])
    if offset != int(stamp.astimezone(NY).utcoffset().total_seconds() // 60):
        raise ValueError("DAILY_BAR_SOURCE_TIME_ZONE_MISMATCH")
    if not _completed_session(day, parsed_utc(result["completed_at_utc"])):
        raise ValueError("DAILY_BAR_QUERIED_BEFORE_CLOSE_BUFFER")
    count = 0
    for field in ("open", "close"):
        val = Decimal(str(bar[field]))
        if not val.is_finite() or val <= 0:
            raise ValueError("INVALID_DAILY_PRICE")
        record = {"security_id": f"SEC:{symbol}:US", "provider_code": f"US.{symbol}",
                  "trade_date": day, "field": field.upper(), "price_text": str(bar[field]),
                  "currency": "UNKNOWN", "currency_basis": "WEBAPI_HISTORY_KLINE_HAS_NO_CURRENCY_FIELD",
                  "session": "REGULAR", "price_semantics": "UNADJUSTED_DAILY_BAR_NOT_OFFICIAL_AUCTION",
                  "adjustment": "UNADJUSTED_AUTYPE_0", "provider": "MOOMOO_WEBAPI",
                  "received_at_utc": result["completed_at_utc"],
                  "raw_sha256": result["response_sha256"], "parser_version": VERSION}
        eid = digest(record)
        before = con.total_changes
        con.execute("INSERT OR IGNORE INTO price_evidence VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (eid, *record.values()))
        count += con.total_changes - before
        con.execute("INSERT OR IGNORE INTO v45_price_observations VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (eid, result["request_id"], day, int(bar["time_key"]), offset,
                     result["completed_at_utc"], now().isoformat(), now().isoformat(),
                     result["response_sha256"], json.dumps({"url": url}, sort_keys=True),
                     "PROVIDER_COMPLETION_FLAG_NOT_PROVIDED"))
    record_daily_currency_evidence(con, symbol, day, result["response_sha256"],
                                   url, result["completed_at_utc"])
    con.commit()
    return count


def _read_action_result(con: sqlite3.Connection, symbol: str, kind: str,
                        after: datetime) -> tuple[str, dict] | None:
    rows = con.execute("""SELECT r.request_id,s.completed_at_utc,s.raw_private_path
      FROM v45_supplier_requests r JOIN v45_supplier_results s USING(request_id)
      WHERE r.symbol=? AND r.kind=? AND s.http_status=200 AND s.ret_code=0
      ORDER BY s.completed_at_utc DESC""", (symbol, kind)).fetchall()
    for rid, stamp, path in rows:
        if parsed_utc(stamp) >= after and path and Path(path).is_file():
            return rid, json.loads(Path(path).read_text(encoding="utf-8"))
    return None


def _action_interval(con: sqlite3.Connection, symbol: str, start: str, end: str,
                     exit_close: datetime, at: datetime) -> tuple[str, list[dict]]:
    cutoff = exit_close + timedelta(minutes=45)
    responses = {kind: _read_action_result(con, symbol, kind, cutoff)
                 for kind in ("rehab", "dividends")}
    outcomes = []
    for kind in ("rehab", "dividends"):
        if responses[kind] is None:
            prior = con.execute("""SELECT reserved_at_utc FROM v45_supplier_requests
                WHERE symbol=? AND trade_date=? AND kind=? ORDER BY reserved_at_utc""",
                (symbol, end, kind)).fetchall()
            schedule = cfg()["price_request_schedule"]
            if len(prior) >= schedule["maximum_attempts_per_security_trade_date"]:
                return "ACTION_ATTEMPT_LIMIT", outcomes
            minute = schedule["after_regular_close_minutes"][len(prior)]
            if at < exit_close + timedelta(minutes=minute):
                return "ACTION_RETRY_SCHEDULED_LATER", outcomes
            url = f"{ENDPOINT}/US.{symbol}/corporate-actions/{kind}"
            if kind == "rehab":
                url += "?divi_mode=include_divi"
            outcome = _request(con, symbol, end, kind, url, at)
            outcomes.append({k: v for k, v in outcome.items() if k != "payload"})
            if outcome["state"] == "SUCCESS":
                responses[kind] = (outcome["request_id"], outcome["payload"])
            else:
                return "ACTION_SOURCE_NOT_COMPLETE", outcomes
    rehab_id, rehab = responses["rehab"]
    dividend_id, dividend = responses["dividends"]
    rh = rehab.get("data", {}).get("rehabs")
    dv = dividend.get("data", {}).get("dividend_list")
    if not isinstance(rh, list) or not isinstance(dv, list):
        return "ACTION_LIST_SCHEMA_UNKNOWN", outcomes
    if (dividend.get("pagination") or {}).get("has_more") or len(dv) >= 100 or (rehab.get("pagination") or {}).get("has_more"):
        return "ACTION_PAGINATION_UNRESOLVED", outcomes
    from scripts.news_sentiment_factor_v44.labels import _date
    relevant_rh = [x for x in rh if start <= _date(x["ex_div_date"]) <= end]
    relevant_dv = [x for x in dv if start <= _date(x["ex_date"]) <= end]
    rh_hash = sha(Path(con.execute("SELECT raw_private_path FROM v45_supplier_results WHERE request_id=?", (rehab_id,)).fetchone()[0]))
    dv_hash = sha(Path(con.execute("SELECT raw_private_path FROM v45_supplier_results WHERE request_id=?", (dividend_id,)).fetchone()[0]))
    status = "PROVIDER_LISTS_NO_ACTION_IN_INTERVAL_V45" if not relevant_rh and not relevant_dv else "REVIEW_ACTIONS"
    action = {"security_id": f"SEC:{symbol}:US", "observed_from_date": start,
              "observed_through_date": end, "split_events": len(relevant_rh),
              "dividend_events": len(relevant_dv), "evidence_status": status,
              "rehab_sha256": rh_hash, "dividends_sha256": dv_hash,
              "checked_at_utc": now().isoformat()}
    aid = digest(action)
    con.execute("INSERT OR IGNORE INTO action_evidence VALUES(?,?,?,?,?,?,?,?,?,?)", (aid, *action.values()))
    con.execute("INSERT OR IGNORE INTO v45_action_coverage VALUES(?,?,?,?,?,?,?,?,?)",
                (aid, rehab_id, dividend_id, start, end, len(rh), len(dv),
                 "COMPLETE_PROVIDER_RESPONSE_BELOW_100_DIVIDEND_CAP",
                 "PROVIDER_LIST_RESULT_NOT_INDEPENDENT_PROOF_NO_ACTION"))
    con.commit()
    return status, outcomes


def tick(db: Path = DB, queue: Path = QUEUE, at: datetime | None = None) -> dict:
    init(db)
    at = (at or now()).astimezone(UTC)
    requirements, excluded = queue_requirements(queue, at)
    dates = sorted({(r["security_id"].split(":")[1], d) for r in requirements
                    for d in (r["entry_session_ny"], r["exit_session_ny"])})
    requests = []
    imports = 0
    with closing(sqlite3.connect(db, timeout=20)) as con:
        for symbol, day in dates:
            if not _completed_session(day, at):
                continue
            count = con.execute("""SELECT COUNT(DISTINCT field) FROM price_evidence
                WHERE security_id=? AND trade_date=? AND session='REGULAR'
                  AND adjustment='UNADJUSTED_AUTYPE_0' AND field IN ('OPEN','CLOSE')""",
                (f"SEC:{symbol}:US", day)).fetchone()[0]
            if count == 2:
                continue
            prior = con.execute("""SELECT r.reserved_at_utc,s.http_status,s.ret_code
                FROM v45_supplier_requests r LEFT JOIN v45_supplier_results s USING(request_id)
                WHERE r.symbol=? AND r.trade_date=? AND r.kind='history'
                ORDER BY r.reserved_at_utc""", (symbol, day)).fetchall()
            if any(status in (401, 403, 439) or ret_code == -12
                   or ((status == 429 or ret_code == -11)
                       and reserved[:10] == at.date().isoformat())
                   for reserved, status, ret_code in prior):
                continue
            if len(prior) >= cfg()["price_request_schedule"]["maximum_attempts_per_security_trade_date"]:
                continue
            wait = cfg()["price_request_schedule"]["after_regular_close_minutes"][len(prior)]
            if not _completed_session(day, at, wait):
                continue
            params = {"start": day, "end": day, "ktype": 2, "autype": 0,
                      "num": 1, "extended_time": 0}
            url = f"{ENDPOINT}/US.{symbol}/history-kline?{urlencode(params)}"
            outcome = _request(con, symbol, day, "history", url, at)
            requests.append({k: v for k, v in outcome.items() if k != "payload"})
            if outcome["state"] == "SUCCESS":
                try:
                    imports += _import_price(con, outcome, symbol, day, url)
                except (ValueError, KeyError, TypeError, ArithmeticError) as exc:
                    requests[-1]["parse_error_class"] = type(exc).__name__
                    requests[-1]["parse_error_code"] = str(exc)[:100]
        actions = []
        for r in requirements:
            start, end = r["entry_session_ny"], r["exit_session_ny"]
            symbol = r["security_id"].split(":")[1]
            if not _completed_session(end, at):
                continue
            have = con.execute("""SELECT 1 FROM action_evidence WHERE security_id=?
              AND observed_from_date<=? AND observed_through_date>=?
              AND evidence_status='PROVIDER_LISTS_NO_ACTION_IN_INTERVAL_V45'""",
              (r["security_id"], start, end)).fetchone()
            if have:
                continue
            status, outcomes = _action_interval(con, symbol, start, end,
                                               regular_session_close(date.fromisoformat(end)), at)
            actions.append({"symbol": symbol, "start": start, "end": end, "status": status})
            requests.extend(outcomes)
    return {"checked_at_utc": at.isoformat(), "qualified_queue_rows": len(requirements),
            "excluded_queue_rows": excluded, "needed_security_dates": len(dates),
            "new_price_field_rows": imports, "new_action_intervals": actions,
            "requests": requests}
