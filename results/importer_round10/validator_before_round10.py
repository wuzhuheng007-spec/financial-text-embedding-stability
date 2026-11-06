"""Validate a canonical historical-data import envelope without financial calculations."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from datetime import date, datetime, timezone
from pathlib import Path


REQUIRED = {
    "sources": ("source_id", "product", "product_version", "official_url",
                "acquired_at_utc", "local_file_sha256", "access_basis"),
    "security_master": ("stable_security_id", "firm_id", "security_type", "mic",
                        "listing_status", "valid_from", "valid_to", "source_id"),
    "cik_security_links": ("cik", "stable_security_id", "valid_from", "valid_to",
                           "is_primary", "primary_basis", "ambiguity_flag", "source_id"),
    "daily_bars": ("stable_security_id", "session_date", "close", "currency",
                   "price_definition", "adjustment_basis", "action_source_id",
                   "missing_reason", "exit_marker", "source_id", "source_record_version",
                   "retrieved_at_utc"),
    "actions_and_exits": ("stable_security_id", "event_date", "event_type",
                          "event_value", "event_value_unit", "terminal_price",
                          "missing_terminal_reason", "source_id", "source_record_version",
                          "retrieved_at_utc"),
    "text_evidence": ("cik", "accession", "section", "section_sha256",
                      "revision_status", "public_time_evidence_class", "public_time_utc",
                      "public_no_later_than_utc", "proxy_rule_id", "source_id",
                      "source_record_version", "retrieved_at_utc"),
}
NULLABLE = {
    "security_master": {"valid_to"},
    "cik_security_links": {"valid_to", "primary_basis"},
    "daily_bars": {"close", "missing_reason"},
    "actions_and_exits": {"event_value", "event_value_unit", "terminal_price",
                          "missing_terminal_reason"},
    "text_evidence": {"public_time_utc", "public_no_later_than_utc", "proxy_rule_id"},
}
PUBLIC_CLASSES = {"EXACT_FIRST_PUBLIC", "NO_LATER_THAN_PUBLIC", "APPROVED_PROXY", "UNKNOWN"}
EXIT_TYPES = {"DELISTING", "MERGER_EXIT", "LIQUIDATION_EXIT", "OTHER_EXIT"}


def _day(value: object) -> date | None:
    if type(value) is not str:
        return None
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.isoformat() == value else None


def _utc(value: object) -> bool:
    if type(value) is not str:
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() == timezone.utc.utcoffset(parsed)


def _interval(row: dict) -> tuple[date, date | None] | None:
    start = _day(row.get("valid_from"))
    end_value = row.get("valid_to")
    end = _day(end_value) if end_value is not None else None
    if start is None or (end_value is not None and end is None) or (end is not None and end <= start):
        return None
    return start, end


def _overlap(left: tuple[date, date | None], right: tuple[date, date | None]) -> bool:
    return (left[1] is None or right[0] < left[1]) and (right[1] is None or left[0] < right[1])


def validate_import_bundle(bundle: dict) -> dict:
    """Return a fail-closed structural ledger; never infer eligibility or a label."""
    issues: list[dict] = []

    def issue(table: str, row: int | None, code: str, detail: str) -> None:
        issues.append({"table": table, "row": row, "code": code, "detail": detail})

    if not isinstance(bundle, dict):
        return {"status": "REJECTED_STRUCTURE_ONLY", "issues": [{"table": "bundle", "row": None,
                 "code": "INVALID_BUNDLE", "detail": "Expected a JSON object"}],
                "admitted_financial_observations": None, "returns_or_scores_computed": False}
    tables: dict[str, list[dict]] = {}
    for name, fields in REQUIRED.items():
        rows = bundle.get(name)
        if not isinstance(rows, list):
            issue(name, None, "MISSING_TABLE", "Expected a list; no records were fabricated")
            rows = []
        tables[name] = rows
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                issue(name, index, "INVALID_RECORD", "Expected an object")
                continue
            missing = [field for field in fields if field not in row]
            if missing:
                issue(name, index, "MISSING_FIELDS", ",".join(missing))
            empty = [field for field in fields if field in row and field not in NULLABLE.get(name, set())
                     and (row[field] is None or (isinstance(row[field], str) and not row[field].strip()))]
            if empty:
                issue(name, index, "MISSING_REQUIRED_VALUE", ",".join(empty))

    source_ids: set[str] = set()
    for index, row in enumerate(tables["sources"]):
        if not isinstance(row, dict):
            continue
        key = row.get("source_id")
        if not isinstance(key, str) or not key.strip():
            issue("sources", index, "EMPTY_SOURCE_ID", "A nonempty source ID is required")
        elif key in source_ids:
            issue("sources", index, "DUPLICATE_SOURCE_ID", key)
        else:
            source_ids.add(key)
        if row.get("acquired_at_utc") is not None and not _utc(row.get("acquired_at_utc")):
            issue("sources", index, "INVALID_ACQUISITION_TIME", "Use a timezone-aware UTC instant")
        digest = row.get("local_file_sha256")
        if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            issue("sources", index, "INVALID_SOURCE_HASH", "A lowercase SHA-256 is required")

    for name, rows in tables.items():
        if name == "sources":
            continue
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                continue
            for field in ("source_id", "action_source_id"):
                if field in row:
                    value = row[field]
                    if not isinstance(value, str) or not value.strip():
                        issue(name, index, "INVALID_SOURCE_REFERENCE", field)
                    elif value not in source_ids:
                        issue(name, index, "UNKNOWN_SOURCE_ID", f"{field}={value}")
            if name in ("security_master", "cik_security_links", "daily_bars", "actions_and_exits"):
                security_id = row.get("stable_security_id")
                if not isinstance(security_id, str) or not security_id.strip():
                    issue(name, index, "INVALID_SECURITY_ID", "Expected a nonempty stable issue ID")
            if name in ("daily_bars", "actions_and_exits", "text_evidence"):
                if row.get("retrieved_at_utc") is not None and not _utc(row.get("retrieved_at_utc")):
                    issue(name, index, "INVALID_RETRIEVAL_TIME", "Use a timezone-aware UTC instant")

    intervals: dict[str, list[tuple[int, dict, tuple[date, date | None]]]] = {}
    for name in ("security_master", "cik_security_links"):
        intervals[name] = []
        for index, row in enumerate(tables[name]):
            if not isinstance(row, dict):
                continue
            parsed = _interval(row)
            if parsed is None:
                issue(name, index, "INVALID_EFFECTIVE_INTERVAL", "Use [valid_from, valid_to); null end means open")
            else:
                intervals[name].append((index, row, parsed))
    for pos, (left_index, left, left_interval) in enumerate(intervals["security_master"]):
        for right_index, right, right_interval in intervals["security_master"][pos + 1:]:
            if left.get("stable_security_id") == right.get("stable_security_id") and _overlap(left_interval, right_interval):
                issue("security_master", right_index, "OVERLAPPING_SECURITY_INTERVAL",
                      f"overlaps row {left_index}; do not choose a record silently")
    known_security_ids = {row["stable_security_id"] for row in tables["security_master"]
                          if isinstance(row, dict) and isinstance(row.get("stable_security_id"), str)
                          and row["stable_security_id"].strip()}
    for name in ("cik_security_links", "daily_bars", "actions_and_exits"):
        for index, row in enumerate(tables[name]):
            if isinstance(row, dict) and isinstance(row.get("stable_security_id"), str) and row["stable_security_id"] not in known_security_ids:
                issue(name, index, "UNKNOWN_SECURITY_ID", "Security missing from master")
    for pos, (left_index, left, left_interval) in enumerate(intervals["cik_security_links"]):
        if not isinstance(left.get("cik"), str) or re.fullmatch(r"[0-9]{10}", left["cik"]) is None:
            issue("cik_security_links", left_index, "INVALID_CIK", "Expected ten decimal digits")
        if type(left.get("is_primary")) is not bool:
            issue("cik_security_links", left_index, "INVALID_PRIMARY_FLAG", "Expected a boolean")
        if type(left.get("ambiguity_flag")) is not bool:
            issue("cik_security_links", left_index, "INVALID_AMBIGUITY_FLAG", "Expected a boolean")
        if left.get("is_primary") is True and not left.get("primary_basis"):
            issue("cik_security_links", left_index, "MISSING_PRIMARY_BASIS", "Primary status needs dated evidence")
        for right_index, right, right_interval in intervals["cik_security_links"][pos + 1:]:
            if left.get("cik") != right.get("cik") or not _overlap(left_interval, right_interval):
                continue
            if left.get("stable_security_id") == right.get("stable_security_id"):
                issue("cik_security_links", right_index, "OVERLAPPING_CIK_SECURITY_LINK",
                      f"overlaps row {left_index}")
            elif left.get("is_primary") is True and right.get("is_primary") is True:
                issue("cik_security_links", right_index, "MULTIPLE_PRIMARY_SECURITIES",
                      f"overlaps row {left_index}; both claim primary status")
            elif left.get("is_primary") is not True and right.get("is_primary") is not True:
                issue("cik_security_links", right_index, "AMBIGUOUS_CONCURRENT_SECURITIES",
                      f"overlaps row {left_index}; no unique primary")
    links_by_cik: dict[str, list[tuple[int, dict, tuple[date, date | None]]]] = {}
    for item in intervals["cik_security_links"]:
        cik = item[1].get("cik")
        if isinstance(cik, str):
            links_by_cik.setdefault(cik, []).append(item)
    for cik, links in links_by_cik.items():
        boundaries = sorted({point for _, _, interval in links
                             for point in interval if point is not None})
        for boundary in boundaries:
            active = [(index, row) for index, row, (start, end) in links
                      if start <= boundary and (end is None or boundary < end)]
            if active and not any(row.get("is_primary") is True and row.get("primary_basis")
                                  for _, row in active):
                issue("cik_security_links", active[0][0], "NO_PRIMARY_SECURITY",
                      f"{cik} has no documented primary from {boundary.isoformat()}")

    bar_keys: dict[tuple[object, object], tuple[int, dict]] = {}
    for index, row in enumerate(tables["daily_bars"]):
        if not isinstance(row, dict):
            continue
        if _day(row.get("session_date")) is None:
            issue("daily_bars", index, "INVALID_SESSION_DATE", "Expected canonical YYYY-MM-DD")
        key = row.get("stable_security_id"), row.get("session_date")
        if not all(isinstance(value, str) for value in key):
            continue
        if key in bar_keys:
            earlier, prior = bar_keys[key]
            code = "CONFLICTING_BAR_SOURCES" if row.get("source_id") != prior.get("source_id") else "DUPLICATE_BAR"
            issue("daily_bars", index, code, f"same security and session as row {earlier}; no preferred source selected")
        else:
            bar_keys[key] = index, row
        close = row.get("close")
        if close is None:
            if not row.get("missing_reason"):
                issue("daily_bars", index, "MISSING_CLOSE_REASON", "Null close needs a reason")
        elif type(close) not in (int, float) or not math.isfinite(close) or close <= 0:
            issue("daily_bars", index, "INVALID_CLOSE", "Close must be finite and positive when present")
        if type(row.get("exit_marker")) is not bool:
            issue("daily_bars", index, "INVALID_EXIT_MARKER", "Expected a boolean")

    exit_count = 0
    for index, row in enumerate(tables["actions_and_exits"]):
        if not isinstance(row, dict):
            continue
        if _day(row.get("event_date")) is None:
            issue("actions_and_exits", index, "INVALID_EVENT_DATE", "Expected canonical YYYY-MM-DD")
        if row.get("event_type") in EXIT_TYPES:
            exit_count += 1
            terminal = row.get("terminal_price")
            if terminal is None and not row.get("missing_terminal_reason"):
                issue("actions_and_exits", index, "MISSING_EXIT_TERMINAL_REASON",
                      "Exit remains in ledger; unknown terminal price needs a reason")
            elif terminal is not None and (type(terminal) not in (int, float) or
                                           not math.isfinite(terminal) or terminal < 0):
                issue("actions_and_exits", index, "INVALID_TERMINAL_PRICE", "Use finite nonnegative value or null with reason")
            elif terminal == 0 and not row.get("terminal_zero_evidence_id"):
                issue("actions_and_exits", index, "ZERO_TERMINAL_NEEDS_SOURCE",
                      "A real zero needs explicit evidence; no zero-fill is permitted")

    for index, row in enumerate(tables["text_evidence"]):
        if not isinstance(row, dict):
            continue
        if not isinstance(row.get("cik"), str) or re.fullmatch(r"[0-9]{10}", row["cik"]) is None:
            issue("text_evidence", index, "INVALID_CIK", "Expected ten decimal digits")
        evidence = row.get("public_time_evidence_class")
        if evidence not in PUBLIC_CLASSES:
            issue("text_evidence", index, "INVALID_PUBLIC_TIME_CLASS", "Choose an explicit evidence class")
        elif evidence == "EXACT_FIRST_PUBLIC" and not _utc(row.get("public_time_utc")):
            issue("text_evidence", index, "MISSING_EXACT_PUBLIC_TIME", "Exact claim requires UTC evidence")
        elif evidence == "NO_LATER_THAN_PUBLIC" and not _utc(row.get("public_no_later_than_utc")):
            issue("text_evidence", index, "MISSING_PUBLIC_UPPER_BOUND", "Upper bound needs UTC evidence")
        elif evidence == "APPROVED_PROXY" and not row.get("proxy_rule_id"):
            issue("text_evidence", index, "MISSING_PROXY_RULE", "Proxy needs a separately accepted rule ID")
        digest = row.get("section_sha256")
        if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            issue("text_evidence", index, "INVALID_SECTION_HASH", "A lowercase SHA-256 is required")

    required_sessions = bundle.get("required_security_sessions", {})
    if not isinstance(required_sessions, dict):
        issue("required_security_sessions", None, "INVALID_SESSION_REQUIREMENTS", "Expected security ID to date-list mapping")
    else:
        for security_id, dates in required_sessions.items():
            if not isinstance(dates, list):
                issue("required_security_sessions", None, "INVALID_SESSION_LIST", str(security_id))
                continue
            for day_text in dates:
                if _day(day_text) is None:
                    issue("required_security_sessions", None, "INVALID_REQUIRED_SESSION", str(day_text))
                elif (security_id, day_text) not in bar_keys:
                    issue("daily_bars", None, "MISSING_SESSION_BAR", f"{security_id} {day_text}; do not skip the date")
                elif bar_keys[(security_id, day_text)][1].get("close") is None:
                    issue("daily_bars", bar_keys[(security_id, day_text)][0], "MISSING_REQUIRED_CLOSE",
                          f"{security_id} {day_text}; a missing reason does not supply a price")

    return {"status": "REJECTED_STRUCTURE_ONLY" if issues else "PASS_STRUCTURE_ONLY",
            "issues": issues,
            "record_counts": {name: len(rows) for name, rows in tables.items()},
            "exit_records_seen": exit_count,
            "admitted_financial_observations": None,
            "returns_or_scores_computed": False,
            "provenance_or_license_verified": False}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Refusing to overwrite an existing report")
    input_bytes = args.input.read_bytes()
    result = validate_import_bundle(json.loads(input_bytes.decode("utf-8")))
    result["input_sha256"] = hashlib.sha256(input_bytes).hexdigest()
    result["validator_source_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(result["status"], "issues=" + str(len(result["issues"])))
    if result["issues"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
