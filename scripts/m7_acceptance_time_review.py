"""Compare two SAVED SEC submissions snapshots with a saved original raw header.

Read-only input analysis. A changed acceptanceDateTime field is evidence of a
saved-response version difference, not proof of why it changed or when the
filing first became public.
"""
from __future__ import annotations

import argparse
import ast
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re

from finembed.sec import _acceptance, raw_filer_cik, raw_header


ACCESSION = "0000320193-25-000008"
CIK = "0000320193"
URL = f"https://data.sec.gov/submissions/CIK{CIK}.json"
FIELDS = ("accessionNumber", "form", "filingDate", "reportDate",
          "acceptanceDateTime", "primaryDocument")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require(ok: bool, message: str) -> None:
    if not ok:
        raise ValueError(message)


def source_function_sha(path: Path, name: str = "_acceptance") -> str:
    text = path.read_text(encoding="utf-8")
    matches = [node for node in ast.parse(text).body
               if isinstance(node, ast.FunctionDef) and node.name == name]
    require(len(matches) == 1, f"Exactly one {name} required: {path}")
    return hashlib.sha256(ast.get_source_segment(text, matches[0]).encode()).hexdigest()


def validated_snapshot(body_path: Path, meta_path: Path) -> dict:
    body = body_path.read_bytes()
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    require(meta.get("url") == URL and meta.get("bytes") == len(body) and
            meta.get("sha256") == hashlib.sha256(body).hexdigest() and
            meta.get("http_status") == 200, f"Saved SEC response integrity failed: {body_path}")
    data = json.loads(body)
    require(str(data.get("cik", "")).zfill(10) == CIK, "Submissions CIK mismatch")
    columns = data.get("filings", {}).get("recent", {})
    accessions = columns.get("accessionNumber")
    require(isinstance(accessions, list), "Missing submissions accession array")
    matches = [i for i, value in enumerate(accessions) if value == ACCESSION]
    require(len(matches) == 1, "Selected accession missing or duplicated in saved response")
    index = matches[0]
    row = {}
    lexemes = {}
    for field in FIELDS:
        values = columns.get(field)
        require(isinstance(values, list) and len(values) == len(accessions),
                f"Malformed submissions column: {field}")
        value = values[index]
        require(isinstance(value, str), f"Selected field is not a literal string: {field}")
        row[field] = value
        lexeme = json.dumps(value, ensure_ascii=False).encode("utf-8")
        lexemes[field] = {"json_literal": lexeme.decode("utf-8"),
                          "present_in_saved_response_bytes": lexeme in body}
        require(lexemes[field]["present_in_saved_response_bytes"],
                f"Selected string literal missing from bytes: {field}")
    return {"body_path": str(body_path), "body_sha256": sha(body_path),
            "body_bytes": len(body), "meta_path": str(meta_path),
            "meta_sha256": sha(meta_path), "retrieved_utc": meta["retrieved_utc"],
            "url": meta["url"], "http_status": meta["http_status"],
            "row_index_in_recent": index, "selected_row": row,
            "raw_json_string_lexemes": lexemes}


def timezone_marker(value: str, field: str) -> str:
    if field not in {"filingDate", "reportDate", "acceptanceDateTime"}:
        return "NOT_A_TIME_FIELD"
    if value.endswith("Z"):
        return "Z_UTC"
    if re.search(r"[+-]\d\d:\d\d$", value):
        return "EXPLICIT_NUMERIC_OFFSET"
    return "NONE"


def compare(old: dict, new: dict, raw: bytes, current_sec: Path,
            original_sec: Path) -> tuple[dict, list[dict]]:
    header = raw_header(raw, ACCESSION, "10-Q")
    require(raw_filer_cik(raw) == CIK, "Raw filer CIK mismatch")
    raw_accept = re.search(rb"<ACCEPTANCE-DATETIME>(\d{14})", raw[:raw.index(b"</SEC-HEADER>")])
    require(raw_accept is not None and raw_accept.group(1).decode("ascii") ==
            header["acceptance_datetime_raw_header"], "Raw header time literal mismatch")
    require(header["filing_date_raw_header"] == old["selected_row"]["filingDate"] ==
            new["selected_row"]["filingDate"], "Raw/API filing date mismatch")
    require(old["selected_row"]["form"] == new["selected_row"]["form"] == "10-Q",
            "Original 10-Q form identity mismatch")

    rows = []
    for field in FIELDS:
        a = old["selected_row"][field]
        b = new["selected_row"][field]
        rows.append({"field": field, "old_saved_string": a, "new_saved_string": b,
                     "old_timezone_marker": timezone_marker(a, field),
                     "new_timezone_marker": timezone_marker(b, field),
                     "literal_string_changed": a != b,
                     "old_raw_json_lexeme": old["raw_json_string_lexemes"][field]["json_literal"],
                     "new_raw_json_lexeme": new["raw_json_string_lexemes"][field]["json_literal"]})
    require([r["field"] for r in rows if r["literal_string_changed"]] ==
            ["acceptanceDateTime"], "Unexpected selected-row field difference")

    a = old["selected_row"]["acceptanceDateTime"]
    b = new["selected_row"]["acceptanceDateTime"]
    old_parsed = datetime.fromisoformat(a)
    new_parsed = datetime.fromisoformat(b)
    require(old_parsed.tzinfo is not None and new_parsed.tzinfo is not None,
            "API timezone marker missing")
    old_eastern = _acceptance(a)
    new_eastern = _acceptance(b)
    header_eastern = datetime.fromisoformat(header["acceptance_datetime_eastern"])
    require(new_eastern == header_eastern and old_eastern != header_eastern,
            "Expected raw/new/old temporal relationship differs")
    delta_seconds = int((old_parsed - new_parsed).total_seconds())
    require(delta_seconds == 5 * 3600, "Expected five-hour saved-string difference changed")
    function_current = source_function_sha(current_sec)
    function_original = source_function_sha(original_sec)
    # Equality describes the two PRESENT code copies, not the historical
    # source revision used at either network retrieval time.
    result = {
        "kind": "M7_SAVED_SEC_ACCEPTANCE_TIME_REVIEW",
        "status": "UNRESOLVED_CAUSE_AND_FIRST_PUBLIC_TIME",
        "accession": ACCESSION, "cik": CIK, "source_url": URL,
        "old_snapshot": old, "new_snapshot": new,
        "saved_original_submission": {"raw_header_path": None,
            "sha256": hashlib.sha256(raw).hexdigest(),
            "acceptance_datetime_raw_header_literal": header["acceptance_datetime_raw_header"],
            "raw_header_has_timezone_marker": False,
            "current_raw_header_interpretation":
                "Treat 14-digit wall-clock value as America/New_York",
            "current_raw_header_eastern": header["acceptance_datetime_eastern"],
            "current_raw_header_utc": header_eastern.astimezone(timezone.utc).isoformat(),
            "filing_date_raw_header": header["filing_date_raw_header"]},
        "current_parser": {"function": "finembed.sec._acceptance",
            "rule": "datetime.fromisoformat; if naive attach America/New_York; if aware convert to America/New_York",
            "publish_sec_path": str(current_sec),
            "publish_sec_sha256": sha(current_sec),
            "original_quant_sec_path": str(original_sec),
            "original_quant_sec_sha256": sha(original_sec),
            "present_function_body_sha256_in_both": function_current,
            "present_function_bodies_equal": function_current == function_original,
            "historical_parser_revision_at_each_download": "NOT_PROVED_BY_PRESENT_FILES"},
        "converted_values": {"old_api_eastern": old_eastern.isoformat(),
            "old_api_utc": old_parsed.astimezone(timezone.utc).isoformat(),
            "new_api_eastern": new_eastern.isoformat(),
            "new_api_utc": new_parsed.astimezone(timezone.utc).isoformat(),
            "raw_header_eastern": header_eastern.isoformat(),
            "raw_header_utc": header_eastern.astimezone(timezone.utc).isoformat(),
            "old_minus_new_seconds": delta_seconds,
            "new_api_equals_raw_header_instant": new_eastern == header_eastern,
            "old_api_equals_raw_header_instant": old_eastern == header_eastern},
        "field_rows": rows,
        "difference_classification": {
            "saved_api_string_changed": True,
            "current_parser_produces_difference_from_the_two_literal_strings": True,
            "difference_created_only_by_current_timezone_conversion": False,
            "cause_of_saved_response_change": "UNRESOLVED",
            "website_first_public_time": "UNVERIFIED",
            "historical_tradable_decision_time": "UNVERIFIED",
            "filing_amendment_inferred_from_this_field": False},
        "limit": "Saved SEC source URL/cache metadata and bytes show a response-version difference; they do not establish the server-side cause, exact dissemination instant or point-in-time tradability."}
    return result, rows


def write_outputs(output: Path, evidence: dict, rows: list[dict]) -> None:
    output.mkdir(parents=True, exist_ok=False)
    (output / "acceptance_time_evidence.json").write_text(
        json.dumps(evidence, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    with (output / "acceptance_time_fields.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    converted = evidence["converted_values"]
    table = ["# AAPL 10-Q saved acceptance-time evidence",
             "", f"Accession `{ACCESSION}`. **Status: UNRESOLVED** for the cause of the saved-response change and exact first-public time.",
             "", "| Field | 2026-10-19 saved API string | 2026-10-24 saved API string | Different? |",
             "|---|---|---|---|"]
    table += [f'| `{r["field"]}` | `{r["old_saved_string"]}` | `{r["new_saved_string"]}` | {"yes" if r["literal_string_changed"] else "no"} |' for r in rows]
    table += ["", "Both API acceptance strings explicitly end in `Z` (UTC). The raw filing header contains `20250131060127` **without** a timezone marker. Current `raw_header` interprets that wall clock as America/New_York (`2025-01-31T06:01:27-05:00`).",
              "", f'Current `_acceptance` parses the old API string as `{converted["old_api_eastern"]}` and the newer string as `{converted["new_api_eastern"]}`. The saved API strings differ by **{converted["old_minus_new_seconds"]} seconds (5 hours)**. The newer API value and raw header denote the same instant, `{converted["raw_header_utc"]}`; the older API value does not.',
              "", "The two current project copies of `_acceptance` have the same function-body SHA-256. Directly reading the saved JSON bytes shows the string itself changed. This is not merely a current timezone-display conversion, but the present files alone cannot prove the exact parser revision running when either response was first cached or why the response changed.",
              "", "## Source receipts",
              "", f'- Older API response: `{evidence["old_snapshot"]["body_path"]}`, SHA-256 `{evidence["old_snapshot"]["body_sha256"]}`, retrieved `{evidence["old_snapshot"]["retrieved_utc"]}`.',
              f'- Newer API response: `{evidence["new_snapshot"]["body_path"]}`, SHA-256 `{evidence["new_snapshot"]["body_sha256"]}`, retrieved `{evidence["new_snapshot"]["retrieved_utc"]}`.',
              f'- Original 10-Q raw submission SHA-256 `{evidence["saved_original_submission"]["sha256"]}`.',
              "", "The receipt time, API acceptance field and raw-header acceptance field do **not** alone establish the first public website time or a tradable decision time. Preserve both response versions and the unresolved label."]
    (output / "acceptance_time_review.md").write_text("\n".join(table) + "\n", encoding="utf-8")
    paths = sorted(p for p in output.iterdir() if p.is_file())
    manifest = {"kind": "M7_ACCEPTANCE_TIME_INPUT_OUTPUT_HASHES",
                "input_files": [
                    {"path": evidence[x]["body_path"], "sha256": evidence[x]["body_sha256"]}
                    for x in ("old_snapshot", "new_snapshot")],
                "raw_submission_sha256": evidence["saved_original_submission"]["sha256"],
                "outputs": [{"path": p.name, "bytes": p.stat().st_size, "sha256": sha(p)} for p in paths],
                "self_manifest_excluded": True}
    (output / "input_output_hashes.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--old-body", type=Path, required=True)
    parser.add_argument("--old-meta", type=Path, required=True)
    parser.add_argument("--new-body", type=Path, required=True)
    parser.add_argument("--new-meta", type=Path, required=True)
    parser.add_argument("--raw-submission", type=Path, required=True)
    parser.add_argument("--current-sec", type=Path, required=True)
    parser.add_argument("--original-sec", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    old = validated_snapshot(args.old_body, args.old_meta)
    new = validated_snapshot(args.new_body, args.new_meta)
    evidence, rows = compare(old, new, args.raw_submission.read_bytes(),
                             args.current_sec, args.original_sec)
    evidence["saved_original_submission"]["raw_header_path"] = str(args.raw_submission)
    write_outputs(args.output, evidence, rows)
    print(json.dumps({"status": evidence["status"], "output": str(args.output)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
