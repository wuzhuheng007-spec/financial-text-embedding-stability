"""Build a retrospective NYSE core-session calendar from bound official rules."""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.metadata
import json
import sys
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo


class CalendarError(ValueError):
    pass


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def iso_date(value: str) -> date:
    try:
        if type(value) is not str:
            raise ValueError("Date is not a string")
        result = date.fromisoformat(value)
        if result.isoformat() != value:
            raise ValueError("Date is not canonical")
        return result
    except ValueError as exc:
        raise CalendarError(f"Invalid canonical date: {value!r}") from exc


def iso_utc(value: str) -> datetime:
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.tzinfo is None or result.utcoffset() != timedelta(0):
            raise ValueError("Not UTC")
        return result
    except (TypeError, ValueError, AttributeError) as exc:
        raise CalendarError(f"Invalid UTC timestamp: {value!r}") from exc


def bound_sources(sources: list[dict], repository: Path) -> dict[str, dict]:
    if not isinstance(sources, list) or not sources:
        raise CalendarError("Official source list is empty")
    by_id: dict[str, dict] = {}
    for source in sources:
        sid = source.get("id")
        if not isinstance(sid, str) or not sid or sid in by_id:
            raise CalendarError("Duplicate or missing source ID")
        url = source.get("url", "")
        if not isinstance(url, str) or not url.startswith("https://"):
            raise CalendarError(f"Source URL must be HTTPS: {sid}")
        kind = source.get("evidence_kind")
        if kind not in {"OFFICIAL_PDF_RAW", "OFFICIAL_HTML_RAW", "OFFICIAL_HTML_EXTRACTED_TEXT",
                        "SYNTHETIC_TEST_ONLY"}:
            raise CalendarError(f"Unknown source evidence kind: {sid}")
        if kind != "SYNTHETIC_TEST_ONLY" and urlparse(url).hostname not in {
                "ir.theice.com", "s2.q4cdn.com", "www.nyse.com", "www.ice.com"}:
            raise CalendarError(f"Source is outside NYSE/ICE official domains: {sid}")
        if source.get("announcement_date") is None:
            if not source.get("announcement_date_reason"):
                raise CalendarError(f"Undated source lacks a reason: {sid}")
        else:
            iso_date(source["announcement_date"])
        iso_utc(source["retrieved_at_utc"])
        relative = Path(source["local_evidence_relative_path"])
        if relative.is_absolute() or ".." in relative.parts:
            raise CalendarError(f"Unsafe source path: {sid}")
        path = (repository / relative).resolve()
        if not path.is_relative_to(repository.resolve()) or not path.is_file():
            raise CalendarError(f"Source evidence unavailable: {sid}")
        if sha256(path) != source["sha256"]:
            raise CalendarError(f"Official source evidence SHA-256 mismatch: {sid}")
        by_id[sid] = source
    return by_id


def build(rules: dict, repository: Path) -> tuple[list[dict], list[dict], dict]:
    if rules.get("schema") != "nyse_round9_official_explicit_dates_v1":
        raise CalendarError("Unexpected calendar rule schema")
    if rules.get("mic") != "XNYS" or rules.get("timezone") != "America/New_York":
        raise CalendarError("This builder is limited to XNYS America/New_York")
    if rules.get("market_scope") != "NYSE_EQUITIES_CORE":
        raise CalendarError("Unexpected market scope")
    sources = bound_sources(rules["sources"], repository)
    for sid in (rules["hours_source_id"], *rules["year_source_ids"].values()):
        if sid not in sources:
            raise CalendarError(f"Unbound calendar source: {sid}")
    start, end = iso_date(rules["start_date"]), iso_date(rules["end_date"])
    if end < start or start.year != 2019 or end.year != 2026:
        raise CalendarError("Calendar range must contain the declared 2019/2026 buffers")
    expected_years = {str(year) for year in range(2019, 2027)}
    if set(rules["year_source_ids"]) != expected_years:
        raise CalendarError("Every 2019-2026 year needs an official annual source")
    if rules.get("normal_open_local") != "09:30" or rules.get("normal_close_local") != "16:00":
        raise CalendarError("Unexpected NYSE core hours")
    if rules.get("early_close_local") != "13:00":
        raise CalendarError("Unexpected NYSE early-close hour")

    closures: dict[str, dict] = {}
    for row in rules["closures"]:
        day = iso_date(row["date"])
        if day < start or day > end or day.weekday() >= 5:
            raise CalendarError(f"Closure outside weekdays/range: {day}")
        if row.get("kind") not in {"ANNUAL_HOLIDAY", "TEMPORARY_CLOSURE"}:
            raise CalendarError(f"Unknown closure rule kind: {day}")
        if row["date"] in closures or row["source_id"] not in sources or not row.get("reason"):
            raise CalendarError(f"Duplicate, unbound or unexplained closure: {day}")
        closures[row["date"]] = row
    early: dict[str, dict] = {}
    for row in rules["early_closes"]:
        day = iso_date(row["date"])
        if day < start or day > end or day.weekday() >= 5:
            raise CalendarError(f"Early close outside weekdays/range: {day}")
        if row["date"] in closures or row["date"] in early or row["source_id"] not in sources:
            raise CalendarError(f"Duplicate, closed or unbound early close: {day}")
        if not row.get("reason"):
            raise CalendarError(f"Missing early-close reason: {day}")
        early[row["date"]] = row

    eastern = ZoneInfo("America/New_York")
    sessions: list[dict] = []
    closure_rows: list[dict] = []
    day = start
    while day <= end:
        label = day.isoformat()
        if day.weekday() < 5:
            year_source = rules["year_source_ids"][str(day.year)]
            if label in closures:
                item = closures[label]
                closure_rows.append({"mic": "XNYS", "date": label,
                                     "reason": item["reason"], "kind": item["kind"],
                                     "source_id": item["source_id"]})
            else:
                item = early.get(label)
                close_text = rules["early_close_local"] if item else rules["normal_close_local"]
                open_et = datetime.combine(day, time.fromisoformat(rules["normal_open_local"]), eastern)
                close_et = datetime.combine(day, time.fromisoformat(close_text), eastern)
                sessions.append({"mic": "XNYS", "market_scope": rules["market_scope"],
                                 "session_date": label, "timezone": rules["timezone"],
                                 "open_et": open_et.isoformat(), "close_et": close_et.isoformat(),
                                 "open_utc": open_et.astimezone(timezone.utc).isoformat(),
                                 "close_utc": close_et.astimezone(timezone.utc).isoformat(),
                                 "session_type": "EARLY_CLOSE" if item else "NORMAL",
                                 "hours_source_id": rules["hours_source_id"],
                                 "annual_source_id": year_source,
                                 "exception_source_id": item["source_id"] if item else ""})
        day += timedelta(days=1)
    if not sessions or not closure_rows:
        raise CalendarError("Empty session or closure output")
    session_dates = [row["session_date"] for row in sessions]
    if len(session_dates) != len(set(session_dates)) or session_dates != sorted(session_dates):
        raise CalendarError("Session dates are not strictly increasing")
    first_core = next(i for i, label in enumerate(session_dates) if label > "2020-01-01")
    last_core = next(i for i, label in enumerate(session_dates) if label > "2025-12-31")
    if first_core < 20 or len(sessions) - last_core - 1 < 21:
        raise CalendarError("Boundary buffer cannot support 21 prior and future sessions")
    summary = {"schema": "nyse_round9_calendar_summary_v1", "mic": "XNYS",
               "market_scope": rules["market_scope"], "calendar_interpretation":
                   "RETROSPECTIVE_ACTUAL_SESSIONS_NOT_PROVEN_AS_OF_ANNOUNCED_VERSION",
               "start_date": start.isoformat(), "end_date": end.isoformat(),
               "first_session": session_dates[0], "last_session": session_dates[-1],
               "sessions": len(sessions), "closures": len(closure_rows),
               "early_closes": len(early),
               "sessions_by_year": {str(year): sum(label[:4] == str(year) for label in session_dates)
                                    for year in range(2019, 2027)},
               "first_2020_decision_session": session_dates[first_core],
               "prior_sessions_available_at_first_2020_decision": first_core + 1,
               "first_2026_decision_session_after_2025": session_dates[last_core],
               "future_sessions_available_after_last_2025_filing": len(sessions) - last_core - 1,
               "nasdaq_calendar_or_listing_applicability_verified": False,
               "finance_observations_admitted": None}
    return sessions, closure_rows, summary


def write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def verify_calendar_dir(rules_path: Path, output: Path, repository: Path) -> tuple[list[dict], dict]:
    """Rebuild from the source-bound rules and compare every saved calendar row."""
    rules = json.loads(rules_path.read_text(encoding="utf-8"))
    expected_sessions, expected_closures, summary = build(rules, repository)
    manifest_path = output / "manifest.json"
    sessions_path = output / "sessions.csv"
    closures_path = output / "closures.csv"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (manifest.get("rules_sha256") != sha256(rules_path) or
            manifest.get("builder_sha256") != sha256(Path(__file__)) or
            manifest.get("sessions_csv_sha256") != sha256(sessions_path) or
            manifest.get("closures_csv_sha256") != sha256(closures_path)):
        raise CalendarError("Calendar identity or saved file SHA-256 mismatch")
    if manifest.get("tzdata_version") != importlib.metadata.version("tzdata"):
        raise CalendarError("Calendar time-zone dependency version changed")
    if manifest.get("python_version") != sys.version.split()[0]:
        raise CalendarError("Calendar Python version changed")
    expected_source_evidence = [{key: source.get(key) for key in
                                 ("id", "url", "announcement_date", "announcement_date_reason",
                                  "retrieved_at_utc", "sha256", "evidence_kind")}
                                for source in rules["sources"]]
    if manifest.get("source_evidence") != expected_source_evidence:
        raise CalendarError("Calendar source manifest changed")
    if manifest.get("return_labels_computed") is not False:
        raise CalendarError("Unexpected financial result field in calendar manifest")
    for key, value in summary.items():
        if manifest.get(key) != value:
            raise CalendarError(f"Calendar summary changed: {key}")
    with sessions_path.open("r", encoding="utf-8", newline="") as handle:
        saved_sessions = list(csv.DictReader(handle))
    with closures_path.open("r", encoding="utf-8", newline="") as handle:
        saved_closures = list(csv.DictReader(handle))
    if saved_sessions != expected_sessions or saved_closures != expected_closures:
        raise CalendarError("Saved calendar rows differ from bound official rules")
    return saved_sessions, manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rules", required=True, type=Path)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--output-dir", type=Path)
    action.add_argument("--verify-dir", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    if args.verify_dir:
        rows, manifest = verify_calendar_dir(args.rules, args.verify_dir.resolve(), root)
        print(f"PASS_SOURCE_BOUND_CALENDAR_REBUILT sessions={len(rows)} manifest={sha256(args.verify_dir / 'manifest.json')}")
        return
    output = args.output_dir.resolve()
    if output.exists():
        raise CalendarError("Refusing to overwrite an existing calendar directory")
    rules = json.loads(args.rules.read_text(encoding="utf-8"))
    sessions, closures, summary = build(rules, root)
    output.mkdir(parents=True)
    write_csv(output / "sessions.csv", sessions)
    write_csv(output / "closures.csv", closures)
    manifest = {**summary, "rules_sha256": sha256(args.rules),
                "builder_sha256": sha256(Path(__file__)),
                "source_evidence": [{key: source.get(key) for key in
                                     ("id", "url", "announcement_date", "announcement_date_reason",
                                      "retrieved_at_utc", "sha256", "evidence_kind")}
                                    for source in rules["sources"]],
                "source_ids": sorted(source["id"] for source in rules["sources"]),
                "python_version": sys.version.split()[0],
                "tzdata_version": importlib.metadata.version("tzdata"),
                "sessions_csv_sha256": sha256(output / "sessions.csv"),
                "closures_csv_sha256": sha256(output / "closures.csv"),
                "generated_at_utc": datetime.now(timezone.utc).isoformat(),
                "return_labels_computed": False}
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                                           encoding="utf-8")
    print(f"NYSE_RETROSPECTIVE_CALENDAR_BUILT sessions={len(sessions)} closures={len(closures)} early={summary['early_closes']}")


if __name__ == "__main__":
    main()
