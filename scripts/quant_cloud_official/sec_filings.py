"""SEC EDGAR filing metadata for the owner's watchlist (official source, idempotent).

refresh  fetch the SEC ticker indexes and each issuer's submissions JSON, keep the
         configured forms filed in the last ``window_days`` (plus the latest periodic
         report per form), and upsert them in one transaction per batch.
status   print the source state and counts.

Provenance kept per batch: code version, config SHA-256, every input URL with HTTP
status, byte count and SHA-256, and read / inserted / updated / unchanged / rejected
counts. Filing times are stored exactly as SEC returns them (``filingDate`` is a
date; ``acceptanceDateTime`` is kept as the raw literal with its marker). A failed
batch never deletes or rewrites earlier rows; it only records the failure.
Only metadata and links are stored, never filing bodies.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
import uuid
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = ROOT / "configs/quant_cloud_official_sources_v1.json"
SOURCE = "SEC_EDGAR"

SCHEMA = """
CREATE TABLE IF NOT EXISTS batches (
  batch_id TEXT PRIMARY KEY, started_at_utc TEXT NOT NULL, finished_at_utc TEXT,
  status TEXT NOT NULL, code_version TEXT, config_sha256 TEXT NOT NULL, window_days INTEGER NOT NULL,
  symbols_json TEXT NOT NULL, inputs_json TEXT NOT NULL DEFAULT '[]',
  read_count INTEGER NOT NULL DEFAULT 0, inserted INTEGER NOT NULL DEFAULT 0,
  updated INTEGER NOT NULL DEFAULT 0, unchanged INTEGER NOT NULL DEFAULT 0,
  rejected INTEGER NOT NULL DEFAULT 0, retired INTEGER NOT NULL DEFAULT 0, error_class TEXT, error_detail TEXT);
CREATE TABLE IF NOT EXISTS instruments (
  symbol TEXT PRIMARY KEY, cik INTEGER, sec_name TEXT, exchange TEXT, entity_type TEXT,
  sic TEXT, sic_description TEXT, fiscal_year_end TEXT, series_id TEXT, class_id TEXT,
  association_scope TEXT NOT NULL, index_source TEXT NOT NULL, source_url TEXT,
  source_sha256 TEXT, retrieved_at_utc TEXT NOT NULL, batch_id TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS filings (
  accession_number TEXT PRIMARY KEY, cik INTEGER NOT NULL, issuer_name TEXT, form TEXT NOT NULL,
  filing_date TEXT NOT NULL, report_date TEXT, acceptance_datetime_raw TEXT,
  acceptance_tz_marker TEXT, primary_document TEXT, primary_doc_description TEXT, items TEXT,
  document_url TEXT, index_url TEXT NOT NULL, size_bytes INTEGER, in_window INTEGER NOT NULL,
  in_scope INTEGER NOT NULL DEFAULT 1,
  content_sha256 TEXT NOT NULL, first_seen_batch TEXT NOT NULL, last_seen_batch TEXT NOT NULL,
  first_seen_at_utc TEXT NOT NULL, last_seen_at_utc TEXT NOT NULL, source_url TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS filing_symbols (
  accession_number TEXT NOT NULL, symbol TEXT NOT NULL, association_scope TEXT NOT NULL,
  PRIMARY KEY (accession_number, symbol));
CREATE INDEX IF NOT EXISTS filings_date ON filings(filing_date DESC, accession_number);
CREATE TABLE IF NOT EXISTS rejects (
  batch_id TEXT NOT NULL, source_key TEXT NOT NULL, reason TEXT NOT NULL, retryable INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS source_state (
  source TEXT PRIMARY KEY, last_attempt_at_utc TEXT, last_attempt_status TEXT,
  last_success_at_utc TEXT, data_as_of TEXT, last_error_class TEXT, last_error_at_utc TEXT,
  last_batch_id TEXT, last_success_batch_id TEXT);
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def code_version() -> str:
    env = os.environ.get("QUANT_CODE_VERSION")
    if env:
        return env
    try:
        return subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True,
                              text=True, timeout=5).stdout.strip() or "UNKNOWN"
    except (OSError, subprocess.SubprocessError):
        return "UNKNOWN"


def tz_marker(value: str | None) -> str:
    if not value:
        return "MISSING"
    if value.endswith("Z"):
        return "Z_AS_RETURNED"
    if re.search(r"[+-]\d\d:\d\d$", value):
        return "EXPLICIT_OFFSET"
    return "NONE"


class SourceError(RuntimeError):
    def __init__(self, error_class: str, detail: str = "", retryable: bool = True):
        super().__init__(error_class)
        self.error_class, self.detail, self.retryable = error_class, detail, retryable


class SecClient:
    """Rate-limited reader; the contact User-Agent comes from the environment only."""

    def __init__(self, config: dict, opener=urlopen, sleep=time.sleep):
        agent = os.environ.get("SEC_USER_AGENT", "")
        if not re.search(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", agent):
            raise SourceError("SEC_USER_AGENT_CONTACT_REQUIRED", retryable=False)
        self.agent, self.cfg, self.opener, self.sleep = agent, config["request"], opener, sleep
        self.inputs: list[dict] = []
        self._last = 0.0

    def get_json(self, url: str) -> tuple[dict, str]:
        attempts = self.cfg["max_attempts"]
        for attempt in range(1, attempts + 1):
            wait = self.cfg["min_interval_seconds"] - (time.monotonic() - self._last)
            if wait > 0:
                self.sleep(wait)
            self._last = time.monotonic()
            record = {"url": url, "attempt": attempt, "retrieved_at_utc": utc_now()}
            try:
                req = Request(url, headers={"User-Agent": self.agent, "Accept-Encoding": "gzip"})
                with self.opener(req, timeout=self.cfg["timeout_seconds"]) as resp:
                    raw = resp.read(self.cfg["max_response_bytes"] + 1)
                    status = getattr(resp, "status", 200)
                    if resp.headers.get("Content-Encoding") == "gzip":
                        raw = gzip.decompress(raw)
                if len(raw) > self.cfg["max_response_bytes"]:
                    raise SourceError("RESPONSE_TOO_LARGE", url, retryable=False)
                record.update(http_status=status, bytes=len(raw), sha256=sha256(raw))
                self.inputs.append(record)
                return json.loads(raw), record["sha256"]
            except HTTPError as exc:
                record.update(http_status=exc.code); self.inputs.append(record)
                if exc.code in self.cfg["stop_on_http"]:
                    raise SourceError(f"HTTP_{exc.code}", url, retryable=False) from None
                error = SourceError(f"HTTP_{exc.code}", url, retryable=exc.code == 429 or exc.code >= 500)
            except (URLError, TimeoutError, OSError) as exc:
                record.update(error_class=type(exc).__name__); self.inputs.append(record)
                error = SourceError("NETWORK_" + type(exc).__name__, url)
            except ValueError:
                record.update(error_class="INVALID_JSON"); self.inputs.append(record)
                error = SourceError("INVALID_JSON", url, retryable=False)
            if not error.retryable or attempt == attempts:
                raise error
            self.sleep(self.cfg["backoff_seconds"][min(attempt - 1, len(self.cfg["backoff_seconds"]) - 1)])
        raise AssertionError("unreachable")


def watchlist_symbols(config: dict) -> list[str]:
    """The owner's watchlist exactly as stored in the portal database."""
    sys.path.insert(0, str(ROOT))
    from scripts.quant_personal_feed.owner import PORTAL, resolve_owner_id, resolver_name
    owner = resolve_owner_id(config)
    if resolver_name(config) == "django_orm":
        from django.apps import apps
        from django.db import connections
        try:
            rows = list(apps.get_model("core", "WatchlistItem").objects.filter(user_id=owner)
                        .order_by("pk").values_list("symbol", flat=True))
        finally:
            connections.close_all()
    else:
        db = (ROOT / config["portal_db"]).resolve()
        with closing(sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)) as con:
            rows = [r[0] for r in con.execute(
                "SELECT symbol FROM core_watchlistitem WHERE user_id=? ORDER BY id", (owner,))]
    return [s.strip().upper() for s in rows]


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, timeout=30, isolation_level=None)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=30000")
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(SCHEMA)
    # Additive, idempotent migration for databases created by the first version.
    for table, column, ddl in (("filings", "in_scope", "INTEGER NOT NULL DEFAULT 1"),
                               ("batches", "retired", "INTEGER NOT NULL DEFAULT 0")):
        if column not in {r[1] for r in con.execute(f"PRAGMA table_info({table})")}:
            con.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
    return con


def _filing_rows(sub: dict, cik: int, forms: set[str], config: dict, today) -> list[dict]:
    recent = sub.get("filings", {}).get("recent", {})
    keys = ("accessionNumber", "form", "filingDate", "reportDate", "acceptanceDateTime",
            "primaryDocument", "primaryDocDescription", "items", "size")
    n = len(recent.get("accessionNumber", []))
    cols = {k: recent.get(k, [None] * n) for k in keys}
    cutoff = (today - timedelta(days=config["window_days"])).isoformat()
    rows, latest_periodic = [], {}
    for i in range(n):
        r = {k: cols[k][i] if i < len(cols[k]) else None for k in keys}
        if r["form"] not in forms or not r["accessionNumber"] or not r["filingDate"]:
            continue
        r["in_window"] = r["filingDate"] >= cutoff
        if r["in_window"]:
            rows.append(r)
        elif config["keep_latest_periodic_outside_window"] and r["form"] in config["periodic_forms"]:
            best = latest_periodic.get(r["form"])
            if best is None or r["filingDate"] > best["filingDate"]:
                latest_periodic[r["form"]] = r
    in_window_periodic = {r["form"] for r in rows if r["form"] in config["periodic_forms"]}
    rows += [r for f, r in latest_periodic.items() if f not in in_window_periodic]
    return rows


def refresh(config: dict, db_path: Path, *, symbols: list[str] | None = None,
            client: SecClient | None = None, today=None) -> dict:
    cfg_sha = sha256(json.dumps(config, sort_keys=True).encode())
    batch_id = "sec-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]
    today = today or datetime.now(timezone.utc).date()
    started = utc_now()
    with closing(connect(db_path)) as con:
        con.execute("INSERT INTO batches(batch_id,started_at_utc,status,code_version,config_sha256,window_days,symbols_json)"
                    " VALUES(?,?,?,?,?,?,?)", (batch_id, started, "RUNNING", code_version(), cfg_sha,
                                               config["window_days"], "[]"))
        con.execute("INSERT INTO source_state(source,last_attempt_at_utc,last_attempt_status,last_batch_id) VALUES(?,?,?,?)"
                    " ON CONFLICT(source) DO UPDATE SET last_attempt_at_utc=excluded.last_attempt_at_utc,"
                    " last_attempt_status=excluded.last_attempt_status, last_batch_id=excluded.last_batch_id",
                    (SOURCE, started, "RUNNING", batch_id))
        counts = dict(read_count=0, inserted=0, updated=0, unchanged=0, rejected=0, retired=0)
        rejects: list[tuple] = []
        client = client
        try:
            client = client or SecClient(config)
            symbols = symbols if symbols is not None else watchlist_symbols(config)
            ep = config["endpoints"]
            ex, ex_sha = client.get_json(ep["ticker_exchange_index"])
            mf, mf_sha = client.get_json(ep["fund_ticker_index"])
            exf, mff = ex["fields"], mf["fields"]
            ex_rows = {r[exf.index("ticker")]: dict(zip(exf, r)) for r in ex["data"]}
            mf_rows = {r[mff.index("symbol")]: dict(zip(mff, r)) for r in mf["data"]}
            instruments, by_cik = [], {}
            for sym in symbols:
                if sym in ex_rows:
                    m = ex_rows[sym]
                    inst = dict(symbol=sym, cik=int(m["cik"]), exchange=m.get("exchange"), series_id=None,
                                class_id=None, index_source="company_tickers_exchange",
                                source_url=ep["ticker_exchange_index"], source_sha256=ex_sha)
                elif sym in mf_rows:
                    m = mf_rows[sym]
                    inst = dict(symbol=sym, cik=int(m["cik"]), exchange=None, series_id=m.get("seriesId"),
                                class_id=m.get("classId"), index_source="company_tickers_mf",
                                source_url=ep["fund_ticker_index"], source_sha256=mf_sha)
                else:
                    rejects.append((batch_id, sym, "SYMBOL_NOT_IN_SEC_TICKER_INDEXES", 0))
                    continue
                instruments.append(inst)
                by_cik.setdefault(inst["cik"], []).append(inst)
            subs = {}
            for cik in by_cik:
                url = ep["submissions"].format(cik10=f"{cik:010d}")
                try:
                    subs[cik] = (client.get_json(url)[0], url)
                except SourceError as exc:
                    if not exc.retryable:
                        raise
                    rejects.append((batch_id, url, exc.error_class, 1))
            now = utc_now()
            planned = []
            for cik, insts in by_cik.items():
                if cik not in subs:
                    continue
                sub, sub_url = subs[cik]
                fund = sub.get("entityType") != "operating"
                for inst in insts:
                    # SEC returns "" for fields that do not apply (e.g. funds have no SIC): store NULL, not "".
                    blank = lambda v: v if v not in ("", None) else None
                    inst.update(sec_name=blank(sub.get("name")), entity_type=blank(sub.get("entityType")),
                                sic=blank(sub.get("sic")), sic_description=blank(sub.get("sicDescription")),
                                fiscal_year_end=blank(sub.get("fiscalYearEnd")),
                                exchange=inst["exchange"] or ",".join(sub.get("exchanges") or []) or None,
                                association_scope="ISSUER_TRUST_ALL_SERIES" if inst["series_id"] else
                                ("FUND_TRUST" if fund else "ISSUER"))
                forms = set(config["forms"]["fund" if fund else "operating"])
                for r in _filing_rows(sub, cik, forms, config, today):
                    counts["read_count"] += 1
                    acc = r["accessionNumber"]
                    nodash = acc.replace("-", "")
                    if not re.fullmatch(r"\d{10}-\d{2}-\d{6}", acc):
                        rejects.append((batch_id, f"{cik}:{acc}", "BAD_ACCESSION_FORMAT", 0)); continue
                    doc_url = (ep["primary_document"].format(cik=cik, accession_nodash=nodash,
                               primary_document=r["primaryDocument"]) if r["primaryDocument"] else None)
                    rec = dict(accession_number=acc, cik=cik, issuer_name=sub.get("name"), form=r["form"],
                               filing_date=r["filingDate"], report_date=r["reportDate"] or None,
                               acceptance_datetime_raw=r["acceptanceDateTime"],
                               acceptance_tz_marker=tz_marker(r["acceptanceDateTime"]),
                               primary_document=r["primaryDocument"] or None,
                               primary_doc_description=r["primaryDocDescription"] or None,
                               items=r["items"] or None, document_url=doc_url,
                               index_url=ep["filing_index"].format(cik=cik, accession_nodash=nodash),
                               size_bytes=r["size"], in_window=int(r["in_window"]), source_url=sub_url)
                    rec["content_sha256"] = sha256(json.dumps({k: rec[k] for k in sorted(rec) if k != "in_window"},
                                                              sort_keys=True).encode())
                    planned.append((rec, [(i["symbol"], i["association_scope"]) for i in insts]))
            # One transaction for the whole batch: all rows or none.
            con.execute("BEGIN IMMEDIATE")
            try:
                for inst in instruments:
                    if inst["cik"] not in subs:
                        continue
                    con.execute("""INSERT INTO instruments(symbol,cik,sec_name,exchange,entity_type,sic,sic_description,
                      fiscal_year_end,series_id,class_id,association_scope,index_source,source_url,source_sha256,
                      retrieved_at_utc,batch_id) VALUES(:symbol,:cik,:sec_name,:exchange,:entity_type,:sic,:sic_description,
                      :fiscal_year_end,:series_id,:class_id,:association_scope,:index_source,:source_url,:source_sha256,:now,:batch)
                      ON CONFLICT(symbol) DO UPDATE SET cik=excluded.cik,sec_name=excluded.sec_name,exchange=excluded.exchange,
                      entity_type=excluded.entity_type,sic=excluded.sic,sic_description=excluded.sic_description,
                      fiscal_year_end=excluded.fiscal_year_end,series_id=excluded.series_id,class_id=excluded.class_id,
                      association_scope=excluded.association_scope,index_source=excluded.index_source,
                      source_url=excluded.source_url,source_sha256=excluded.source_sha256,
                      retrieved_at_utc=excluded.retrieved_at_utc,batch_id=excluded.batch_id""",
                                {**inst, "now": now, "batch": batch_id})
                for rec, links in planned:
                    old = con.execute("SELECT content_sha256,in_window,in_scope FROM filings WHERE accession_number=?",
                                      (rec["accession_number"],)).fetchone()
                    if old is None:
                        cols = list(rec) + ["first_seen_batch", "last_seen_batch", "first_seen_at_utc", "last_seen_at_utc"]
                        con.execute(f"INSERT INTO filings({','.join(cols)}) VALUES({','.join('?' * len(cols))})",
                                    [rec[k] for k in rec] + [batch_id, batch_id, now, now])
                        counts["inserted"] += 1
                    elif (old["content_sha256"] != rec["content_sha256"] or old["in_window"] != rec["in_window"]
                          or not old["in_scope"]):
                        sets = ",".join(f"{k}=?" for k in rec if k != "accession_number")
                        con.execute(f"UPDATE filings SET {sets},in_scope=1,last_seen_batch=?,last_seen_at_utc=? WHERE accession_number=?",
                                    [rec[k] for k in rec if k != "accession_number"] + [batch_id, now, rec["accession_number"]])
                        counts["updated"] += 1
                    else:
                        con.execute("UPDATE filings SET last_seen_batch=?,last_seen_at_utc=? WHERE accession_number=?",
                                    (batch_id, now, rec["accession_number"]))
                        counts["unchanged"] += 1
                    for sym, scope in links:
                        con.execute("INSERT OR IGNORE INTO filing_symbols VALUES(?,?,?)",
                                    (rec["accession_number"], sym, scope))
                # Rows of the issuers fetched in this batch that the current config / window no longer
                # selects (form dropped, aged out, superseded periodic report) stay for traceability
                # but leave the page. Issuers whose fetch failed are untouched.
                kept: dict[int, set] = {}
                for rec, _ in planned:
                    kept.setdefault(rec["cik"], set()).add(rec["accession_number"])
                for cik in subs:
                    keep = kept.get(cik, set())
                    marks = ",".join("?" * len(keep)) or "''"
                    cur = con.execute(f"UPDATE filings SET in_scope=0,in_window=0,last_seen_batch=?,last_seen_at_utc=?"
                                      f" WHERE cik=? AND in_scope=1 AND accession_number NOT IN ({marks})",
                                      [batch_id, now, cik, *sorted(keep)])
                    counts["retired"] += cur.rowcount
                counts["rejected"] = len(rejects)
                con.executemany("INSERT INTO rejects VALUES(?,?,?,?)", rejects)
                status = "SUCCESS" if not rejects else "PARTIAL"
                data_as_of = max((r["filing_date"] for r, _ in planned), default=None)
                con.execute("""UPDATE batches SET finished_at_utc=?,status=?,symbols_json=?,inputs_json=?,read_count=?,
                    inserted=?,updated=?,unchanged=?,rejected=?,retired=? WHERE batch_id=?""",
                            (utc_now(), status, json.dumps(symbols), json.dumps(client.inputs), counts["read_count"],
                             counts["inserted"], counts["updated"], counts["unchanged"], counts["rejected"],
                             counts["retired"], batch_id))
                con.execute("""UPDATE source_state SET last_attempt_status=?,last_success_at_utc=?,
                    data_as_of=COALESCE((SELECT max(filing_date) FROM filings WHERE in_scope=1),?),last_success_batch_id=?,
                    last_error_class=CASE WHEN ?='PARTIAL' THEN 'PARTIAL_REJECTS' ELSE last_error_class END
                    WHERE source=?""", (status, utc_now(), data_as_of, batch_id, status, SOURCE))
                con.execute("COMMIT")
            except BaseException:
                con.execute("ROLLBACK")
                raise
            return {"batch_id": batch_id, "status": status, "symbols": symbols, **counts,
                    "requests": len(client.inputs), "rejects": [r[1:] for r in rejects]}
        except BaseException as exc:
            error_class = getattr(exc, "error_class", type(exc).__name__)
            inputs = json.dumps(getattr(client, "inputs", []) if isinstance(client, SecClient) else [])
            con.execute("UPDATE batches SET finished_at_utc=?,status='FAILED',error_class=?,error_detail=?,inputs_json=?"
                        " WHERE batch_id=?", (utc_now(), error_class, str(getattr(exc, "detail", ""))[:300], inputs, batch_id))
            con.execute("UPDATE source_state SET last_attempt_status='FAILED',last_error_class=?,last_error_at_utc=?"
                        " WHERE source=?", (error_class, utc_now(), SOURCE))
            raise


def read_for_portal(db_path: Path, symbols: set[str], limit: int = 200) -> dict:
    """Read-only view for the web page; never contacts SEC."""
    if not db_path.is_file():
        return {"state": "NO_DATA", "source": SOURCE, "instruments": [], "filings": [], "source_state": None}
    with closing(sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=5)) as con:
        con.row_factory = sqlite3.Row
        state = con.execute("SELECT * FROM source_state WHERE source=?", (SOURCE,)).fetchone()
        last_success = con.execute("SELECT batch_id,finished_at_utc,code_version,config_sha256,read_count,inserted,"
                                   "updated,unchanged,rejected,retired FROM batches WHERE batch_id=?",
                                   (state["last_success_batch_id"] if state else None,)).fetchone()
        marks = ",".join("?" * len(symbols)) or "''"
        inst = [dict(r) for r in con.execute(f"SELECT * FROM instruments WHERE symbol IN ({marks}) ORDER BY symbol",
                                             sorted(symbols))]
        filings = [dict(r) for r in con.execute(f"""
            SELECT f.accession_number,f.issuer_name,f.form,f.filing_date,f.report_date,f.acceptance_datetime_raw,
                   f.acceptance_tz_marker,f.primary_doc_description,f.items,f.document_url,f.index_url,f.in_window,
                   f.first_seen_at_utc,f.last_seen_at_utc,
                   group_concat(s.symbol) AS symbols, min(s.association_scope) AS association_scope
            FROM filings f JOIN filing_symbols s ON s.accession_number=f.accession_number
            WHERE s.symbol IN ({marks}) AND f.in_scope=1 GROUP BY f.accession_number
            ORDER BY f.filing_date DESC, f.accession_number DESC LIMIT ?""", [*sorted(symbols), limit])]
    window = None
    if last_success:
        batch_row = None
        with closing(sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=5)) as con:
            batch_row = con.execute("SELECT started_at_utc, window_days FROM batches WHERE batch_id=?",
                                    (last_success["batch_id"],)).fetchone()
        if batch_row:
            day = datetime.fromisoformat(batch_row[0].replace("Z", "+00:00")).date()
            window = {"days": batch_row[1], "start_filing_date": (day - timedelta(days=batch_row[1])).isoformat(),
                      "basis": "filingDate >= start_filing_date, computed by the last successful batch (UTC date)",
                      "supplementary": "latest periodic report per form (10-K, 10-Q, 20-F, N-CSR, N-CSRS) "
                                       "filed before the window, kept as context"}
    return {"state": (state["last_attempt_status"] if state else "NO_DATA"), "source": SOURCE,
            "source_state": dict(state) if state else None, "window": window,
            "last_success_batch": dict(last_success) if last_success else None,
            "instruments": inst, "filings": filings}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("action", choices=("refresh", "status"))
    ap.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    ap.add_argument("--db", type=Path)
    ap.add_argument("--symbols", help="comma list; default: the owner's watchlist from the portal database")
    args = ap.parse_args(argv)
    config = json.loads(args.config.read_text(encoding="utf-8"))
    db = args.db or (ROOT / config["database"])
    if args.action == "status":
        print(json.dumps(read_for_portal(db, set((args.symbols or "").split(",")) - {""}, limit=5), default=str))
        return 0
    syms = [s.strip().upper() for s in args.symbols.split(",")] if args.symbols else None
    try:
        result = refresh(config, db, symbols=syms)
    except SourceError as exc:
        print(json.dumps({"status": "FAILED", "error_class": exc.error_class}))
        return 2
    print(json.dumps(result))
    return 0 if result["status"] == "SUCCESS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
