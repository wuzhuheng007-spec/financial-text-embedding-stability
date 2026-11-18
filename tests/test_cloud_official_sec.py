"""SEC EDGAR filing import: provenance, idempotency, failure keeps old data (synthetic, no network)."""
from __future__ import annotations

import io
import json
import sqlite3
from datetime import date
from pathlib import Path
from urllib.error import HTTPError, URLError

import pytest

from scripts.quant_cloud_official import sec_filings as sec

ROOT = Path(__file__).resolve().parents[1]
CONFIG = json.loads((ROOT / "configs/quant_cloud_official_sources_v1.json").read_text(encoding="utf-8"))
TODAY = date(2026, 11, 19)


def _sub(name, etype, rows):
    keys = ("accessionNumber", "form", "filingDate", "reportDate", "acceptanceDateTime",
            "primaryDocument", "primaryDocDescription", "items", "size")
    return {"name": name, "entityType": etype, "sic": "3674", "sicDescription": "Semis", "fiscalYearEnd": "0125",
            "exchanges": ["Nasdaq"], "filings": {"recent": {k: [r[i] for r in rows] for i, k in enumerate(keys)}}}


NVDA = _sub("NVIDIA CORP", "operating", [
    ("0001045810-26-000088", "8-K", "2026-11-17", "2026-11-17", "2026-11-17T21:20:51.000Z", "a.htm", "8-K", "2.02,9.01", 100),
    ("0001045810-26-000082", "4", "2026-11-12", "", "2026-11-12T13:52:41.000Z", "b.xml", "", "", 10),         # form not configured
    ("0001045810-26-000075", "10-Q", "2026-08-26", "2026-07-26", "2026-08-26T20:36:00.000Z", "q.htm", "10-Q", "", 9),
    ("0001045810-26-000060", "10-Q", "2026-05-28", "2026-04-26", "2026-05-28T20:00:00.000Z", "q0.htm", "10-Q", "", 9),
    ("0001045810-26-000010", "8-K", "2026-09-01", "2026-09-01", "2026-09-01T12:00:00.000Z", "old.htm", "8-K", "", 9),  # outside window
])
FUND = _sub("PROSHARES TRUST", "investment", [
    ("0001174610-26-000500", "N-CSRS", "2026-11-10", "2026-09-30", "2026-11-10T10:00:00.000Z", "n.htm", "N-CSRS", "", 5),
    ("0001174610-26-000501", "497", "2026-11-11", "", "2026-11-11T10:00:00.000Z", "p.htm", "497", "", 5),
])
EXCHANGE = {"fields": ["cik", "name", "ticker", "exchange"], "data": [[1045810, "NVIDIA CORP", "NVDA", "Nasdaq"]]}
MF = {"fields": ["cik", "seriesId", "classId", "symbol"], "data": [[1174610, "S000024908", "C000074098", "TQQQ"]]}


class Resp(io.BytesIO):
    status = 200
    headers = {}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def opener_for(routes: dict, fail: dict | None = None):
    calls = []

    def opener(req, timeout):
        url = req.full_url
        calls.append(url)
        if fail and url in fail:
            raise fail[url]
        for key, payload in routes.items():
            if key in url:
                return Resp(json.dumps(payload).encode())
        raise HTTPError(url, 404, "nf", {}, io.BytesIO())
    opener.calls = calls
    return opener


ROUTES = {"company_tickers_exchange": EXCHANGE, "company_tickers_mf": MF,
          "CIK0001045810": NVDA, "CIK0001174610": FUND}


@pytest.fixture(autouse=True)
def agent(monkeypatch):
    monkeypatch.setenv("SEC_USER_AGENT", "test-suite contact@example.invalid")
    monkeypatch.setenv("QUANT_CODE_VERSION", "testsha")


def run(db, routes=ROUTES, fail=None, symbols=("NVDA", "TQQQ", "ZZZZ")):
    client = sec.SecClient(CONFIG, opener=opener_for(routes, fail), sleep=lambda s: None)
    return sec.refresh(CONFIG, db, symbols=list(symbols), client=client, today=TODAY)


def rows(db, sql):
    with sqlite3.connect(db) as c:
        return c.execute(sql).fetchall()


def test_import_window_forms_provenance_and_rejects(tmp_path):
    db = tmp_path / "f.sqlite3"
    r = run(db)
    assert r["status"] == "PARTIAL" and r["rejects"] == [("ZZZZ", "SYMBOL_NOT_IN_SEC_TICKER_INDEXES", 0)]
    got = rows(db, "select accession_number, form, in_window from filings order by accession_number")
    # 8-K in window, latest 10-Q outside window kept as periodic, older 10-Q / old 8-K / Form 4 / 497 not kept
    assert got == [("0001045810-26-000075", "10-Q", 0), ("0001045810-26-000088", "8-K", 1),
                   ("0001174610-26-000500", "N-CSRS", 1)]
    assert r["read_count"] == 3 and r["inserted"] == 3
    assert rows(db, "select acceptance_datetime_raw, acceptance_tz_marker from filings where form='8-K'") == [
        ("2026-11-17T21:20:51.000Z", "Z_AS_RETURNED")]  # stored as returned, not converted
    assert rows(db, "select symbol, association_scope from filing_symbols order by symbol") == [
        ("NVDA", "ISSUER"), ("NVDA", "ISSUER"), ("TQQQ", "ISSUER_TRUST_ALL_SERIES")]
    batch = rows(db, "select code_version, config_sha256, inputs_json from batches")[0]
    inputs = json.loads(batch[2])
    assert batch[0] == "testsha" and len(batch[1]) == 64
    assert all(len(i["sha256"]) == 64 and i["http_status"] == 200 for i in inputs) and len(inputs) == 4
    assert "contact@example.invalid" not in batch[2]  # the User-Agent is never stored


def test_rerun_is_idempotent(tmp_path):
    db = tmp_path / "f.sqlite3"
    run(db)
    second = run(db)
    assert (second["inserted"], second["updated"], second["unchanged"]) == (0, 0, 3)
    assert rows(db, "select count(*) from filings")[0][0] == 3
    assert rows(db, "select count(*) from filing_symbols")[0][0] == 3


def test_failed_refresh_keeps_last_success(tmp_path):
    db = tmp_path / "f.sqlite3"
    run(db)
    before = rows(db, "select * from filings order by accession_number")
    with pytest.raises(sec.SourceError):
        run(db, fail={CONFIG["endpoints"]["ticker_exchange_index"]: URLError("down")})
    assert rows(db, "select * from filings order by accession_number") == before
    state = rows(db, "select last_attempt_status, last_error_class, last_success_batch_id is not null from source_state")[0]
    assert state == ("FAILED", "NETWORK_URLError", 1)
    view = sec.read_for_portal(db, {"NVDA", "TQQQ"})
    assert view["state"] == "FAILED" and len(view["filings"]) == 3 and view["last_success_batch"]


def test_http_403_stops_without_retry(tmp_path):
    db = tmp_path / "f.sqlite3"
    url = CONFIG["endpoints"]["ticker_exchange_index"]
    op = opener_for(ROUTES, {url: HTTPError(url, 403, "forbidden", {}, io.BytesIO())})
    client = sec.SecClient(CONFIG, opener=op, sleep=lambda s: None)
    with pytest.raises(sec.SourceError) as e:
        sec.refresh(CONFIG, db, symbols=["NVDA"], client=client, today=TODAY)
    assert e.value.error_class == "HTTP_403" and op.calls.count(url) == 1


def test_transient_503_is_retried_then_succeeds(tmp_path):
    db = tmp_path / "f.sqlite3"
    url = CONFIG["endpoints"]["ticker_exchange_index"]
    state = {"n": 0}
    base = opener_for(ROUTES)

    def op(req, timeout):
        if req.full_url == url and state["n"] == 0:
            state["n"] += 1
            raise HTTPError(url, 503, "busy", {}, io.BytesIO())
        return base(req, timeout)
    client = sec.SecClient(CONFIG, opener=op, sleep=lambda s: None)
    assert sec.refresh(CONFIG, db, symbols=["NVDA"], client=client, today=TODAY)["status"] == "SUCCESS"


def test_user_agent_contact_required(monkeypatch):
    monkeypatch.setenv("SEC_USER_AGENT", "no-contact")
    with pytest.raises(sec.SourceError, match="SEC_USER_AGENT_CONTACT_REQUIRED"):
        sec.SecClient(CONFIG)


def test_portal_read_without_database(tmp_path):
    assert sec.read_for_portal(tmp_path / "missing.sqlite3", {"NVDA"})["state"] == "NO_DATA"


def test_config_change_retires_rows_without_deleting(tmp_path):
    db = tmp_path / "f.sqlite3"
    run(db)
    narrower = json.loads(json.dumps(CONFIG))
    narrower["forms"]["fund"] = ["8-K"]  # drop N-CSRS for the trust
    client = sec.SecClient(narrower, opener=opener_for(ROUTES), sleep=lambda s: None)
    r = sec.refresh(narrower, db, symbols=["NVDA", "TQQQ"], client=client, today=TODAY)
    assert r["retired"] == 1
    assert rows(db, "select count(*), sum(in_scope) from filings") == [(3, 2)]  # kept, hidden
    shown = {f["accession_number"] for f in sec.read_for_portal(db, {"NVDA", "TQQQ"})["filings"]}
    assert "0001174610-26-000500" not in shown and len(shown) == 2
    again = sec.refresh(CONFIG, db, symbols=["NVDA", "TQQQ"], client=sec.SecClient(
        CONFIG, opener=opener_for(ROUTES), sleep=lambda s: None), today=TODAY)
    assert again["updated"] == 1 and rows(db, "select sum(in_scope) from filings") == [(3,)]  # restored


def test_old_schema_is_migrated(tmp_path):
    db = tmp_path / "old.sqlite3"
    old_schema = sec.SCHEMA.replace("  in_scope INTEGER NOT NULL DEFAULT 1,\n", "").replace(
        " retired INTEGER NOT NULL DEFAULT 0,", "")
    with sqlite3.connect(db) as c:
        c.executescript(old_schema)
    sec.connect(db).close()
    cols = {r[1] for r in rows(db, "pragma table_info(filings)")}
    assert "in_scope" in cols and "retired" in {r[1] for r in rows(db, "pragma table_info(batches)")}


def test_portal_view_reports_window_and_separates_supplementary(tmp_path):
    db = tmp_path / "f.sqlite3"
    run(db)
    view = sec.read_for_portal(db, {"NVDA", "TQQQ"})
    assert view["window"]["days"] == 30 and len(view["window"]["start_filing_date"]) == 10
    recent = [f for f in view["filings"] if f["in_window"]]
    supplementary = [f for f in view["filings"] if not f["in_window"]]
    assert all(f["filing_date"] >= view["window"]["start_filing_date"] for f in recent)
    assert [f["form"] for f in supplementary] == ["10-Q"]  # only the latest periodic report, outside the window
    trust = [f for f in view["filings"] if "TQQQ" in f["symbols"]]
    assert trust and all(f["association_scope"] == "ISSUER_TRUST_ALL_SERIES" for f in trust)


def test_blank_sec_fields_are_stored_as_null(tmp_path):
    db = tmp_path / "f.sqlite3"
    fund = json.loads(json.dumps(FUND)); fund["sic"] = ""; fund["sicDescription"] = ""
    routes = {**ROUTES, "CIK0001174610": fund}
    run(db, routes=routes, symbols=["TQQQ"])
    assert rows(db, "select sic, sic_description from instruments where symbol='TQQQ'") == [(None, None)]


def test_supplementary_periodic_reports_have_a_maximum_age(tmp_path):
    db = tmp_path / "f.sqlite3"
    old_fund = _sub("SPDR S&P 500 ETF TRUST", "other", [
        ("0000884394-04-000001", "N-CSR", "2004-12-03", "2004-09-30", "2004-12-03T10:00:00.000Z", "o.htm", "N-CSR", "", 5),
        ("0000884394-26-000002", "N-30D", "2026-05-29", "2026-03-31", "2026-05-29T17:25:34.000Z", "s.htm", "N-30D", "", 5)])
    routes = {**ROUTES, "company_tickers_exchange": {"fields": EXCHANGE["fields"],
              "data": EXCHANGE["data"] + [[884394, "SPDR S&P 500 ETF TRUST", "SPY", "NYSE"]]}, "CIK0000884394": old_fund}
    run(db, routes=routes, symbols=["SPY"])
    assert rows(db, "select form, filing_date, in_window from filings") == [("N-30D", "2026-05-29", 0)]
