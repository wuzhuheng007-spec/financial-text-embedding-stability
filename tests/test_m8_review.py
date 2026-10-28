"""Software fixtures only: M8 review packets are not researcher decisions."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import subprocess

import pytest

import scripts.m8_build_review as m8_review
from scripts.m8_build_review import build_overview, build_packet, make_form


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def fixture(tmp_path: Path, *, ambiguous: bool = False, hostile_grid: bool = False,
            heading_table: bool = False, adjacent_note: bool = False):
    ingest = tmp_path / "ingest" / "MSFT"
    extraction = tmp_path / "extraction" / "MSFT"
    ingest.mkdir(parents=True)
    extraction.mkdir(parents=True)
    item3 = ("<table><tr><td>Item 3. Quantitative and Qualitative Disclosures</td></tr></table>"
             if heading_table else "<div>Item 3. Quantitative and Qualitative Disclosures</div>")
    note = "Note 1: supplied quantities are estimates."
    note_html = f"<p>{note}</p>" if adjacent_note else ""
    html = ("<html><body><div>Item 2. Management’s Discussion and Analysis</div>"
            "<div>Revenue <table><tr><th>Year</th><th>USD</th></tr>"
            "<tr><td>2025</td><td>$2</td></tr></table></div>" + note_html + item3 + "</body></html>")
    html_bytes = html.encode("utf-8")
    raw_prefix = b"SEC synthetic header\n<TEXT>\n"
    raw = raw_prefix + html_bytes + b"\n</TEXT>"
    flat = "YearUSD\n\n2025$2"
    primary = ("Contents\nItem 2. Management’s Discussion and Analysis\n"
               "Item 1. Financial Statements\n"
               "Item 2. Management’s Discussion and Analysis\nRevenue " + flat + "\n"
               + (note + "\n" if adjacent_note else "") +
               "Item 3. Quantitative and Qualitative Disclosures")
    section_start = primary.index("Item 2.", primary.index("Item 1."))
    section_end = primary.index("Item 3.", section_start)
    section = primary[section_start:section_end]
    flat_start = section.index(flat)
    table_start = html_bytes.index(b"<table>")
    table_end = html_bytes.index(b"</table>") + len(b"</table>")
    table_raw = html_bytes[table_start:table_end]
    (ingest / "original_submission.txt").write_bytes(raw)
    (ingest / "primary_document_source.html").write_bytes(html_bytes)
    (ingest / "primary_text.txt").write_text(primary, encoding="utf-8")
    identity = {
        "status": "VERIFIED_RAW_AND_PRIMARY", "ticker": "MSFT", "company": "Synthetic Microsoft",
        "cik": "0000789019", "accession": "0000950170-25-010491", "form": "10-Q",
        "report_period_end": "2024-12-31", "filing_date": "2025-01-29",
        "primary_document": "msft-20241231.htm", "original_submission_url": "https://www.sec.gov/synthetic-raw",
        "primary_document_url": "https://www.sec.gov/synthetic-html",
        "primary_raw_byte_span": [len(raw_prefix), len(raw_prefix) + len(html_bytes)],
        "original_submission_sha256": digest(raw), "primary_document_sha256": digest(html_bytes),
        "primary_text_sha256": digest((ingest / "primary_text.txt").read_bytes()),
    }
    write_json(ingest / "source_identity.json", identity)
    (extraction / "primary_document_source.html").write_bytes(html_bytes)
    (extraction / "primary_text.txt").write_text(primary, encoding="utf-8")
    selected = None if ambiguous else {"start_char": section_start, "end_char_exclusive": section_end,
                                       "section_sha256": None}
    if not ambiguous:
        (extraction / "item2_candidate.txt").write_text(section, encoding="utf-8")
        selected["section_sha256"] = digest((extraction / "item2_candidate.txt").read_bytes())
    boundary = {
        "status": "NEEDS_REVIEW" if ambiguous else "PROGRAM_BOUNDARY_PASS",
        "selected_candidate_index": None if ambiguous else 1,
        "item2_heading_count": 2, "item3_heading_count": 1,
        "substantive_candidate_count": 2 if ambiguous else 1,
        "candidates": [
            {"start_char": 9, "end_char_exclusive": 58, "classification": "TOC", "heading": "Item 2."},
            {"start_char": section_start, "end_char_exclusive": section_end,
             "classification": "SUBSTANTIVE", "heading": "Item 2. Management’s Discussion and Analysis"}],
        "selected_span": selected,
        "coordinates": {"primary_text_unit": "Python Unicode characters in saved normalized primary_text.txt",
                        "primary_html_unit": "UTF-8 bytes in saved primary_document_source.html",
                        "complete_submission_unit": "bytes in saved original_submission.txt",
                        "primary_html_raw_byte_span": identity["primary_raw_byte_span"]},
        "html_heading_anchors": {"selected_item2": None if ambiguous else {"html_source_char_start": html.index("Item 2.", 10)},
                                 "selected_item3": None if ambiguous else {"html_source_char_start": html.index("Item 3.")}},
        "human_review": "PENDING_HUMAN_REVIEW",
    }
    write_json(extraction / "boundary_candidates.json", boundary)
    grid = ("<table><tr><td>2025</td><td>$2</td></tr></table>" if not hostile_grid else
            "<table><tr><td onclick='steal()'>2025</td></tr></table><script>steal()</script><img src='https://tracker.example'>")
    table_rows = [] if ambiguous else [{
        "table_id": "MDA-1", "source_table_index": 1,
        "html_source_byte_start": table_start, "html_source_byte_end_exclusive": table_end,
        "complete_submission_byte_start": len(raw_prefix) + table_start,
        "complete_submission_byte_end_exclusive": len(raw_prefix) + table_end,
        "raw_html_fragment_sha256": digest(table_raw),
        "flattened_section_char_start": flat_start, "flattened_section_char_end_exclusive": flat_start + len(flat),
        "flattened_text_sha256": digest(flat.encode("utf-8")), "flattened_text": flat,
        "source_grid_html": grid, "preceding_context": "Revenue, dollars in millions",
        "period_hints": ["three months ended 2024-12-31"], "unit_hints": ["USD millions"],
        "footnote_hints": [], "review_status": "NEEDS_HUMAN_TABLE_REVIEW",
    }]
    if table_rows:
        context_html = html[:html.index("<table>")]
        caption_text = section[:flat_start]
        following_start = html.index("</table>") + len("</table>")
        following_html = html[following_start:]
        table_rows[0].update({
            "source_caption_context": {
                "html_source_char_span": [0, len(context_html)],
                "complete_submission_byte_span": [len(raw_prefix), len(raw_prefix) + len(context_html.encode("utf-8"))],
                "raw_html_sha256": digest(context_html.encode("utf-8")),
                "rendered_excerpt": "Revenue", "context_truncated_at_4000_source_chars": False,
                "status": "SOURCE_CONTEXT_ONLY_CAPTION_NOT_CONFIRMED"},
            "caption_candidate": {
                "text": caption_text, "normalized_section_char_span": [0, flat_start],
                "normalized_context_sha256": digest(caption_text.encode("utf-8")),
                "status": "BEST_EFFORT_CONTEXT_NOT_CONFIRMED_TITLE", "reason": None},
            "unit_evidence": {"resolved_unit": None, "status": "EXPLICIT_CUES_NEED_HUMAN_INTERPRETATION"},
            "footnote_evidence": {"confirmed_complete_footnotes": None,
                                  "adjacent_text_note_count": 0,
                                  "status": "NO_SUP_ELEMENT_FOUND_NOT_A_NO_FOOTNOTE_CLAIM"},
            "adjacent_text_note_candidates": [],
            "following_source_context": {
                "html_source_char_span": [following_start, len(html)],
                "complete_submission_byte_span": [len(raw_prefix) + len(html[:following_start].encode("utf-8")),
                                                  len(raw_prefix) + len(html.encode("utf-8"))],
                "raw_html_sha256": digest(following_html.encode("utf-8")),
                "rendered_excerpt": "Item 3", "status": "SOURCE_CONTEXT_ONLY_NOTE_ASSIGNMENT_UNCONFIRMED"},
        })
        if adjacent_note:
            n0 = section.index(note)
            table_rows[0]["adjacent_text_note_candidates"] = [{
                "label": "Note 1", "text": note,
                "normalized_section_char_span": [n0, n0 + len(note)],
                "text_sha256": digest(note.encode("utf-8")),
                "distance_from_table_end_chars": n0 - (flat_start + len(flat)),
                "status": "ADJACENT_NOTE_CANDIDATE_NEEDS_HUMAN_REVIEW"}]
            table_rows[0]["footnote_evidence"]["adjacent_text_note_count"] = 1
    boundary_layout = []
    if heading_table:
        a = html.index("<table>", html.index("</table>") + 8)
        b = html.index("</table>", a) + len("</table>")
        boundary_layout = [{"source_table_index": 2, "roles": ["CONTAINS_ITEM3_HEADING"],
            "html_source_char_span": [a, b],
            "complete_submission_byte_span": [len(raw_prefix) + len(html[:a].encode("utf-8")),
                                              len(raw_prefix) + len(html[:b].encode("utf-8"))],
            "raw_html_sha256": digest(html[a:b].encode("utf-8")),
            "flattened_excerpt": "Item 3. Quantitative and Qualitative Disclosures",
            "review_status": "BOUNDARY_LAYOUT_TABLE_NEEDS_HUMAN_REVIEW"}]
    write_json(extraction / "table_index.json", {"status": "NEEDS_REVIEW" if ambiguous else "PROGRAM_TABLES_CHECKED",
        "source_identity": {"ticker": "MSFT", "cik": identity["cik"], "accession": identity["accession"]},
        "section_sha256": None if ambiguous else selected["section_sha256"],
        "source_html_table_count": len(table_rows),
        "layout_risks": ["DIV_SPAN_NUMERIC_LAYOUT_REQUIRES_REVIEW"] if ambiguous else [],
        "boundary_layout_tables": boundary_layout,
        "human_review": "PENDING_HUMAN_REVIEW",
        "tables": table_rows})
    write_json(extraction / "period_ledger.json", {
        "status": "NEEDS_REVIEW", "report_fiscal_year": 2025, "report_fiscal_quarter": "Q2",
        "report_period_end": "2024-12-31", "narrative_periods": [], "table_periods": [], "chapter_period": None,
        "human_review": "PENDING_HUMAN_REVIEW",
    })
    outputs = {p.name: {"sha256": digest(p.read_bytes()), "bytes": p.stat().st_size}
               for p in extraction.iterdir() if p.is_file()}
    write_json(extraction / "extraction_receipt.json", {
        "status": "NEEDS_REVIEW" if ambiguous else "PROGRAM_EXTRACTED_HUMAN_PENDING",
        "source_identity": {k: identity[k] for k in ("ticker", "cik", "accession", "form")},
        "outputs": outputs, "human_review": "PENDING_HUMAN_REVIEW",
    })
    return ingest, extraction


def test_build_packet_binds_realized_synthetic_spans_and_leaves_form_blank(tmp_path: Path):
    ingest, extraction = fixture(tmp_path)
    output = tmp_path / "review" / "MSFT"
    receipt = build_packet(ingest, extraction, output)
    assert receipt["status"] == "PENDING_HUMAN_REVIEW"
    assert receipt["table_count"] == 1
    assert receipt["human_decision"] is None and receipt["human_review_time"] is None
    assert "version_adjudication" in receipt
    page = (output / "index.html").read_text(encoding="utf-8")
    form = (output / "review_form.html").read_text(encoding="utf-8")
    assert "原 HTML 行列" in page and "YearUSD\n\n2025$2" in page
    assert "Python Unicode 字符" in page and "原始字节" in page
    assert "PENDING_HUMAN_REVIEW" in form and "review_page_sha256" in form
    assert "MSFT_10Q_MDA_review_M8_" in form
    assert "<input name='reviewer' autocomplete='off'>" in form
    assert "<option value='' selected>— 尚未决定 —</option>" in form
    index = json.loads((output / "review_index.json").read_text(encoding="utf-8"))
    assert index["table_count"] == 1 and index["human_decision"] is None
    assert receipt["review_page_sha256"] == digest((output / "index.html").read_bytes())


def test_source_tamper_fails_before_creating_packet(tmp_path: Path):
    ingest, extraction = fixture(tmp_path)
    (ingest / "original_submission.txt").write_bytes(b"tampered")
    output = tmp_path / "review" / "MSFT"
    with pytest.raises(ValueError, match="original_submission_sha256 mismatch"):
        build_packet(ingest, extraction, output)
    assert not output.exists()


def test_ambiguous_boundary_never_becomes_program_pass(tmp_path: Path):
    ingest, extraction = fixture(tmp_path, ambiguous=True)
    output = tmp_path / "review" / "MSFT"
    receipt = build_packet(ingest, extraction, output)
    assert receipt["program_boundary_status"] == "NEEDS_REVIEW"
    assert receipt["section_sha256"] is None
    assert receipt["table_count"] == 0
    page = (output / "index.html").read_text(encoding="utf-8")
    form = (output / "review_form.html").read_text(encoding="utf-8")
    assert "未生成唯一 Item 2 提取文本" in page
    assert "不能据此认定没有语义表格" in page
    assert "DIV_SPAN_NUMERIC_LAYOUT_REQUIRES_REVIEW" in page
    assert "fixed.program_boundary_status !== 'PROGRAM_BOUNDARY_PASS'" in form
    assert "section_sha256</th><td><code>null" in form


def test_untrusted_grid_is_sanitized_in_visible_page(tmp_path: Path):
    ingest, extraction = fixture(tmp_path, hostile_grid=True)
    output = tmp_path / "review" / "MSFT"
    build_packet(ingest, extraction, output)
    page = (output / "index.html").read_text(encoding="utf-8")
    assert "onclick=" not in page and "tracker.example" not in page
    assert "<script" not in page and "<img" not in page
    assert "2025" in page


def test_heading_layout_table_is_separate_and_requires_human_review(tmp_path: Path):
    ingest, extraction = fixture(tmp_path, heading_table=True)
    output = tmp_path / "review" / "MSFT"
    receipt = build_packet(ingest, extraction, output)
    assert receipt["table_count"] == 1
    assert receipt["boundary_layout_table_count"] == 1
    page = (output / "index.html").read_text(encoding="utf-8")
    form = (output / "review_form.html").read_text(encoding="utf-8")
    assert "BOUNDARY-2" in page and "包含 Item 2 / Item 3 标题" in page
    assert "name='BOUNDARY-2'" in form
    assert "PENDING_HUMAN_REVIEW" in form
    table_path = extraction / "table_index.json"
    table_index = json.loads(table_path.read_text(encoding="utf-8"))
    table_index["boundary_layout_tables"][0]["raw_html_sha256"] = "0" * 64
    write_json(table_path, table_index)
    with pytest.raises(ValueError, match="extraction output mismatch"):
        build_packet(ingest, extraction, tmp_path / "review" / "MSFT_modified")


def test_nonunique_flattened_table_span_remains_explicitly_pending(tmp_path: Path):
    ingest, extraction = fixture(tmp_path)
    table_path = extraction / "table_index.json"
    table_index = json.loads(table_path.read_text(encoding="utf-8"))
    table_index["tables"][0]["flattened_section_char_start"] = None
    table_index["tables"][0]["flattened_section_char_end_exclusive"] = None
    table_index["tables"][0]["review_status"] = "NEEDS_REVIEW_FLATTENED_SPAN_NOT_UNIQUE"
    write_json(table_path, table_index)
    receipt_path = extraction / "extraction_receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["outputs"]["table_index.json"] = {"sha256": digest(table_path.read_bytes()),
                                                 "bytes": table_path.stat().st_size}
    write_json(receipt_path, receipt)
    output = tmp_path / "review" / "MSFT"
    build_packet(ingest, extraction, output)
    page = (output / "index.html").read_text(encoding="utf-8")
    assert "UNMAPPED_FLATTENED_TABLE" in page
    assert "NEEDS_REVIEW_FLATTENED_SPAN_NOT_UNIQUE" in page


def test_adjacent_plain_text_note_is_bound_but_not_auto_assigned(tmp_path: Path):
    ingest, extraction = fixture(tmp_path, adjacent_note=True)
    output = tmp_path / "review" / "MSFT"
    build_packet(ingest, extraction, output)
    page = (output / "index.html").read_text(encoding="utf-8")
    assert "Note 1: supplied quantities are estimates." in page
    assert "ADJACENT_NOTE_CANDIDATE_NEEDS_HUMAN_REVIEW" in page
    assert "脚注完整性未确认" in page
    assert "PENDING_HUMAN_REVIEW" in (output / "review_form.html").read_text(encoding="utf-8")


def test_overview_reports_missing_firms_without_claiming_acceptance(tmp_path: Path):
    ingest, extraction = fixture(tmp_path)
    build_packet(ingest, extraction, tmp_path / "review" / "MSFT")
    packet_path = tmp_path / "review" / "MSFT" / "receipt.json"
    packet = json.loads(packet_path.read_text(encoding="utf-8"))
    packet["version_adjudication"] = {
        "adjudication_status": "PROCEED_WITH_NEW_RAW_SNAPSHOT_FOR_UNREVIEWED_PILOT",
        "historical_snapshot_equivalence": "NOT_ESTABLISHED"}
    write_json(packet_path, packet)
    result = build_overview(tmp_path, tmp_path / "review" / "index.html", "extraction")
    assert result["rows"] == 4
    overview = json.loads((tmp_path / "review" / "overview.json").read_text(encoding="utf-8"))
    assert [r["ticker"] for r in overview["rows"]] == ["MSFT", "AMZN", "GOOGL", "META"]
    assert overview["rows"][0]["human_review_status"] == "PENDING_HUMAN_REVIEW"
    assert overview["rows"][0]["version_adjudication_status"] == "PROCEED_WITH_NEW_RAW_SNAPSHOT_FOR_UNREVIEWED_PILOT"
    assert overview["rows"][0]["historical_snapshot_equivalence"] == "NOT_ESTABLISHED"
    assert overview["rows"][1]["acquisition_status"] == "NOT_AVAILABLE"
    assert overview["rows"][1]["human_review_status"] == "NOT_READY_FOR_HUMAN_DECISION"
    assert (tmp_path / "review" / "AMZN" / "status.html").is_file()
    assert "没有提取章节或人工决定" in (tmp_path / "review" / "AMZN" / "status.html").read_text(encoding="utf-8")
    assert overview["batch_representation"] == "NOT_RUN"
    page = (tmp_path / "review" / "index.html").read_text(encoding="utf-8")
    assert "overview-cards" in page and "firm-card" in page
    assert "原 ingest 状态" in page and "附加版本核验" in page


def test_version_conflict_blocks_packet_and_is_visible_in_overview(tmp_path: Path):
    ingest, extraction = fixture(tmp_path)
    identity_path = ingest / "source_identity.json"
    identity = json.loads(identity_path.read_text(encoding="utf-8"))
    identity["status"] = "NEEDS_VERSION_REVIEW"
    write_json(identity_path, identity)
    with pytest.raises(ValueError, match="version difference lacks extraction adjudication"):
        build_packet(ingest, extraction, tmp_path / "review" / "MSFT")
    build_overview(tmp_path, tmp_path / "review" / "index.html", "extraction")
    overview = json.loads((tmp_path / "review" / "overview.json").read_text(encoding="utf-8"))
    assert overview["rows"][0]["acquisition_status"] == "NEEDS_VERSION_REVIEW"
    assert "version review required" in overview["rows"][0]["failure_or_blocker"]
    assert overview["rows"][0]["human_review_status"] == "NOT_READY_FOR_HUMAN_DECISION"


def test_exact_version_adjudication_gate_accepts_only_bound_structural_proof(tmp_path: Path, monkeypatch):
    run = tmp_path / "run"
    ingest = run / "ingest" / "MSFT"
    extraction = run / "extraction" / "MSFT"
    version = run / "version_adjudication"
    for path in (ingest, extraction, version):
        path.mkdir(parents=True)
    inner = b"<html><body>Text</body></html>"
    html = b"<XBRL>\n" + inner + b"</XBRL>"
    script_prefix, script_suffix = b'<script src="', b'"></script>'
    script = script_prefix + b"x" * (114 - len(script_prefix) - len(script_suffix)) + script_suffix
    assert len(script) == 114
    prior = inner.replace(b"</body>", script + b"</body>")
    prior_path = tmp_path / "prior.html"
    prior_path.write_bytes(prior)
    frozen_path = tmp_path / "candidates.json"
    write_json(frozen_path, {"rows": [{"ticker": "MSFT", "accession": "0000950170-25-010491",
                                      "primary_html": {"path": str(prior_path), "sha256": digest(prior)}}]})
    monkeypatch.setattr(m8_review, "FROZEN_CANDIDATES", frozen_path)
    identity = {"ticker": "MSFT", "cik": "0000789019", "accession": "0000950170-25-010491",
                "status": "NEEDS_VERSION_REVIEW"}
    raw = b"synthetic header" + html
    adjudication_path = version / "MSFT.json"
    write_json(adjudication_path, {"status": "PROCEED_WITH_NEW_RAW_SNAPSHOT_FOR_UNREVIEWED_PILOT",
        "ticker": "MSFT", "cik": identity["cik"], "accession": identity["accession"],
        "new_raw_sha256": digest(raw), "new_embedded_primary_sha256": digest(html),
        "prior_standalone_path": str(prior_path), "prior_standalone_primary_sha256": digest(prior),
        "equivalence_proof": {"status": "PASS_STRUCTURAL_EQUIVALENCE",
            "rule": "XBRL_WRAPPER_AND_ONE_STANDALONE_SCRIPT_ELEMENT",
            "exact_unwrapped_bytes_equal": True, "script_bytes": 114, "script_sha256": digest(script)}})
    info = {"status": "STRUCTURAL_DIFFERENCE_RECHECKED_FOR_UNREVIEWED_EXTRACTION",
            "adjudication_path": str(adjudication_path), "adjudication_sha256": digest(adjudication_path.read_bytes()),
            "prior_standalone_sha256": digest(prior), "new_embedded_primary_sha256": digest(html),
            "script_element_sha256": digest(script), "script_element_bytes": 114,
            "historical_snapshot_equivalence": "NOT_ESTABLISHED", "human_review": "PENDING_HUMAN_REVIEW"}
    checked = m8_review.checked_version_adjudication(ingest, extraction, identity,
                                                      {"version_adjudication": info}, raw, html)
    assert checked["script_element_bytes"] == 114
    assert "unverified origin/function" in checked["interpretation"]
    info["adjudication_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="path/hash mismatch"):
        m8_review.checked_version_adjudication(ingest, extraction, identity,
                                               {"version_adjudication": info}, raw, html)


def test_reused_v4_form_logic_blocks_ambiguous_program_gate():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node unavailable for JavaScript software check")
    fixed = {"raw_sha256": "a" * 64, "section_sha256": None,
             "review_page_sha256": "b" * 64, "review_packet_version": "M8-v1",
             "program_boundary_status": "NEEDS_REVIEW"}
    form = make_form(fixed, ["MDA-1"], "MSFT", False)
    script = form.split("<script>", 1)[1].split("</script>", 1)[0]
    program = """
const vm=require('node:vm'); const assert=require('node:assert/strict');
const context={document:{getElementById:()=>({addEventListener:()=>{}})}};
vm.runInNewContext(SOURCE+';globalThis.gate=gateFor;',context);
const fields={start:'YES',end:'YES',body:'YES','MDA-1':'YES',issues:'',reviewer:'Synthetic Tester',review_time:'2026-10-26T10:00',decision:'ACCEPT'};
assert.equal(context.gate(fields),'NOT_MET');
""".replace("SOURCE", json.dumps(script))
    done = subprocess.run([node, "-e", program], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
