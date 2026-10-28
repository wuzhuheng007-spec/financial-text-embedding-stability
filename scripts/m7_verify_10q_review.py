"""Read-only static verification of the frozen local 10-Q review packet.

This checks hashes, coordinate identities, table spans and initial human-review
state. It cannot prove browser layout or substitute for a human boundary review.
"""
from __future__ import annotations

import argparse
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def require(ok: bool, message: str) -> None:
    if not ok:
        raise ValueError(message)


class PageTags(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tags: list[tuple[str, dict[str, str | None]]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append((tag, dict(attrs)))


def verify(source: Path, packet: Path) -> dict:
    receipt = json.loads((packet / "receipt.json").read_text(encoding="utf-8"))
    require(receipt["status"] == "PENDING_HUMAN_REVIEW" and
            all(receipt[k] is None for k in ("human_reviewer", "human_review_time", "human_decision")),
            "Packet falsely records a human decision")
    for row in receipt["outputs"]:
        path = packet / row["path"]
        require(path.is_file() and path.stat().st_size == row["bytes"] and
                digest(path.read_bytes()) == row["sha256"], f"Packet output changed: {path}")
    for row in receipt["input_files_rehashed"]:
        path = Path(row["path"])
        require(path.is_file() and path.stat().st_size == row["bytes"] and
                digest(path.read_bytes()) == row["sha256"], f"Frozen input changed: {path}")
    index = json.loads((packet / "table_review_index.json").read_text(encoding="utf-8"))
    coords = index["coordinates"]
    require(index["status"] == "PENDING_HUMAN_REVIEW" and
            index["human_decision"] is None and index["human_reviewer"] is None,
            "Machine-readable index falsely records human review")
    raw = (source / "original_submission.txt").read_bytes()
    html = (source / "primary_document_source.html").read_bytes()
    primary = (source / "primary_text.txt").read_text(encoding="utf-8")
    section = (source / "mda_section_program_candidate.txt").read_text(encoding="utf-8")
    ra, rb = coords["complete_submission_primary_html_byte_span"]
    require(raw[ra:rb] == html, "Primary HTML not exact raw submission bytes")
    a, b = coords["normalized_primary_text_unicode_char_span"]
    require(primary[a:b] == section and [len(primary[:a].encode()), len(primary[:b].encode())] ==
            coords["normalized_primary_text_utf8_byte_span"],
            "Rendered character/UTF-8 byte coordinate mismatch")
    require(primary[b:].startswith("Item 3. Quantitative"), "Boundary leaked into Item 3")
    tables = index["tables"]
    require(len(tables) == 6 and [t["table_id"] for t in tables] ==
            [f"MDA-{i}" for i in range(1, 7)], "Expected six ordered MD&A tables")
    for table in tables:
        x, y = table["complete_submission_byte_start"], table["complete_submission_byte_end_exclusive"]
        c, d = table["flattened_section_char_start"], table["flattened_section_char_end_exclusive"]
        require(digest(raw[x:y]) == table["raw_html_fragment_sha256"] and
                digest(section[c:d].encode("utf-8")) == table["flattened_text_sha256"] and
                table["review_status"] == "NEEDS_HUMAN_TABLE_REVIEW",
                f"Table source or flattened span differs: {table['table_id']}")
    page_bytes = (packet / "index.html").read_bytes()
    form_bytes = (packet / "review_form.html").read_bytes()
    page, form = page_bytes.decode("utf-8"), form_bytes.decode("utf-8")
    main_tags, form_tags = PageTags(), PageTags()
    main_tags.feed(page)
    form_tags.feed(form)
    source_grid_count = sum(tag == "table" and "source-grid" in (attrs.get("class") or "")
                            for tag, attrs in main_tags.tags)
    require(source_grid_count == 6, "HTML page does not show six source tables")
    require(not any(tag in {"script", "iframe", "img", "link", "object"}
                    for tag, _attrs in main_tags.tags) and len(page_bytes) < 100_000 and
            "<SEC-DOCUMENT>" not in page, "Page contains active/unbounded source content")
    require(sum(tag == "script" for tag, _attrs in form_tags.tags) == 1 and
            "fetch(" not in form and "XMLHttpRequest" not in form,
            "Form has unexpected active or network behavior")
    input_values = {attrs.get("name"): attrs.get("value") for tag, attrs in form_tags.tags
                    if tag == "input"}
    require(not input_values.get("reviewer") and not input_values.get("review_time"),
            "Reviewer or time prefilled")
    require("<option value='' selected>— 尚未决定 —</option>" in form and
            "PENDING_HUMAN_REVIEW" in form,
            "Form decision is preselected or pending label missing")
    hrefs = [attrs.get("href") for tag, attrs in main_tags.tags if tag == "a"]
    allowed = {"review_form.html", "#identity", "#start", "#end", "#tables", "#anomalies",
               "https://www.sec.gov/Archives/edgar/data/320193/000032019325000008/0000320193-25-000008-index.html",
               "https://www.sec.gov/Archives/edgar/data/320193/000032019325000008/aapl-20241228.htm"}
    require(set(hrefs).issubset(allowed) and
            not any(attrs.get("src") for _tag, attrs in main_tags.tags),
            "Page has an unreviewed link or active source asset")
    return {"kind": "M7_REVIEW_PACKET_STATIC_RECHECK", "status": "PASS_STATIC_ONLY",
            "receipt_sha256": digest((packet / "receipt.json").read_bytes()),
            "source_raw_sha256": digest(raw), "source_html_sha256": digest(html),
            "source_primary_sha256": digest(primary.encode("utf-8")),
            "source_section_sha256": digest(section.encode("utf-8")),
            "input_files_rehashed": len(receipt["input_files_rehashed"]),
            "output_files_rehashed": len(receipt["outputs"]),
            "table_source_and_flattened_spans_rehashed": len(tables),
            "source_grid_previews": source_grid_count,
            "normalized_primary_text_unicode_char_span": [a, b],
            "html_anchor_byte_offsets_separate": [coords["primary_html_item2_heading_byte_start"],
                                                   coords["primary_html_item3_heading_byte_start"]],
            "form_reviewer_and_time_blank": True, "form_decision_blank": True,
            "human_review": "PENDING_HUMAN_REVIEW",
            "browser_render_confirmed": False,
            "audit_label": "same-agent static self-check; not independent or human review"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--packet", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    require(not args.output.exists(), "Audit output exists; preserve and choose new path")
    result = verify(args.source, args.packet)
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n",
                           encoding="utf-8")
    print(json.dumps({"status": result["status"], "output": str(args.output)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
