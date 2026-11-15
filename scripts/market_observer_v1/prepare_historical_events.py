"""Convert bounded private source receipts into a non-content alignment input.

This does not approve issuer relations or historical first availability.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo


ET = ZoneInfo("America/New_York")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def convert(sec_path: Path, jin10_path: Path, out_path: Path,
            manifest_path: Path, *, start: date, end: date,
            macro_path: Path | None = None) -> dict:
    converted: list[dict] = []
    inputs = ((sec_path, "SEC_FILING_METADATA"),
              (jin10_path, "JIN10_SEARCH_CANDIDATE"))
    for path, kind in inputs:
        for row in _read_jsonl(path):
            raw_time = row["published_at_raw"]
            published = datetime.fromisoformat(raw_time.replace("Z", "+00:00"))
            if published.utcoffset() is None:
                raise ValueError("source publication timestamp lacks UTC offset")
            publication_date_et = published.astimezone(ET).date()
            if not start <= publication_date_et <= end:
                continue
            converted.append({
                "event_id": f"{kind}:{row['event_id']}",
                "source_type": kind,
                "source_hash": row["source_response_sha256"],
                "source_hash_scope": row["source_hash_scope"],
                "source_url": row.get("source_url"),
                "ticker_candidate": row.get("ticker_candidate"),
                "issuer_relation_status": row.get("issuer_match_status", row.get("fund_scope", "CANDIDATE")),
                "published_at": raw_time,
                "publication_semantics": row.get("published_at_semantics", "PROVIDER_TIMESTAMP"),
                "time_precision": row["publication_precision"],
                "source_timezone": row["source_timezone"],
                "retrieved_at": row["retrieved_at_utc"],
                "first_available_at": row.get("first_publicly_available_at"),
                "availability_evidence_hash": None,
            })
    if macro_path is not None:
        for row in _read_jsonl(macro_path):
            if row.get("access_status") != "LOCAL_HTTP_200" or not row.get("source_sha256"):
                continue
            release = row["release_not_before_at"]
            published = datetime.fromisoformat(release.replace("Z", "+00:00"))
            if published.utcoffset() is None:
                raise ValueError("macro release clock lacks UTC offset")
            if not start <= published.astimezone(ET).date() <= end:
                continue
            converted.append({
                "event_id": f"OFFICIAL_MACRO_RELEASE_CLOCK:{row['event_id']}",
                "source_type": "OFFICIAL_MACRO_RELEASE_CLOCK",
                "source_hash": row["source_sha256"],
                "source_hash_scope": "saved_official_original",
                "source_url": row["source_url"],
                "ticker_candidate": None,
                "issuer_relation_status": "MACRO_NOT_DIRECT_SECURITY_LINK",
                "published_at": release,
                "publication_semantics": "OFFICIAL_RELEASE_NOT_BEFORE_CLOCK_NOT_FIRST_VISIBLE",
                "time_precision": row["publication_precision"],
                "source_timezone": row["source_timezone"],
                "retrieved_at": row["retrieved_at_utc"],
                "first_available_at": row.get("first_publicly_available_at"),
                "availability_evidence_hash": None,
            })
    converted.sort(key=lambda item: (item["published_at"], item["event_id"]))
    if len({item["event_id"] for item in converted}) != len(converted):
        raise ValueError("duplicate converted event ID")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
                                for row in converted), encoding="utf-8")
    manifest_inputs = list(inputs)
    if macro_path is not None:
        manifest_inputs.append((macro_path, "OFFICIAL_MACRO_ORIGINALS"))
    manifest = {
        "schema": "historical_event_conversion_manifest_v1",
        "window_start": start.isoformat(), "window_end": end.isoformat(),
        "source_files": [
            {"source_type": kind, "sha256": _sha(path), "rows": len(_read_jsonl(path))}
            for path, kind in manifest_inputs
        ],
        "converted_rows": len(converted), "converted_sha256": _sha(out_path),
        "historical_first_availability": "UNKNOWN_FOR_ALL_UNLESS_INDEPENDENTLY_EVIDENCED",
        "issuer_relation": "CANDIDATE_NOT_HISTORICALLY_ADMITTED",
    }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sec", required=True, type=Path)
    parser.add_argument("--jin10", required=True, type=Path)
    parser.add_argument("--macro", type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--start", default="2025-04-01")
    parser.add_argument("--end", default="2026-07-31")
    args = parser.parse_args()
    result = convert(args.sec, args.jin10, args.out, args.manifest,
                     start=date.fromisoformat(args.start), end=date.fromisoformat(args.end),
                     macro_path=args.macro)
    print(json.dumps({"converted_rows": result["converted_rows"],
                      "source_rows": [row["rows"] for row in result["source_files"]]}))


if __name__ == "__main__":
    main()
