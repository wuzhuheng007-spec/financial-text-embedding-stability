"""Version one source-grid serializer for the frozen AMZN Item 2 review.

Generated row/column labels are formatting, not SEC wording. The original
chapter, table index, and HTML remain read-only. No encoder is loaded here.
"""
from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import datetime, timezone
from html import escape
import hashlib
import json
from pathlib import Path
import re
import sys

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from scripts.m7_build_10q_review import safe_preview, source_rendered
from scripts.m8_extract_10q import AllTablesHTML


VERSION = "amzn_source_grid_order_v1"
EXPECTED = {
    "original_submission.txt": "834b76e2ff7363205aa7acc8e9219ded61259ecda3debd5b04c79be7388352b5",
    "primary_document_source.html": "c41a62c1adc4a886085b495f30fbbeafcf85e3499ac607451966567331830172",
    "primary_text.txt": "edd0795154aba40b298300e3318657d5c78ec91ff10ddf34806115f2ad47e675",
    "item2_candidate.txt": "43015b9006e416f31ff627e52d20bf522e50935c109d269d798641c48c420033",
    "table_index.json": "b1683799c9b2a5c8dcd73004a2e0d53f95055f684974463d1a526ff0455b163b",
}


class SerializationError(ValueError):
    pass


def need(ok: bool, message: str) -> None:
    if not ok:
        raise SerializationError(message)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_sha(path: Path) -> str:
    return sha(path.read_bytes())


def write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


@dataclass(frozen=True)
class Profile:
    years_per_group: tuple[int, ...]
    subheaders_per_year: int = 1

    @property
    def value_count(self) -> int:
        return sum(self.years_per_group) * self.subheaders_per_year


# A fixed, fail-closed interpretation of this saved filing's observed row order.
# These numbers are validation expectations, not replacements for source labels.
PROFILES = {
    "MDA-1": Profile((2, 2)),
    "MDA-2": Profile((2,)),
    "MDA-3": Profile((2,)),
    "MDA-4": Profile((2,)),
    "MDA-5": Profile((2,)),
    "MDA-6": Profile((2,), 3),
}


class ReadableTablesHTML(AllTablesHTML):
    """Reuse saved table parsing and add boundaries inside source cells."""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"td", "th"}:
            for key, value in attrs:
                if key in {"colspan", "rowspan"}:
                    need(value is not None and value.isdigit() and 1 <= int(value) <= 50,
                         f"Invalid source {key}")
        if self.cell is not None and tag in {"br", "div", "p", "li"}:
            self.cell.text_parts.append(" ")
        start = self.source_offset() if tag in {"td", "th"} else None
        super().handle_starttag(tag, attrs)
        if start is not None and self.cell is not None:
            self.cell.source_start_char = start

    def handle_endtag(self, tag: str) -> None:
        if self.cell is not None and tag in {"div", "p", "li"}:
            self.cell.text_parts.append(" ")
        if tag in {"td", "th"} and self.cell is not None:
            start = self.cell.source_start_char
            end = self.tag_end()
            super().handle_endtag(tag)
            need(self.row is not None and bool(self.row), "Closed source cell is missing")
            self.row[-1]["html_fragment_char_span"] = [start, end]
            self.row[-1]["html_fragment_sha256"] = sha(self.source[start:end].encode("utf-8"))
            return
        super().handle_endtag(tag)


def parse_table(fragment: str) -> list[list[dict]]:
    parser = ReadableTablesHTML(fragment)
    parser.feed(fragment)
    parser.close()
    need(parser.table is None and len(parser.tables) == 1, "Expected exactly one non-nested source table")
    rows = parser.tables[0]["rows"]
    need(bool(rows), "Source table has no rows")
    occupied: dict[tuple[int, int], str] = {}
    for row_number, row in enumerate(rows, 1):
        column = 1
        for cell_number, cell in enumerate(row, 1):
            while (row_number, column) in occupied:
                column += 1
            cell_id = f"R{row_number}C{cell_number}"
            cell["cell_id"] = cell_id
            cell["source_row"] = row_number
            cell["source_cell_ordinal"] = cell_number
            cell["grid_column_span"] = [column, column + cell["colspan"]]
            for row_offset in range(cell["rowspan"]):
                for col_offset in range(cell["colspan"]):
                    key = (row_number + row_offset, column + col_offset)
                    need(key not in occupied, "Overlapping rowspan/colspan in source grid")
                    occupied[key] = cell_id
            column += cell["colspan"]
    need(all(row <= len(rows) for row, _column in occupied), "Rowspan extends past the source table")
    return rows


def visible(row: list[dict]) -> list[dict]:
    return [cell for cell in row if cell["text"]]


def header_columns(rows: list[list[dict]], profile: Profile) -> tuple[list[dict], int]:
    need(len(rows) >= 4, "Missing source header/data rows")
    groups = visible(rows[1])
    years = visible(rows[2])
    need(len(groups) == len(profile.years_per_group), "Period-group header count differs from the fixed profile")
    need(len(years) == sum(profile.years_per_group), "Year-header count differs from the fixed profile")
    need(all(re.fullmatch(r"(?:19|20)\d{2}", cell["text"]) for cell in years),
         "A year header is not an explicit four-digit source year")
    subheaders = visible(rows[3]) if profile.subheaders_per_year > 1 else []
    if profile.subheaders_per_year > 1:
        need(len(subheaders) == profile.value_count, "Subheader count differs from the fixed profile")
        step = profile.subheaders_per_year
        first = [cell["text"] for cell in subheaders[:step]]
        need(all([cell["text"] for cell in subheaders[i:i + step]] == first
                 for i in range(0, len(subheaders), step)),
             "Year groups have inconsistent source subheader order")
    result = []
    year_cursor = 0
    sub_cursor = 0
    for group_number, count in enumerate(profile.years_per_group, 1):
        group = groups[group_number - 1]
        for _ in range(count):
            year = years[year_cursor]
            year_cursor += 1
            for _sub in range(profile.subheaders_per_year):
                sub = subheaders[sub_cursor] if subheaders else None
                if sub is not None:
                    sub_cursor += 1
                result.append({"group_number": group_number,
                               "period_label": group["text"], "year_label": year["text"],
                               "subheader_label": sub["text"] if sub else None,
                               "header_source_cell_ids": [group["cell_id"], year["cell_id"]] +
                                                        ([sub["cell_id"]] if sub else [])})
    return result, 4 if subheaders else 3


def row_values(cells: list[dict], expected: int) -> list[dict]:
    """Join only source-adjacent currency and percent marker cells."""
    values: list[dict] = []
    pending: list[dict] = []
    for cell in cells:
        value = cell["text"]
        if value == "$":
            need(not pending, "Consecutive currency markers")
            pending = [cell]
        elif value == "%":
            need(not pending and bool(values), "Percent sign without a preceding value")
            need("%" not in values[-1]["text"], "Repeated percent marker")
            values[-1]["text"] += " %"
            values[-1]["source_cell_ids"].append(cell["cell_id"])
        else:
            need(bool(re.fullmatch(r"(?:\(?-?[\d,]+(?:\.\d+)?\)?|—|–)", value)),
                 f"Unexpected data cell: {value!r}")
            values.append({"text": ("$ " if pending else "") + value,
                           "source_cell_ids": [x["cell_id"] for x in pending] + [cell["cell_id"]]})
            pending = []
    need(not pending, "Currency marker without a following value")
    need(len(values) == expected, f"Expected {expected} source values; found {len(values)}")
    return values


def serialize_table(fragment: str, table_id: str, profile: Profile) -> tuple[str, dict]:
    rows = parse_table(fragment)
    columns, header_end = header_columns(rows, profile)
    lines: list[str] = []
    row_maps: list[dict] = []
    value_maps: list[dict] = []
    def add(line: str, source_row: int | None = None) -> None:
        start = sum(len(x) + 1 for x in lines)
        lines.append(line)
        if source_row is not None:
            row_maps.append({"source_row": source_row, "output_char_span": [start, start + len(line)],
                             "source_cell_ids": [c["cell_id"] for c in rows[source_row - 1]]})
    add(f"[Generated structure: table {table_id} begins]")
    for row_number, row in enumerate(rows, 1):
        cells = visible(row)
        if not cells:
            add(f"Source row {row_number}: [blank source spacer row; no observation]", row_number)
        elif row_number == 2:
            labels = [f"Group {i}: {cell['text']}" for i, cell in enumerate(cells, 1)]
            add(f"Source row {row_number}, period groups: " + " | ".join(labels), row_number)
        elif row_number == 3:
            labels = [f"Year {i}: {cell['text']}" for i, cell in enumerate(cells, 1)]
            add(f"Source row {row_number}, year headers: " + " | ".join(labels), row_number)
        elif row_number == 4 and profile.subheaders_per_year > 1:
            labels = [f"Subheader {i}: {cell['text']}" for i, cell in enumerate(cells, 1)]
            add(f"Source row {row_number}, column subheaders: " + " | ".join(labels), row_number)
        elif row_number > header_end and len(cells) == 1:
            add(f"Source row {row_number}, section label: {cells[0]['text']}", row_number)
        elif row_number > header_end:
            label = cells[0]
            values = row_values(cells[1:], len(columns))
            line = f"Source row {row_number}, label: {label['text']}"
            entries = []
            for number, (column, value) in enumerate(zip(columns, values), 1):
                head = f"Column {number} [period: {column['period_label']}; year: {column['year_label']}"
                if column["subheader_label"]:
                    head += f"; basis: {column['subheader_label']}"
                field = f" | {head}] = {value['text']}"
                field_start = sum(len(x) + 1 for x in lines) + len(line)
                line += field
                entries.append({"column": number, "output_char_span": [field_start, field_start + len(field)],
                                "source_value_cell_ids": value["source_cell_ids"],
                                "header_source_cell_ids": column["header_source_cell_ids"],
                                "source_row_label_cell_id": label["cell_id"],
                                "formatted_value": value["text"]})
            add(line, row_number)
            value_maps.extend(entries)
        else:
            raise SerializationError(f"Unexpected header structure in source row {row_number}")
    add(f"[Generated structure: table {table_id} ends]")
    output = "\n".join(lines)
    inventory = [cell for row in rows for cell in row]
    need(len(row_maps) == len(rows), "A source row is missing from the serialization")
    for cell in inventory:
        if cell["text"]:
            a, b = row_maps[cell["source_row"] - 1]["output_char_span"]
            need(cell["text"] in output[a:b],
                 f"Source cell {cell['cell_id']} missing from its serialized row")
    return output, {"source_row_count": len(rows), "source_cell_count": len(inventory),
                    "nonempty_source_cell_count": sum(bool(c["text"]) for c in inventory),
                    "columns": columns, "source_cells": inventory,
                    "source_row_to_output": row_maps, "source_value_to_output": value_maps,
                    "generated_format_labels": ["Generated structure", "Source row", "Group", "Year",
                                                "Subheader", "Column", "period", "year", "basis",
                                                "section label", "blank source spacer row"]}


def build_derivative(section: str, html: str, table_index: dict,
                     profiles: dict[str, Profile] | None = None) -> tuple[str, list[dict], list[dict]]:
    profiles = profiles or PROFILES
    tables = table_index["tables"]
    need([row["table_id"] for row in tables] == list(profiles), "Unexpected table IDs or order")
    output_parts: list[str] = []
    replacements: list[dict] = []
    unchanged: list[dict] = []
    input_cursor = output_cursor = 0
    for row in tables:
        table_id = row["table_id"]
        a, b = row["flattened_section_char_start"], row["flattened_section_char_end_exclusive"]
        h0, h1 = row["html_source_char_start"], row["html_source_char_end_exclusive"]
        need(isinstance(a, int) and input_cursor <= a < b <= len(section), "Invalid section table mapping")
        need(0 <= h0 < h1 <= len(html), "Invalid source HTML table mapping")
        fragment = html[h0:h1]
        old = section[a:b]
        need(sha(fragment.encode("utf-8")) == row["raw_html_fragment_sha256"], "Source table HTML hash mismatch")
        need(old == row["flattened_text"] and sha(old.encode("utf-8")) == row["flattened_text_sha256"],
             "Saved section table span differs from the old model input")
        need(source_rendered(fragment) == old, "Fresh source flattening differs from the old model input")
        outside = section[input_cursor:a]
        output_parts.append(outside)
        unchanged.append({"parent_section_unicode_span": [input_cursor, a],
                          "derivative_unicode_span": [output_cursor, output_cursor + len(outside)],
                          "sha256": sha(outside.encode("utf-8"))})
        output_cursor += len(outside)
        new, detail = serialize_table(fragment, table_id, profiles[table_id])
        for cell in detail["source_cells"]:
            x, y = cell["html_fragment_char_span"]
            cell["primary_html_unicode_span"] = [h0 + x, h0 + y]
            need(sha(html[h0 + x:h0 + y].encode("utf-8")) == cell["html_fragment_sha256"],
                 "Source cell span/hash mismatch")
        for mapping in detail["source_row_to_output"]:
            x, y = mapping.pop("output_char_span")
            mapping["derivative_unicode_span"] = [output_cursor + x, output_cursor + y]
        for mapping in detail["source_value_to_output"]:
            x, y = mapping.pop("output_char_span")
            mapping["derivative_unicode_span"] = [output_cursor + x, output_cursor + y]
        output_parts.append(new)
        replacements.append({"table_id": table_id, "source_table_index_reported": row["source_table_index"],
                             "source_html_unicode_span": [h0, h1],
                             "source_html_byte_span": [row["html_source_byte_start"],
                                                       row["html_source_byte_end_exclusive"]],
                             "raw_submission_byte_span": [row["complete_submission_byte_start"],
                                                          row["complete_submission_byte_end_exclusive"]],
                             "source_html_fragment_sha256": row["raw_html_fragment_sha256"],
                             "parent_section_unicode_span": [a, b],
                             "parent_table_text_sha256": row["flattened_text_sha256"],
                             "derivative_unicode_span": [output_cursor, output_cursor + len(new)],
                             "derivative_table_text_sha256": sha(new.encode("utf-8")),
                             "profile": {"years_per_group": profiles[table_id].years_per_group,
                                         "subheaders_per_year": profiles[table_id].subheaders_per_year},
                             "source_cell_spans_relative_to": "source HTML fragment",
                             "row_and_value_output_spans_relative_to": "complete UTF-8 derivative decoded as Unicode",
                             **detail})
        output_cursor += len(new)
        input_cursor = b
    outside = section[input_cursor:]
    output_parts.append(outside)
    unchanged.append({"parent_section_unicode_span": [input_cursor, len(section)],
                      "derivative_unicode_span": [output_cursor, output_cursor + len(outside)],
                      "sha256": sha(outside.encode("utf-8"))})
    derivative = "".join(output_parts)
    for segment in unchanged:
        a, b = segment["parent_section_unicode_span"]
        c, d = segment["derivative_unicode_span"]
        need(section[a:b] == derivative[c:d], "Non-table chapter text changed")
    return derivative, replacements, unchanged


def check_adjacent_notes(section: str, derivative: str, table_index: dict,
                         unchanged: list[dict]) -> list[dict]:
    records = []
    for table in table_index["tables"]:
        for note in table.get("adjacent_text_note_candidates", []):
            a, b = note["normalized_section_char_span"]
            original = section[a:b]
            need(sha(original.encode("utf-8")) == note["text_sha256"],
                 "Saved adjacent-note hash mismatch")
            match = next((item for item in unchanged
                          if item["parent_section_unicode_span"][0] <= a and
                          b <= item["parent_section_unicode_span"][1]), None)
            need(match is not None, "An adjacent note would be replaced with table text")
            offset = a - match["parent_section_unicode_span"][0]
            c = match["derivative_unicode_span"][0] + offset
            d = c + len(original)
            need(derivative[c:d] == original, "Adjacent note changed in derivative")
            records.append({"table_id": table["table_id"], "label": note["label"],
                            "parent_section_unicode_span": [a, b],
                            "derivative_unicode_span": [c, d],
                            "text_sha256": note["text_sha256"],
                            "source_assignment_status": note["status"],
                            "text_preserved_exactly": True})
    return records


def tokenizer_check(text: str, replacements: list[dict], config: dict, tokenizer_file: Path,
                    model_config_file: Path, pooling_file: Path) -> dict:
    from tokenizers import Tokenizer
    embed = config["embedding"]
    for file, field in ((tokenizer_file, "tokenizer_sha256"), (model_config_file, "model_config_sha256"),
                        (pooling_file, "pooling_config_sha256")):
        need(file_sha(file) == embed[field], f"Pinned model-file hash mismatch: {field}")
    model = json.loads(model_config_file.read_text(encoding="utf-8"))
    pooling = json.loads(pooling_file.read_text(encoding="utf-8"))
    limit = min(int(model["max_position_embeddings"]), int(pooling["max_seq_length"]))
    tok = Tokenizer.from_file(str(tokenizer_file))
    tok.no_truncation()
    tok.no_padding()
    specials = tok.num_special_tokens_to_add(False)
    need(specials == 2, "Unexpected pinned tokenizer special-token count")
    encoded = tok.encode(text, add_special_tokens=False)
    need(bool(encoded.ids) and max(e for _s, e in encoded.offsets) >= len(text.rstrip()) - 2,
         "Whole derivative was truncated before chunking")
    tokens = encoded.tokens
    need("row" in tokens and "column" in tokens and "period" in tokens and "year" in tokens,
         "Generated structural word labels did not survive tokenization")
    span_checks = []
    for row in replacements:
        a, b = row["derivative_unicode_span"]
        part = text[a:b]
        e = tok.encode(part, add_special_tokens=False)
        need("row" in e.tokens and "column" in e.tokens, "Table structure missing in tokenized span")
        span_checks.append({"table_id": row["table_id"], "body_tokens_in_isolated_span": len(e.ids),
                            "year_labels_separated": "20242025" not in part,
                            "source_table_text_sha256": row["source_html_fragment_sha256"]})
    chunks = {}
    for body in (embed["body_tokens"], embed["alternate_body_tokens"]):
        need(body + specials <= limit, "Chunk plus specials exceeds pinned model input limit")
        rows = []
        cursor = 0
        for i, start in enumerate(range(0, len(encoded.ids), body)):
            end = min(start + body, len(encoded.ids))
            offsets = [x for x in encoded.offsets[start:end] if x[1] > x[0]]
            need(bool(offsets) and start == cursor, "Tokenizer chunk coverage failed")
            ca, cb = offsets[0][0], offsets[-1][1]
            rows.append({"chunk": i, "token_span": [start, end], "source_unicode_span": [ca, cb],
                         "source_span_sha256": sha(text[ca:cb].encode("utf-8")),
                         "model_input_tokens_including_specials": end - start + specials})
            cursor = end
        need(cursor == len(encoded.ids), "Final tokenizer chunk coverage failed")
        chunks[str(body)] = {"chunk_count": len(rows), "tail_body_tokens": rows[-1]["token_span"][1] -
                            rows[-1]["token_span"][0], "rows": rows}
    return {"status": "TOKENIZATION_AND_CHUNK_COVERAGE_PASS_NO_ENCODER_RUN",
            "model_id": embed["model_id"], "model_revision": embed["revision"],
            "tokenizer_revision": embed["tokenizer_revision"],
            "tokenizer_sha256": file_sha(tokenizer_file), "model_config_sha256": file_sha(model_config_file),
            "pooling_config_sha256": file_sha(pooling_file), "model_input_limit": limit,
            "special_tokens_per_chunk": specials, "complete_derivative_body_tokens": len(encoded.ids),
            "max_token_end_offset": max(e for _s, e in encoded.offsets),
            "structural_tokens_present": ["row", "column", "period", "year"],
            "table_span_checks": span_checks, "chunks": chunks,
            "pipeline_input": "Exact saved UTF-8 derivative; no subsequent whitespace rewrite or encoder inference"}


STYLE = """body{font:15px/1.5 system-ui,sans-serif;color:#172335;margin:24px;max-width:1600px}
h1,h2{line-height:1.2}.meta{background:#eaf1f8;padding:14px;border-radius:8px}
.warn{background:#fff3d6;padding:12px;border-left:4px solid #aa7900}.card{border:1px solid #aeb9c7;margin:18px 0;padding:12px;border-radius:7px}
.grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}
.panel{min-width:0;overflow:auto;max-height:650px;padding:9px;border:1px solid #cad2dc}
.panel pre{white-space:pre-wrap;overflow-wrap:anywhere}.source-table{min-width:max-content;overflow:auto}
.source-table table{border-collapse:collapse}.source-table td,.source-table th{border:1px solid #9ba8ba;padding:4px;vertical-align:top}
@media(max-width:1100px){.grid{grid-template-columns:1fr}}"""


def comparison_html(section: str, html: str, derivative: str, replacements: list[dict]) -> str:
    parts = ["<!doctype html><html lang='en'><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>",
             "<title>AMZN Item 2 table serialization comparison</title><style>", STYLE, "</style><body>",
             "<h1>AMZN 10-Q Item 2: source-grid serialization comparison</h1>",
             "<p class='meta'>Accession 0001018724-25-000036. Old and new text are distinct versions. "
             "Structural labels are generated formatting, not original SEC wording. "
             "All researcher decisions remain pending; this page is a same-agent source check.</p>",
             f"<p>Original Item 2 SHA-256: <code>{sha(section.encode('utf-8'))}</code><br>",
             f"Proposed structured input SHA-256: <code>{sha(derivative.encode('utf-8'))}</code></p>",
             "<p class='warn'>Each source grid may scroll horizontally. Read the table caption and adjacent notes in the unchanged chapter context. "
             "No model inference or financial experiment was performed.</p>"]
    for row in replacements:
        h0, h1 = row["source_html_unicode_span"]
        a, b = row["parent_section_unicode_span"]
        c, d = row["derivative_unicode_span"]
        before = section[max(0, a - 260):a]
        after = section[b:min(len(section), b + (600 if row["table_id"] == "MDA-6" else 180))]
        parts += [f"<section class='card' id='{row['table_id']}'><h2>{row['table_id']}</h2>",
                  f"<p>Source HTML Unicode [{h0},{h1}); original Item 2 Unicode [{a},{b}); "
                  f"new input Unicode [{c},{d}). Source rows: {row['source_row_count']}; "
                  f"nonempty source cells: {row['nonempty_source_cell_count']}.</p>",
                  "<details><summary>Unchanged preceding unit/caption context and following notes</summary>",
                  "<h3>Before original table</h3><pre>", escape(before),
                  "</pre><h3>After original table</h3><pre>", escape(after), "</pre></details>",
                  "<div class='grid'><div class='panel'><h3>Old exact input</h3><pre>",
                  escape(section[a:b]), "</pre></div>",
                  "<div class='panel'><h3>Sanitized original HTML grid</h3><div class='source-table'>",
                  safe_preview(html[h0:h1]), "</div></div>",
                  "<div class='panel'><h3>Proposed structured plain text</h3><pre>",
                  escape(derivative[c:d]), "</pre></div></div></section>"]
    parts.append("</body></html>")
    return "".join(parts)


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
    need(not args.output.exists(), "Output directory already exists; previous attempts must be retained")
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
        derivative, replacements, unchanged = build_derivative(section, html, index)
        for row in replacements:
            a, b = row["raw_submission_byte_span"]
            h0, h1 = row["source_html_unicode_span"]
            need(raw[a:b] == html[h0:h1].encode("utf-8"),
                 f"Original submission and saved HTML differ for {row['table_id']}")
        notes = check_adjacent_notes(section, derivative, index, unchanged)
        token = tokenizer_check(derivative, replacements, config, args.tokenizer,
                                args.model_config, args.pooling_config)
        (args.output / "amzn_item2_structured_text_v1.txt").write_text(derivative, encoding="utf-8", newline="")
        write_json(args.output / "tokenizer_coverage.json", token)
        provenance = {"kind": "AMZN_ITEM2_STRUCTURED_TEXT_DERIVATIVE", "status": "PROGRAM_CHECKED_RESEARCHER_PENDING",
                      "serializer_version": VERSION, "started_utc": datetime.now(timezone.utc).isoformat(),
                      "filing": {"cik": "0001018724", "accession": "0001018724-25-000036", "form": "10-Q",
                                 "target": "Part I Item 2 MD&A"},
                      "parent_inputs": {name: {"path": str((args.ingest if name == "original_submission.txt" else args.extraction) / name),
                                               "sha256": digest} for name, digest in EXPECTED.items()},
                      "config_path": str(args.config), "config_sha256": file_sha(args.config),
                      "serializer_source_sha256": file_sha(Path(__file__)),
                      "derivative_sha256": sha(derivative.encode("utf-8")),
                      "derivative_characters": len(derivative),
                      "offset_unit": "Python Unicode character positions; end exclusive",
                      "replacements": replacements, "unchanged_segments": unchanged,
                      "adjacent_notes_outside_tables": notes,
                      "notes_outside_tables": "Adjacent notes remain exact non-table substrings; no note text was copied into a table replacement.",
                      "section_boundary_and_review_state": "Original v4 review packet is unchanged; all AMZN researcher choices remain null.",
                      "generated_labels_are_sec_text": False, "model_inference": "NOT_RUN"}
        write_json(args.output / "amzn_structured_text_provenance.json", provenance)
        (args.output / "amzn_table_serialization_comparison.html").write_text(
            comparison_html(section, html, derivative, replacements), encoding="utf-8", newline="")
        csv_path = args.output / "amzn_table_content_verification.csv"
        with csv_path.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=["table_id", "source_row_count", "source_nonempty_cells",
                                                        "mapped_values", "source_html_sha256", "old_text_sha256",
                                                        "new_text_sha256", "source_period_labels", "source_year_labels",
                                                        "source_subheader_labels", "preceding_unit_caption_line",
                                                        "source_currency_cell_count", "source_percent_cell_count",
                                                        "source_parenthetical_value_count", "source_em_dash_cell_count",
                                                        "all_nonempty_cells_present", "non_table_text_unchanged",
                                                        "adjacent_notes_preserved", "adjacent_note_sha256",
                                                        "researcher_decision"])
            writer.writeheader()
            for row in replacements:
                a = row["parent_section_unicode_span"][0]
                preceding_lines = [x.strip() for x in section[max(0, a - 1200):a].splitlines() if x.strip()]
                caption = preceding_lines[-1] if preceding_lines else ""
                source_texts = [x["text"] for x in row["source_cells"]]
                adjacent = [x for x in notes if x["table_id"] == row["table_id"]]
                writer.writerow({"table_id": row["table_id"], "source_row_count": row["source_row_count"],
                                 "source_nonempty_cells": row["nonempty_source_cell_count"],
                                 "mapped_values": len(row["source_value_to_output"]),
                                 "source_html_sha256": row["source_html_fragment_sha256"],
                                 "old_text_sha256": row["parent_table_text_sha256"],
                                 "new_text_sha256": row["derivative_table_text_sha256"],
                                 "source_period_labels": " | ".join(dict.fromkeys(c["period_label"] for c in row["columns"])),
                                 "source_year_labels": " | ".join(c["year_label"] for c in row["columns"]),
                                 "source_subheader_labels": " | ".join(c["subheader_label"] or "" for c in row["columns"]),
                                 "preceding_unit_caption_line": caption,
                                 "source_currency_cell_count": sum(x == "$" for x in source_texts),
                                 "source_percent_cell_count": sum(x == "%" for x in source_texts),
                                 "source_parenthetical_value_count": sum(bool(re.fullmatch(r"\(\d[\d,.]*\)", x))
                                                                       for x in source_texts),
                                 "source_em_dash_cell_count": sum(x == "—" for x in source_texts),
                                 "all_nonempty_cells_present": True, "non_table_text_unchanged": True,
                                 "adjacent_notes_preserved": len(adjacent),
                                 "adjacent_note_sha256": " | ".join(x["text_sha256"] for x in adjacent),
                                 "researcher_decision": "PENDING_HUMAN_REVIEW"})
        outputs = [p for p in args.output.iterdir() if p.is_file()]
        manifest = {"kind": "AMZN_TABLE_SERIALIZATION_INPUT_OUTPUT_MANIFEST",
                    "source_code_git_head": __import__("subprocess").check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
                    "command_argv": sys.argv,
                    "inputs": [{"path": str((args.ingest if name == "original_submission.txt" else args.extraction) / name),
                                "sha256": digest} for name, digest in EXPECTED.items()] +
                              [{"path": str(args.config), "sha256": file_sha(args.config)},
                               {"path": str(Path(__file__)), "sha256": file_sha(Path(__file__))}],
                    "outputs": [{"path": str(path), "sha256": file_sha(path), "bytes": path.stat().st_size}
                                for path in sorted(outputs)],
                    "checks": {"source_hashes": "PASS", "six_table_spans": "PASS",
                               "outside_table_text": "PASS", "tokenizer_and_chunk_coverage": "PASS",
                               "researcher_review": "PENDING"}}
        write_json(args.output / "input_output_manifest.json", manifest)
        print(json.dumps({"status": "PROGRAM_CHECKED_RESEARCHER_PENDING", "output": str(args.output),
                          "parent_sha256": EXPECTED["item2_candidate.txt"],
                          "derivative_sha256": provenance["derivative_sha256"],
                          "tables": len(replacements), "body_tokens": token["complete_derivative_body_tokens"]}))
        return 0
    except Exception as exc:
        write_json(args.output / "failed_attempt.json", {"kind": "AMZN_TABLE_SERIALIZATION_FAILED_ATTEMPT",
                                                      "exception_type": type(exc).__name__, "reason": str(exc),
                                                      "original_inputs_untouched": True})
        raise


if __name__ == "__main__":
    raise SystemExit(main())
