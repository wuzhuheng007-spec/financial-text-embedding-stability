"""A versioned, fail-closed GOOGL Item 2 table-text preprocessing variant.

This one-filing profile does not turn source-grid associations into human
approval. The old SEC-derived files are read only; no encoder is loaded.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
from html import escape
import json
from pathlib import Path
import sys

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from scripts.m7_build_10q_review import safe_preview, source_rendered
from scripts.m9_serialize_tables import (SerializationError, check_adjacent_notes, file_sha,
                                         need, parse_table, row_values, sha, tokenizer_check, visible,
                                         write_json)


VERSION = "googl_source_grid_order_v1"
SIMPLE = {"MDA-2", "MDA-4", "MDA-6", "MDA-7", "MDA-8", "MDA-9",
          "MDA-10", "MDA-11", "MDA-12", "MDA-13"}
CONSERVATIVE = {"MDA-1", "MDA-5"}
EXPECTED = {
    "original_submission.txt": "e39849eb31baac03f4d0f0ba254735f20ef039635b1812339aa640c0bfb17730",
    "primary_document_source.html": "c65f5b83b4fe83b74c497d04ef64d2d06867a234309f45f3c67c29b414d24c48",
    "primary_text.txt": "3d24a3e9d13db562c2212571224ca7f8423669e97d2b67f34bbf99d363093942",
    "item2_candidate.txt": "5e75ca737c479302764d35550f581badd5461183479da2665806ce109a924883",
    "table_index.json": "f087e5e11b048c9a40a97efcee3dab101a995fc538aab9c438a2414337d2b23d",
}


def profile_for(table_id: str) -> str:
    if table_id in SIMPLE:
        return "SOURCE_VERIFIED_TWO_YEAR"
    if table_id in CONSERVATIVE:
        return "SOURCE_CELL_COORDINATES_HEADER_ASSOCIATION_PENDING"
    if table_id == "MDA-3":
        return "SOURCE_GROUP_PERCENT_NO_YEAR_HEADER"
    if table_id == "MDA-14":
        return "SOURCE_SHARES_AMOUNT_SINGLE_PERIOD"
    raise SerializationError(f"Unknown GOOGL table profile: {table_id}")


def header_check(rows: list[list[dict]], table_id: str, profile: str) -> dict:
    if profile == "SOURCE_VERIFIED_TWO_YEAR":
        need(len(rows) >= 5, f"{table_id} missing data rows")
        period = visible(rows[1]); day = visible(rows[2]); years = visible(rows[3])
        need([x["text"] for x in period] == ["Three Months Ended"] and
             [x["text"] for x in day] == ["March 31,"] and
             [x["text"] for x in years] == ["2024", "2025"],
             f"{table_id} two-year source header profile mismatch")
        return {"period_text": "Three Months Ended March 31,",
                "period_source_cell_ids": [period[0]["cell_id"], day[0]["cell_id"]],
                "column_headers": [{"label": x["text"], "source_cell_id": x["cell_id"]} for x in years],
                "association_status": "SOURCE_ORDER_PROGRAM_CHECKED_HUMAN_PENDING"}
    if table_id == "MDA-1":
        need(len(rows) >= 17 and [x["text"] for x in visible(rows[3])] ==
             ["2024", "2025", "$ Change", "% Change"], "MDA-1 header changed")
        return {"source_header_rows": [2, 3, 4],
                "association_status": "UNRESOLVED_SPARSE_HEADER_TO_VALUE_MAPPING"}
    if table_id == "MDA-5":
        need(len(rows) >= 12 and [x["text"] for x in visible(rows[4])] == ["2024", "2025"] and
             len(visible(rows[3])) == 7, "MDA-5 multi-level header changed")
        return {"source_header_rows": [2, 3, 4, 5],
                "association_status": "UNRESOLVED_SPARSE_MULTI_LEVEL_HEADER_TO_VALUE_MAPPING"}
    if table_id == "MDA-3":
        need([x["text"] for x in visible(rows[1])] == ["Google Search & other"] and
             [x["text"] for x in visible(rows[4])] == ["Google Network"],
             "MDA-3 source groups changed")
        return {"source_group_rows": [2, 5], "source_year_header": None,
                "association_status": "SOURCE_ROW_PERCENT_ONLY_NO_YEAR_IN_TABLE"}
    if table_id == "MDA-14":
        period = visible(rows[1]); headers = visible(rows[2])
        need([x["text"] for x in period] == ["Three Months Ended March 31, 2025"] and
             [x["text"] for x in headers] == ["Shares", "Amount"],
             "MDA-14 shares/amount source header changed")
        return {"period_text": period[0]["text"],
                "period_source_cell_ids": [period[0]["cell_id"]],
                "column_headers": [{"label": x["text"], "source_cell_id": x["cell_id"]}
                                   for x in headers],
                "association_status": "SOURCE_ORDER_PROGRAM_CHECKED_HUMAN_PENDING"}
    raise SerializationError(f"No source header rule for {table_id}")


def serialize_table(fragment: str, table_id: str) -> tuple[str, dict]:
    rows = parse_table(fragment)
    profile = profile_for(table_id)
    headers = header_check(rows, table_id, profile)
    lines: list[str] = []
    row_maps: list[dict] = []
    value_maps: list[dict] = []
    cell_maps: list[dict] = []
    def add(line: str, row_number: int | None = None) -> None:
        start = sum(len(x) + 1 for x in lines)
        lines.append(line)
        if row_number is not None:
            row_maps.append({"source_row": row_number, "output_char_span": [start, start + len(line)],
                             "source_cell_ids": [x["cell_id"] for x in rows[row_number - 1]]})
    add(f"[Generated structure: table {table_id} begins; profile {profile}]")
    for row_number, row in enumerate(rows, 1):
        cells = visible(row)
        if not cells:
            add(f"Source row {row_number}: [blank source spacer row; no observation]", row_number)
            continue
        if profile == "SOURCE_CELL_COORDINATES_HEADER_ASSOCIATION_PENDING":
            line = f"Source row {row_number}, ordered source cells: "
            for ordinal, cell in enumerate(cells):
                x, y = cell["grid_column_span"]
                field = (" | " if ordinal else "") + f"Source cell {cell['cell_id']} [source grid Column {x}:{y}, end exclusive] = {cell['text']}"
                start = sum(len(x) + 1 for x in lines) + len(line)
                line += field
                cell_maps.append({"source_cell_id": cell["cell_id"],
                                  "output_char_span": [start, start + len(field)]})
            add(line, row_number)
            continue
        if profile == "SOURCE_VERIFIED_TWO_YEAR":
            if row_number == 2:
                add(f"Source row {row_number}, period heading: {cells[0]['text']}", row_number)
            elif row_number == 3:
                add(f"Source row {row_number}, period continuation: {cells[0]['text']}", row_number)
            elif row_number == 4:
                add(f"Source row {row_number}, year columns: Column 1 {cells[0]['text']} | Column 2 {cells[1]['text']}", row_number)
            elif len(cells) == 1:
                add(f"Source row {row_number}, section label: {cells[0]['text']}", row_number)
            else:
                values = row_values(cells[1:], 2)
                line = f"Source row {row_number}, label: {cells[0]['text']}"
                for number, value in enumerate(values, 1):
                    header = headers["column_headers"][number - 1]
                    field = f" | Column {number} [period: {headers['period_text']}; year: {header['label']}] = {value['text']}"
                    start = sum(len(x) + 1 for x in lines) + len(line)
                    line += field
                    value_maps.append({"source_row": row_number, "column": number,
                                       "output_char_span": [start, start + len(field)],
                                       "source_value_cell_ids": value["source_cell_ids"],
                                       "header_source_cell_ids": headers["period_source_cell_ids"] +
                                                                 [header["source_cell_id"]],
                                       "formatted_value": value["text"]})
                add(line, row_number)
            continue
        if profile == "SOURCE_GROUP_PERCENT_NO_YEAR_HEADER":
            if row_number in (2, 5):
                add(f"Source row {row_number}, source group: {cells[0]['text']}", row_number)
            else:
                values = row_values(cells[1:], 1)
                prefix = f"Source row {row_number}, label: {cells[0]['text']}"
                field = f" | Column 1 [percentage; no year in table] = {values[0]['text']}"
                start = sum(len(x) + 1 for x in lines) + len(prefix)
                line = prefix + field
                add(line, row_number)
                value_maps.append({"source_row": row_number, "column": 1,
                                   "output_char_span": [start, start + len(field)],
                                   "source_value_cell_ids": values[0]["source_cell_ids"],
                                   "header_source_cell_ids": [], "formatted_value": values[0]["text"]})
            continue
        if profile == "SOURCE_SHARES_AMOUNT_SINGLE_PERIOD":
            if row_number == 2:
                add(f"Source row {row_number}, source period: {cells[0]['text']}", row_number)
            elif row_number == 3:
                add(f"Source row {row_number}, source columns: Column 1 {cells[0]['text']} | Column 2 {cells[1]['text']}", row_number)
            else:
                values = row_values(cells[1:], 2)
                line = f"Source row {row_number}, label: {cells[0]['text']}"
                for number, value in enumerate(values, 1):
                    header = headers["column_headers"][number - 1]
                    field = f" | Column {number} [period: {headers['period_text']}; measure: {header['label']}] = {value['text']}"
                    start = sum(len(x) + 1 for x in lines) + len(line)
                    line += field
                    value_maps.append({"source_row": row_number, "column": number,
                                       "output_char_span": [start, start + len(field)],
                                       "source_value_cell_ids": value["source_cell_ids"],
                                       "header_source_cell_ids": headers["period_source_cell_ids"] +
                                                                 [header["source_cell_id"]],
                                       "formatted_value": value["text"]})
                add(line, row_number)
            continue
        raise SerializationError(f"Unrecognized profile branch for {table_id}")
    add(f"[Generated structure: table {table_id} ends]")
    text = "\n".join(lines)
    need(len(row_maps) == len(rows), "One or more source rows were not represented")
    for cell in (x for row in rows for x in row if x["text"]):
        a, b = row_maps[cell["source_row"] - 1]["output_char_span"]
        need(cell["text"] in text[a:b], f"Source cell missing: {table_id} {cell['cell_id']}")
    return text, {"profile": profile, "header_evidence": headers,
                  "source_row_count": len(rows), "source_cell_count": sum(len(row) for row in rows),
                  "source_nonempty_cell_count": sum(bool(x["text"]) for row in rows for x in row),
                  "source_cells": [x for row in rows for x in row],
                  "source_row_to_output": row_maps, "source_value_to_output": value_maps,
                  "source_cell_to_output": cell_maps,
                  "generated_labels_are_sec_wording": False}


def build_derivative(section: str, html: str, raw: bytes, index: dict) -> tuple[str, list[dict], list[dict]]:
    tables = index["tables"]
    need([x["table_id"] for x in tables] == [f"MDA-{n}" for n in range(1, 15)],
         "Unexpected table count or order")
    need(not index["boundary_layout_tables"], "GOOGL boundary-layout table set changed")
    parts: list[str] = []
    replacements: list[dict] = []
    unchanged: list[dict] = []
    parent_cursor = output_cursor = 0
    for table in tables:
        a, b = table["flattened_section_char_start"], table["flattened_section_char_end_exclusive"]
        h0, h1 = table["html_source_char_start"], table["html_source_char_end_exclusive"]
        r0, r1 = table["complete_submission_byte_start"], table["complete_submission_byte_end_exclusive"]
        need(isinstance(a, int) and parent_cursor <= a < b <= len(section), "Invalid Item 2 source order")
        need(0 <= h0 < h1 <= len(html), "Invalid source HTML span")
        fragment = html[h0:h1]
        old = section[a:b]
        need(sha(fragment.encode("utf-8")) == table["raw_html_fragment_sha256"] and
             raw[r0:r1] == fragment.encode("utf-8"), "HTML fragment differs from saved original submission")
        need(old == table["flattened_text"] and sha(old.encode("utf-8")) == table["flattened_text_sha256"] and
             old == source_rendered(fragment), "Old table differs from exact saved model input")
        outside = section[parent_cursor:a]
        parts.append(outside)
        unchanged.append({"parent_section_unicode_span": [parent_cursor, a],
                          "derivative_unicode_span": [output_cursor, output_cursor + len(outside)],
                          "sha256": sha(outside.encode("utf-8"))})
        output_cursor += len(outside)
        new, detail = serialize_table(fragment, table["table_id"])
        for cell in detail["source_cells"]:
            x, y = cell["html_fragment_char_span"]
            cell["primary_html_unicode_span"] = [h0 + x, h0 + y]
            need(sha(html[h0 + x:h0 + y].encode("utf-8")) == cell["html_fragment_sha256"],
                 "Source cell hash mismatch")
        for key in ("source_row_to_output", "source_value_to_output", "source_cell_to_output"):
            for mapping in detail[key]:
                if "output_char_span" in mapping:
                    x, y = mapping.pop("output_char_span")
                    mapping["derivative_unicode_span"] = [output_cursor + x, output_cursor + y]
        parts.append(new)
        replacements.append({"table_id": table["table_id"], "source_table_index_reported": table["source_table_index"],
                             "source_html_unicode_span": [h0, h1],
                             "source_html_byte_span": [table["html_source_byte_start"], table["html_source_byte_end_exclusive"]],
                             "raw_submission_byte_span": [r0, r1],
                             "source_html_fragment_sha256": table["raw_html_fragment_sha256"],
                             "parent_section_unicode_span": [a, b],
                             "parent_table_text_sha256": table["flattened_text_sha256"],
                             "derivative_unicode_span": [output_cursor, output_cursor + len(new)],
                             "derivative_table_text_sha256": sha(new.encode("utf-8")),
                             "source_cell_spans_relative_to": "source HTML fragment",
                             "row_and_value_output_spans_relative_to": "complete UTF-8 derivative decoded as Unicode",
                             **detail})
        output_cursor += len(new)
        parent_cursor = b
    outside = section[parent_cursor:]
    parts.append(outside)
    unchanged.append({"parent_section_unicode_span": [parent_cursor, len(section)],
                      "derivative_unicode_span": [output_cursor, output_cursor + len(outside)],
                      "sha256": sha(outside.encode("utf-8"))})
    derivative = "".join(parts)
    for segment in unchanged:
        a, b = segment["parent_section_unicode_span"]
        c, d = segment["derivative_unicode_span"]
        need(section[a:b] == derivative[c:d], "Non-table Item 2 text changed")
    return derivative, replacements, unchanged


CSS = """body{font:15px/1.5 system-ui,sans-serif;color:#172335;margin:24px;max-width:1600px}
.meta{background:#eaf1f8;padding:12px}.warn{background:#fff2d1;padding:12px;border-left:4px solid #aa7900}
.card{border:1px solid #aeb9c7;padding:12px;margin:18px 0}.grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px}
.panel{min-width:0;overflow:auto;max-height:640px;padding:8px;border:1px solid #cad2dc}.panel pre{white-space:pre-wrap;overflow-wrap:anywhere}
.source-table{min-width:max-content;overflow:auto}.source-table table{border-collapse:collapse}
.source-table td,.source-table th{border:1px solid #9ba8ba;padding:4px;vertical-align:top}
@media(max-width:1100px){.grid{grid-template-columns:1fr}}"""


def comparison_html(section: str, html: str, derivative: str, replacements: list[dict]) -> str:
    out = ["<!doctype html><html lang='en'><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>",
           "<title>GOOGL Item 2 table serialization comparison</title><style>", CSS, "</style><body>",
           "<h1>GOOGL 10-Q Item 2: old input, saved source, proposed structured text</h1>",
           "<p class='meta'>Accession 0001652044-25-000043. Generated structural labels are not SEC wording. "
           "All researcher choices remain pending. No encoder was run.</p>",
           f"<p>Parent Item 2 SHA-256: <code>{sha(section.encode('utf-8'))}</code><br>",
           f"Proposed derivative SHA-256: <code>{sha(derivative.encode('utf-8'))}</code></p>",
           "<p class='warn'>MDA-1 and MDA-5 preserve ordered source cells and physical grid spans, but their sparse "
           "header-to-value associations remain unresolved. Do not infer zero for missing cells or treat this as human approval.</p>"]
    for item in replacements:
        h0, h1 = item["source_html_unicode_span"]
        a, b = item["parent_section_unicode_span"]
        c, d = item["derivative_unicode_span"]
        out += [f"<section id='{item['table_id']}' class='card'><h2>{item['table_id']}</h2>",
                f"<p>Profile: {item['profile']}; association: {item['header_evidence']['association_status']}; "
                f"source rows {item['source_row_count']}, nonempty source cells {item['source_nonempty_cell_count']}. "
                f"Old Item 2 Unicode [{a},{b}); new Unicode [{c},{d}); source HTML Unicode [{h0},{h1}).</p>",
                "<details><summary>Unchanged caption and following notes/context</summary><pre>",
                escape(section[max(0, a-250):a]), "\n--- After source table ---\n",
                escape(section[b:min(len(section), b+480)]), "</pre></details>",
                "<div class='grid'><div class='panel'><h3>Old exact text</h3><pre>", escape(section[a:b]),
                "</pre></div><div class='panel'><h3>Sanitized original HTML grid</h3><div class='source-table'>",
                safe_preview(html[h0:h1]), "</div></div><div class='panel'><h3>Proposed structured plain text</h3><pre>",
                escape(derivative[c:d]), "</pre></div></div></section>"]
    out.append("</body></html>")
    return "".join(out)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ingest", type=Path, required=True)
    parser.add_argument("--extraction", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--tokenizer", type=Path, required=True)
    parser.add_argument("--model-config", type=Path, required=True)
    parser.add_argument("--pooling-config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    need(not args.output.exists(), "Output directory already exists; preserve prior attempts")
    args.output.mkdir(parents=True)
    try:
        for name, digest in EXPECTED.items():
            path = (args.ingest if name == "original_submission.txt" else args.extraction) / name
            need(file_sha(path) == digest, f"Frozen input hash mismatch: {name}")
        section = (args.extraction / "item2_candidate.txt").read_text(encoding="utf-8")
        html = (args.extraction / "primary_document_source.html").read_text(encoding="utf-8")
        raw = (args.ingest / "original_submission.txt").read_bytes()
        index = json.loads((args.extraction / "table_index.json").read_text(encoding="utf-8"))
        config = yaml.safe_load(args.config.read_text(encoding="utf-8"))
        derivative, replacements, unchanged = build_derivative(section, html, raw, index)
        notes = check_adjacent_notes(section, derivative, index, unchanged)
        coverage = tokenizer_check(derivative, replacements, config, args.tokenizer,
                                   args.model_config, args.pooling_config)
        (args.output / "googl_item2_structured_text_v1.txt").write_text(derivative, encoding="utf-8", newline="")
        write_json(args.output / "tokenizer_coverage.json", coverage)
        write_json(args.output / "googl_structured_text_provenance.json",
                   {"kind": "GOOGL_ITEM2_STRUCTURED_TEXT_DERIVATIVE",
                    "status": "PROGRAM_CHECKED_RESEARCHER_PENDING_COMPLEX_HEADERS_UNRESOLVED",
                    "serializer_version": VERSION, "created_utc": datetime.now(timezone.utc).isoformat(),
                    "filing": {"cik": "0001652044", "accession": "0001652044-25-000043",
                               "form": "10-Q", "target": "Part I Item 2 MD&A"},
                    "parent_inputs": {name: {"path": str((args.ingest if name == "original_submission.txt" else args.extraction)/name),
                                             "sha256": digest} for name, digest in EXPECTED.items()},
                    "config_path": str(args.config), "config_sha256": file_sha(args.config),
                    "serializer_source_sha256": file_sha(Path(__file__)),
                    "parent_section_sha256": EXPECTED["item2_candidate.txt"],
                    "derivative_sha256": sha(derivative.encode("utf-8")),
                    "derivative_unicode_characters": len(derivative),
                    "offset_unit": "Python Unicode characters; end exclusive, except separately labeled raw/HTML bytes",
                    "replacements": replacements, "unchanged_segments": unchanged,
                    "adjacent_notes_outside_tables": notes,
                    "generated_labels_are_sec_wording": False,
                    "human_review": "PENDING_HUMAN_REVIEW", "model_inference": "NOT_RUN"})
        (args.output / "googl_table_serialization_comparison.html").write_text(
            comparison_html(section, html, derivative, replacements), encoding="utf-8", newline="")
        with (args.output / "googl_table_content_verification.csv").open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=["table_id", "profile", "association_status",
                                                        "source_rows", "source_nonempty_cells", "mapped_values",
                                                        "source_html_sha256", "old_table_sha256", "new_table_sha256",
                                                        "old_adjacent_year_join", "external_note_count",
                                                        "source_unit_caption_line", "human_review"])
            writer.writeheader()
            for item in replacements:
                a, b = item["parent_section_unicode_span"]
                before = [x.strip() for x in section[max(0,a-1200):a].splitlines() if x.strip()]
                writer.writerow({"table_id": item["table_id"], "profile": item["profile"],
                                 "association_status": item["header_evidence"]["association_status"],
                                 "source_rows": item["source_row_count"],
                                 "source_nonempty_cells": item["source_nonempty_cell_count"],
                                 "mapped_values": len(item["source_value_to_output"]),
                                 "source_html_sha256": item["source_html_fragment_sha256"],
                                 "old_table_sha256": item["parent_table_text_sha256"],
                                 "new_table_sha256": item["derivative_table_text_sha256"],
                                 "old_adjacent_year_join": "20242025" in section[a:b],
                                 "external_note_count": sum(x["table_id"]==item["table_id"] for x in notes),
                                 "source_unit_caption_line": before[-1] if before else "",
                                 "human_review": "PENDING_HUMAN_REVIEW"})
        output_files = sorted(x for x in args.output.iterdir() if x.is_file())
        write_json(args.output / "input_output_manifest.json",
                   {"kind": "GOOGL_TABLE_SERIALIZATION_INPUT_OUTPUT_MANIFEST",
                    "command_argv": sys.argv, "serializer_source_sha256": file_sha(Path(__file__)),
                    "inputs": [{"path": str((args.ingest if name == "original_submission.txt" else args.extraction)/name),
                                "sha256": digest} for name,digest in EXPECTED.items()] +
                              [{"path": str(args.config), "sha256": file_sha(args.config)},
                               {"path": str(args.tokenizer), "sha256": file_sha(args.tokenizer)},
                               {"path": str(args.model_config), "sha256": file_sha(args.model_config)},
                               {"path": str(args.pooling_config), "sha256": file_sha(args.pooling_config)}],
                    "outputs": [{"path": str(x), "sha256": file_sha(x), "bytes": x.stat().st_size}
                                for x in output_files],
                    "checks": {"source_hashes": "PASS", "fourteen_table_spans": "PASS",
                               "non_table_text_and_external_notes": "PASS",
                               "tokenizer_no_encoder": coverage["status"],
                               "complex_table_header_associations": "UNRESOLVED_RESEARCHER_PENDING"}})
        print(json.dumps({"status": "PROGRAM_CHECKED_RESEARCHER_PENDING_COMPLEX_HEADERS_UNRESOLVED",
                          "output": str(args.output), "tables": len(replacements),
                          "derivative_sha256": sha(derivative.encode("utf-8")),
                          "body_tokens": coverage["complete_derivative_body_tokens"]}))
        return 0
    except Exception as exc:
        write_json(args.output / "failed_attempt.json", {"kind": "GOOGL_TABLE_SERIALIZATION_FAILED_ATTEMPT",
                                                      "error_type": type(exc).__name__, "reason": str(exc),
                                                      "original_inputs_untouched": True})
        raise


if __name__ == "__main__":
    raise SystemExit(main())
