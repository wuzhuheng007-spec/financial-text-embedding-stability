"""Append-only, externally sourced quote-currency linkage for two US securities.

The moomoo daily-bar payload has no currency field. This code never rewrites it.
USD is an explicit, reviewable cross-source inference from the issuer's Nasdaq
listing, Nasdaq UTP issue-currency specification, and moomoo's quoted-currency
price convention. The two 2026 pilot dates are the earliest admitted dates.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from bs4 import BeautifulSoup

from scripts.news_sentiment_factor_v44.labels import DB, digest, init as init_v44
from .preflight import CONFIG, PRIVATE, PUBLIC, ROOT, dump, sha

VERSION = "v45_external_quote_currency_chain_1"
DOCS = PRIVATE / "source_docs"
PILOT = ROOT / "artifacts/news_sentiment_factor_v44_20261117/price_raw"
SOURCES = {
    "nasdaq_utp": ("nasdaq_utp_quote_spec.pdf", "https://www.nasdaqtrader.com/content/technicalsupport/specifications/utp/uqdfspecification.pdf"),
    "moomoo_market": ("moomoo_cli_market.html", "https://open.moomoo.com/cli/market"),
    "NVDA": ("nvda_2026_10k.html", "https://www.sec.gov/Archives/edgar/data/1045810/000104581026000021/nvda-20260125.htm"),
    "MU": ("mu_2026_10k.html", "https://www.sec.gov/Archives/edgar/data/723125/000072312526000023/mu-20260903.htm"),
}


def init(db: Path = DB) -> None:
    init_v44(db)
    with closing(sqlite3.connect(db, timeout=20)) as con, con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS v45_security_metadata(
          metadata_id TEXT PRIMARY KEY,security_id TEXT NOT NULL,provider_code TEXT NOT NULL,
          venue TEXT NOT NULL,quote_currency TEXT NOT NULL,valid_from TEXT NOT NULL,
          valid_to TEXT NOT NULL,observed_at_utc TEXT NOT NULL,source_links_json TEXT NOT NULL,
          source_hashes_json TEXT NOT NULL,provider_basicinfo_sha256 TEXT NOT NULL,
          verification_method TEXT NOT NULL,rule_version TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS v45_price_currency_links(
          link_id TEXT PRIMARY KEY,price_evidence_id TEXT NOT NULL,metadata_id TEXT NOT NULL,
          quote_currency TEXT NOT NULL,linked_at_utc TEXT NOT NULL,
          rule_version TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS v45_metadata_scope_corrections(
          correction_id TEXT PRIMARY KEY,metadata_id TEXT NOT NULL UNIQUE,
          effective_valid_to TEXT NOT NULL,recorded_at_utc TEXT NOT NULL,
          reason TEXT NOT NULL);
        """)
        for table in ("v45_security_metadata", "v45_price_currency_links",
                      "v45_metadata_scope_corrections"):
            con.execute(f"CREATE TRIGGER IF NOT EXISTS {table}_no_update BEFORE UPDATE ON {table} "
                        f"BEGIN SELECT RAISE(ABORT,'APPEND_ONLY'); END")
            con.execute(f"CREATE TRIGGER IF NOT EXISTS {table}_no_delete BEFORE DELETE ON {table} "
                        f"BEGIN SELECT RAISE(ABORT,'APPEND_ONLY'); END")


def _verified_sources() -> dict:
    receipt = json.loads((DOCS / "source_download_receipt_private.json").read_text(encoding="utf-8"))
    by_name = {row["name"]: row for row in receipt}
    hashes = {}
    for name, (filename, url) in SOURCES.items():
        row = by_name.get(filename)
        path = DOCS / filename
        if not row or row.get("http_status") != 200 or row.get("url") != url or not path.is_file():
            raise ValueError(f"SOURCE_NOT_ARCHIVED_{name}")
        actual = sha(path)
        if actual != row.get("sha256"):
            raise ValueError(f"SOURCE_HASH_CHANGED_{name}")
        hashes[name] = actual
    pdf_text = (DOCS / "nasdaq_utp_quote_spec.txt").read_text(encoding="utf-8", errors="replace")
    if "Version 14.5" not in pdf_text or "USD" not in pdf_text or "US Dollars" not in pdf_text or "currency of an issue" not in pdf_text:
        raise ValueError("NASDAQ_UTP_CURRENCY_EVIDENCE_NOT_LOCATED")
    moomoo = (DOCS / "moomoo_cli_market.html").read_text(encoding="utf-8", errors="replace")
    if "quoted currency" not in moomoo or "does not provide a currency field" not in moomoo:
        raise ValueError("MOOMOO_PRICE_CURRENCY_CONVENTION_NOT_LOCATED")
    for symbol in ("NVDA", "MU"):
        soup = BeautifulSoup((DOCS / SOURCES[symbol][0]).read_bytes(), "html.parser")
        matching_tables = [" ".join(t.stripped_strings) for t in soup.find_all("table")
                           if symbol in " ".join(t.stripped_strings) and
                           "Nasdaq Global Select Market" in " ".join(t.stripped_strings)]
        if not any("Common Stock" in s and len(s) < 1200 for s in matching_tables):
            raise ValueError(f"SEC_COVER_IDENTITY_NOT_LOCATED_{symbol}")
    return hashes


def link_price(con: sqlite3.Connection, metadata_id: str, security_id: str,
               provider_code: str, currency: str, start: str, end: str) -> int:
    rows = con.execute("""SELECT evidence_id,trade_date FROM price_evidence
        WHERE security_id=? AND provider_code=? AND provider='MOOMOO_WEBAPI'
          AND trade_date BETWEEN ? AND ?""", (security_id, provider_code, start, end)).fetchall()
    count = 0
    for row in rows:
        evidence_id, date = row
        link_id = digest([evidence_id, metadata_id, currency, VERSION])
        before = con.total_changes
        con.execute("INSERT OR IGNORE INTO v45_price_currency_links VALUES(?,?,?,?,?,?)",
                    (link_id, evidence_id, metadata_id, currency,
                     datetime.now(timezone.utc).isoformat(), VERSION))
        count += con.total_changes - before
    return count


def resolve_currency(con: sqlite3.Connection, price_evidence_id: str,
                     as_of: datetime) -> tuple[str | None, str | None, str]:
    """Return currency, metadata ID, status; conflicting evidence blocks use."""
    rows = con.execute("""SELECT l.quote_currency,m.metadata_id,m.observed_at_utc,
            m.valid_from,m.valid_to,p.trade_date,m.security_id,m.provider_code,
            c.effective_valid_to
        FROM v45_price_currency_links l JOIN v45_security_metadata m USING(metadata_id)
        JOIN price_evidence p ON p.evidence_id=l.price_evidence_id
        LEFT JOIN v45_metadata_scope_corrections c ON c.metadata_id=m.metadata_id
        WHERE l.price_evidence_id=? AND m.security_id=p.security_id
          AND m.provider_code=p.provider_code""", (price_evidence_id,)).fetchall()
    usable = [(r[0], r[1]) for r in rows if r[3] <= r[5] <= min(r[4], r[8] or r[4])
              and datetime.fromisoformat(r[2].replace("Z", "+00:00")) <= as_of
              and r[6] and r[7]]
    currencies = {c for c, _ in usable}
    if not currencies:
        return None, None, "NO_VALID_METADATA_LINK"
    if len(currencies) != 1:
        return None, None, "CURRENCY_CONFLICT"
    currency = next(iter(currencies))
    chosen = sorted(mid for c, mid in usable if c == currency)[-1]
    return currency, chosen, "EXTERNAL_EVIDENCE_CHAIN_NOT_PROVIDER_FIELD"


def correct_initial_scope(db: Path = DB) -> dict:
    """Append an explicit cap; preserve the earlier overbroad metadata rows."""
    init(db)
    at = datetime.now(timezone.utc).isoformat()
    changes = []
    with closing(sqlite3.connect(db, timeout=20)) as con, con:
        rows = con.execute("""SELECT metadata_id,security_id,valid_to,observed_at_utc
            FROM v45_security_metadata WHERE rule_version=? ORDER BY security_id""",
            (VERSION,)).fetchall()
        for mid, sid, original_end, observed in rows:
            cap = datetime.fromisoformat(observed.replace("Z", "+00:00")).date().isoformat()
            if original_end <= cap:
                continue
            reason = "INITIAL_FUTURE_END_WAS_POLICY_HORIZON_NOT_OBSERVED_DATE_EVIDENCE"
            before = con.total_changes
            con.execute("INSERT OR IGNORE INTO v45_metadata_scope_corrections VALUES(?,?,?,?,?)",
                        (digest([mid, cap, reason]), mid, cap, at, reason))
            changes.append({"security_id": sid, "old_recorded_valid_to": original_end,
                            "effective_valid_to": cap, "new_correction": con.total_changes > before})
    result = {"schema": "news_sentiment_factor_v45_metadata_scope_correction_1",
              "recorded_at_utc": at, "reason": "earlier valid_to was an unproven future policy horizon",
              "pilot_source_dates_preserved": ["2026-11-13", "2026-11-16"],
              "future_dates_require_new_day_specific_evidence": True, "changes": changes}
    dump(PUBLIC / "metadata_scope_correction_redacted.json", result)
    return result


def record_daily_currency_evidence(con: sqlite3.Connection, symbol: str, day: str,
                                   response_sha256: str, request_url: str,
                                   received_at_utc: str) -> str:
    """Bind a new completed day only to its own raw bar and archived identity chain."""
    if symbol not in ("NVDA", "MU") or not response_sha256:
        raise ValueError("DAY_SPECIFIC_METADATA_SCOPE_INVALID")
    rows = con.execute("""SELECT * FROM v45_security_metadata WHERE security_id=?
        AND provider_code=? AND rule_version=? ORDER BY observed_at_utc LIMIT 1""",
        (f"SEC:{symbol}:US", f"US.{symbol}", VERSION)).fetchall()
    if len(rows) != 1:
        raise ValueError("INITIAL_METADATA_CHAIN_NOT_UNIQUE")
    row = rows[0]
    source_hashes = json.loads(row[9]); source_hashes["day_specific_history"] = response_sha256
    source_links = json.loads(row[8]); source_links["day_specific_history"] = request_url
    method = "DAY_SPECIFIC_PROVIDER_CODE_AND_NAME_PLUS_EXTERNAL_ISSUE_CURRENCY_INFERENCE"
    version = VERSION + "_day_specific_1"
    record = [f"SEC:{symbol}:US", f"US.{symbol}", row[3], "USD", day, day,
              received_at_utc, json.dumps(source_links, sort_keys=True),
              json.dumps(source_hashes, sort_keys=True), row[10], method, version]
    mid = digest(record)
    con.execute("INSERT OR IGNORE INTO v45_security_metadata VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (mid, *record))
    link_price(con, mid, f"SEC:{symbol}:US", f"US.{symbol}", "USD", day, day)
    return mid


def import_evidence(db: Path = DB) -> dict:
    init(db)
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    if config["securities"] != ["NVDA", "MU"]:
        raise ValueError("FROZEN_SECURITY_SCOPE_CHANGED")
    hashes = _verified_sources()
    basic_path = next(PILOT.glob("BATCH_basicinfo_*.json"))
    basic = json.loads(basic_path.read_text(encoding="utf-8"))
    if basic.get("ret_code") != 0:
        raise ValueError("MOOMOO_BASICINFO_NOT_SUCCESS")
    by_code = {x["code"]: x for x in basic["data"]["basic_list"]}
    now = datetime.now(timezone.utc).isoformat()
    created = 0
    links = 0
    summary = []
    with closing(sqlite3.connect(db, timeout=20)) as con, con:
        for symbol in config["securities"]:
            code = config["provider_codes"][symbol]
            item = by_code.get(code)
            if not item or item.get("stock_type") != "STOCK" or item.get("exchange") != "US":
                raise ValueError(f"PROVIDER_SECURITY_IDENTITY_UNVERIFIED_{symbol}")
            raw = next(PILOT.glob(f"{symbol}_history_*.json"))
            history = json.loads(raw.read_text(encoding="utf-8"))
            if history.get("ret_code") != 0 or history.get("pagination", {}).get("has_more"):
                raise ValueError(f"PILOT_PRICE_SCOPE_UNVERIFIED_{symbol}")
            dates = sorted(str(x["date"]) for x in history["data"]["kline_list"])
            if dates != ["20261113", "20261116"]:
                raise ValueError(f"PILOT_DATES_UNEXPECTED_{symbol}")
            if not all(str(x.get("name", "")).casefold() == str(item.get("name", "")).casefold()
                       for x in history["data"]["kline_list"]):
                raise ValueError(f"PRICE_BASICINFO_NAME_MISMATCH_{symbol}")
            start, end = "2026-11-13", "2026-12-31"
            sources = {key: {"url": SOURCES[key][1], "sha256": hashes[key]}
                       for key in ("nasdaq_utp", "moomoo_market", symbol)}
            sources["moomoo_basicinfo"] = {"url": "https://open.moomoo.com/api/quote/basic-data/stock-basicinfo",
                                           "sha256": sha(basic_path)}
            sources["moomoo_pilot_history"] = {"url": "https://open.moomoo.com/api/quote/basic-data/history-kline",
                                                "sha256": sha(raw)}
            record = {"security_id": f"SEC:{symbol}:US", "provider_code": code,
                      "venue": "NASDAQ_GLOBAL_SELECT_MARKET", "quote_currency": "USD",
                      "valid_from": start, "valid_to": end,
                      "source_links_json": json.dumps({k: v["url"] for k, v in sources.items()}, sort_keys=True),
                      "source_hashes_json": json.dumps({k: v["sha256"] for k, v in sources.items()}, sort_keys=True),
                      "provider_basicinfo_sha256": sha(basic_path),
                      "verification_method": "EXTERNAL_ISSUE_CURRENCY_INFERENCE_WITH_SEC_AND_PROVIDER_CODE_NOT_MOOMOO_CURRENCY_FIELD",
                      "rule_version": VERSION}
            metadata_id = digest(record)
            before = con.total_changes
            con.execute("INSERT OR IGNORE INTO v45_security_metadata VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (metadata_id, record["security_id"], record["provider_code"], record["venue"],
                         record["quote_currency"], record["valid_from"], record["valid_to"], now,
                         record["source_links_json"], record["source_hashes_json"],
                         record["provider_basicinfo_sha256"], record["verification_method"], VERSION))
            created += con.total_changes - before
            links += link_price(con, metadata_id, record["security_id"], code, "USD", start, end)
            summary.append({"symbol": symbol, "security_id": record["security_id"],
                            "provider_code": code, "venue": record["venue"],
                            "quote_currency": "USD", "valid_from": start, "valid_to": end,
                            "metadata_id": metadata_id, "source_hashes": record["source_hashes_json"]})
    result = {"schema": "news_sentiment_v45_metadata_redacted_1", "generated_at_utc": now,
              "new_metadata_rows": created, "new_currency_links": links,
              "currency_evidence": "EXTERNAL_CROSS_SOURCE_INFERENCE_NOT_MOOMOO_RESPONSE_FIELD",
              "no_historical_dates_before": "2026-11-13", "rows": summary}
    dump(PUBLIC / "metadata_redacted.json", result)
    return result


if __name__ == "__main__":
    print(json.dumps(import_evidence(), ensure_ascii=False))
