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
