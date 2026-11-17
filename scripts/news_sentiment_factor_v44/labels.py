"""Append-only, maturity-gated forward price-return labels.

The v4.3 auction-proxy target is not silently replaced. This separate v4.4
candidate uses provider regular-session *daily bar* open and close, not an
official opening auction price or a total return. No historic pilot bar may be
joined to a signal generated later.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from zoneinfo import ZoneInfo

from scripts.news_sentiment_factor_v44.preflight import PRIVATE, ROOT, sha

DB = PRIVATE / "price_labels.sqlite3"
QUEUE = ROOT / "artifacts/news_sentiment_factor_v42_20261117/queue.sqlite3"
CONTRACT = "v44_REGULAR_DAILY_BAR_OPEN_TO_CLOSE_PRICE_RETURN_1"
PARSER = "v44_moomoo_history_kline_unadjusted_1"


def utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def parsed_utc(value: str) -> datetime:
    stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        raise ValueError("PRICE_OR_SIGNAL_TIME_HAS_NO_ZONE")
    return stamp.astimezone(timezone.utc)


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def init(db: Path = DB) -> None:
    db.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(db, timeout=20)) as con, con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS price_evidence(
          evidence_id TEXT PRIMARY KEY,security_id TEXT NOT NULL,provider_code TEXT NOT NULL,
          trade_date TEXT NOT NULL,field TEXT NOT NULL,price_text TEXT NOT NULL,
          currency TEXT NOT NULL,currency_basis TEXT NOT NULL,
          session TEXT NOT NULL,price_semantics TEXT NOT NULL,adjustment TEXT NOT NULL,
          provider TEXT NOT NULL,received_at_utc TEXT NOT NULL,raw_sha256 TEXT NOT NULL,
          parser_version TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS action_evidence(
          evidence_id TEXT PRIMARY KEY,security_id TEXT NOT NULL,
          observed_from_date TEXT NOT NULL,observed_through_date TEXT NOT NULL,
          split_events INTEGER NOT NULL,dividend_events INTEGER NOT NULL,
          evidence_status TEXT NOT NULL,rehab_sha256 TEXT NOT NULL,
          dividends_sha256 TEXT NOT NULL,checked_at_utc TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS label_versions(
          label_id TEXT PRIMARY KEY,snapshot_id TEXT NOT NULL,entity_id TEXT NOT NULL,
          horizon_sessions INTEGER NOT NULL,revision INTEGER NOT NULL,
          contract TEXT NOT NULL,signal_available_at_utc TEXT NOT NULL,
          entry_evidence_id TEXT NOT NULL,exit_evidence_id TEXT NOT NULL,
          action_evidence_id TEXT NOT NULL,price_return_text TEXT NOT NULL,
          formula TEXT NOT NULL,calculation_code_sha256 TEXT NOT NULL,
          generated_at_utc TEXT NOT NULL,label_available_at_utc TEXT NOT NULL,
          input_manifest_sha256 TEXT NOT NULL,
          UNIQUE(snapshot_id,entity_id,horizon_sessions,contract,revision));
        CREATE TABLE IF NOT EXISTS label_attempts(
          attempt_id TEXT PRIMARY KEY,checked_at_utc TEXT NOT NULL,
          snapshot_id TEXT NOT NULL,entity_id TEXT NOT NULL,horizon_sessions INTEGER NOT NULL,
          state TEXT NOT NULL,reason TEXT NOT NULL);
        """)
        for table in ("price_evidence", "action_evidence", "label_versions", "label_attempts"):
            con.execute(f"CREATE TRIGGER IF NOT EXISTS {table}_no_update BEFORE UPDATE ON {table} "
                        f"BEGIN SELECT RAISE(ABORT,'APPEND_ONLY'); END")
            con.execute(f"CREATE TRIGGER IF NOT EXISTS {table}_no_delete BEFORE DELETE ON {table} "
                        f"BEGIN SELECT RAISE(ABORT,'APPEND_ONLY'); END")


def _date(value: object) -> str:
    text = str(value).replace("/", "-")
    if len(text) == 8 and text.isdigit():
        text = f"{text[:4]}-{text[4:6]}-{text[6:]}"
    datetime.strptime(text, "%Y-%m-%d")
    return text


def import_pilot(db: Path = DB) -> dict:
    """Import only already saved 2026-11-13/16 raw evidence; no supplier call."""
    init(db)
    folder = PRIVATE / "price_raw"
    wanted = {"2026-11-13", "2026-11-16"}
    basic_raw = next(folder.glob("BATCH_basicinfo_*.json"))
    basic = json.loads(basic_raw.read_text(encoding="utf-8"))
    if basic.get("ret_code") != 0:
        raise ValueError("SECURITY_BASICINFO_NOT_SUCCESS")
    identities = {x["code"]: x for x in basic["data"]["basic_list"]}
    from .price_pilot import LEDGER
    outcomes = [json.loads(line) for line in LEDGER.read_text(encoding="utf-8").splitlines()
                if line.strip() and json.loads(line).get("phase") == "RESULT"]
    stamps = {(r["symbol"], r["endpoint"]): r["completed_at_utc"] for r in outcomes}
    inserted = 0
    action_inserted = 0
    with closing(sqlite3.connect(db, timeout=20)) as con, con:
        for symbol in ("NVDA", "MU"):
            identity = identities.get(f"US.{symbol}")
            if not identity or identity.get("stock_type") != "STOCK" or identity.get("exchange") != "US":
                raise ValueError("PILOT_SECURITY_IDENTITY_NOT_MATCHED")
            prices = sorted(folder.glob(f"{symbol}_history_*.json"))
            if len(prices) != 1:
                raise ValueError(f"EXPECTED_ONE_PILOT_HISTORY_RESPONSE_{symbol}")
            raw = prices[0]
            payload = json.loads(raw.read_text(encoding="utf-8"))
            if payload.get("ret_code") != 0 or payload.get("pagination", {}).get("has_more"):
                raise ValueError("HISTORY_RESPONSE_NOT_COMPLETE_SUCCESS")
            bars = payload["data"]["kline_list"]
            if {_date(bar["date"]) for bar in bars} != wanted:
                raise ValueError("PILOT_SESSION_SET_MISMATCH")
            for bar in bars:
                day = _date(bar["date"])
                stamp = datetime.fromtimestamp(int(bar["time_key"]) / 1000, timezone.utc)
                if stamp.astimezone(ZoneInfo("America/New_York")).date().isoformat() != day:
                    raise ValueError("PROVIDER_BAR_DATE_TIME_KEY_MISMATCH")
                for field in ("open", "close"):
                    value = Decimal(str(bar[field]))
                    if not value.is_finite() or value <= 0:
                        raise ValueError("NONPOSITIVE_OR_NONFINITE_BAR_PRICE")
                    record = {"security_id": f"SEC:{symbol}:US", "provider_code": f"US.{symbol}",
                              "trade_date": day, "field": field.upper(), "price_text": str(bar[field]),
                              "currency": "UNKNOWN", "currency_basis": "WEBAPI_HISTORY_KLINE_HAS_NO_CURRENCY_FIELD",
                              "session": "REGULAR", "price_semantics": "UNADJUSTED_DAILY_BAR_NOT_OFFICIAL_AUCTION",
                              "adjustment": "UNADJUSTED_AUTYPE_0", "provider": "MOOMOO_WEBAPI",
                              "received_at_utc": stamps[(symbol, "history-kline")],
                              "raw_sha256": sha(raw), "parser_version": PARSER}
                    eid = digest(record)
                    before = con.total_changes
                    con.execute("INSERT OR IGNORE INTO price_evidence VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                (eid, *record.values()))
                    inserted += con.total_changes - before
            rehab = next(folder.glob(f"{symbol}_rehab_*.json"))
            dividends = next(folder.glob(f"{symbol}_dividends_*.json"))
            rh = json.loads(rehab.read_text(encoding="utf-8"))
            dv = json.loads(dividends.read_text(encoding="utf-8"))
            if rh.get("ret_code") != 0 or dv.get("ret_code") != 0:
                raise ValueError("ACTION_RESPONSE_NOT_SUCCESS")
            relevant_rehab = [x for x in rh["data"]["rehabs"] if "2026-11-13" <= _date(x["ex_div_date"]) <= "2026-11-16"]
            relevant_dividends = [x for x in dv["data"]["dividend_list"] if "2026-11-13" <= _date(x["ex_date"]) <= "2026-11-16"]
            # The schema's legacy split_events column conservatively counts
            # every rehab-factor row. A nonzero value blocks qualification;
            # it is not interpreted as a verified split classification.
            action = {"security_id": f"SEC:{symbol}:US", "observed_from_date": "2026-11-13",
                      "observed_through_date": "2026-11-16", "split_events": len(relevant_rehab),
                      "dividend_events": len(relevant_dividends),
                      "evidence_status": "PROVIDER_LISTS_NO_ACTION_IN_PILOT_INTERVAL" if not relevant_rehab and not relevant_dividends else "REVIEW_ACTIONS",
                      "rehab_sha256": sha(rehab), "dividends_sha256": sha(dividends),
                      "checked_at_utc": max(stamps[(symbol, "rehab")], stamps[(symbol, "dividends")])}
            aid = digest(action)
            before = con.total_changes
            con.execute("INSERT OR IGNORE INTO action_evidence VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (aid, *action.values()))
            action_inserted += con.total_changes - before
    return {"price_field_rows_inserted": inserted, "action_interval_rows_inserted": action_inserted,
            "qualified_v43_auction_price_rows": 0,
            "note": "Pilot bars predate the first scheduled signal and cannot form a forward label"}


def _attempt(con: sqlite3.Connection, q: sqlite3.Row, state: str, reason: str, at: str) -> None:
    latest = con.execute("""SELECT state,reason FROM label_attempts
        WHERE snapshot_id=? AND entity_id=? AND horizon_sessions=?
        ORDER BY checked_at_utc DESC LIMIT 1""",
        (q["snapshot_id"], q["entity_id"], q["horizon_sessions"])).fetchone()
    if latest and tuple(latest) == (state, reason):
        return
    record = [at, q["snapshot_id"], q["entity_id"], q["horizon_sessions"], state, reason]
    con.execute("INSERT OR IGNORE INTO label_attempts VALUES(?,?,?,?,?,?,?)", (digest(record), *record))


def _latest_available(con: sqlite3.Connection, table: str, sql: str,
                      args: tuple, timestamp_field: str, as_of: datetime) -> sqlite3.Row | None:
    if table not in {"price_evidence", "action_evidence"}:
        raise ValueError("UNSUPPORTED_EVIDENCE_TABLE")
    rows = con.execute(sql, args).fetchall()
    eligible = [row for row in rows if parsed_utc(row[timestamp_field]) <= as_of]
    return max(eligible, key=lambda row: (parsed_utc(row[timestamp_field]), row["evidence_id"])) if eligible else None


def advance(db: Path = DB, queue: Path = QUEUE, observed_at: datetime | None = None) -> dict:
    """Finalize only released, matured, qualified real rows; no supplier calls."""
    init(db)
    when = (observed_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    states: dict[str, int] = {}
    created = 0
    with closing(sqlite3.connect(f"file:{queue.as_posix()}?mode=ro", uri=True)) as source, \
         closing(sqlite3.connect(db, timeout=20)) as out, out:
        source.row_factory = sqlite3.Row
        out.row_factory = sqlite3.Row
        candidates = source.execute("""SELECT q.*,c.n1,r.signal_available_at_utc,w.release_commit_observed_at_utc
            FROM daily_label_queue q JOIN daily_snapshot_company c
              ON c.snapshot_id=q.snapshot_id AND c.entity_id=q.entity_id
            JOIN daily_snapshot_releases r USING(snapshot_id)
            JOIN daily_snapshot_publication_witness w USING(snapshot_id)
            ORDER BY q.snapshot_id,q.entity_id,q.horizon_sessions""").fetchall()
        rows = [row for row in candidates if
                parsed_utc(row["release_commit_observed_at_utc"]) <=
                parsed_utc(row["signal_available_at_utc"]) <= when]
        for q in rows:
            state, reason = "WAIT_QUALIFIED_PRICE", "QUALIFIED_PRICE_OR_ACTION_EVIDENCE_MISSING"
            available = parsed_utc(q["signal_available_at_utc"])
            entry = parsed_utc(q["entry_at_utc"]) if q["entry_at_utc"] else None
            exit_at = parsed_utc(q["exit_at_utc"]) if q["exit_at_utc"] else None
            if q["status"] in ("MISSED_ENTRY", "BLOCKED_SECURITY_MAPPING", "BLOCKED_CALENDAR_RANGE"):
                state, reason = q["status"], q["reason"]
            elif entry is None or exit_at is None:
                state, reason = "BLOCKED_CALENDAR_RANGE", "MISSING_VERIFIED_SESSION_TIME"
            elif available >= entry:
                state, reason = "MISSED_ENTRY", "SIGNAL_NOT_AVAILABLE_BEFORE_ENTRY"
            elif when < entry:
                state, reason = "WAIT_ENTRY", "ENTRY_NOT_REACHED"
            elif when < exit_at:
                state, reason = "WAIT_EXIT", "EXIT_NOT_REACHED"
            elif q["n1"] is None:
                state, reason = "NO_FACTOR_PAIR", "N1_MISSING"
            else:
                entry_price = _latest_available(out, "price_evidence", """SELECT * FROM price_evidence
                    WHERE security_id=? AND trade_date=? AND field='OPEN' AND session='REGULAR'""",
                    (q["security_id"], q["entry_session_ny"]), "received_at_utc", when)
                exit_price = _latest_available(out, "price_evidence", """SELECT * FROM price_evidence
                    WHERE security_id=? AND trade_date=? AND field='CLOSE' AND session='REGULAR'""",
                    (q["security_id"], q["exit_session_ny"]), "received_at_utc", when)
                action = _latest_available(out, "action_evidence", """SELECT * FROM action_evidence
                    WHERE security_id=? AND observed_from_date<=? AND observed_through_date>=?""",
                    (q["security_id"], q["entry_session_ny"], q["exit_session_ny"]), "checked_at_utc", when)
                if entry_price and exit_price and action:
                    same = (entry_price["provider_code"] == exit_price["provider_code"] and
                            entry_price["adjustment"] == exit_price["adjustment"] == "UNADJUSTED_AUTYPE_0" and
                            entry_price["currency"] == exit_price["currency"] != "UNKNOWN" and
                            entry_price["price_semantics"] == exit_price["price_semantics"] ==
                            "UNADJUSTED_DAILY_BAR_NOT_OFFICIAL_AUCTION" and
                            action["split_events"] == 0 and
                            action["evidence_status"] == "PROVIDER_LISTS_NO_ACTION_IN_PILOT_INTERVAL")
                    if same:
                        try:
                            value = Decimal(exit_price["price_text"]) / Decimal(entry_price["price_text"]) - 1
                        except (InvalidOperation, ZeroDivisionError):
                            same = False
                    if same:
                        manifest = digest([entry_price["evidence_id"], exit_price["evidence_id"], action["evidence_id"],
                                           q["signal_available_at_utc"], CONTRACT])
                        previous = out.execute("""SELECT revision,input_manifest_sha256 FROM label_versions
                            WHERE snapshot_id=? AND entity_id=? AND horizon_sessions=? AND contract=?
                            ORDER BY revision DESC LIMIT 1""",
                            (q["snapshot_id"], q["entity_id"], q["horizon_sessions"], CONTRACT)).fetchone()
                        if previous is None or previous["input_manifest_sha256"] != manifest:
                            revision = 1 if previous is None else previous["revision"] + 1
                            generated = utc()
                            lid = digest([q["snapshot_id"], q["entity_id"], q["horizon_sessions"], CONTRACT, revision])
                            out.execute("INSERT INTO label_versions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                        (lid, q["snapshot_id"], q["entity_id"], q["horizon_sessions"], revision,
                                         CONTRACT, q["signal_available_at_utc"], entry_price["evidence_id"],
                                         exit_price["evidence_id"], action["evidence_id"], str(value),
                                         "unadjusted_regular_daily_bar_close / unadjusted_regular_daily_bar_open - 1",
                                         sha(Path(__file__)), generated, generated, manifest))
                            created += 1
                        state, reason = "READY_DAILY_BAR_PRICE_RETURN", "NOT_OFFICIAL_AUCTION_OR_TOTAL_RETURN"
                    else:
                        state, reason = "WAIT_QUALIFIED_PRICE", "CURRENCY_SEMANTICS_OR_ACTION_CONTRACT_UNMET"
            _attempt(out, q, state, reason, when.isoformat())
            states[state] = states.get(state, 0) + 1
    return {"observed_at_utc": when.isoformat(), "queue_rows_checked": len(rows),
            "new_label_versions": created, "states": states}


if __name__ == "__main__":
    print(json.dumps({"pilot_import": import_pilot(), "advance": advance()}, ensure_ascii=False))
