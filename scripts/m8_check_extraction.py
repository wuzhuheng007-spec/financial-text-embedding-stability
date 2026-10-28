"""Recompute M8 four-filing extraction identities from saved bytes.

This is a same-agent program self-check, not independent human review. It does
not parse or fetch new filings, and it never writes inside ingest/extraction.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
from html import unescape
import json
from pathlib import Path
import re
import sys


FIRMS = ("MSFT", "AMZN", "GOOGL", "META")


def digest(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def source_table_spans(html: str) -> list[tuple[int, int]]:
    """Separate tag-level scan; reject unbalanced/nested source tables."""
    spans: list[tuple[int, int]] = []
    open_at: int | None = None
    for match in re.finditer(r"<table\b[^>]*>|</table\s*>", html, re.I | re.S):
        if re.match(r"<table\b", match.group(), re.I):
            if open_at is not None:
                raise ValueError("Nested literal HTML tables require separate review")
            open_at = match.start()
        else:
            if open_at is None:
                raise ValueError("Source HTML table closing tag lacks opening tag")
            spans.append((open_at, match.end()))
            open_at = None
    if open_at is not None:
        raise ValueError("Source HTML table opening tag lacks closing tag")
    return spans


def visible_item_prefix(html: str, offset: int, number: int) -> bool:
    """A source text-node heading can encode the Item/number gap as an entity."""
    return re.match(rf"item\s*{number}\b", unescape(html[offset:offset + 80]), re.I) is not None


def check_firm(run_root: Path, firm: str, extraction_version: str = "extraction_v4") -> dict:
    ingest = run_root / "ingest" / firm
    extraction = run_root / extraction_version / firm
    checks: list[dict] = []

    def check(name: str, passed: bool, detail: str | None = None) -> None:
        checks.append({"check": name, "pass": bool(passed), "detail": detail})

    identity = read_json(ingest / "source_identity.json")
    receipt = read_json(extraction / "extraction_receipt.json")
    boundary = read_json(extraction / "boundary_candidates.json")
    index = read_json(extraction / "table_index.json")
    periods = read_json(extraction / "period_ledger.json")
    raw = (ingest / "original_submission.txt").read_bytes()
    html_bytes = (ingest / "primary_document_source.html").read_bytes()
    primary_bytes = (ingest / "primary_text.txt").read_bytes()
    html = html_bytes.decode("utf-8")
    primary = primary_bytes.decode("utf-8")
    section_bytes = (extraction / "item2_candidate.txt").read_bytes()
    section = section_bytes.decode("utf-8")

    check("fixed_firm_identity", identity.get("ticker") == firm and
          receipt.get("source_identity", {}).get("ticker") == firm)
    check("program_only_human_pending", receipt.get("status") == "PROGRAM_EXTRACTED_HUMAN_PENDING" and
          receipt.get("human_review") == "PENDING_HUMAN_REVIEW" and
          index.get("human_review") == "PENDING_HUMAN_REVIEW" and
          periods.get("human_review") == "PENDING_HUMAN_REVIEW")
    project = Path(__file__).resolve().parents[1]
    check("source_code_sha_at_execution",
          receipt.get("source_code_sha256") == digest((project / "scripts/m8_extract_10q.py").read_bytes()) and
          receipt.get("module_sha256") == digest((project / "src/finembed/m8_tenq.py").read_bytes()))
    check("command_recorded", isinstance(receipt.get("command_argv"), list) and
          any(str(part).endswith("m8_extract_10q.py") for part in receipt["command_argv"]))
    for name, value in (("original_submission.txt", raw),
                        ("primary_document_source.html", html_bytes),
                        ("primary_text.txt", primary_bytes),
                        ("source_identity.json", (ingest / "source_identity.json").read_bytes())):
        check(f"input_sha:{name}", receipt.get("input_hashes", {}).get(name) == digest(value))
    check("raw_sha_in_identity", identity.get("original_submission_sha256") == digest(raw))
    for name in ("primary_document_source.html", "primary_text.txt"):
        check(f"extraction_copy:{name}", (extraction / name).read_bytes() == (ingest / name).read_bytes())
    for name, record in receipt.get("outputs", {}).items():
        saved = extraction / name
        check(f"output_sha:{name}", saved.is_file() and digest(saved.read_bytes()) == record.get("sha256") and
              saved.stat().st_size == record.get("bytes"))
    selected = boundary.get("selected_span") or {}
    a, b = selected.get("start_char"), selected.get("end_char_exclusive")
    span_ok = isinstance(a, int) and isinstance(b, int) and 0 <= a < b <= len(primary)
    check("selected_normalized_span", span_ok and primary[a:b] == section and
          selected.get("section_sha256") == digest(section_bytes) and
          index.get("section_sha256") == digest(section_bytes))
    check("selected_index_zero_based_binding", isinstance(boundary.get("selected_candidate_index"), int) and
          boundary["candidates"][boundary["selected_candidate_index"]]["start_char"] == a and
          boundary["candidates"][boundary["selected_candidate_index"]]["end_char_exclusive"] == b)

    anchors = boundary.get("html_heading_anchors") or {}
    start_heading = (anchors.get("selected_item2") or {}).get("html_source_char_start")
    end_heading = (anchors.get("selected_item3") or {}).get("html_source_char_start")
    anchor_ok = isinstance(start_heading, int) and isinstance(end_heading, int) and 0 <= start_heading < end_heading <= len(html)
    check("source_heading_interval", anchor_ok and
          visible_item_prefix(html, start_heading, 2) and visible_item_prefix(html, end_heading, 3))

    independent_tables = source_table_spans(html)
    expected = [(x, y) for x, y in independent_tables if
                anchor_ok and start_heading <= x and y <= end_heading]
    saved_spans = [(row["html_source_char_start"], row["html_source_char_end_exclusive"])
                   for row in index.get("tables", [])]
    check("independent_literal_table_count_and_spans",
          expected == saved_spans and len(saved_spans) == index.get("source_html_table_count"),
          f"tag_scan={len(expected)}; index={len(saved_spans)}")
    check("period_ledger_table_id_order",
          [row["table_id"] for row in index.get("tables", [])] ==
          [row["table_id"] for row in periods.get("table_periods", [])])

    raw_html_start = boundary.get("coordinates", {}).get("primary_html_raw_byte_span", [None])[0]
    check("raw_primary_byte_insertion", isinstance(raw_html_start, int) and
          raw[raw_html_start:raw_html_start + len(html_bytes)] == html_bytes)
    for row, ledger in zip(index.get("tables", []), periods.get("table_periods", [])):
        label = row["table_id"]
        x, y = row["html_source_char_start"], row["html_source_char_end_exclusive"]
        raw_x, raw_y = row["complete_submission_byte_start"], row["complete_submission_byte_end_exclusive"]
        source_fragment = html[x:y].encode("utf-8")
        check(f"{label}:raw_html_span_sha", source_fragment == raw[raw_x:raw_y] and
              digest(source_fragment) == row["raw_html_fragment_sha256"] and
              ledger.get("raw_html_fragment_sha256") == row["raw_html_fragment_sha256"])
        sx, sy = row["flattened_section_char_start"], row["flattened_section_char_end_exclusive"]
        check(f"{label}:normalized_span_sha", isinstance(sx, int) and isinstance(sy, int) and
              section[sx:sy] == row["flattened_text"] and
              digest(row["flattened_text"].encode("utf-8")) == row["flattened_text_sha256"] and
              ledger.get("normalized_section_char_span") == [sx, sy])
        context = row["source_caption_context"]
        cx, cy = context["html_source_char_span"]
        bx, by = context["complete_submission_byte_span"]
        check(f"{label}:caption_source_sha", html[cx:cy].encode("utf-8") == raw[bx:by] and
              digest(raw[bx:by]) == context["raw_html_sha256"] and
              ledger.get("source_caption_context") == context)
        caption = row["caption_candidate"]
        ns = caption.get("normalized_section_char_span")
        check(f"{label}:caption_normalized_sha", isinstance(ns, list) and
              digest(section[ns[0]:ns[1]].encode("utf-8")) == caption.get("normalized_context_sha256") and
              ledger.get("caption_candidate") == caption)
        check(f"{label}:unit_footnote_ledger", ledger.get("unit_evidence") == row["unit_evidence"] and
              ledger.get("footnote_evidence") == row["footnote_evidence"] and
              ledger.get("footnote_hints") == row["footnote_hints"] and
              ledger.get("adjacent_text_note_candidates") == row["adjacent_text_note_candidates"] and
              ledger.get("following_source_context") == row["following_source_context"])
        for j, note in enumerate(row["footnote_hints"]):
            nx, ny = note["complete_submission_byte_span"]
            check(f"{label}:sup_{j}_sha", digest(raw[nx:ny]) == note["raw_html_sha256"])
        follow = row["following_source_context"]
        fx, fy = follow["html_source_char_span"]
        fbx, fby = follow["complete_submission_byte_span"]
        check(f"{label}:following_source_sha", html[fx:fy].encode("utf-8") == raw[fbx:fby] and
              digest(raw[fbx:fby]) == follow["raw_html_sha256"])
        check(f"{label}:adjacent_note_count",
              row["footnote_evidence"].get("adjacent_text_note_count") ==
              len(row["adjacent_text_note_candidates"]))
        for j, note in enumerate(row["adjacent_text_note_candidates"]):
            nx, ny = note["normalized_section_char_span"]
            check(f"{label}:adjacent_note_{j}_sha", section[nx:ny] == note["text"] and
                  digest(note["text"].encode("utf-8")) == note["text_sha256"] and
                  nx - sy == note["distance_from_table_end_chars"])
    for j, row in enumerate(index.get("boundary_layout_tables", [])):
        x, y = row["html_source_char_span"]
        bx, by = row["complete_submission_byte_span"]
        check(f"boundary_layout_{j}:raw_sha", html[x:y].encode("utf-8") == raw[bx:by] and
              digest(raw[bx:by]) == row["raw_html_sha256"])
    for version in ("extraction", "extraction_v2", "extraction_v3"):
        older = run_root / version / firm
        for name in ("primary_document_source.html", "primary_text.txt", "item2_candidate.txt"):
            check(f"same_frozen_input:{version}:{name}",
                  older.is_dir() and (older / name).read_bytes() == (extraction / name).read_bytes())
    return {"ticker": firm, "status": "PASS" if all(row["pass"] for row in checks) else "FAIL",
            "checks": checks, "table_count": len(index["tables"]),
            "boundary_layout_table_count": len(index.get("boundary_layout_tables", [])),
            "item2_sha256": digest(section_bytes), "raw_submission_sha256": digest(raw),
            "primary_html_sha256": digest(html_bytes)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--extraction-version", default="extraction_v4",
                        choices=("extraction_v4",), help="Versioned output; prior versions remain read-only")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    results = []
    for firm in FIRMS:
        try:
            results.append(check_firm(args.run_root, firm, args.extraction_version))
        except (OSError, ValueError, KeyError, IndexError, TypeError, UnicodeError) as exc:
            results.append({"ticker": firm, "status": "FAIL", "error": f"{type(exc).__name__}: {exc}"})
    payload = {"kind": "M8_EXTRACTION_V4_SAME_AGENT_PROGRAM_SELF_CHECK",
               "checked_utc": datetime.now(timezone.utc).isoformat(),
               "run_root": str(args.run_root), "extraction_version": args.extraction_version,
               "command_argv": sys.argv,
               "checker_source_sha256": digest(Path(__file__).read_bytes()),
               "results": results,
               "status": "PASS" if all(row["status"] == "PASS" for row in results) else "FAIL",
               "limitations": ["Same-agent software check, not independent audit or researcher acceptance",
                               "Checks exact saved bytes/spans; semantic table and period interpretation remains human pending"]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(json.dumps({"status": payload["status"], "output": str(args.output),
                      "firms": {row["ticker"]: row["status"] for row in results}}))
    return 0 if payload["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
