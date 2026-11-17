"""Bounded accession index/header/exhibit collection for frozen 8-K candidates.

An index plus complete submission are attempted once per accession. No ticker,
issuer, amount, or return can substitute for the accession's own evidence.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .source import CONFIG, LEDGER, PRIVATE, PUBLIC, SecReader, digest, now, write_json


def prior_requests() -> list[dict]:
    return [json.loads(line) for line in LEDGER.read_text(encoding="utf-8").splitlines()]


def read_once(reader: SecReader, prior: dict[str, dict], url: str, stem: str) -> tuple[dict, bytes | None]:
    existing = prior.get(url)
    if existing:
        path = Path(existing["raw_path"]) if existing.get("raw_path") else None
        if not path or not path.is_file() or digest(path) != existing["sha256"]:
            return {"url": url, "status": "PRIOR_FAILURE_OR_CACHE_MISMATCH",
                    "http_status": existing.get("http_status")}, None
        return {"url": url, "status": "CACHED_VERIFIED", "sha256": existing["sha256"],
                "http_status": existing.get("http_status"), "raw_path": str(path)}, path.read_bytes()
    stem = re.sub(r"[^A-Za-z0-9_-]", "_", stem)
    row, body = reader.fetch(url, stem, "archive")
    prior[url] = row
    return row, body


def parse_documents(raw: bytes) -> list[dict]:
    found = []
    for match in re.finditer(rb"(?is)<DOCUMENT>\s*(.*?)\s*</DOCUMENT>", raw):
        part = match.group(1)
        def field(name: bytes) -> str | None:
            m = re.search(rb"(?im)^<" + name + rb">\s*([^\r\n<]+)", part)
            return m.group(1).decode("utf-8", "replace").strip() if m else None
        content = re.search(rb"(?is)<TEXT>(.*?)</TEXT>", part)
        found.append({"type": field(b"TYPE"), "filename": field(b"FILENAME"),
                      "body": content.group(1) if content else None,
                      "submission_span": [match.start(), match.end()]})
    return found


def inspect_event(meta: dict, index_raw: bytes, submission_raw: bytes) -> tuple[dict, bytes | None]:
    row = {k: meta.get(k) for k in ("symbol", "cik", "accessionNumber", "acceptanceDateTime",
                                     "filingDate", "primaryDocument", "items")}
    row["status"] = "UNREVIEWED"
    try:
        index = json.loads(index_raw)
        items = index["directory"]["item"]
        names = {x["name"] for x in items if isinstance(x, dict) and isinstance(x.get("name"), str)}
    except (ValueError, KeyError, TypeError):
        row["status"] = "INDEX_UNREADABLE"
        return row, None
    header = re.search(rb"(?im)^<ACCEPTANCE-DATETIME>\s*(\d{14})", submission_raw[:60000])
    if not header:
        row["status"] = "FULL_HEADER_ACCEPTANCE_MISSING"
        return row, None
    raw_stamp = header.group(1).decode("ascii")
    naive = datetime.strptime(raw_stamp, "%Y%m%d%H%M%S")
    anchored = naive.replace(tzinfo=ZoneInfo("America/New_York")).astimezone(timezone.utc)
    api_stamp = datetime.fromisoformat(meta["acceptanceDateTime"].replace("Z", "+00:00"))
    row["header_acceptance_raw"] = raw_stamp
    row["header_acceptance_utc"] = anchored.isoformat()
    row["api_acceptance_utc"] = api_stamp.isoformat()
    row["time_conversion_rule"] = "SEC header local US/Eastern converted via America/New_York"
    if anchored != api_stamp:
        row["status"] = "ACCEPTANCE_TIME_CONFLICT"
        return row, None
    documents = parse_documents(submission_raw)
    main_matches = [d for d in documents if d["filename"] == meta["primaryDocument"]]
    if len(main_matches) != 1 or main_matches[0]["type"] not in ("8-K", "8-K/A"):
        row["status"] = "PRIMARY_DOC_OR_TYPE_CONFLICT"
        return row, None
    if main_matches[0]["filename"] not in names:
        row["status"] = "PRIMARY_NOT_IN_INDEX"
        return row, None
    exhibits = [d for d in documents if (d["type"] or "").upper().startswith("EX-99.")
                and d["body"] and d["filename"] in names]
    row["available_exhibit_filenames"] = [d["filename"] for d in exhibits]
    if not exhibits:
        row["status"] = "NO_INDEXED_EX99_EXHIBIT"
        return row, None
    # The source heading/context check is a gate, not a source of new values.
    earnings = []
    for doc in exhibits:
        preview = re.sub(rb"(?s)<[^>]+>", b" ", doc["body"][:12000])
        preview = re.sub(rb"\s+", b" ", preview).decode("utf-8", "replace")
        if re.search(r"(?i)earnings|financial results|quarterly results|revenue|net income", preview):
            earnings.append(doc)
    if len(earnings) != 1:
        row["status"] = "EARNINGS_EXHIBIT_UNRESOLVED" if earnings else "NO_EARNINGS_HEADING_IN_EXHIBIT"
        row["earnings_exhibit_candidates"] = len(earnings)
        return row, None
    exhibit = earnings[0]
    row["status"] = "INDEX_HEADER_AND_EXHIBIT_MATCHED_SOURCE_REVIEW_PENDING"
    row["exhibit_filename"] = exhibit["filename"]
    row["exhibit_type"] = exhibit["type"]
    row["exhibit_submission_span"] = exhibit["submission_span"]
    row["exhibit_sha256"] = hashlib.sha256(exhibit["body"]).hexdigest()
    row["exhibit_bytes"] = len(exhibit["body"])
    return row, exhibit["body"]


def collect(limit: int | None) -> dict:
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    frozen = json.loads((PRIVATE / "metadata_private.json").read_text(encoding="utf-8"))
    prior_rows = prior_requests()
    reader = SecReader(config)
    reader.requests = len(prior_rows)
    reader.bytes = sum(r.get("bytes", 0) for r in prior_rows)
    reader.stopped = any(r.get("http_status") in config["source_request_budget"]["stop_source_on_http"] for r in prior_rows)
    prior = {r["url"]: r for r in prior_rows}
    metas = sorted(frozen["selected_metadata_events"],
                   key=lambda x: (x["acceptanceDateTime"], x["symbol"], x["accessionNumber"]))
    if limit is not None:
        metas = metas[:limit]
    output = []
    for meta in metas:
        accession = meta["accessionNumber"]
        clean = accession.replace("-", "")
        base = f"https://www.sec.gov/Archives/edgar/data/{meta['cik']}/{clean}/"
        stem = f"{meta['symbol']}_{clean}"
        try:
            index_row, index_raw = read_once(reader, prior, base + "index.json", stem + "_index")
            if index_raw is None or reader.stopped:
                output.append({"symbol": meta["symbol"], "accessionNumber": accession,
                               "status": "INDEX_REQUEST_FAILED", "index_http_status": index_row.get("http_status")})
                if reader.stopped: break
                continue
            submission_row, submission_raw = read_once(reader, prior, base + accession + ".txt", stem + "_complete")
            if submission_raw is None or reader.stopped:
                output.append({"symbol": meta["symbol"], "accessionNumber": accession,
                               "status": "COMPLETE_SUBMISSION_REQUEST_FAILED",
                               "submission_http_status": submission_row.get("http_status")})
                if reader.stopped: break
                continue
            result, exhibit = inspect_event(meta, index_raw, submission_raw)
            result.update({"index_sha256": hashlib.sha256(index_raw).hexdigest(),
                           "full_submission_sha256": hashlib.sha256(submission_raw).hexdigest(),
                           "index_url": base + "index.json", "full_submission_url": base + accession + ".txt",
                           "index_retrieved_at_utc": index_row.get("completed_at_utc"),
                           "submission_retrieved_at_utc": submission_row.get("completed_at_utc")})
            if exhibit is not None:
                path = PRIVATE / "exhibits" / (stem + ".bin")
                if path.exists():
                    if digest(path) != result["exhibit_sha256"]:
                        raise ValueError("EXHIBIT_CACHE_HASH_MISMATCH")
                else:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(exhibit)
                result["exhibit_private_path"] = str(path)
            output.append(result)
        except RuntimeError as error:
            output.append({"symbol": meta["symbol"], "accessionNumber": accession,
                           "status": type(error).__name__ + ":" + str(error)})
            break
    name = "archive_probe_private.json" if limit is not None else "archive_manifest_private.json"
    write_json(PRIVATE / name, {"schema": "sentiment_v3_archive_collection_1",
                                     "config_sha256": digest(CONFIG), "metadata_sha256": digest(PRIVATE / "metadata_private.json"),
                                     "created_at_utc": now(), "rows": output})
    receipt = {"schema": "sentiment_v3_archive_redacted_1", "created_at_utc": now(),
               "scope": "PROBE" if limit is not None else "FULL_FROZEN_SELECTION",
               "config_sha256": digest(CONFIG), "metadata_sha256": digest(PRIVATE / "metadata_private.json"),
               "private_manifest_sha256": digest(PRIVATE / name),
               "requests_cumulative": reader.requests, "bytes_cumulative": reader.bytes,
               "attempted_events": len(output), "status_counts": dict(Counter(x["status"] for x in output)),
               "issuer_status_counts": {sym: dict(Counter(x["status"] for x in output if x["symbol"] == sym))
                                        for sym in config["issuer_order"]},
               "source_stopped": reader.stopped, "no_outcome_labels_read": True}
    write_json(PUBLIC / ("archive_probe_redacted.json" if limit is not None else "archive_collection_redacted.json"), receipt)
    return {k: receipt[k] for k in ("scope", "requests_cumulative", "attempted_events", "status_counts", "source_stopped")}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    print(json.dumps(collect(args.limit)))
