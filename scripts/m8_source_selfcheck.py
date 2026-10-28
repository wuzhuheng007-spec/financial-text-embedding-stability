"""Read-only second-path hash/header checks for the four saved original 10-Q inputs."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from zoneinfo import ZoneInfo


FIXED = (("MSFT", "0000789019", "0000950170-25-010491"),
         ("AMZN", "0001018724", "0001018724-25-000036"),
         ("GOOGL", "0001652044", "0001652044-25-000043"),
         ("META", "0001326801", "0001326801-25-000054"))


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def field(header: bytes, pattern: bytes) -> str:
    match = re.search(pattern, header, re.M)
    if not match:
        raise ValueError(f"Raw header field missing: {pattern!r}")
    return match.group(1).decode("ascii").strip()


def check_firm(run: Path, ticker: str, cik: str, accession: str) -> dict:
    base = run / "ingest" / ticker
    identity = json.loads((base / "source_identity.json").read_text(encoding="utf-8"))
    receipts = json.loads((base / "source_receipts.json").read_text(encoding="utf-8"))
    raw = (base / "original_submission.txt").read_bytes()
    primary = (base / "primary_document_source.html").read_bytes()
    index_raw = (base / "filing_index_response.json").read_bytes()
    current_raw = (base / "submissions_current_response.json").read_bytes()
    header_end = raw.find(b"</SEC-HEADER>")
    if header_end < 0:
        raise ValueError("Raw SEC header is not closed")
    header = raw[:header_end]
    raw_fields = {
        "accession": field(header, rb"ACCESSION NUMBER:\s*([0-9-]+)"),
        "form": field(header, rb"CONFORMED SUBMISSION TYPE:\s*([^\r\n]+)"),
        "report_period_raw": field(header, rb"CONFORMED PERIOD OF REPORT:\s*(\d{8})"),
        "filed_raw": field(header, rb"FILED AS OF DATE:\s*(\d{8})"),
        "accepted_raw": field(header, rb"<ACCEPTANCE-DATETIME>(\d{14})"),
        "filer_cik": field(header, rb"^[ \t]+CENTRAL INDEX KEY:[ \t]*(\d{1,10})[ \t]*\r?$"),
    }
    assert identity["ticker"] == ticker and identity["cik"] == cik
    assert identity["accession"] == accession and raw_fields["accession"] == accession
    assert raw_fields["form"] == "10-Q" and raw_fields["filer_cik"].zfill(10) == cik
    assert raw_fields["report_period_raw"] == identity["report_period_end"].replace("-", "")
    assert raw_fields["filed_raw"] == identity["filing_date"].replace("-", "")
    assert raw_fields["accepted_raw"] == identity["raw_header"]["acceptance_datetime_raw_header"]
    assert digest(raw) == identity["original_submission_sha256"]
    assert digest(primary) == identity["primary_document_sha256"]
    start, end = identity["primary_raw_byte_span"]
    assert raw[start:end] == primary
    index = json.loads(index_raw)
    items = index["directory"]["item"]
    matches = [item for item in items if item.get("name") == identity["primary_document"]]
    assert len(matches) == 1
    assert primary.startswith(b"<XBRL>\n") and primary.endswith(b"</XBRL>")
    unwrapped_bytes = len(primary) - len(b"<XBRL>\n") - len(b"</XBRL>")
    index_primary_size = int(matches[0]["size"])
    assert index_primary_size == unwrapped_bytes
    current = json.loads(current_raw)
    assert str(current["cik"]).zfill(10) == cik
    columns = current["filings"]["recent"]
    matches = [i for i, value in enumerate(columns["accessionNumber"]) if value == accession]
    assert len(matches) == 1
    i = matches[0]
    current_row = {name: columns[name][i] for name in
                   ("accessionNumber", "form", "filingDate", "reportDate", "acceptanceDateTime", "primaryDocument")}
    assert current_row == {name: identity["current_submissions_row"][name] for name in current_row}
    assert current_row["form"] == "10-Q" and current_row["filingDate"] == identity["filing_date"]
    assert current_row["reportDate"] == identity["report_period_end"]
    assert current_row["primaryDocument"] == identity["primary_document"]
    accepted_eastern = datetime.strptime(raw_fields["accepted_raw"], "%Y%m%d%H%M%S").replace(tzinfo=ZoneInfo("America/New_York"))
    accepted_api = datetime.fromisoformat(current_row["acceptanceDateTime"].replace("Z", "+00:00"))
    same_instant = accepted_eastern.astimezone(timezone.utc) == accepted_api.astimezone(timezone.utc)
    assert same_instant
    assert digest(index_raw) == identity["filing_index_sha256"]
    assert receipts["raw_submission"]["saved"]["sha256"] == digest(raw)
    assert receipts["filing_index"]["saved"]["sha256"] == digest(index_raw)
    assert receipts["submissions_current"]["main"]["saved"]["sha256"] == digest(current_raw)
    return {
        "ticker": ticker, "cik": cik, "accession": accession,
        "raw_sha256": digest(raw), "raw_bytes": len(raw),
        "primary_sha256": digest(primary), "primary_raw_byte_span": [start, end],
        "filing_index_sha256": digest(index_raw), "index_primary_size": index_primary_size,
        "current_submissions_sha256": digest(current_raw),
        "raw_header_fields": raw_fields, "current_submissions_fields": current_row,
        "acceptance_strings_same_instant": same_instant,
        "prior_acceptance_string": identity["submissions_row"]["acceptanceDateTime"],
        "current_acceptance_string": current_row["acceptanceDateTime"],
        "same_report_period_10q_amendments_in_saved_snapshot": len(identity["same_report_period_10q_amendments_in_saved_submissions"]),
        "same_report_period_10q_amendments_in_current_snapshot": len(identity["same_report_period_10q_amendments_in_current_submissions"]),
        "amendment_scope": receipts["submissions_current"]["amendment_search_scope"],
        "first_public_or_tradable_time": None,
        "status": "PASS_SAVED_SOURCE_FIELDS",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    args = parser.parse_args()
    run = args.run.resolve(strict=True)
    rows = []
    for ticker, cik, accession in FIXED:
        try:
            rows.append(check_firm(run, ticker, cik, accession))
        except Exception as exc:
            rows.append({"ticker": ticker, "cik": cik, "accession": accession,
                         "status": "FAIL", "reason": f"{type(exc).__name__}: {exc}"})
    result = {"kind": "M8_FOUR_10Q_SAVED_SOURCE_SAME_AGENT_SELFCHECK",
              "rows": rows,
              "all_pass": all(row["status"] == "PASS_SAVED_SOURCE_FIELDS" for row in rows),
              "independent_audit": False}
    output = run / "source_self_check_v1.json"
    with output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(json.dumps({"all_pass": result["all_pass"],
                      "statuses": [(row["ticker"], row["status"], row.get("reason")) for row in rows]}))
    return 0 if result["all_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
