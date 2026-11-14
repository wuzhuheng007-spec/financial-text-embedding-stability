"""Create a shareable aggregate profile of a local bounded FNSPID sample.

The input JSONL and manifest stay in ignored artifacts/. The output contains
counts, hashes and field names, never article text or row-level identifiers.
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import re
import statistics
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

MISSING = {"", "nan", "null", "none", "0", "2", "3"}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def present(value: str) -> bool:
    return value is not None and value.strip().lower() not in MISSING


def duplicate_excess(values) -> dict:
    kept = [value for value in values if present(value)]
    return {"present": len(kept), "unique": len(set(kept)),
            "duplicate_excess": len(kept) - len(set(kept))}


def date_kind(value: str) -> str:
    value = value.strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return "date_only"
    if re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} UTC", value):
        return "seconds_with_literal_UTC"
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:Z|[+-]\d{2}:\d{2})", value):
        return "seconds_with_offset"
    return "other_or_missing"


def profile_sample(sample_path: Path, manifest_path: Path) -> dict:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    sample_hash = sha256_file(sample_path)
    if sample_hash != manifest["sample"]["sha256"]:
        raise ValueError("SAMPLE_HASH_MISMATCH")
    header = manifest["result"]["header"]
    if len(header) != len(set(header)):
        raise ValueError("DUPLICATE_HEADER")
    needed = {"Article", "Article_title", "Stock_symbol", "Date", "Url"}
    if not needed.issubset(header):
        raise ValueError("REQUIRED_COLUMN_MISSING")
    rows = []
    with sample_path.open("r", encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                rows.append(json.loads(line))
    if len(rows) != manifest["result"]["saved_nonempty_body_rows"]:
        raise ValueError("SAVED_ROW_COUNT_MISMATCH")
    scanned = manifest["result"]["complete_logical_rows_scanned"]
    ordinals = [row["source_row_ordinal"] for row in rows]
    if (ordinals != sorted(set(ordinals)) or
            any(not 1 <= ordinal <= scanned for ordinal in ordinals)):
        raise ValueError("INVALID_SOURCE_ORDER")
    if any(len(row["fields"]) != len(header) for row in rows):
        raise ValueError("FIELD_WIDTH_MISMATCH")
    data = {name: [row["fields"][i] for row in rows]
            for i, name in enumerate(header)}
    presence = {name: {"present": sum(present(value) for value in values),
                       "missing_or_sentinel": sum(not present(value) for value in values)}
                for name, values in data.items()}
    article = data["Article"]
    dates = data["Date"]
    parsed_dates = []
    for value in dates:
        try:
            parsed_dates.append(datetime.strptime(value.strip(), "%Y-%m-%d %H:%M:%S UTC").date().isoformat())
        except ValueError:
            pass
    domains = collections.Counter(
        urlparse(value).hostname.lower() if urlparse(value).hostname else "MISSING_OR_INVALID"
        for value in data["Url"]
    )
    body_lengths = [len(value) for value in article if present(value)]
    profile = {
        "schema": "fnspid_bounded_sample_profile_v2",
        "source_dataset": manifest["source"]["dataset"],
        "source_revision": manifest["source"]["revision"],
        "source_file": manifest["source"]["target_file"],
        "sample_sha256": sample_hash,
        "acquisition_manifest_sha256": sha256_file(manifest_path),
        "profile_script_sha256": sha256_file(Path(__file__).resolve()),
        "scope": "Saved rows in a bounded first-record scan; no corpus inference",
        "complete_logical_rows_scanned": scanned,
        "saved_rows": len(rows),
        "stop_reason": manifest["result"]["stop_reason"],
        "raw_columns_in_order": header,
        "field_presence_saved_rows": presence,
        "duplicate_exact_nonmissing_saved_rows": {
            "url": duplicate_excess(data["Url"]),
            "title": duplicate_excess(data["Article_title"]),
            "body_sha256": duplicate_excess(
                [hashlib.sha256(value.encode("utf-8")).hexdigest()
                 for value in article if present(value)]),
        },
        "stock_symbol_saved_rows": {
            "present_rows": presence["Stock_symbol"]["present"],
            "distinct_nonmissing_candidates": len({value.strip() for value in data["Stock_symbol"]
                                                   if present(value)}),
            "historical_security_identity_verified": False,
        },
        "date_saved_rows": {
            "string_format_counts": dict(collections.Counter(date_kind(value) for value in dates)),
            "literal_utc_midnight_count": sum(bool(re.fullmatch(
                r"\d{4}-\d{2}-\d{2} 00:00:00 UTC", value.strip())) for value in dates),
            "parseable_utc_date_min": min(parsed_dates) if parsed_dates else None,
            "parseable_utc_date_max": max(parsed_dates) if parsed_dates else None,
            "publication_instant_verified": False,
            "source_timestamp_precision_verified": False,
            "comment": "A stored UTC midnight string does not establish an intraday publication time.",
        },
        "url_domain_counts_saved_rows": dict(domains),
        "article_saved_rows": {
            "nonempty_nonsentinel_rows": presence["Article"]["present"],
            "embedded_newline_rows": sum("\n" in value for value in article),
            "length_chars_min": min(body_lengths) if body_lengths else None,
            "length_chars_median": statistics.median(body_lengths) if body_lengths else None,
            "length_chars_max": max(body_lengths) if body_lengths else None,
            "html_tag_pattern_rows": sum(bool(re.search(r"<[A-Za-z][^>]*>", value))
                                         for value in article),
            "full_article_vs_excerpt_independently_verified": False,
        },
        "summary_columns": [name for name in header if name.lower().endswith("_summary")],
        "sentiment_label_columns": [name for name in header
                                    if "sentiment" in name.lower() or "label" in name.lower()],
        "provenance_note": "Article, title and named summary columns are distinct; no row values are in this profile.",
    }
    return profile


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    artifacts = (Path(__file__).resolve().parents[1] / "artifacts").resolve()
    output = args.output.resolve()
    if output.exists():
        raise FileExistsError(f"Output already exists: {output}")
    if not output.is_relative_to(artifacts):
        raise ValueError("OUTPUT_MUST_BE_INSIDE_IGNORED_ARTIFACTS")
    profile = profile_sample(args.sample, args.manifest)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(profile, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"saved_rows": profile["saved_rows"],
                      "sample_sha256": profile["sample_sha256"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
