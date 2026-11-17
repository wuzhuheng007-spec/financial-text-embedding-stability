"""Append-only qualification of v4.4 regular daily-bar price-return labels.

No quote is substituted for a regular-session daily bar. A previously issued
label can be superseded by an appended invalidation decision, never deleted.
"""
from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

from scripts.news_sentiment_factor_v44.labels import (
    CONTRACT, DB, QUEUE, digest, parsed_utc,
)
from .daily_prices import init as init_prices
from .metadata import resolve_currency
from .preflight import sha

VERSION = "v45_label_qualification_1"


def init(db: Path = DB) -> None:
    init_prices(db)
    with closing(sqlite3.connect(db, timeout=20)) as con, con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS v45_label_decisions(
          decision_id TEXT PRIMARY KEY, snapshot_id TEXT NOT NULL,
          entity_id TEXT NOT NULL,horizon_sessions INTEGER NOT NULL,
          decision_at_utc TEXT NOT NULL,state TEXT NOT NULL,reason TEXT NOT NULL,
          label_id TEXT,input_manifest_sha256 TEXT,contract TEXT NOT NULL,
          rule_version TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS v45_label_checks(
          check_id TEXT PRIMARY KEY,checked_at_utc TEXT NOT NULL,
          snapshot_id TEXT NOT NULL,entity_id TEXT NOT NULL,
          horizon_sessions INTEGER NOT NULL,state TEXT NOT NULL,reason TEXT NOT NULL);
        """)
        for table in ("v45_label_decisions", "v45_label_checks"):
            con.execute(f"CREATE TRIGGER IF NOT EXISTS {table}_no_update BEFORE UPDATE ON {table} "
                        "BEGIN SELECT RAISE(ABORT,'APPEND_ONLY'); END")
            con.execute(f"CREATE TRIGGER IF NOT EXISTS {table}_no_delete BEFORE DELETE ON {table} "
                        "BEGIN SELECT RAISE(ABORT,'APPEND_ONLY'); END")


def _latest_price(con: sqlite3.Connection, sid: str, day: str, field: str,
                  at: datetime) -> sqlite3.Row | None:
    rows = con.execute("""SELECT p.* FROM price_evidence p
      JOIN v45_price_observations o ON o.evidence_id=p.evidence_id
      WHERE p.security_id=? AND p.trade_date=? AND p.field=? AND p.session='REGULAR'
        AND p.adjustment='UNADJUSTED_AUTYPE_0'
        AND p.price_semantics='UNADJUSTED_DAILY_BAR_NOT_OFFICIAL_AUCTION'""",
        (sid, day, field)).fetchall()
    valid = [r for r in rows if parsed_utc(r["received_at_utc"]) <= at]
    return max(valid, key=lambda r: (parsed_utc(r["received_at_utc"]), r["evidence_id"])) if valid else None


def _latest_action(con: sqlite3.Connection, sid: str, start: str, end: str,
                   at: datetime) -> sqlite3.Row | None:
    rows = con.execute("""SELECT a.* FROM action_evidence a
      JOIN v45_action_coverage c ON c.action_evidence_id=a.evidence_id
      WHERE a.security_id=? AND a.observed_from_date<=? AND a.observed_through_date>=?""",
      (sid, start, end)).fetchall()
    valid = [r for r in rows if parsed_utc(r["checked_at_utc"]) <= at]
    return max(valid, key=lambda r: (parsed_utc(r["checked_at_utc"]), r["evidence_id"])) if valid else None


def _previous_decision(con: sqlite3.Connection, q: sqlite3.Row) -> sqlite3.Row | None:
    return con.execute("""SELECT * FROM v45_label_decisions
      WHERE snapshot_id=? AND entity_id=? AND horizon_sessions=?
      ORDER BY decision_at_utc DESC,decision_id DESC LIMIT 1""",
      (q["snapshot_id"], q["entity_id"], q["horizon_sessions"])).fetchone()


def _decision(con: sqlite3.Connection, q: sqlite3.Row, state: str,
              reason: str, label_id: str | None, manifest: str | None,
              at: datetime) -> int:
    old = _previous_decision(con, q)
    if old and (old["state"], old["reason"], old["label_id"], old["input_manifest_sha256"]) == \
               (state, reason, label_id, manifest):
        return 0
    identity = [q["snapshot_id"], q["entity_id"], q["horizon_sessions"],
                state, reason, label_id, manifest, at.isoformat(), VERSION]
    con.execute("INSERT INTO v45_label_decisions VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (digest(identity), q["snapshot_id"], q["entity_id"], q["horizon_sessions"],
                 at.isoformat(), state, reason, label_id, manifest, CONTRACT, VERSION))
    return 1


def _check(con: sqlite3.Connection, q: sqlite3.Row, state: str, reason: str,
           at: datetime) -> None:
    old = con.execute("""SELECT state,reason FROM v45_label_checks
      WHERE snapshot_id=? AND entity_id=? AND horizon_sessions=?
      ORDER BY checked_at_utc DESC,check_id DESC LIMIT 1""",
      (q["snapshot_id"], q["entity_id"], q["horizon_sessions"])).fetchone()
    if old and tuple(old) == (state, reason):
        return
    identity = [at.isoformat(), q["snapshot_id"], q["entity_id"],
                q["horizon_sessions"], state, reason]
    con.execute("INSERT INTO v45_label_checks VALUES(?,?,?,?,?,?,?)",
                (digest(identity), *identity))


def advance(db: Path = DB, queue: Path = QUEUE,
            observed_at: datetime | None = None) -> dict:
    init(db)
    at = (observed_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if not queue.exists():
        return {"checked_at_utc": at.isoformat(), "queue_rows": 0,
                "new_labels": 0, "new_decisions": 0, "states": {}}
    with closing(sqlite3.connect(f"file:{queue.as_posix()}?mode=ro", uri=True)) as src, \
         closing(sqlite3.connect(db, timeout=20)) as con:
        src.row_factory = sqlite3.Row
        con.row_factory = sqlite3.Row
        rows = src.execute("""SELECT q.*,c.n1,s.kind,r.signal_available_at_utc,
          w.release_commit_observed_at_utc FROM daily_label_queue q
          JOIN daily_snapshot_company c ON c.snapshot_id=q.snapshot_id AND c.entity_id=q.entity_id
          JOIN daily_snapshots s USING(snapshot_id)
          JOIN daily_snapshot_releases r USING(snapshot_id)
          JOIN daily_snapshot_publication_witness w USING(snapshot_id)
          WHERE q.security_id IN ('SEC:NVDA:US','SEC:MU:US')
          ORDER BY q.snapshot_id,q.entity_id,q.horizon_sessions""").fetchall()
        states: dict[str, int] = {}
        new_labels = new_decisions = 0
        for q in rows:
            state, reason = "WAIT_QUALIFIED_PRICE", "QUALIFIED_DAILY_PRICE_OR_ACTION_MISSING"
            old = _previous_decision(con, q)
            existing_active = old is not None and old["state"] == "CURRENT_QUALIFIED"
            available = parsed_utc(q["signal_available_at_utc"])
            witness = parsed_utc(q["release_commit_observed_at_utc"])
            entry = parsed_utc(q["entry_at_utc"]) if q["entry_at_utc"] else None
            exit_at = parsed_utc(q["exit_at_utc"]) if q["exit_at_utc"] else None
            qualified = False
            label_id = manifest = None
            if q["kind"] not in ("ON_TIME_SCHEDULED", "LATE_SCHEDULED") or witness > available:
                state, reason = "BLOCKED_SIGNAL_PROVENANCE", "NOT_RELEASED_REAL_SCHEDULED"
            elif q["n1"] is None:
                state, reason = "NO_FACTOR_PAIR", "N1_MISSING"
            elif entry is None or exit_at is None:
                state, reason = "BLOCKED_CALENDAR", "ENTRY_OR_EXIT_TIME_MISSING"
            elif available >= entry:
                state, reason = "MISSED_ENTRY", "SIGNAL_NOT_AVAILABLE_BEFORE_ENTRY"
            elif at < entry:
                state, reason = "WAIT_ENTRY", "ENTRY_NOT_REACHED"
            elif at < exit_at:
                state, reason = "WAIT_EXIT", "EXIT_NOT_REACHED"
            else:
                ep = _latest_price(con, q["security_id"], q["entry_session_ny"], "OPEN", at)
                xp = _latest_price(con, q["security_id"], q["exit_session_ny"], "CLOSE", at)
                action = _latest_action(con, q["security_id"], q["entry_session_ny"],
                                        q["exit_session_ny"], at)
                if not ep or not xp:
                    reason = "ENTRY_OR_EXIT_REGULAR_DAILY_BAR_NOT_COLLECTED"
                elif not action:
                    state, reason = "WAIT_ACTION_COVERAGE", "NO_POST_EXIT_COMPLETE_ACTION_LIST_PAIR"
                elif action["evidence_status"] != "PROVIDER_LISTS_NO_ACTION_IN_INTERVAL_V45" or \
                     action["split_events"] or action["dividend_events"]:
                    state, reason = "BLOCKED_ACTION_REVIEW", "ACTION_PRESENT_OR_SOURCE_SCOPE_INCOMPLETE"
                else:
                    ec, em, es = resolve_currency(con, ep["evidence_id"], at)
                    xc, xm, xs = resolve_currency(con, xp["evidence_id"], at)
                    if ec != xc or ec is None or es != xs:
                        reason = "CURRENCY_EVIDENCE_MISSING_OR_CONFLICT"
                    elif ep["provider"] != xp["provider"] or \
                         ep["provider_code"] != xp["provider_code"] or \
                         ep["adjustment"] != xp["adjustment"] or \
                         ep["security_id"] != xp["security_id"] or \
                         ep["security_id"] != q["security_id"]:
                        reason = "PRICE_SERIES_IDENTITY_OR_ADJUSTMENT_MISMATCH"
                    else:
                        try:
                            start_price = Decimal(ep["price_text"])
                            end_price = Decimal(xp["price_text"])
                            if not start_price.is_finite() or not end_price.is_finite() or \
                               start_price <= 0 or end_price <= 0:
                                raise InvalidOperation("INVALID_POSITIVE_PRICE")
                            value = end_price / start_price - 1
                            qualified = True
                        except (InvalidOperation, ZeroDivisionError):
                            reason = "NONFINITE_OR_NONPOSITIVE_PRICE"
                    if qualified:
                        manifest = digest([ep["evidence_id"], xp["evidence_id"],
                                           action["evidence_id"], em, xm, ec,
                                           q["signal_available_at_utc"], CONTRACT, VERSION])
                        prior = con.execute("""SELECT revision,input_manifest_sha256,label_id
                          FROM label_versions WHERE snapshot_id=? AND entity_id=?
                          AND horizon_sessions=? AND contract=?
                          ORDER BY revision DESC LIMIT 1""",
                          (q["snapshot_id"], q["entity_id"], q["horizon_sessions"], CONTRACT)).fetchone()
                        if prior and prior["input_manifest_sha256"] == manifest:
                            label_id = prior["label_id"]
                        else:
                            revision = 1 if prior is None else prior["revision"] + 1
                            label_id = digest([q["snapshot_id"], q["entity_id"],
                                               q["horizon_sessions"], CONTRACT, revision])
                            generated = datetime.now(timezone.utc).isoformat()
                            con.execute("INSERT INTO label_versions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                        (label_id, q["snapshot_id"], q["entity_id"],
                                         q["horizon_sessions"], revision, CONTRACT,
                                         q["signal_available_at_utc"], ep["evidence_id"],
                                         xp["evidence_id"], action["evidence_id"], str(value),
                                         "unadjusted_regular_daily_bar_close / unadjusted_regular_daily_bar_open - 1; cash_dividends excluded",
                                         sha(Path(__file__)), generated, generated, manifest))
                            new_labels += 1
                        state, reason = "CURRENT_QUALIFIED", "DAILY_BAR_PRICE_RETURN_NOT_AUCTION_OR_TOTAL_RETURN"
            if qualified:
                new_decisions += _decision(con, q, state, reason, label_id, manifest, at)
            elif existing_active:
                new_decisions += _decision(con, q, "INVALIDATED", reason, None, None, at)
            _check(con, q, state, reason, at)
            states[state] = states.get(state, 0) + 1
        con.commit()
    return {"checked_at_utc": at.isoformat(), "queue_rows": len(rows),
            "new_labels": new_labels, "new_decisions": new_decisions,
            "states": states}
