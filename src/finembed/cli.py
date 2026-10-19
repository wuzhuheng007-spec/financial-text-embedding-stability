"""Offline doctor and one-filing SEC ingestion entry points."""
from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import sys

from .analysis import analyze, AnalysisError
from .config import load_config, Company
from .doctor import inspect
from .extract import primary_text, extract_item1, comparison, ExtractionError
from .model import fetch_model, ModelError
from .m4 import execute as execute_m4, M4Error
from .run import Run, sha256, write_json
from .sec import SecClient, SecError, MissingContact, contact_from_environment, discover_cik, discover_filing, submission_url, _acceptance, raw_header


def _ingest_one(run: Run, client: SecClient, company: Company) -> dict:
    config = run.config
    cik, ticker_source = discover_cik(client, company.ticker)
    selected, amendments, submission_sources = discover_filing(client, cik, config.as_of)
    accession = selected["accessionNumber"]
    url = submission_url(cik, accession)
    raw, raw_source = client.get(url)
    header = raw_header(raw, accession)
    if header["filing_date_raw_header"] != selected["filingDate"]:
        raise SecError("Raw SEC header filing date differs from submissions metadata")
    if header["acceptance_datetime_eastern"][:10] > config.as_of.isoformat():
        raise SecError("Raw SEC header acceptance is after configured as_of")
    api_time = _acceptance(selected["acceptanceDateTime"])
    header_time = datetime.fromisoformat(header["acceptance_datetime_eastern"])
    filing = {
        "ticker": company.ticker,
        "configured_company_name": company.name,
        "cik": cik,
        "accession": accession,
        "form": selected["form"],
        "acceptance_datetime_sec_source": selected["acceptanceDateTime"],
        "acceptance_datetime_sec_local": api_time.isoformat(),
        "raw_sec_header": header,
        "api_minus_raw_header_seconds": int((api_time - header_time).total_seconds()),
        "time_selection_policy": "API time is preliminary; raw SEC header Eastern date is the final as_of gate",
        "filing_date": selected["filingDate"],
        "report_date": selected["reportDate"],
        "as_of": config.as_of.isoformat(),
        "archive_url": url,
        "raw_sha256": sha256(raw),
        "raw_bytes": len(raw),
        "raw_retrieval": raw_source,
        "ticker_mapping_source": ticker_source,
        "submissions_sources": submission_sources,
        "amendments_accepted_by_as_of": [{"accession": x["accessionNumber"],
                                          "acceptance_datetime_sec_source": x["acceptanceDateTime"],
                                          "acceptance_datetime_sec_local": _acceptance(x["acceptanceDateTime"]).isoformat(),
                                          "filing_date": x["filingDate"], "report_date": x["reportDate"]} for x in amendments],
        "revision_policy": "original 10-K selected; amendments recorded separately and not substituted",
    }
    filing_dir = run.path / "filings" / company.ticker
    filing_dir.mkdir(parents=True, exist_ok=True)
    raw_path = filing_dir / "original_submission.txt"
    raw_path.write_bytes(raw)
    run.manifest["inputs"].extend([
        {"source_url": x["url"], "sha256": x["sha256"], "retrieved_utc": x["retrieved_utc"]}
        for x in [ticker_source, *submission_sources, raw_source]
    ])
    run.save()
    run.output(raw_path)
    write_json(filing_dir / "filing_metadata.json", filing)
    run.output(filing_dir / "filing_metadata.json")
    run.log("raw_filing_saved", cik=cik, accession=accession, sha256=filing["raw_sha256"], bytes=len(raw))

    text, doc_meta = primary_text(raw)
    primary_path = filing_dir / "primary_text.txt"
    primary_path.write_text(text, encoding="utf-8", newline="\n")
    run.output(primary_path)
    item, offsets = extract_item1(text)
    item_path = filing_dir / "item1_business.txt"
    item_path.write_text(item, encoding="utf-8", newline="\n")
    run.output(item_path)
    item_meta = {**doc_meta, **offsets, "primary_text_sha256": sha256(primary_path.read_bytes()),
                 "item1_sha256": sha256(item_path.read_bytes()), "source_raw_sha256": filing["raw_sha256"]}
    write_json(filing_dir / "item1_metadata.json", item_meta)
    run.output(filing_dir / "item1_metadata.json")
    write_json(filing_dir / "boundary_comparison.json", comparison(text, item, offsets))
    run.output(filing_dir / "boundary_comparison.json")
    run.log("item1_saved", start=offsets["start_char"], end=offsets["end_char_exclusive"], sha256=item_meta["item1_sha256"])
    return {"ticker": company.ticker, "status": "INCLUDED", "cik": cik, "accession": accession,
            "filing_dir": str(filing_dir), "item1_path": str(item_path),
            "item1_sha256": item_meta["item1_sha256"], "raw_sha256": filing["raw_sha256"]}


def ingest(run: Run) -> str:
    config = run.config
    if config.first_run_symbol_limit is not None:
        raise ValueError("first_run_symbol_limit must be explicitly released")
    entries = []
    try:
        contact = contact_from_environment()
    except MissingContact as exc:
        entries = [{"ticker": c.ticker, "status": "BLOCKED", "reason": str(exc)} for c in config.companies]
        write_json(run.path / "corpus_index.json", {"schema_version": 1, "as_of": config.as_of.isoformat(),
                                                     "first_run_symbol_limit": None, "entries": entries})
        run.output(run.path / "corpus_index.json")
        raise
    client = SecClient(config, contact)
    for company in config.companies:
        try:
            entries.append(_ingest_one(run, client, company))
        except (SecError, ExtractionError, OSError, ValueError, KeyError) as exc:
            entries.append({"ticker": company.ticker, "status": "FAILED", "reason": f"{type(exc).__name__}: {exc}"})
            run.log("company_failed", ticker=company.ticker, reason=f"{type(exc).__name__}: {exc}")
    index = {"schema_version": 1, "as_of": config.as_of.isoformat(), "first_run_symbol_limit": None,
             "entries": entries, "included_count": sum(x["status"] == "INCLUDED" for x in entries),
             "failed_count": sum(x["status"] == "FAILED" for x in entries)}
    write_json(run.path / "corpus_index.json", index)
    run.output(run.path / "corpus_index.json")
    return "DONE" if index["failed_count"] == 0 and index["included_count"] == len(config.companies) else "PARTIAL"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="finembed")
    sub = parser.add_subparsers(dest="command", required=True)
    doctor = sub.add_parser("doctor", help="Write an offline environment report and run manifest")
    doctor.add_argument("--config", default="configs/smoke.yaml")
    doctor.add_argument("--offline", action="store_true", help="Never make a network check")
    command = sub.add_parser("ingest", help="Fetch one real SEC 10-K and extract Item 1")
    command.add_argument("--config", default="configs/smoke.yaml")
    model_command = sub.add_parser("model-fetch", help="Download and verify exact pinned model/tokenizer files")
    model_command.add_argument("--config", default="configs/smoke.yaml")
    analysis_command = sub.add_parser("analyze", help="Verify real M1 corpus and run repeat/chunk-length study")
    analysis_command.add_argument("--config", default="configs/smoke.yaml")
    analysis_command.add_argument("--corpus", required=True, help="Explicit real M1 corpus_index.json path")
    m4_command = sub.add_parser("m4", help="Run M4 TF-IDF and authorized Massive market baseline")
    m4_command.add_argument("--config", default="configs/smoke.yaml")
    m4_command.add_argument("--m4-config", default="configs/m4_demo.yaml")
    m4_command.add_argument("--corpus", required=True, help="Explicit real M1 corpus_index.json path")
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"CONFIG_ERROR: {exc}", file=sys.stderr)
        return 2
    run = Run(config, args.command)
    try:
        if args.command == "doctor":
            report = inspect(config, offline=args.offline)
            output = run.path / "doctor.json"
            write_json(output, report)
            run.output(output)
            run.log("doctor_completed", offline=args.offline, sec_contact_present=report["env_present"]["SEC_USER_AGENT"])
            run.finish("DONE")
            print(json.dumps({"status": "DONE", "run_path": str(run.path), "report": str(output)}, ensure_ascii=False))
            return 0
        if args.command == "model-fetch":
            identity = fetch_model(config)
            write_json(run.path / "model_receipt.json", identity)
            run.output(run.path / "model_receipt.json")
            for file in identity["files"].values():
                run.input(Path(file["path"]))
            run.finish("DONE")
            print(json.dumps({"status": "DONE", "run_path": str(run.path)}, ensure_ascii=False))
            return 0
        if args.command == "analyze":
            status, reason = analyze(run, Path(args.corpus).resolve())
            run.finish(status, reason or None)
            print(json.dumps({"status": status, "reason": reason, "run_path": str(run.path)}, ensure_ascii=False))
            return 0 if status == "DONE" else 2 if status == "BLOCKED" else 1
        if args.command == "m4":
            status, reason = execute_m4(run, Path(args.m4_config), Path(args.corpus).resolve())
            run.finish(status, reason or None)
            print(json.dumps({"status": status, "reason": reason, "run_path": str(run.path)}, ensure_ascii=False))
            return 0 if status == "DONE" else 2 if status == "BLOCKED" else 1
        status = ingest(run)
        run.finish(status)
        print(json.dumps({"status": status, "run_path": str(run.path)}, ensure_ascii=False))
        return 0 if status == "DONE" else 1
    except MissingContact as exc:
        run.finish("BLOCKED", str(exc))
        print(json.dumps({"status": "BLOCKED", "reason": str(exc), "run_path": str(run.path)}, ensure_ascii=False), file=sys.stderr)
        return 2
    except (SecError, ExtractionError, ModelError, AnalysisError, M4Error, OSError, ValueError, KeyError) as exc:
        run.finish("FAILED", f"{type(exc).__name__}: {exc}")
        print(json.dumps({"status": "FAILED", "reason": str(exc), "run_path": str(run.path)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

