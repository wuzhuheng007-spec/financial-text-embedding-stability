"""Read back a saved AMZN derivative without calling its serializer or encoder."""
from __future__ import annotations

import argparse
import csv
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def check(ok: bool, message: str, results: list[dict]) -> None:
    results.append({"check": message, "pass": bool(ok)})
    if not ok:
        raise ValueError(message)


class PageAudit(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.section_ids: list[str] = []
        self.table_count = 0
        self.external_resources: list[str] = []
        self.scripts = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "section" and attributes.get("id"):
            self.section_ids.append(attributes["id"])
        if tag == "table":
            self.table_count += 1
        if tag == "script":
            self.scripts += 1
        for key in ("src", "href"):
            if attributes.get(key):
                self.external_resources.append(attributes[key])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    base = args.output
    provenance = json.loads((base / "amzn_structured_text_provenance.json").read_text(encoding="utf-8"))
    coverage = json.loads((base / "tokenizer_coverage.json").read_text(encoding="utf-8"))
    original_path = Path(provenance["parent_inputs"]["item2_candidate.txt"]["path"])
    source_path = Path(provenance["parent_inputs"]["primary_document_source.html"]["path"])
    raw_path = Path(provenance["parent_inputs"]["original_submission.txt"]["path"])
    original = original_path.read_text(encoding="utf-8")
    source = source_path.read_text(encoding="utf-8")
    raw = raw_path.read_bytes()
    derivative = (base / "amzn_item2_structured_text_v1.txt").read_text(encoding="utf-8")
    results: list[dict] = []
    check(digest(original.encode("utf-8")) == provenance["parent_inputs"]["item2_candidate.txt"]["sha256"],
          "Original section hash matches frozen receipt", results)
    check(digest(derivative.encode("utf-8")) == provenance["derivative_sha256"],
          "Derivative hash matches provenance", results)
    check(len(provenance["replacements"]) == 6, "Six substantive table replacements", results)
    mda1 = provenance["replacements"][0]["columns"]
    check([x["period_label"] for x in mda1] ==
          ["Three Months Ended March 31,"] * 2 + ["Twelve Months Ended March 31,"] * 2 and
          [x["year_label"] for x in mda1] == ["2024", "2025", "2024", "2025"],
          "MDA-1 quarter and trailing-twelve-month source group order", results)
    mda6 = provenance["replacements"][5]["columns"]
    check([x["year_label"] for x in mda6] == ["2024"] * 3 + ["2025"] * 3 and
          [x["subheader_label"] for x in mda6] ==
          ["As Reported", "Exchange Rate Effect (1)", "At Prior Year Rates (2)"] * 2,
          "MDA-6 source year and FX-basis group order", results)
    cursor_old = cursor_new = 0
    for i, item in enumerate(provenance["replacements"]):
        a, b = item["parent_section_unicode_span"]
        c, d = item["derivative_unicode_span"]
        check(a >= cursor_old and c >= cursor_new,
              f"{item['table_id']} nonoverlapping ordered spans", results)
        check(original[cursor_old:a] == derivative[cursor_new:c],
              f"{item['table_id']} preceding non-table text unchanged", results)
        old = original[a:b]
        new = derivative[c:d]
        h0, h1 = item["source_html_unicode_span"]
        r0, r1 = item["raw_submission_byte_span"]
        fragment = source[h0:h1].encode("utf-8")
        check(digest(old.encode("utf-8")) == item["parent_table_text_sha256"],
              f"{item['table_id']} old table hash", results)
        check(digest(new.encode("utf-8")) == item["derivative_table_text_sha256"],
              f"{item['table_id']} new table hash", results)
        check(digest(fragment) == item["source_html_fragment_sha256"] and raw[r0:r1] == fragment,
              f"{item['table_id']} original raw HTML bytes", results)
        inventory = {cell["cell_id"]: cell for cell in item["source_cells"]}
        check(len(inventory) == item["source_cell_count"],
              f"{item['table_id']} all source cells uniquely inventoried", results)
        for cell in inventory.values():
            x, y = cell["primary_html_unicode_span"]
            check(digest(source[x:y].encode("utf-8")) == cell["html_fragment_sha256"],
                  f"{item['table_id']} source cell {cell['cell_id']} hash", results)
        for mapping in item["source_row_to_output"]:
            x, y = mapping["derivative_unicode_span"]
            row_text = derivative[x:y]
            check(c <= x < y <= d, f"{item['table_id']} mapped row within replacement", results)
            for cell_id in mapping["source_cell_ids"]:
                cell = inventory[cell_id]
                if cell["text"]:
                    check(cell["text"] in row_text,
                          f"{item['table_id']} {cell_id} appears in its output row", results)
        for mapping in item["source_value_to_output"]:
            x, y = mapping["derivative_unicode_span"]
            field = derivative[x:y]
            check(mapping["formatted_value"] in field and "Column " in field,
                  f"{item['table_id']} mapped value field text", results)
            for cell_id in mapping["source_value_cell_ids"] + mapping["header_source_cell_ids"]:
                check(cell_id in inventory, f"{item['table_id']} mapped value/header source cell", results)
        check("20242025" not in new, f"{item['table_id']} adjacent year labels separated", results)
        cursor_old, cursor_new = b, d
    check(original[cursor_old:] == derivative[cursor_new:], "Final non-table text unchanged", results)
    for note in provenance["adjacent_notes_outside_tables"]:
        a, b = note["parent_section_unicode_span"]
        c, d = note["derivative_unicode_span"]
        check(original[a:b] == derivative[c:d] and
              digest(derivative[c:d].encode("utf-8")) == note["text_sha256"],
              f"{note['table_id']} adjacent note {note['label']} unchanged", results)
    with (base / "amzn_table_content_verification.csv").open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    check([row["table_id"] for row in rows] == [x["table_id"] for x in provenance["replacements"]],
          "CSV table order matches provenance", results)
    for row, item in zip(rows, provenance["replacements"]):
        check(int(row["mapped_values"]) == len(item["source_value_to_output"]) and
              row["new_text_sha256"] == item["derivative_table_text_sha256"],
              f"{item['table_id']} CSV counts and hashes match", results)
    page = (base / "amzn_table_serialization_comparison.html").read_text(encoding="utf-8")
    parsed = PageAudit()
    parsed.feed(page)
    parsed.close()
    check(parsed.section_ids == [x["table_id"] for x in provenance["replacements"]],
          "Static comparison page has all six source-bound sections", results)
    check(parsed.table_count == 6 and parsed.scripts == 0 and not parsed.external_resources,
          "Static comparison page has six source grids and no active or external resource", results)
    check(provenance["derivative_sha256"] in page and
          provenance["parent_inputs"]["item2_candidate.txt"]["sha256"] in page,
          "Static comparison page labels both exact input versions", results)
    for length, group in coverage["chunks"].items():
        chunks = group["rows"]
        check(chunks[0]["token_span"][0] == 0 and
              chunks[-1]["token_span"][1] == coverage["complete_derivative_body_tokens"] and
              all(a["token_span"][1] == b["token_span"][0] for a, b in zip(chunks, chunks[1:])),
              f"{length} body-token chunks contiguous and complete", results)
        check(all(x["model_input_tokens_including_specials"] <= coverage["model_input_limit"]
                  for x in chunks), f"{length} model input limit", results)
        for item in chunks:
            a, b = item["source_unicode_span"]
            check(digest(derivative[a:b].encode("utf-8")) == item["source_span_sha256"],
                  f"{length} chunk {item['chunk']} source hash", results)
    receipt = {"kind": "AMZN_TABLE_SERIALIZATION_SAME_AGENT_READBACK",
               "status": "PASS", "independent_audit": False,
               "checks": len(results), "passed": sum(x["pass"] for x in results),
               "failed": [x for x in results if not x["pass"]],
               "source_hashes": {"original_section": digest(original.encode("utf-8")),
                                 "source_html": digest(source.encode("utf-8")),
                                 "raw_submission": digest(raw)},
               "derivative_sha256": digest(derivative.encode("utf-8")),
               "static_comparison_page": {"sections": parsed.section_ids,
                                          "source_table_elements": parsed.table_count,
                                          "scripts": parsed.scripts,
                                          "external_resources": parsed.external_resources,
                                          "browser_rendering": "UNVERIFIED_FILE_URL_BLOCKED_BY_BROWSER_POLICY"},
               "checks_detail": results}
    (base / "same_agent_readback.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n",
                                                  encoding="utf-8")
    print(json.dumps({"status": receipt["status"], "checks": receipt["checks"],
                      "tables": len(provenance["replacements"])}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
