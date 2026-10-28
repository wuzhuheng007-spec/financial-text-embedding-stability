"""Build a local, pending-human-review packet from one frozen AAPL 10-Q.

The packet is a bounded visual aid, not a new extraction or human attestation.
Source HTML is untrusted: only escaped text or attribute-stripped safe markup is
put in the local pages. No SEC request is made by this command.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html import escape
from html.parser import HTMLParser
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import quote

from finembed.extract import _Text, primary_text
from finembed.qpilot import _primary_source
from finembed.sec import raw_filer_cik, raw_header


EXPECTED_ACCESSION = "0000320193-25-000008"
EXPECTED_CIK = "0000320193"
SAFE_TAGS = {"div", "p", "span", "b", "strong", "i", "em", "u", "sup", "sub",
             "br", "table", "tr", "th", "td", "tbody", "thead", "tfoot", "ul", "ol", "li"}
VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link",
             "meta", "param", "source", "track", "wbr"}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def need(ok: bool, message: str) -> None:
    if not ok:
        raise ValueError(message)


def clean_space(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace("\xa0", " ")).strip()


def source_rendered(fragment: str) -> str:
    """Apply the saved pipeline's HTML text conversion to a bounded fragment."""
    parser = _Text()
    parser.feed(fragment)
    parser.close()
    value = "".join(parser.parts).replace("\xa0", " ").replace("\r", "")
    value = re.sub(r"[\t ]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    return re.sub(r"\n{3,}", "\n\n", value).strip()


@dataclass
class Cell:
    tag: str
    text_parts: list[str] = field(default_factory=list)
    colspan: int = 1
    rowspan: int = 1

    def as_dict(self) -> dict:
        return {"tag": self.tag, "text": clean_space("".join(self.text_parts)),
                "colspan": self.colspan, "rowspan": self.rowspan}


class FilingHTML(HTMLParser):
    """Locate direct-body blocks and tables in the exact saved HTML string."""

    def __init__(self, source: str):
        super().__init__(convert_charrefs=True)
        self.source = source
        self.line_starts = [0]
        for match in re.finditer("\n", source):
            self.line_starts.append(match.end())
        self.stack: list[str] = []
        self.blocks: list[dict] = []
        self.active: dict | None = None
        self.active_depth: int | None = None
        self.tables: list[dict] = []
        self.table: dict | None = None
        self.row: list[dict] | None = None
        self.cell: Cell | None = None
        self.suppressed = 0

    def source_offset(self) -> int:
        line, col = self.getpos()
        return self.line_starts[line - 1] + col

    def tag_end(self) -> int:
        end = self.source.find(">", self.source_offset())
        need(end >= 0, "Unclosed source HTML tag")
        return end + 1

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        parent = self.stack[-1] if self.stack else None
        if tag in {"script", "style"}:
            self.suppressed += 1
        if tag == "div" and parent == "body":
            need(self.active is None, "Overlapping direct-body HTML blocks")
            self.active = {"index": len(self.blocks), "start_char_in_html": self.source_offset(),
                           "end_char_exclusive_in_html": None, "text_parts": []}
            self.active_depth = len(self.stack) + 1
        if tag == "table" and self.active is not None:
            need(self.table is None, "Nested HTML tables need separate review")
            self.table = {"index": len(self.tables) + 1,
                          "body_block_index": self.active["index"],
                          "start_char_in_html": self.source_offset(),
                          "end_char_exclusive_in_html": None, "rows": []}
        if self.table is not None:
            if tag == "tr":
                need(self.row is None, "Nested HTML table rows")
                self.row = []
            if tag in {"td", "th"}:
                need(self.cell is None and self.row is not None, "HTML table cell outside row")
                attrs_map = dict(attrs)
                def span(key: str) -> int:
                    raw = attrs_map.get(key)
                    return int(raw) if raw and raw.isdigit() and 1 <= int(raw) <= 50 else 1
                self.cell = Cell(tag=tag, colspan=span("colspan"), rowspan=span("rowspan"))
        if tag not in VOID_TAGS:
            self.stack.append(tag)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in VOID_TAGS:
            self.handle_endtag(tag)

    def handle_data(self, data: str) -> None:
        if self.suppressed:
            return
        if self.active is not None:
            self.active["text_parts"].append(data)
        if self.cell is not None:
            self.cell.text_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"} and self.suppressed:
            self.suppressed -= 1
        if tag in {"td", "th"} and self.cell is not None:
            need(self.row is not None, "Table cell has no row")
            self.row.append(self.cell.as_dict())
            self.cell = None
        if tag == "tr" and self.row is not None and self.table is not None:
            self.table["rows"].append(self.row)
            self.row = None
        if tag == "table" and self.table is not None:
            need(self.row is None and self.cell is None, "Unclosed table row/cell")
            self.table["end_char_exclusive_in_html"] = self.tag_end()
            self.tables.append(self.table)
            self.table = None
        if (tag == "div" and self.active is not None and self.active_depth == len(self.stack)
                and self.stack and self.stack[-1] == "div"):
            self.active["end_char_exclusive_in_html"] = self.tag_end()
            self.active["text"] = clean_space("".join(self.active.pop("text_parts")))
            self.blocks.append(self.active)
            self.active = None
            self.active_depth = None
        if tag in self.stack:
            while self.stack:
                popped = self.stack.pop()
                if popped == tag:
                    break


class SafePreview(HTMLParser):
    """Strip source attributes/active content; retain only visual text/tables."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.open_tags: list[str] = []
        self.suppressed = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "iframe", "object", "svg"}:
            self.suppressed += 1
        if self.suppressed:
            return
        if tag in SAFE_TAGS:
            if tag == "br":
                self.out.append("<br>")
                return
            safe_attrs = ""
            if tag in {"td", "th"}:
                for key, val in attrs:
                    if key in {"rowspan", "colspan"} and val and val.isdigit() and 1 <= int(val) <= 50:
                        safe_attrs += f' {key}="{int(val)}"'
            self.out.append(f"<{tag}{safe_attrs}>")
            self.open_tags.append(tag)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag != "br":
            self.handle_endtag(tag)

    def handle_data(self, data: str) -> None:
        if not self.suppressed:
            self.out.append(escape(data))

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "iframe", "object", "svg"} and self.suppressed:
            self.suppressed -= 1
            return
        if self.suppressed or tag not in SAFE_TAGS or tag == "br":
            return
        if tag in self.open_tags:
            while self.open_tags:
                popped = self.open_tags.pop()
                self.out.append(f"</{popped}>")
                if popped == tag:
                    break


def safe_preview(fragment: str) -> str:
    parser = SafePreview()
    parser.feed(fragment)
    parser.close()
    while parser.open_tags:
        parser.out.append(f"</{parser.open_tags.pop()}>")
    return "".join(parser.out)


def html_byte_offset(text: str, char_offset: int) -> int:
    return len(text[:char_offset].encode("utf-8"))


def load_and_audit(source: Path) -> dict:
    receipt = json.loads((source / "receipt.json").read_text(encoding="utf-8"))
    boundary = json.loads((source / "boundary_evidence.json").read_text(encoding="utf-8"))
    selection = json.loads((source / "selection_ledger.json").read_text(encoding="utf-8"))
    need(receipt["status"] == "DONE_BOUNDARY_PROGRAM_PASS_HUMAN_PENDING" and
         boundary["program_check"] == "PASS_EXACT_SUBSTRING_AND_UNIQUE_SUBSTANTIVE_CANDIDATE" and
         boundary["human_review"] == "PENDING", "Source boundary is not program-pass/human-pending")
    paths = ["original_submission.txt", "primary_document_source.html", "primary_text.txt",
             "mda_section_program_candidate.txt", "boundary_evidence.json", "selection_ledger.json"]
    verified = []
    for name in paths:
        path = source / name
        data = path.read_bytes()
        specification = receipt["outputs"][name]
        need(len(data) == specification["bytes"] and sha(data) == specification["sha256"],
             f"Frozen source receipt hash mismatch: {name}")
        verified.append({"path": str(path), "bytes": len(data), "sha256": sha(data)})
    raw = (source / paths[0]).read_bytes()
    html_bytes = (source / paths[1]).read_bytes()
    primary_bytes = (source / paths[2]).read_bytes()
    section_bytes = (source / paths[3]).read_bytes()
    need(sha(raw) == boundary["raw_submission_sha256"] and
         sha(html_bytes) == boundary["primary_source_html"]["sha256"] and
         sha(primary_bytes) == boundary["primary_text_sha256"], "Raw/HTML/primary hash mismatch")
    html_location = boundary["primary_source_html"]
    need(raw[html_location["raw_byte_start"]:html_location["raw_byte_end_exclusive"]] ==
         html_bytes and html_location["raw_byte_end_exclusive"] -
         html_location["raw_byte_start"] == len(html_bytes), "Primary HTML not exact raw byte span")
    header = raw_header(raw, EXPECTED_ACCESSION, "10-Q")
    need(raw_filer_cik(raw) == EXPECTED_CIK, "Raw filer CIK differs")
    chosen = selection["selected"]["submission_row"]
    need(chosen["accessionNumber"] == EXPECTED_ACCESSION and chosen["form"] == "10-Q" and
         selection["cik"] == EXPECTED_CIK, "Selection ledger identity differs")
    need(header["filing_date_raw_header"] == chosen["filingDate"],
         "Raw filing date differs from official submissions row")
    raw_primary, primary_meta = _primary_source(raw)
    need(raw_primary == html_bytes and
         primary_meta["filename_raw_header"] == chosen["primaryDocument"],
         "Raw primary filename or bytes differ from official submissions row")
    primary, _ = primary_text(raw, "10-Q")
    need(primary.encode("utf-8") == primary_bytes, "Primary text cannot be regenerated")
    index = boundary["selected_candidate_index"]
    need(index is not None and boundary["substantive_candidate_count"] == 1,
         "Expected one selected substantive candidate")
    candidate = boundary["candidates"][index]
    start, end = candidate["start_char"], candidate["end_char_exclusive"]
    need(0 <= start < end <= len(primary) and primary[start:end].encode("utf-8") == section_bytes
         and sha(section_bytes) == candidate["content_sha256"], "Selected section span/hash differs")
    need(boundary["candidates"][0]["classification"] == "TOC_OR_SHORT_CANDIDATE" and
         boundary["candidates"][0]["end_char_exclusive"] < start,
         "Saved contents-page candidate is not separated")
    return {"receipt": receipt, "boundary": boundary, "selection": selection,
            "raw": raw, "html": html_bytes.decode("utf-8", errors="strict"),
            "primary": primary, "section": section_bytes.decode("utf-8", errors="strict"),
            "verified_inputs": verified, "header": header, "start": start, "end": end,
            "html_start_in_raw": html_location["raw_byte_start"]}


def table_record(source_html: str, section: str, table: dict,
                 html_start_in_raw: int, ordinal: int) -> dict:
    a, b = table["start_char_in_html"], table["end_char_exclusive_in_html"]
    need(a is not None and b is not None and source_html[a:a + 6].lower() == "<table" and
         source_html[b - 8:b].lower() == "</table>", "HTML table boundaries are not exact")
    raw_fragment = source_html[a:b]
    flat = source_rendered(raw_fragment)
    offset = section.find(flat)
    need(offset >= 0 and section.find(flat, offset + 1) < 0, "Table flat text not unique in section")
    rows = table["rows"]
    need(rows and all(row for row in rows), "Empty HTML table row")
    context = section[max(0, offset - 330):offset]
    grid = "".join("<tr>" + "".join(
        f'<{cell["tag"]} rowspan="{cell["rowspan"]}" colspan="{cell["colspan"]}">{escape(cell["text"])}</{cell["tag"]}>'
        for cell in row) + "</tr>" for row in rows)
    indicators = {"date_strings": sorted(set(re.findall(r"(?:December|January|February|March|April|May|June|July|August|September|October|November)\s+\d{1,2},?\s+\d{4}", flat))),
                  "currency_marker_count": flat.count("$"), "percent_marker_count": flat.count("%"),
                  "parenthesized_numeric_count": len(re.findall(r"\(\s*\d[\d,.]*\s*\)", flat)),
                  "sup_tag_count_in_html": len(re.findall(r"<sup\b", raw_fragment, re.I)),
                  "unit_context": clean_space(context[-250:]),
                  "possible_flattened_cell_join_count": len(re.findall(r"[A-Za-z]\$\d|\d{4}(?=[A-Za-z])|[A-Za-z](?=\d)", flat))}
    html_byte_a, html_byte_b = html_byte_offset(source_html, a), html_byte_offset(source_html, b)
    return {"table_id": f"MDA-{ordinal}", "source_table_index": table["index"],
            "body_block_index": table["body_block_index"],
            "html_source_char_start": a, "html_source_char_end_exclusive": b,
            "html_source_byte_start": html_byte_a, "html_source_byte_end_exclusive": html_byte_b,
            "complete_submission_byte_start": html_start_in_raw + html_byte_a,
            "complete_submission_byte_end_exclusive": html_start_in_raw + html_byte_b,
            "raw_html_fragment_sha256": sha(raw_fragment.encode("utf-8")),
            "flattened_section_char_start": offset,
            "flattened_section_char_end_exclusive": offset + len(flat),
            "flattened_text_sha256": sha(flat.encode("utf-8")),
            "row_count": len(rows), "max_source_cells_in_row": max(len(row) for row in rows),
            "source_cells_with_spans": sum(cell["colspan"] > 1 or cell["rowspan"] > 1
                                           for row in rows for cell in row),
            "source_grid_html": '<table class="source-grid">' + grid + "</table>",
            "flattened_text": flat, "indicators": indicators,
            "review_status": "NEEDS_HUMAN_TABLE_REVIEW",
            "review_reason": "HTML source has row/column structure; normalized primary text does not preserve cell boundaries or all visual relationships."}


STYLE = """
body{font:15px/1.55 system-ui,Segoe UI,Arial,sans-serif;max-width:1400px;margin:auto;padding:20px;color:#17233b;background:#f6f8fb}
h1,h2,h3{line-height:1.22}h1{font-size:27px}h2{margin-top:36px;border-bottom:2px solid #d5dce8;padding-bottom:5px}
a{color:#174d9c}.warn{border-left:5px solid #b45309;background:#fff5e5;padding:12px;margin:12px 0}
.ok{border-left:5px solid #18794e;background:#e8f7ef;padding:12px}.card{background:#fff;border:1px solid #d9e0ea;border-radius:8px;padding:16px;margin:14px 0}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(310px,1fr));gap:12px}.panel{border:1px solid #d9e0ea;background:#fff;border-radius:8px;padding:12px;min-width:0}
pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f1f4fa;padding:10px;border-radius:6px;max-height:480px;overflow:auto}
.source-preview{background:white;border:1px solid #cbd5e1;padding:14px;max-height:420px;overflow:auto}
.source-preview div{margin:7px 0}.source-grid{border-collapse:collapse;width:100%;font-size:13px}.source-grid td,.source-grid th{border:1px solid #9ba8ba;padding:5px;text-align:left;vertical-align:top}
.source-grid tr:first-child{background:#eaf0f8;font-weight:600}.table-scroll{overflow:auto;max-height:520px}
table.meta{border-collapse:collapse;width:100%}.meta th,.meta td{border-bottom:1px solid #d9e0ea;padding:6px;text-align:left;vertical-align:top}
code{word-break:break-all}small{color:#52627b}.tag{display:inline-block;background:#fff0d3;color:#7c4500;padding:2px 7px;border-radius:4px;font-size:12px}
label{display:block;margin:14px 0;font-weight:600}input,select,textarea{display:block;width:100%;box-sizing:border-box;padding:8px;font:inherit;margin-top:4px;border:1px solid #9ba8ba;border-radius:4px}
textarea{min-height:85px}button{padding:10px 14px;margin:8px 7px 8px 0;border:0;border-radius:4px;background:#174d9c;color:white;cursor:pointer}
@media print{body{background:white;max-width:none}.card,.panel{break-inside:avoid}pre,.table-scroll,.source-preview{max-height:none;overflow:visible}nav,button{display:none}}
"""


def block_fragment(html: str, block: dict) -> str:
    return html[block["start_char_in_html"]:block["end_char_exclusive_in_html"]]


def build_packet(source: Path, output: Path) -> dict:
    need(not output.exists(), "Review output directory already exists; choose a new path")
    data = load_and_audit(source)
    parsed = FilingHTML(data["html"])
    parsed.feed(data["html"])
    parsed.close()
    need(parsed.active is None and parsed.table is None, "Unclosed source DOM block/table")
    start_blocks = [b for b in parsed.blocks if re.match(
        r"^Item\s+2\.\s*Management.s Discussion and Analysis", b["text"], re.I)]
    end_blocks = [b for b in parsed.blocks if re.match(
        r"^Item\s+3\.\s*Quantitative and Qualitative Disclosures About Market Risk", b["text"], re.I)]
    need(len(start_blocks) == len(end_blocks) == 1 and
         start_blocks[0]["index"] < end_blocks[0]["index"],
         "Unique body HTML Item 2/Item 3 heading blocks not found")
    start_block, end_block = start_blocks[0], end_blocks[0]
    source_tables = [t for t in parsed.tables if
                     start_block["index"] <= t["body_block_index"] < end_block["index"]]
    tables = [table_record(data["html"], data["section"], t,
                           data["html_start_in_raw"], ordinal)
              for ordinal, t in enumerate(source_tables, 1)]
    need(tables, "No source MD&A table was found; require review")
    need(all(data["raw"][t["complete_submission_byte_start"]:t["complete_submission_byte_end_exclusive"]] ==
             data["html"].encode("utf-8")[t["html_source_byte_start"]:t["html_source_byte_end_exclusive"]]
             for t in tables), "Table HTML byte mapping does not match complete submission")
    end_title = data["primary"][data["end"]:data["end"] + 140].splitlines()[0]
    need(re.match(r"Item\s+3\.\s*Quantitative", end_title, re.I),
         "Rendered primary next heading is not Item 3")
    previous_headings = list(re.finditer(r"(?im)^Item\s+1\.\s*Financial Statements\s*$",
                                        data["primary"][:data["start"]]))
    prior_heading = previous_headings[-1].group(0) if previous_headings else "NOT_LOCATED"
    source_html = data["html"]
    start_heading_byte = html_byte_offset(source_html, start_block["start_char_in_html"])
    end_heading_byte = html_byte_offset(source_html, end_block["start_char_in_html"])
    source_identity = {"ticker": "AAPL", "company": "Apple Inc.", "cik": EXPECTED_CIK,
                       "accession": EXPECTED_ACCESSION, "form": "10-Q",
                       "report_period_end": data["selection"]["selected"]["submission_row"]["reportDate"],
                       "filing_date": data["selection"]["selected"]["submission_row"]["filingDate"],
                       "primary_document": data["selection"]["selected"]["submission_row"]["primaryDocument"],
                       "raw_sha256": sha(data["raw"]),
                       "html_sha256": sha(data["html"].encode("utf-8")),
                       "primary_text_sha256": sha(data["primary"].encode("utf-8")),
                       "section_sha256": sha(data["section"].encode("utf-8")),
                       "program_boundary": "PASS", "human_review": "PENDING_HUMAN_REVIEW"}
    coordinates = {"complete_submission_primary_html_byte_span": [data["html_start_in_raw"],
                       data["html_start_in_raw"] + len(data["html"].encode("utf-8"))],
                   "primary_html_item2_heading_byte_start": start_heading_byte,
                   "primary_html_item3_heading_byte_start": end_heading_byte,
                   "complete_submission_item2_heading_byte_start": data["html_start_in_raw"] + start_heading_byte,
                   "complete_submission_item3_heading_byte_start": data["html_start_in_raw"] + end_heading_byte,
                   "normalized_primary_text_unicode_char_span": [data["start"], data["end"]],
                   "normalized_primary_text_utf8_byte_span": [
                       len(data["primary"][:data["start"]].encode("utf-8")),
                       len(data["primary"][:data["end"]].encode("utf-8"))],
                   "coordinate_warning": "[35413,51404) indexes normalized primary_text Python Unicode characters, not HTML source characters or SEC response bytes. HTML heading offsets are separate DOM tag anchors, not an extracted HTML byte-equivalent section."}
    page_numbers = sorted(set(re.findall(r"Apple Inc\.\s*\|\s*Q1 2025 Form 10-Q\s*\|\s*(\d+)",
                                          data["section"])), key=int)
    refs = list(re.finditer(r"(?i)\b(?:2024\s+Form\s+10-K|Form\s+10-K|Part\s+[IVX]+,?\s+Item\s+\d+[A-Z]?)\b",
                            data["section"]))
    anomalies = [
        {"type": "REPEATED_HEADING", "evidence": "Two Item 2 and two Item 3 headings in rendered primary text, including contents page; short candidate explicitly excluded.", "status": "PROGRAM_EXPLAINED_HUMAN_CONFIRM"},
        {"type": "PAGE_BREAK_OR_FOOTER", "evidence": f"MD&A includes source page/footer markers {', '.join(page_numbers) if page_numbers else 'none detected'}; confirm no paragraph/table is split or omitted.", "status": "NEEDS_HUMAN_REVIEW"},
        {"type": "CROSS_REFERENCE", "evidence": f"{len(refs)} regex matches for annual-form or Part/Item references in selected text; linked documents are not part of this MD&A extraction.", "status": "NEEDS_HUMAN_REVIEW"},
        {"type": "TABLE_LAYOUT", "evidence": f"{len(tables)} source HTML tables in MD&A; normalized text removes cell boundaries, including dates/units/%/negative formatting.", "status": "NEEDS_HUMAN_REVIEW"},
        {"type": "UNUSUAL_CHARACTERS", "evidence": f"U+FFFD replacement characters in selected text: {data['section'].count(chr(0xfffd))}; control characters other than line/tab/CR: {sum(ord(c)<32 and c not in chr(10)+chr(9)+chr(13) for c in data['section'])}.", "status": "PROGRAM_CHECK_ONLY"},
        {"type": "MISSING_CONTENT_OR_BOUNDARY", "evidence": "Exact substring and adjacent Item 3 heading pass program checks; semantic completeness, table relationships and final boundary remain unapproved by a researcher.", "status": "PENDING_HUMAN_REVIEW"},
    ]
    official_index = ("https://www.sec.gov/Archives/edgar/data/320193/"
                      "000032019325000008/0000320193-25-000008-index.html")
    official_primary = ("https://www.sec.gov/Archives/edgar/data/320193/"
                        "000032019325000008/" + quote(source_identity["primary_document"]))
    toc = data["boundary"]["candidates"][0]
    intro_html = "".join(safe_preview(block_fragment(source_html, parsed.blocks[i]))
                         for i in range(start_block["index"], min(start_block["index"] + 3,
                                                                      end_block["index"])))
    last_content = [b for b in parsed.blocks[start_block["index"]:end_block["index"]]
                    if b["text"]]
    closing_html = "".join(safe_preview(block_fragment(source_html, b))
                           for b in last_content[-2:])
    next_html = "".join(safe_preview(block_fragment(source_html, parsed.blocks[i]))
                        for i in range(end_block["index"], min(end_block["index"] + 2,
                                                                  len(parsed.blocks))))
    parts = ["<!doctype html><html lang='zh'><head><meta charset='utf-8'>",
             "<meta name='viewport' content='width=device-width,initial-scale=1'>",
             "<meta http-equiv='Content-Security-Policy' content=\"default-src 'none'; style-src 'unsafe-inline'; img-src data:; connect-src 'none'\">",
             "<title>AAPL 10-Q MD&A 人工审阅包</title><style>", STYLE, "</style></head><body>",
             "<h1>AAPL 10-Q Part I Item 2 MD&amp;A：人工审阅包</h1>",
             "<p class='warn'><strong>状态：PENDING_HUMAN_REVIEW。</strong>下面的程序哈希和边界检查通过，不代表研究者已经认可章节或表格。请先看开头、结尾，再逐一检查六张表；在独立的 <a href='review_form.html'>可填写审阅表</a> 留下决定。</p>",
             "<nav><a href='#identity'>文件身份</a> · <a href='#start'>开头</a> · <a href='#end'>结尾</a> · <a href='#tables'>六张表</a> · <a href='#anomalies'>异常</a> · <a href='review_form.html'>填写决定</a></nav>",
             "<section id='identity'><h2>1. 文件身份与坐标</h2><div class='card'><table class='meta'>"]
    meta_rows = [
        ("公司 / CIK", "Apple Inc. (AAPL) / " + EXPECTED_CIK),
        ("Accession / 类型", EXPECTED_ACCESSION + " / 10-Q"),
        ("报告期末 / 申报日期", source_identity["report_period_end"] + " / " + source_identity["filing_date"]),
        ("SEC 官方来源", f"<a href='{escape(official_index, quote=True)}'>申报详情</a> · <a href='{escape(official_primary, quote=True)}'>主文档</a>"),
        ("完整申报 SHA-256", "<code>" + source_identity["raw_sha256"] + "</code>"),
        ("主文档 HTML SHA-256", "<code>" + source_identity["html_sha256"] + "</code>"),
        ("清理后主文档 SHA-256", "<code>" + source_identity["primary_text_sha256"] + "</code>"),
        ("Item 2 候选 SHA-256", "<code>" + source_identity["section_sha256"] + "</code>"),
    ]
    parts += ["<tr><th>字段</th><th>保存值</th></tr>"] + [
        "<tr><th>" + escape(key) + "</th><td>" + value + "</td></tr>"
        for key, value in meta_rows]
    parts += ["</table></div><div class='warn'><strong>不可混用的三套坐标：</strong><ul>",
              f"<li>完整 SEC 申报的原始字节：主 HTML 在 <code>{coordinates['complete_submission_primary_html_byte_span']}</code>。这是原始响应 byte offset。</li>",
              f"<li>主文档 HTML：Item 2 / Item 3 标题标签起点分别约在保存 HTML 的 byte <code>{start_heading_byte}</code> / <code>{end_heading_byte}</code>，由 DOM 标签位置定位；不是清理文本的章节 span。</li>",
              f"<li>清理后 <code>primary_text.txt</code>：Item 2 的精确 Python Unicode 字符区间为 <code>[{data['start']},{data['end']})</code>；同文件的 UTF-8 byte 区间为 <code>{coordinates['normalized_primary_text_utf8_byte_span']}</code>。这两个值不能当作原始 HTML 位置。</li>",
              "</ul><small>HTML 标签锚点仅帮助打开源代码定位；章节内容的哈希与位置以清理后主文档为准。</small></div></section>"]
    parts += ["<section id='start'><h2>2. 开头对照</h2>",
              f"<p>上一个实际章节标题：<strong>{escape(prior_heading)}</strong>。程序排除的目录候选：清理文本字符 <code>[{toc['start_char']},{toc['end_char_exclusive']})</code>，{toc['characters']} 字符，SHA-256 <code>{toc['content_sha256']}</code>。</p>",
              "<details class='card'><summary>查看被排除的目录条目</summary><pre>",
              escape(data["primary"][toc["start_char"]:toc["end_char_exclusive"]]),
              "</pre></details><div class='grid'>",
              "<div class='panel'><h3>原始 HTML 的安全可视预览</h3><small>从保存的原始 HTML 的 Item 2 标题标签和随后两个直接正文块生成；已移除源属性、脚本和外部资源。最终仍需核对官方 HTML。</small><div class='source-preview'>",
              intro_html, "</div></div>",
              "<div class='panel'><h3>清理后主文档：边界前后</h3><pre>",
              escape(data["primary"][max(0, data["start"] - 520):data["start"]]),
              "\n━━ ITEM 2 START ━━\n", escape(data["primary"][data["start"]:data["start"] + 1050]),
              "</pre></div><div class='panel'><h3>实际提取文本开头</h3><pre>",
              escape(data["section"][:1250]), "</pre></div></div></section>"]
    parts += ["<section id='end'><h2>3. 结尾对照</h2>",
              "<p class='warn'>程序边界正好止于清理后主文档的 <strong>Item 3. Quantitative and Qualitative Disclosures About Market Risk</strong> 标题之前。请人工判断最后一个 MD&amp;A 段落是否完整，以及 Item 3 的标题或正文是否漏入提取文本。</p><div class='grid'>",
              "<div class='panel'><h3>原始 HTML 的安全可视预览</h3><small>Item 2 最后两个非空直接正文块：</small><div class='source-preview'>",
              closing_html, "</div><small>随后的 Item 3 标题及第一正文块：</small><div class='source-preview'>",
              next_html, "</div></div>",
              "<div class='panel'><h3>清理后主文档：边界前后</h3><pre>",
              escape(data["primary"][data["end"] - 1150:data["end"]]),
              "\n━━ ITEM 2 END / ITEM 3 START ━━\n",
              escape(data["primary"][data["end"]:data["end"] + 900]),
              "</pre></div><div class='panel'><h3>实际提取文本结尾</h3><pre>",
              escape(data["section"][-1350:]), "</pre><small>提取文本的精确结尾不含 Item 3 标题；程序性 exact-substring 检查通过。</small></div></div></section>"]
    parts += ["<section id='tables'><h2>4. 六张 MD&amp;A 表逐一核对</h2>",
              "<p class='warn'>左侧保留原始 HTML 的行、列与合并单元格，且去除了任何源脚本和外部资源；右侧是与保存章节完全对应的压平文本。所有表的状态先设为 <strong>NEEDS_HUMAN_TABLE_REVIEW</strong>。请重点检查日期、单位、负数、百分号及脚注。</p>"]
    for t in tables:
        ind = t["indicators"]
        parts += ["<details class='card' open><summary><strong>", escape(t["table_id"]),
                  f"</strong>（主 HTML 表格 #{t['source_table_index']}）— 源表 {t['row_count']} 行；章节字符 [{t['flattened_section_char_start']},{t['flattened_section_char_end_exclusive']})；<span class='tag'>NEEDS_HUMAN_TABLE_REVIEW</span></summary>",
                  "<p><small>原始 HTML 片段：主 HTML byte ", str(t["html_source_byte_start"]), "–",
                  str(t["html_source_byte_end_exclusive"]), "；完整申报 byte ",
                  str(t["complete_submission_byte_start"]), "–",
                  str(t["complete_submission_byte_end_exclusive"]), "；片段 SHA-256 <code>",
                  t["raw_html_fragment_sha256"], "</code>。</small></p>",
                  "<div class='grid'><div class='panel'><h3>源 HTML 表格的安全网格预览</h3><div class='table-scroll'>",
                  t["source_grid_html"], "</div></div><div class='panel'><h3>管道压平后的精确文本</h3><pre>",
                  escape(t["flattened_text"]), "</pre></div></div>",
                  "<p><strong>提示：</strong>日期 ", escape(", ".join(ind["date_strings"]) or "未在表内检出"),
                  f"；$ 标记 {ind['currency_marker_count']}；% 标记 {ind['percent_marker_count']}；括号数字 {ind['parenthesized_numeric_count']}；原 HTML 上标 {ind['sup_tag_count_in_html']}；合并单元格 {t['source_cells_with_spans']}；可能被压平粘连的片段 {ind['possible_flattened_cell_join_count']}。这些是检查线索，不是合格判定。</p>",
                  "<p><strong>表前文字片段（可能包含上一张表；单位需人工判断）：</strong> ", escape(ind["unit_context"]), "</p>",
                  "<label>人工检查：行列、单位、日期、负数、百分号、脚注仍可理解吗？<textarea disabled placeholder='请到可填写审阅表逐表记录'></textarea></label></details>"]
    parts += ["</section><section id='anomalies'><h2>5. 异常及未解决问题</h2><table class='meta'><tr><th>类别</th><th>保存证据 / 需看内容</th><th>状态</th></tr>"]
    parts += ["<tr><td>" + escape(a["type"]) + "</td><td>" + escape(a["evidence"]) +
              "</td><td>" + escape(a["status"]) + "</td></tr>" for a in anomalies]
    parts += ["</table><p>如发现漏入、漏出或表格关系不可恢复，请在 <a href='review_form.html'>审阅表</a> 选择 REVISE 或 REJECT 并注明原始 HTML 对应位置。不能仅凭本页的程序状态填写 ACCEPT。</p></section>",
              "<p><small>本页只在本地生成；未重新下载 SEC 文件。审阅表初始状态 PENDING_HUMAN_REVIEW，审阅人、时间和结论均为空。</small></p></body></html>"]
    output.mkdir(parents=True)
    (output / "index.html").write_text("".join(parts), encoding="utf-8")
    make_form(output / "review_form.html", source_identity, tables)
    make_markdown_template(output / "review_form.md", source_identity, tables)
    (output / "table_review_index.json").write_text(json.dumps({
        "status": "PENDING_HUMAN_REVIEW", "source_identity": source_identity,
        "coordinates": coordinates, "previous_heading": prior_heading,
        "next_heading": end_title, "tables": [{k: v for k, v in t.items()
            if k not in {"source_grid_html", "flattened_text"}} for t in tables],
        "anomalies": anomalies, "human_reviewer": None, "human_review_time": None,
        "human_decision": None}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (output / "START_HERE.md").write_text(
        "# AAPL 10-Q MD&A review packet\n\n"
        "Open `index.html` locally. Check (1) the Item 1-to-Item 2 start, including the excluded TOC hit; "
        "(2) the final MD&A paragraph and the immediately following Item 3; and (3) all six source-grid/flattened-table pairs. "
        "Then fill `review_form.html` or `review_form.md` with reviewer, time, and ACCEPT/REVISE/REJECT. "
        "Until a person actually records that decision, the status is PENDING_HUMAN_REVIEW.\n\n"
        "Coordinates `[35413,51404)` refer to Python Unicode characters in the saved normalized "
        "`primary_text.txt`; they are not HTML or complete-submission byte offsets. See `table_review_index.json` "
        "for distinct HTML/SEC coordinates and hashes. The local page embeds only bounded, sanitized source excerpts.\n",
        encoding="utf-8")
    outputs = [{"path": p.name, "bytes": p.stat().st_size, "sha256": sha(p.read_bytes())}
               for p in sorted(output.iterdir()) if p.is_file()]
    receipt = {"kind": "M7_LOCAL_10Q_HUMAN_REVIEW_PACKET", "status": "PENDING_HUMAN_REVIEW",
               "generated_utc": datetime.now(timezone.utc).isoformat(),
               "source_run": str(source), "source_receipt_sha256": sha((source / "receipt.json").read_bytes()),
               "generator_sha256": sha(Path(__file__).read_bytes()),
               "input_files_rehashed": data["verified_inputs"], "outputs": outputs,
               "table_count": len(tables), "human_reviewer": None, "human_review_time": None,
               "human_decision": None, "network_request_during_build": False,
               "coordinate_warning": coordinates["coordinate_warning"]}
    (output / "receipt.json").write_text(json.dumps(receipt, indent=2, ensure_ascii=False) + "\n",
                                         encoding="utf-8")
    return receipt


def make_form(path: Path, identity: dict, tables: list[dict]) -> None:
    metadata = {k: identity[k] for k in ("company", "cik", "accession", "form", "report_period_end",
                                       "filing_date", "raw_sha256", "html_sha256",
                                       "primary_text_sha256", "section_sha256")}
    body = ["<!doctype html><html lang='zh'><head><meta charset='utf-8'>",
            "<meta http-equiv='Content-Security-Policy' content=\"default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'none'\">",
            "<title>AAPL MD&A 人工审阅表</title><style>", STYLE, "</style></head><body>",
            "<h1>研究者人工审阅表</h1><p class='warn'>当前状态：<strong>PENDING_HUMAN_REVIEW</strong>。此页没有预填审阅人、时间或决定。请先看 <a href='index.html'>对照页面</a>；本表只在本地导出 JSON，不向网络发送。</p>",
            "<div class='card'><h2>固定文件与章节版本</h2><table class='meta'>"]
    body += ["<tr><th>" + escape(k) + "</th><td><code>" + escape(str(v)) + "</code></td></tr>"
             for k, v in metadata.items()]
    body += ["</table></div><form id='review'><div class='card'><h2>开头、结尾与正文</h2>"]
    for key, label in (("start", "Item 2 开头正确且目录误匹配已排除"),
                       ("end", "Item 2 结尾完整且无 Item 3 内容漏入"),
                       ("body", "正文完整性及分页/交叉引用可接受")):
        body += [f"<label>{escape(label)}<select name='{key}'><option value='' selected>— 尚未判断 —</option>",
                 "<option value='YES'>YES</option><option value='NO'>NO</option>",
                 "<option value='UNCLEAR'>UNCLEAR</option></select></label>"]
    body += ["</div><div class='card'><h2>六张表：逐表判断</h2>"]
    for t in tables:
        body += [f"<label>{escape(t['table_id'])} 行列/单位/日期/负数/%/脚注可读性",
                 f"<select name='{escape(t['table_id'])}'><option value='' selected>— 尚未判断 —</option>",
                 "<option value='YES'>YES</option><option value='NO'>NO</option>",
                 "<option value='UNCLEAR'>UNCLEAR</option></select></label>"]
    body += ["</div><div class='card'><h2>异常与处理意见</h2>",
             "<label>发现的异常、原始 HTML 或清理文本位置、建议处理<textarea name='issues'></textarea></label>",
             "<label>审阅人（留空，待本人填写）<input name='reviewer' autocomplete='off'></label>",
             "<label>审阅时间（留空，待本人填写）<input name='review_time' type='datetime-local'></label>",
             "<label>总决定<select name='decision'><option value='' selected>— 尚未决定 —</option>",
             "<option value='ACCEPT'>ACCEPT</option><option value='REVISE'>REVISE</option>",
             "<option value='REJECT'>REJECT</option></select></label>",
             "<p><small>只有所有判断、审阅人、时间与总决定均填写后，导出的 JSON 才记为 HUMAN_DECISION_RECORDED；否则仍为 PENDING_HUMAN_REVIEW。该规则不替代审阅人签名或来源核验。</small></p>",
             "<button type='button' id='save'>下载本地 JSON</button><button type='button' onclick='print()'>打印</button>",
             "<p id='save-status' aria-live='polite'></p></div></form>",
             "<script>const fixed=", json.dumps(metadata, ensure_ascii=False).replace("<", "\\u003c"), ";",
             "document.getElementById('save').addEventListener('click',()=>{const f=document.getElementById('review');const fields=Object.fromEntries(new FormData(f).entries());",
             "const required=['start','end','body',",
             ",".join(json.dumps(t["table_id"]) for t in tables),
             ",'reviewer','review_time','decision'];const complete=required.every(k=>String(fields[k]||'').trim());",
             "const payload={file_and_section:fixed,fields,status:complete?'HUMAN_DECISION_RECORDED':'PENDING_HUMAN_REVIEW',exported_at_utc:new Date().toISOString()};",
             "const blob=new Blob([JSON.stringify(payload,null,2)],{type:'application/json'});const url=URL.createObjectURL(blob);",
             "const a=document.createElement('a');a.href=url;a.download='AAPL_10Q_MDA_human_review.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);",
             "document.getElementById('save-status').textContent=complete?'已导出填写的决定；请另行保存并提交给项目。':'已导出未完成草稿，状态保持 PENDING_HUMAN_REVIEW。';});</script>",
             "</body></html>"]
    path.write_text("".join(body), encoding="utf-8")


def make_markdown_template(path: Path, identity: dict, tables: list[dict]) -> None:
    content = ["# AAPL 10-Q Part I Item 2 MD&A — 人工审阅表\n\n",
               "状态：**PENDING_HUMAN_REVIEW**（尚无人类结论）\n\n",
               f"- 公司 / CIK：Apple Inc. / {EXPECTED_CIK}\n",
               f"- Accession / Form：{EXPECTED_ACCESSION} / 10-Q\n",
               f"- 报告期末 / 申报日期：{identity['report_period_end']} / {identity['filing_date']}\n",
               f"- 原始申报 SHA-256：`{identity['raw_sha256']}`\n",
               f"- 主 HTML SHA-256：`{identity['html_sha256']}`\n",
               f"- 清理主文档 SHA-256：`{identity['primary_text_sha256']}`\n",
               f"- Item 2 候选 SHA-256：`{identity['section_sha256']}`\n\n",
               "## 必填判断\n\n",
               "- 开头正确、目录误匹配排除：[ ] YES  [ ] NO  [ ] UNCLEAR\n",
               "- 结尾正确、无 Item 3 漏入：[ ] YES  [ ] NO  [ ] UNCLEAR\n",
               "- 正文完整、分页与交叉引用可接受：[ ] YES  [ ] NO  [ ] UNCLEAR\n"]
    content += [f"- {t['table_id']} 行列/单位/日期/负数/%/脚注可读：[ ] YES  [ ] NO  [ ] UNCLEAR\n"
                for t in tables]
    content += ["\n## 异常及处理意见\n\n（请填写原始 HTML 或清理主文档位置、所见问题与拟议处理。）\n\n",
                "## 审阅记录（留空，待本人填写）\n\n",
                "- 审阅人：\n- 审阅时间：\n- 决定：[ ] ACCEPT  [ ] REVISE  [ ] REJECT\n",
                "- 决定理由及下一步：\n"]
    path.write_text("".join(content), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True,
                        help="Frozen M7 tenq/pilot_v3 directory; read-only")
    parser.add_argument("--output", type=Path, required=True,
                        help="New output directory for review packet")
    args = parser.parse_args(argv)
    result = build_packet(args.source, args.output)
    print(json.dumps({"status": result["status"], "tables": result["table_count"],
                      "output": str(args.output)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
