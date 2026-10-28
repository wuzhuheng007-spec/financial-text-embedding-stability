"""Read back the saved GOOGL variant without invoking its source serializer."""
from __future__ import annotations

import argparse
import csv
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path


class PageAudit(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.sections: list[str] = []
        self.tables = 0
        self.scripts = 0
        self.resources: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "section" and values.get("id"):
            self.sections.append(values["id"])
        if tag == "table":
            self.tables += 1
        if tag == "script":
            self.scripts += 1
        for key in ("src", "href"):
            if values.get(key):
                self.resources.append(values[key])


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    base = args.output
    provenance = json.loads((base / "googl_structured_text_provenance.json").read_text(encoding="utf-8"))
    coverage = json.loads((base / "tokenizer_coverage.json").read_text(encoding="utf-8"))
    source_paths = provenance["parent_inputs"]
    original = Path(source_paths["item2_candidate.txt"]["path"]).read_text(encoding="utf-8")
    html = Path(source_paths["primary_document_source.html"]["path"]).read_text(encoding="utf-8")
    raw = Path(source_paths["original_submission.txt"]["path"]).read_bytes()
    derivative = (base / "googl_item2_structured_text_v1.txt").read_text(encoding="utf-8")
    checks = 0
    def need(value: bool, description: str) -> None:
        nonlocal checks
        checks += 1
        if not value:
            raise ValueError(f"Same-agent readback failed: {description}")
    need(digest(original.encode("utf-8")) == source_paths["item2_candidate.txt"]["sha256"],
         "parent section hash")
    need(digest(derivative.encode("utf-8")) == provenance["derivative_sha256"],
         "derivative hash")
    need(len(provenance["replacements"]) == 14, "14 substantive tables")
    old_cursor = new_cursor = 0
    for table in provenance["replacements"]:
        table_id = table["table_id"]
        a, b = table["parent_section_unicode_span"]
        c, d = table["derivative_unicode_span"]
        h0, h1 = table["source_html_unicode_span"]
        r0, r1 = table["raw_submission_byte_span"]
        need(old_cursor <= a < b and new_cursor <= c < d, f"{table_id} ordered spans")
        need(original[old_cursor:a] == derivative[new_cursor:c], f"{table_id} preceding text exact")
        need(digest(original[a:b].encode("utf-8")) == table["parent_table_text_sha256"],
             f"{table_id} parent table hash")
        need(digest(derivative[c:d].encode("utf-8")) == table["derivative_table_text_sha256"],
             f"{table_id} derivative table hash")
        fragment = html[h0:h1].encode("utf-8")
        need(digest(fragment) == table["source_html_fragment_sha256"] and raw[r0:r1] == fragment,
             f"{table_id} original raw HTML fragment")
        cells = {x["cell_id"]: x for x in table["source_cells"]}
        need(len(cells) == table["source_cell_count"], f"{table_id} unique cell inventory")
        for cell in cells.values():
            x, y = cell["primary_html_unicode_span"]
            need(digest(html[x:y].encode("utf-8")) == cell["html_fragment_sha256"],
                 f"{table_id} cell {cell['cell_id']} source fragment")
        for row in table["source_row_to_output"]:
            x, y = row["derivative_unicode_span"]
            need(c <= x < y <= d, f"{table_id} output row range")
            for cell_id in row["source_cell_ids"]:
                cell = cells[cell_id]
                if cell["text"]:
                    need(cell["text"] in derivative[x:y], f"{table_id} cell {cell_id} output row")
        for value in table["source_value_to_output"]:
            x, y = value["derivative_unicode_span"]
            need(c <= x < y <= d and value["formatted_value"] in derivative[x:y],
                 f"{table_id} value output field")
            for cell_id in value["source_value_cell_ids"] + value["header_source_cell_ids"]:
                need(cell_id in cells, f"{table_id} value/header source cell reference")
        for mapping in table["source_cell_to_output"]:
            x, y = mapping["derivative_unicode_span"]
            cell_id = mapping["source_cell_id"]
            need(cell_id in cells and c <= x < y <= d and cells[cell_id]["text"] in derivative[x:y],
                 f"{table_id} exact conservative source-cell output mapping")
        need("20242025" not in derivative[c:d], f"{table_id} adjacent years separated")
        old_cursor, new_cursor = b, d
    need(original[old_cursor:] == derivative[new_cursor:], "final outside-table text")
    for note in provenance["adjacent_notes_outside_tables"]:
        a, b = note["parent_section_unicode_span"]
        c, d = note["derivative_unicode_span"]
        need(original[a:b] == derivative[c:d] and
             digest(derivative[c:d].encode("utf-8")) == note["text_sha256"],
             f"external note {note['table_id']} {note['label']}")
    need(len(provenance["adjacent_notes_outside_tables"]) == 5,
         "five external note candidates preserved")
    with (base / "googl_table_content_verification.csv").open(encoding="utf-8", newline="") as stream:
        csv_rows = list(csv.DictReader(stream))
    need([x["table_id"] for x in csv_rows] == [x["table_id"] for x in provenance["replacements"]],
         "CSV 14 table order")
    for row, table in zip(csv_rows, provenance["replacements"]):
        need(int(row["mapped_values"]) == len(table["source_value_to_output"]) and
             row["new_table_sha256"] == table["derivative_table_text_sha256"],
             f"{table['table_id']} CSV count/hash")
    page = (base / "googl_table_serialization_comparison.html").read_text(encoding="utf-8")
    parsed = PageAudit()
    parsed.feed(page)
    parsed.close()
    need(parsed.sections == [x["table_id"] for x in provenance["replacements"]] and
         parsed.tables == 14 and parsed.scripts == 0 and not parsed.resources,
         "static page has 14 sanitized source grids and no active/external resource")
    for length, group in coverage["chunks"].items():
        rows = group["rows"]
        need(rows[0]["token_span"][0] == 0 and
             rows[-1]["token_span"][1] == coverage["complete_derivative_body_tokens"] and
             all(x["token_span"][1] == y["token_span"][0] for x, y in zip(rows, rows[1:])),
             f"{length} token chunk coverage")
        for row in rows:
            a, b = row["source_unicode_span"]
            need(row["model_input_tokens_including_specials"] <= coverage["model_input_limit"] and
                 digest(derivative[a:b].encode("utf-8")) == row["source_span_sha256"],
                 f"{length} chunk {row['chunk']} source and limit")
    receipt = {"kind": "GOOGL_TABLE_SERIALIZATION_SAME_AGENT_READBACK", "status": "PASS",
               "independent_audit": False, "checks": checks, "passed": checks,
               "source_identity": provenance["filing"],
               "source_hashes": {"raw": digest(raw), "html": digest(html.encode("utf-8")),
                                 "old_section": digest(original.encode("utf-8"))},
               "derivative_sha256": digest(derivative.encode("utf-8")),
               "browser_rendering": "NOT_VERIFIED_LOCAL_BROWSER_RESTRICTION"}
    (base / "same_agent_readback.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2)+"\n",
                                                 encoding="utf-8")
    print(json.dumps({"status": "PASS", "checks": checks, "tables": 14,
                      "external_notes": len(provenance["adjacent_notes_outside_tables"])}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
