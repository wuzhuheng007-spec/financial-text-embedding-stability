"""Build version-bound, local-only M8 10-Q MD&A researcher review packets.

This reads verified saved inputs and the separate extraction result. It never
downloads filings, changes source evidence, or records a human decision.
Run as ``python -m scripts.m8_build_review`` from the repository root.
"""

from __future__ import annotations

import argparse
import hashlib
from html import escape
import json
from pathlib import Path
import re

from scripts.m7_build_10q_review import STYLE, safe_preview, source_rendered
from scripts.m7_upgrade_10q_review import js_for_form


FIXED = {
    "MSFT": ("0000789019", "0000950170-25-010491"),
    "AMZN": ("0001018724", "0001018724-25-000036"),
    "GOOGL": ("0001652044", "0001652044-25-000043"),
    "META": ("0001326801", "0001326801-25-000054"),
}
VERSION = "M8-review-v7"
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
TABLE_STYLE = (".table-scroll{max-width:100%;overflow:auto}.table-scroll table{border-collapse:collapse;font-size:13px}"
               ".table-scroll td,.table-scroll th{border:1px solid #9ba8ba;padding:5px;vertical-align:top}"
               "@media(max-width:700px){.grid{grid-template-columns:minmax(0,1fr)}table.meta{display:block;overflow-x:auto}}")
ROOT = Path(__file__).resolve().parents[1]
FROZEN_CANDIDATES = ROOT / "artifacts/m7_review/20261024T235855Z-closeout/candidates/run_v2/candidates.json"


def need(ok: bool, message: str) -> None:
    if not ok:
        raise ValueError(message)


def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha(path: Path) -> str:
    return sha_bytes(path.read_bytes())


def read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    need(isinstance(value, dict), f"Expected object: {path}")
    return value


def esc(value: object) -> str:
    return escape(str(value if value is not None else "null"))


def display_json(value: object) -> str:
    return escape(json.dumps(value, ensure_ascii=False, indent=2))


def checked_version_adjudication(ingest: Path, extraction: Path, identity: dict,
                                 receipt: dict, raw: bytes, html_bytes: bytes) -> dict | None:
    """Recheck a version difference without replacing either saved snapshot."""
    ticker = identity["ticker"]
    if identity["status"] == "VERIFIED_RAW_AND_PRIMARY":
        return None
    need(identity["status"] == "NEEDS_VERSION_REVIEW",
         f"{ticker}: raw source not verified")
    info = receipt.get("version_adjudication")
    need(isinstance(info, dict) and
         info.get("status") == "STRUCTURAL_DIFFERENCE_RECHECKED_FOR_UNREVIEWED_EXTRACTION",
         f"{ticker}: version difference lacks extraction adjudication")
    adjudication_path = extraction.parents[1] / "version_adjudication" / f"{ticker}.json"
    reported_path = Path(info.get("adjudication_path", ""))
    if not reported_path.is_absolute():
        reported_path = ROOT / reported_path
    need(adjudication_path.is_file() and
         reported_path.resolve() == adjudication_path.resolve() and
         sha(adjudication_path) == info.get("adjudication_sha256"),
         f"{ticker}: version adjudication path/hash mismatch")
    adjudication = read_json(adjudication_path)
    need(adjudication.get("status") == "PROCEED_WITH_NEW_RAW_SNAPSHOT_FOR_UNREVIEWED_PILOT",
         f"{ticker}: adjudication does not authorize an unreviewed source pilot")
    for key in ("ticker", "cik", "accession"):
        need(adjudication.get(key) == identity.get(key), f"{ticker}: adjudication {key} conflict")
    need(adjudication.get("new_raw_sha256") == sha_bytes(raw) and
         adjudication.get("new_embedded_primary_sha256") == sha_bytes(html_bytes),
         f"{ticker}: adjudication new-source hash conflict")
    frozen = read_json(FROZEN_CANDIDATES)
    matching = [row for row in frozen["rows"] if row["ticker"] == ticker and
                row["accession"] == identity["accession"]]
    need(len(matching) == 1, f"{ticker}: frozen prior candidate absent or ambiguous")
    prior_path = Path(matching[0]["primary_html"]["path"])
    need(prior_path.is_file() and prior_path.resolve() ==
         Path(adjudication.get("prior_standalone_path", "")).resolve(),
         f"{ticker}: adjudication prior path differs from frozen candidate")
    prior = prior_path.read_bytes()
    prior_sha = sha_bytes(prior)
    need(prior_sha == matching[0]["primary_html"]["sha256"] ==
         adjudication.get("prior_standalone_primary_sha256") == info.get("prior_standalone_sha256"),
         f"{ticker}: prior standalone HTML SHA conflict")
    wrapper_start, wrapper_end = b"<XBRL>\n", b"</XBRL>"
    need(html_bytes.startswith(wrapper_start) and html_bytes.endswith(wrapper_end),
         f"{ticker}: expected exact raw XBRL wrapper absent")
    embedded_inner = html_bytes[len(wrapper_start):-len(wrapper_end)]
    start = prior.rfind(b"<script")
    end = prior.find(b"</script>", start) + len(b"</script>")
    need(start >= 0 and end > start and prior[:start] + prior[end:] == embedded_inner,
         f"{ticker}: prior/raw difference is not one extra script element")
    script = prior[start:end]
    proof = adjudication.get("equivalence_proof", {})
    need(proof.get("status") == "PASS_STRUCTURAL_EQUIVALENCE" and
         proof.get("rule") == "XBRL_WRAPPER_AND_ONE_STANDALONE_SCRIPT_ELEMENT" and
         proof.get("exact_unwrapped_bytes_equal") is True and
         proof.get("script_bytes") == len(script) == info.get("script_element_bytes") == 114 and
         proof.get("script_sha256") == info.get("script_element_sha256") == sha_bytes(script),
         f"{ticker}: structural equivalence proof mismatch")
    need(info.get("new_embedded_primary_sha256") == sha_bytes(html_bytes) and
         info.get("historical_snapshot_equivalence") == "NOT_ESTABLISHED" and
         info.get("human_review") == "PENDING_HUMAN_REVIEW",
         f"{ticker}: extraction version scope or review status conflict")
    return {"adjudication_status": adjudication["status"],
            "adjudication_sha256": sha(adjudication_path),
            "prior_standalone_primary_sha256": prior_sha,
            "new_embedded_primary_sha256": sha_bytes(html_bytes),
            "script_element_sha256": sha_bytes(script), "script_element_bytes": len(script),
            "historical_snapshot_equivalence": "NOT_ESTABLISHED",
            "interpretation": "Raw-embedded primary has exact XBRL wrapper; prior standalone response has one extra 114-byte script element of unverified origin/function. Remaining bytes match; historical snapshot equivalence is not established.",
            "human_review": "PENDING_HUMAN_REVIEW"}


def checked_inputs(ingest: Path, extraction: Path) -> dict:
    identity = read_json(ingest / "source_identity.json")
    ticker = identity.get("ticker")
    need(ticker in FIXED, "Ticker is outside fixed four-company candidate list")
    cik, accession = FIXED[ticker]
    need(identity.get("cik") == cik and identity.get("accession") == accession,
         f"{ticker}: fixed CIK/accession conflict")
    need(identity.get("form") == "10-Q", f"{ticker}: not an original 10-Q")
    raw_path = ingest / "original_submission.txt"
    html_path = ingest / "primary_document_source.html"
    primary_path = ingest / "primary_text.txt"
    raw = raw_path.read_bytes()
    html_bytes = html_path.read_bytes()
    primary = primary_path.read_text(encoding="utf-8")
    hashes = {
        "original_submission_sha256": sha_bytes(raw),
        "primary_document_sha256": sha_bytes(html_bytes),
        "primary_text_sha256": sha(primary_path),
    }
    for key, actual in hashes.items():
        expected = identity.get(key)
        need(isinstance(expected, str) and SHA_RE.fullmatch(expected) and expected == actual,
             f"{ticker}: {key} mismatch")
    span = identity.get("primary_raw_byte_span")
    need(isinstance(span, list) and len(span) == 2 and
         all(isinstance(n, int) for n in span) and 0 <= span[0] < span[1] <= len(raw),
         f"{ticker}: invalid raw primary byte span")
    need(raw[span[0]:span[1]] == html_bytes, f"{ticker}: primary HTML differs from raw submission span")
    html = html_bytes.decode("utf-8")

    receipt = read_json(extraction / "extraction_receipt.json")
    adjudication = checked_version_adjudication(ingest, extraction, identity,
                                                receipt, raw, html_bytes)
    boundary = read_json(extraction / "boundary_candidates.json")
    tables = read_json(extraction / "table_index.json")
    periods = read_json(extraction / "period_ledger.json")
    need(receipt.get("human_review") == "PENDING_HUMAN_REVIEW" and
         tables.get("human_review") == "PENDING_HUMAN_REVIEW" and
         periods.get("human_review") == "PENDING_HUMAN_REVIEW",
         f"{ticker}: human review state cannot be inferred or prefilled")
    for key in ("ticker", "cik", "accession", "form"):
        recorded = receipt.get("source_identity", {}).get(key)
        if recorded is not None:
            need(recorded == identity.get(key), f"{ticker}: extraction receipt {key} conflict")
    for rel, item in receipt.get("outputs", {}).items():
        need("/" not in rel and "\\" not in rel and rel not in (".", ".."), "Unsafe receipt output path")
        path = extraction / rel
        need(path.is_file() and path.stat().st_size == item["bytes"] and sha(path) == item["sha256"],
             f"{ticker}: extraction output mismatch: {rel}")
    for name, expected in (("primary_document_source.html", hashes["primary_document_sha256"]),
                           ("primary_text.txt", hashes["primary_text_sha256"])):
        need(sha(extraction / name) == expected, f"{ticker}: extraction copy mismatch: {name}")
    need(boundary.get("human_review") == "PENDING_HUMAN_REVIEW", "Boundary human state must be pending")
    need(boundary.get("status") in {"PROGRAM_BOUNDARY_PASS", "NEEDS_REVIEW"},
         f"{ticker}: unrecognized boundary status")
    need(receipt.get("status") == ("PROGRAM_EXTRACTED_HUMAN_PENDING" if
         boundary["status"] == "PROGRAM_BOUNDARY_PASS" else "NEEDS_REVIEW"),
         f"{ticker}: extraction receipt/boundary status conflict")
    need(isinstance(boundary.get("candidates"), list), f"{ticker}: no candidate ledger")
    coordinates = boundary.get("coordinates", {})
    need(coordinates.get("primary_text_unit") ==
         "Python Unicode characters in saved normalized primary_text.txt",
         "Primary-text coordinate/file unit missing")
    need(coordinates.get("primary_html_unit") ==
         "UTF-8 bytes in saved primary_document_source.html",
         "HTML coordinate/file unit missing")
    need(coordinates.get("complete_submission_unit") ==
         "bytes in saved original_submission.txt",
         "Raw coordinate/file unit missing")

    section_path = extraction / "item2_candidate.txt"
    section = None
    if boundary["status"] == "PROGRAM_BOUNDARY_PASS":
        selected = boundary.get("selected_span")
        need(isinstance(selected, dict), f"{ticker}: missing selected span")
        start, end = selected.get("start_char"), selected.get("end_char_exclusive")
        need(isinstance(start, int) and isinstance(end, int) and 0 <= start < end <= len(primary),
             f"{ticker}: invalid normalized text span")
        need(section_path.is_file(), f"{ticker}: missing exact section file")
        section = section_path.read_text(encoding="utf-8")
        need(primary[start:end] == section, f"{ticker}: section not exact primary-text slice")
        need(selected.get("section_sha256") == sha(section_path), f"{ticker}: section SHA mismatch")
        selected_index = boundary.get("selected_candidate_index")
        need(isinstance(selected_index, int) and 0 <= selected_index < len(boundary["candidates"]),
             f"{ticker}: selected candidate index absent or invalid")
        candidate = boundary["candidates"][selected_index]
        need(candidate["start_char"] == start and candidate["end_char_exclusive"] == end,
             f"{ticker}: selected candidate/span conflict")
    else:
        need(boundary.get("selected_span") is None and not section_path.exists(),
             f"{ticker}: ambiguous boundary cannot supply definitive section")
    need(tables.get("section_sha256") == (sha(section_path) if section else None),
         f"{ticker}: table index section binding mismatch")
    for key in ("ticker", "cik", "accession", "form"):
        recorded = tables.get("source_identity", {}).get(key)
        if recorded is not None:
            need(recorded == identity.get(key), f"{ticker}: table index {key} conflict")
    if periods.get("report_period_end") is not None:
        need(periods["report_period_end"] == identity.get("report_period_end"),
             f"{ticker}: period ledger report-end conflict")
    table_rows = tables.get("tables")
    need(isinstance(table_rows, list), f"{ticker}: missing table list")
    recorded_table_count = tables.get("source_html_table_count")
    if recorded_table_count is not None:
        need(recorded_table_count == len(table_rows),
             f"{ticker}: source HTML table count conflict")
    if section is not None:
        anchors = boundary.get("html_heading_anchors", {})
        start_anchor = anchors.get("selected_item2") or {}
        end_anchor = anchors.get("selected_item3") or {}
        source_start = start_anchor.get("html_source_char_start")
        source_end = end_anchor.get("html_source_char_start")
        need(isinstance(source_start, int) and isinstance(source_end, int) and
             0 <= source_start < source_end <= len(html),
             f"{ticker}: source HTML heading anchors absent or reversed")
        literal_opens = len(re.findall(r"<table(?=[\s>])", html[source_start:source_end], re.I))
        boundary_opens = sum(source_start <= row["html_source_char_span"][0] < source_end
                             for row in tables.get("boundary_layout_tables", []))
        need(literal_opens == len(table_rows) + boundary_opens,
             f"{ticker}: HTML source table openings differ from indexed body and boundary tables")
    need(not table_rows or section is not None, f"{ticker}: tables without definite section")
    for row in table_rows:
        table_id = row["table_id"]
        h0, h1 = row["html_source_byte_start"], row["html_source_byte_end_exclusive"]
        r0, r1 = row["complete_submission_byte_start"], row["complete_submission_byte_end_exclusive"]
        t0, t1 = row["flattened_section_char_start"], row["flattened_section_char_end_exclusive"]
        need(0 <= h0 < h1 <= len(html_bytes) and 0 <= r0 < r1 <= len(raw) and
             section is not None, f"{ticker} {table_id}: invalid source spans or absent section")
        raw_fragment = raw[r0:r1]
        need(raw_fragment == html_bytes[h0:h1], f"{ticker} {table_id}: raw/HTML table fragments differ")
        need(sha_bytes(raw_fragment) == row["raw_html_fragment_sha256"],
             f"{ticker} {table_id}: raw table SHA mismatch")
        flat = row["flattened_text"]
        need(sha_bytes(flat.encode("utf-8")) == row["flattened_text_sha256"],
             f"{ticker} {table_id}: flattened table SHA mismatch")
        need(source_rendered(raw_fragment.decode("utf-8")) == flat,
             f"{ticker} {table_id}: flattened table not reproduced from raw HTML")
        if t0 is None or t1 is None:
            need(t0 is None and t1 is None and
                 row["review_status"] == "NEEDS_REVIEW_FLATTENED_SPAN_NOT_UNIQUE",
                 f"{ticker} {table_id}: unbound flattened span lacks explicit review status")
        else:
            need(isinstance(t0, int) and isinstance(t1, int) and 0 <= t0 < t1 <= len(section),
                 f"{ticker} {table_id}: invalid flattened section span")
            need(section[t0:t1] == flat,
                 f"{ticker} {table_id}: flattened table differs from normalized section span")
        context = row.get("source_caption_context")
        need(isinstance(context, dict), f"{ticker} {table_id}: missing source caption context")
        c0, c1 = context["html_source_char_span"]
        cr0, cr1 = context["complete_submission_byte_span"]
        need(0 <= c0 < c1 <= len(html) and 0 <= cr0 < cr1 <= len(raw) and
             html[c0:c1].encode("utf-8") == raw[cr0:cr1] and
             sha_bytes(raw[cr0:cr1]) == context["raw_html_sha256"],
             f"{ticker} {table_id}: caption source context mismatch")
        caption = row.get("caption_candidate")
        need(isinstance(caption, dict), f"{ticker} {table_id}: missing caption candidate ledger")
        a, b = caption["normalized_section_char_span"]
        need(0 <= a < b <= len(section) and
             sha_bytes(section[a:b].encode("utf-8")) == caption["normalized_context_sha256"],
             f"{ticker} {table_id}: normalized caption context mismatch")
        need(isinstance(row.get("unit_evidence"), dict) and
             row["unit_evidence"].get("resolved_unit") is None and
             isinstance(row.get("footnote_evidence"), dict) and
             row["footnote_evidence"].get("confirmed_complete_footnotes") is None,
             f"{ticker} {table_id}: unit/footnote evidence overstated")
        following = row.get("following_source_context")
        need(isinstance(following, dict), f"{ticker} {table_id}: missing following source context")
        f0, f1 = following["html_source_char_span"]
        fr0, fr1 = following["complete_submission_byte_span"]
        need(0 <= f0 <= f1 <= len(html) and 0 <= fr0 <= fr1 <= len(raw) and
             html[f0:f1].encode("utf-8") == raw[fr0:fr1] and
             sha_bytes(raw[fr0:fr1]) == following["raw_html_sha256"],
             f"{ticker} {table_id}: following source context mismatch")
        adjacent = row.get("adjacent_text_note_candidates")
        need(isinstance(adjacent, list) and
             row["footnote_evidence"].get("adjacent_text_note_count") == len(adjacent),
             f"{ticker} {table_id}: adjacent note count mismatch")
        for note in adjacent:
            n0, n1 = note["normalized_section_char_span"]
            need(isinstance(n0, int) and isinstance(n1, int) and
                 0 <= n0 < n1 <= len(section) and
                 section[n0:n1] == note["text"] and
                 sha_bytes(note["text"].encode("utf-8")) == note["text_sha256"] and
                 (t1 is None or n0 - t1 == note["distance_from_table_end_chars"]) and
                 note.get("status") == "ADJACENT_NOTE_CANDIDATE_NEEDS_HUMAN_REVIEW",
                 f"{ticker} {table_id}: adjacent note source/status mismatch")
    need(len({row["table_id"] for row in table_rows}) == len(table_rows), f"{ticker}: duplicate table IDs")
    boundary_layout = tables.get("boundary_layout_tables", [])
    need(isinstance(boundary_layout, list), f"{ticker}: invalid boundary layout table ledger")
    for row in boundary_layout:
        h0, h1 = row["html_source_char_span"]
        r0, r1 = row["complete_submission_byte_span"]
        need(0 <= h0 < h1 <= len(html) and 0 <= r0 < r1 <= len(raw),
             f"{ticker}: invalid boundary layout table span")
        fragment = html[h0:h1].encode("utf-8")
        need(fragment == raw[r0:r1] and sha_bytes(fragment) == row["raw_html_sha256"],
             f"{ticker}: boundary layout table source/hash mismatch")
        need(row.get("review_status") == "BOUNDARY_LAYOUT_TABLE_NEEDS_HUMAN_REVIEW",
             f"{ticker}: boundary layout table review status missing")
    numeric_layout = tables.get("numeric_layout_candidates", [])
    need(isinstance(numeric_layout, list), f"{ticker}: invalid numeric layout ledger")
    for row in numeric_layout:
        h0, h1 = row["html_source_char_span"]
        need(0 <= h0 < h1 <= len(html) and
             sha_bytes(html[h0:h1].encode("utf-8")) == row["source_html_sha256"],
             f"{ticker}: numeric layout source/hash mismatch")
    return {"identity": identity, "hashes": hashes, "raw": raw, "html": html,
            "primary": primary, "section": section, "boundary": boundary,
            "tables": table_rows, "boundary_layout_tables": boundary_layout,
            "numeric_layout_candidates": numeric_layout, "table_index": tables, "periods": periods,
            "receipt": receipt, "version_adjudication": adjudication}


def source_anchor_preview(html: str, anchor: dict | None) -> str:
    if not isinstance(anchor, dict):
        return "<p>未提供对应 HTML 标题锚点；请按清理文本候选和完整原件继续核对。</p>"
    start = anchor.get("html_source_char_start")
    if not isinstance(start, int) or not 0 <= start < len(html):
        return "<p>HTML 标题锚点位置无效，需人工复核。</p>"
    # A bounded, sanitized visual hint. It is not equated to normalized-text spans.
    tail = html[start:start + 3000]
    end = tail.lower().find("</div>")
    fragment = tail[:end + 6] if end >= 0 else tail
    return "<div class='source-preview'>" + safe_preview(fragment) + "</div>"


def anomalies(boundary: dict, section: str | None, table_rows: list[dict],
              table_index: dict, identity: dict, adjudication: dict | None) -> list[dict]:
    result = [{"type": "REPEATED_OR_TOC_HEADING",
               "evidence": f"Item 2 heading candidates: {boundary.get('item2_heading_count')}; Item 3: {boundary.get('item3_heading_count')}; candidate classifications require human review.",
               "status": "PENDING_HUMAN_REVIEW"},
              {"type": "TABLE_LAYOUT",
               "evidence": f"{len(table_rows)} literal source HTML <table> tags in selected Item 2; div/span tabular layout may also exist and requires human inspection.",
               "status": "PENDING_HUMAN_REVIEW"},
              {"type": "PAGINATION_AND_REFERENCES",
               "evidence": "Page breaks, footers, cross references and linked disclosures require direct human inspection; no whole-chapter coverage interval is inferred.",
               "status": "PENDING_HUMAN_REVIEW"}]
    for risk in table_index.get("layout_risks", []):
        result.append({"type": "SOURCE_LAYOUT_RISK", "evidence": str(risk),
                       "status": "PENDING_HUMAN_REVIEW"})
    if table_index.get("boundary_layout_tables"):
        result.append({"type": "HEADING_LAYOUT_TABLES",
                       "evidence": f"{len(table_index['boundary_layout_tables'])} source tables enclose an Item 2 or Item 3 heading and are shown separately from in-body data tables.",
                       "status": "PENDING_HUMAN_REVIEW"})
    if table_index.get("numeric_layout_candidates"):
        result.append({"type": "NON_TABLE_NUMERIC_LAYOUT_CANDIDATES",
                       "evidence": f"{len(table_index['numeric_layout_candidates'])} numeric source blocks require human layout review.",
                       "status": "PENDING_HUMAN_REVIEW"})
    if any(row.get("flattened_section_char_start") is None for row in table_rows):
        result.append({"type": "UNMAPPED_FLATTENED_TABLE",
                       "evidence": "One or more source tables lack a unique exact location in normalized Item 2 text; their source and flattened text are retained for review.",
                       "status": "NEEDS_REVIEW"})
    auxiliary = {key: identity.get(key) for key in
                 ("acquisition_status", "current_submissions_status", "filing_index_status")}
    if any(value is not None for value in auxiliary.values()):
        result.append({"type": "AUXILIARY_SOURCE_STATUS", "evidence": json.dumps(auxiliary, ensure_ascii=False),
                       "status": "SOURCE_SCOPE_RECORDED"})
    if adjudication is not None:
        result.append({"type": "VERSION_ADJUDICATION",
                       "evidence": adjudication["interpretation"],
                       "status": "UNREVIEWED_NEW_RAW_SNAPSHOT"})
    if section is None:
        result.append({"type": "BOUNDARY_AMBIGUITY", "evidence": "No unique programmatic Item 2 body; no extracted section is accepted.",
                       "status": "NEEDS_REVIEW"})
    else:
        control = sum(ord(c) < 32 and c not in "\t\n\r" for c in section)
        result.append({"type": "CHARACTERS", "evidence": f"U+FFFD={section.count(chr(0xfffd))}; unusual controls={control}.",
                       "status": "PROGRAM_CHECK_ONLY"})
    return result


def make_page(data: dict, review_index: dict) -> str:
    identity, boundary = data["identity"], data["boundary"]
    ticker = identity["ticker"]
    section = data["section"]
    selected = boundary.get("selected_span") or {}
    anchors = boundary.get("html_heading_anchors") or {}
    parts = ["<!doctype html><html lang='zh'><head><meta charset='utf-8'>",
             "<meta name='viewport' content='width=device-width,initial-scale=1'>",
             "<meta http-equiv='Content-Security-Policy' content=\"default-src 'none'; style-src 'unsafe-inline'; connect-src 'none'\">",
             f"<title>{esc(ticker)} 10-Q MD&amp;A 审阅</title><style>{STYLE}{TABLE_STYLE}</style></head><body>",
             f"<h1>{esc(ticker)} 10-Q Part I Item 2 MD&amp;A：研究者审阅</h1>",
             "<p class='warn'><strong>人工状态：PENDING_HUMAN_REVIEW。</strong>程序检查和候选页面不等于研究者决定。",
             f"边界状态：{esc(boundary['status'])}。请逐项核对后使用 <a href='review_form.html'>空白审阅表</a>。</p>",
             "<nav><a href='#identity'>身份</a> · <a href='#start'>开头</a> · <a href='#end'>结尾</a> · <a href='#tables'>表格</a> · <a href='#periods'>期间</a> · <a href='#anomalies'>异常</a></nav>",
             "<section id='identity'><h2>文件身份与版本</h2><table class='meta'>"]
    meta = [("公司", identity.get("company")), ("Ticker / CIK", f"{ticker} / {identity['cik']}"),
            ("Accession / Form", f"{identity['accession']} / {identity['form']}"),
            ("报告期末 / 申报日期", f"{identity.get('report_period_end')} / {identity.get('filing_date')}"),
            ("主文件", identity.get("primary_document")),
            ("完整原文 SHA-256", data["hashes"]["original_submission_sha256"]),
            ("主 HTML SHA-256", data["hashes"]["primary_document_sha256"]),
            ("清理主文档 SHA-256", data["hashes"]["primary_text_sha256"]),
            ("Item 2 候选 SHA-256", selected.get("section_sha256")),
            ("原始来源", identity.get("original_submission_url")),
            ("主文档来源", identity.get("primary_document_url")),
            ("旧独立 HTML SHA-256", (data["version_adjudication"] or {}).get("prior_standalone_primary_sha256")),
            ("附加版本核验决定", (data["version_adjudication"] or {}).get("adjudication_status")),
            ("历史快照等价", (data["version_adjudication"] or {}).get("historical_snapshot_equivalence")),
            ("版本差异核验", (data["version_adjudication"] or {}).get("interpretation")),
            ("当前 submissions 状态", identity.get("current_submissions_status")),
            ("filing index 状态", identity.get("filing_index_status"))]
    parts += [f"<tr><th>{esc(k)}</th><td><code>{esc(v)}</code></td></tr>" for k, v in meta]
    parts += ["</table><p class='warn'>清理主文档位置是 Python Unicode 字符；主 HTML 位置是 UTF-8 字节；完整申报位置是原始字节。三者不可互换。HTML 标题锚点仅作定位线索，不等于清理章节边界。</p></section>",
              "<section id='start'><h2>Item 2 开头及目录候选</h2><table class='meta'><tr><th>候选</th><th>分类</th><th>清理文本字符起止</th><th>标题</th></tr>"]
    for n, candidate in enumerate(boundary["candidates"]):
        parts.append(f"<tr><td>{n}</td><td>{esc(candidate.get('classification'))}</td><td>{esc(candidate.get('start_char'))}–{esc(candidate.get('end_char_exclusive'))}</td><td>{esc(candidate.get('heading'))}</td></tr>")
    parts.append("</table><div class='grid'><div class='panel'><h3>原 HTML 标题附近的安全可视片段</h3>")
    parts.append(source_anchor_preview(data["html"], anchors.get("selected_item2")))
    parts.append("<p><small>仅为保存 HTML 的局部、安全预览；请用原件核对上下文。</small></p></div><div class='panel'><h3>清理主文档对应处</h3><pre>")
    if selected:
        a = selected["start_char"]
        parts.append(esc(data["primary"][max(0, a - 450):a + 750]))
    else:
        parts.append("无唯一选中章节；请检查上方全部候选及 boundary_candidates.json。")
    parts.append("</pre></div><div class='panel'><h3>实际提取文本开头</h3><pre>")
    parts.append(esc(section[:900] if section else "未生成唯一 Item 2 提取文本；NEEDS_REVIEW。"))
    parts.append("</pre></div></div></section>")

    parts += ["<section id='end'><h2>Item 2 结尾与 Item 3 边界</h2><div class='grid'><div class='panel'><h3>提取文本结尾</h3><pre>",
              esc(section[-900:] if section else "未生成唯一 Item 2 提取文本。"),
              "</pre></div><div class='panel'><h3>清理主文档后续内容</h3><pre>"]
    if selected:
        b = selected["end_char_exclusive"]
        parts.append(esc(data["primary"][max(0, b - 400):b + 750]))
    else:
        parts.append("边界未选定。")
    parts += ["</pre></div><div class='panel'><h3>原 HTML 下一标题附近的安全可视片段</h3>",
              source_anchor_preview(data["html"], anchors.get("selected_item3")),
              "</div></div></section>"]

    parts += [f"<section id='tables'><h2>Item 2 内的字面 HTML &lt;table&gt; 标签：{len(data['tables'])} 个</h2>",
              "<p>这个计数不等于语义表格数。左侧展示保存的原始 HTML 表格结构的安全预览，右侧展示程序压平文本；原始 HTML 的脚本、外部资源和事件属性不执行。期间、单位、负号、百分比和脚注仍须研究者逐张判断。</p>"]
    if data["boundary_layout_tables"]:
        parts.append("<h3>包含 Item 2 / Item 3 标题的边界布局表（不计入正文数据表）</h3>")
        for row in data["boundary_layout_tables"]:
            a, b = row["html_source_char_span"]
            label = f"BOUNDARY-{row['source_table_index']}"
            parts += [f"<details class='card'><summary>{esc(label)} · {esc(', '.join(row['roles']))} · {esc(row['review_status'])}</summary>",
                      "<p>需判断表格包裹的标题与相邻正文分界；下方保留其完整来源表格的安全预览。该表不计入 Item 2 正文表格数量。</p>",
                      "<div class='table-scroll'>", safe_preview(data["html"][a:b]), "</div>",
                      "<p>清理文本片段（程序记录，可能截短）：</p><pre>", esc(row["flattened_excerpt"]), "</pre>",
                      "<pre>", display_json({k: row.get(k) for k in
                          ("html_source_char_span", "complete_submission_byte_span", "raw_html_sha256")}),
                      "</pre></details>"]
    for row in data["tables"]:
        details = {"source_table_index": row.get("source_table_index"),
                   "primary_html_byte_span": [row["html_source_byte_start"], row["html_source_byte_end_exclusive"]],
                   "complete_submission_byte_span": [row["complete_submission_byte_start"], row["complete_submission_byte_end_exclusive"]],
                   "normalized_section_unicode_char_span": [row["flattened_section_char_start"], row["flattened_section_char_end_exclusive"]],
                   "raw_html_fragment_sha256": row["raw_html_fragment_sha256"],
                   "flattened_text_sha256": row["flattened_text_sha256"]}
        h0, h1 = row["html_source_byte_start"], row["html_source_byte_end_exclusive"]
        original_table = data["html"].encode("utf-8")[h0:h1].decode("utf-8")
        parts += [f"<details class='card'><summary>{esc(row['table_id'])} · 源表 #{esc(row.get('source_table_index'))} · {esc(row.get('review_status'))}</summary>",
                  "<div class='grid'><div class='panel'><h3>原 HTML 行列（安全预览）</h3><div class='table-scroll'>",
                  safe_preview(original_table),
                  "</div></div><div class='panel'><h3>压平后精确文本</h3><pre>",
                  esc(row["flattened_text"]),
                  "</pre></div></div><p>表前上下文：</p><pre>",
                  esc(row.get("preceding_context", "")),
                  "</pre><p>标题/表前来源、期间、单位与脚注线索（仅程序记录；标题、统一单位和脚注完整性未确认）：</p><pre>",
                  display_json({k: row.get(k) for k in
                      ("source_caption_context", "caption_candidate", "period_hints", "unit_hints",
                       "unit_evidence", "footnote_hints", "footnote_evidence",
                       "following_source_context", "adjacent_text_note_candidates")}),
                  "</pre>"]
        if row["adjacent_text_note_candidates"]:
            f0, f1 = row["following_source_context"]["html_source_char_span"]
            parts += ["<p class='warn'>以下是紧邻表后的文字候选；程序没有证明这些文字属于本表脚注。请核对来源和位置。</p>",
                      "<div class='grid'><div class='panel'><h3>表后原 HTML 安全预览</h3><div class='source-preview'>",
                      safe_preview(data["html"][f0:f1]), "</div></div>",
                      "<div class='panel'><h3>清理后相邻文字候选</h3><pre>",
                      display_json(row["adjacent_text_note_candidates"]),
                      "</pre></div></div>"]
        parts += ["<p>来源坐标和哈希：</p><pre>", display_json(details), "</pre></details>"]
    if not data["tables"]:
        parts.append("<p class='warn'>当前选定章节中没有字面 HTML &lt;table&gt; 标签，或边界尚未选定；不能据此认定没有语义表格。请特别检查 div/span 数字布局。</p>")
    if data["numeric_layout_candidates"]:
        parts.append("<h3>非 table 数字布局候选</h3><p class='warn'>这些只是数字密集区块，不是已确认语义表格；需对照原始布局核查。</p>")
        for n, row in enumerate(data["numeric_layout_candidates"], 1):
            a, b = row["html_source_char_span"]
            parts += [f"<details class='card'><summary>LAYOUT-{n} · {esc(row.get('classification'))}</summary>",
                      "<div class='table-scroll'>", safe_preview(data["html"][a:b]), "</div><pre>",
                      esc(row.get("text_excerpt")), "</pre><pre>",
                      display_json({k: row.get(k) for k in ("html_source_char_span", "source_html_sha256")}),
                      "</pre></details>"]
    parts.append("</section>")

    periods = data["periods"]
    parts += ["<section id='periods'><h2>叙述与表格期间</h2>",
              "<p class='warn'>前一报告期末次日只是代理；不充当整章真实覆盖起点。季度、年初至今、滚动期及时点可同时存在；未证实值保留 null。以下是程序找到的线索，不是研究者确认的期间。</p>",
              "<table class='meta'>"]
    for label, key in (("期间线索状态", "status"), ("报告财年", "report_fiscal_year"),
                       ("报告财季", "report_fiscal_quarter"), ("报告期末", "report_period_end"),
                       ("整章期间", "chapter_period"), ("整章期间未定原因", "chapter_period_reason"),
                       ("前期末次日代理", "coverage_start_proxy")):
        parts.append(f"<tr><th>{esc(label)}</th><td>{esc(periods.get(key))}</td></tr>")
    parts.append("</table>")
    for label, key in (("叙述期间线索", "narrative_periods"),
                       ("表格期间线索", "table_periods"), ("其他期间线索", "other_periods")):
        records = periods.get(key) or []
        parts += [f"<details class='card'><summary>{esc(label)}：{len(records)} 条（来源位置均属提取章节 Unicode 字符）</summary><table class='meta'>",
                  "<tr><th>位置 / 表号</th><th>词句与日期</th><th>状态和完整记录</th></tr>"]
        for record in records:
            location = (str(record.get("table_id")) if record.get("table_id") is not None else
                        f"{record.get('source_start_char')}–{record.get('source_end_char_exclusive')}")
            brief = record.get("source_excerpt") or record.get("phrase") or record.get("title") or ""
            parts.append(f"<tr><td>{esc(location)}</td><td>{esc(str(brief)[:350])}</td>"
                         f"<td><details><summary>{esc(record.get('status'))}</summary><pre>{display_json(record)}</pre></details></td></tr>")
        parts.append("</table></details>")
    parts += ["</section>",
              "<section id='anomalies'><h2>异常与人工核查</h2><table class='meta'><tr><th>类别</th><th>线索</th><th>状态</th></tr>"]
    for item in review_index["anomalies"]:
        parts.append(f"<tr><td>{esc(item['type'])}</td><td>{esc(item['evidence'])}</td><td>{esc(item['status'])}</td></tr>")
    parts += ["</table><p>请在 <a href='review_form.html'>空白审阅表</a> 记录逐项判断。程序通过、打开页面或回复“继续”不构成人工接受。</p></section>",
              "</body></html>"]
    return "".join(parts)


def make_form(fixed: dict, table_ids: list[str], ticker: str, program_pass: bool) -> str:
    body = ["<!doctype html><html lang='zh'><head><meta charset='utf-8'>",
            "<meta name='viewport' content='width=device-width,initial-scale=1'>",
            "<meta http-equiv='Content-Security-Policy' content=\"default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'none'\">",
            f"<title>{esc(ticker)} MD&amp;A 审阅表</title><style>{STYLE}{TABLE_STYLE}</style></head><body>",
            f"<h1>{esc(ticker)} 研究者审阅表</h1><p class='warn'>初始状态：<strong>PENDING_HUMAN_REVIEW</strong>。没有预填审阅人、时间或决定。请先看 <a href='index.html'>证据页面</a>；导出和回读仅在本地浏览器进行。</p>",
            "<div class='card'><h2>绑定的文件及页面版本</h2><table class='meta'>"]
    for key, value in fixed.items():
        body.append(f"<tr><th>{esc(key)}</th><td><code>{esc(value)}</code></td></tr>")
    body += ["</table></div><form id='review'><div class='card'><h2>逐项判断</h2>"]
    for key, label in (("start", "Item 2 开头与目录排除"), ("end", "Item 2 结尾与 Item 3 边界"),
                       ("body", "正文、分页、交叉引用及异常")):
        body += [f"<label>{esc(label)}<select name='{key}'><option value='' selected>— 尚未判断 —</option>",
                 "<option value='YES'>YES</option><option value='NO'>NO</option><option value='UNCLEAR'>UNCLEAR</option></select></label>"]
    for table_id in table_ids:
        label = ("叙述、表格与其他期间覆盖" if table_id == "PERIODS" else
                 f"{table_id} 标题布局表及章节边界" if table_id.startswith("BOUNDARY-") else
                 f"{table_id} 数字布局与正文对应" if table_id.startswith("LAYOUT-") else
                 f"{table_id} 行列、日期、单位、负数、百分号和脚注")
        body += [f"<label>{esc(label)}",
                 f"<select name='{esc(table_id)}'><option value='' selected>— 尚未判断 —</option>",
                 "<option value='YES'>YES</option><option value='NO'>NO</option><option value='UNCLEAR'>UNCLEAR</option></select></label>"]
    body += ["</div><div class='card'><h2>意见与身份（本人填写）</h2>",
             "<label>异常及处理意见<textarea name='issues'></textarea></label>",
             "<label>审阅人<input name='reviewer' autocomplete='off'></label>",
             "<label>审阅时间<input name='review_time' type='datetime-local'></label>",
             "<label>总体决定<select name='decision'><option value='' selected>— 尚未决定 —</option>",
             "<option value='ACCEPT'>ACCEPT</option><option value='REVISE'>REVISE</option><option value='REJECT'>REJECT</option></select></label>",
             "<button type='button' id='save'>下载带版本哈希的 JSON</button>",
             "<label>重新载入已导出的 JSON<input id='import-file' type='file' accept='.json,application/json'></label>",
             "<button type='button' id='load'>校验并回读 JSON</button>",
             "<p id='save-status' aria-live='polite'></p><p id='load-status' aria-live='polite'></p>",
             "<p><small>导出不自动改变项目验收；实际浏览器保存/回读需另行实测。边界程序未通过时，即使表单填满也不能越过程序门槛。</small></p></div></form>"]
    script = js_for_form(fixed, table_ids)
    needle = "'AAPL_10Q_MDA_review_v4_'"
    need(script.count(needle) == 1, "v4 form export prefix changed; inspect before reuse")
    script = script.replace(needle, json.dumps(f"{ticker}_10Q_MDA_review_M8_"), 1)
    guard = "function gateFor(fields) {\n  return"
    need(script.count(guard) == 1, "v4 form gate code changed; inspect before reuse")
    script = script.replace(guard, "function gateFor(fields) {\n  if (fixed.program_boundary_status !== 'PROGRAM_BOUNDARY_PASS') return 'NOT_MET';\n  return", 1)
    need(program_pass == (fixed["program_boundary_status"] == "PROGRAM_BOUNDARY_PASS"), "Form program status conflict")
    return "".join(body) + script + "</body></html>"


def build_packet(ingest: Path, extraction: Path, output: Path) -> dict:
    need(not output.exists(), "Review packet path exists; choose a new run/version")
    data = checked_inputs(ingest, extraction)
    identity = data["identity"]
    boundary = data["boundary"]
    section_sha = sha(extraction / "item2_candidate.txt") if data["section"] is not None else None
    extra_review_ids = ["PERIODS"]
    extra_review_ids += [f"BOUNDARY-{row['source_table_index']}" for row in data["boundary_layout_tables"]]
    extra_review_ids += [f"LAYOUT-{n}" for n, _ in enumerate(data["numeric_layout_candidates"], 1)]
    review_index = {
        "kind": "M8_10Q_MDA_REVIEW_INDEX",
        "status": "PENDING_HUMAN_REVIEW",
        "program_boundary_status": boundary["status"],
        "identity": {key: identity.get(key) for key in ("ticker", "company", "cik", "accession", "form",
                     "report_period_end", "filing_date", "primary_document", "original_submission_url", "primary_document_url")},
        "source_hashes": {**data["hashes"], "section_sha256": section_sha,
                          "boundary_candidates_sha256": sha(extraction / "boundary_candidates.json"),
                          "table_index_sha256": sha(extraction / "table_index.json"),
                          "period_ledger_sha256": sha(extraction / "period_ledger.json")},
        "version_adjudication": data["version_adjudication"],
        "coordinates": boundary["coordinates"],
        "selected_span": boundary.get("selected_span"),
        "table_count": len(data["tables"]),
        "table_count_meaning": "literal HTML <table> tags within selected Item 2 source span",
        "source_html_table_count": data["table_index"].get("source_html_table_count"),
        "boundary_layout_table_count": len(data["boundary_layout_tables"]),
        "numeric_layout_candidate_count": len(data["numeric_layout_candidates"]),
        "layout_risks": data["table_index"].get("layout_risks", []),
        "table_ids": [row["table_id"] for row in data["tables"]],
        "additional_review_item_ids": extra_review_ids,
        "period_status": data["periods"].get("status"),
        "anomalies": anomalies(boundary, data["section"], data["tables"],
                               data["table_index"], identity, data["version_adjudication"]),
        "human_reviewer": None, "human_review_time": None, "human_decision": None,
        "browser_render_status": "NOT_VERIFIED", "browser_export_import_status": "NOT_VERIFIED",
    }
    output.mkdir(parents=True)
    (output / "index.html").write_text(make_page(data, review_index), encoding="utf-8")
    page_sha = sha(output / "index.html")
    fixed = {"company": identity.get("company"), "cik": identity["cik"],
             "accession": identity["accession"], "form": identity["form"],
             "report_period_end": identity.get("report_period_end"), "filing_date": identity.get("filing_date"),
             "raw_sha256": data["hashes"]["original_submission_sha256"],
             "html_sha256": data["hashes"]["primary_document_sha256"],
             "primary_text_sha256": data["hashes"]["primary_text_sha256"],
             "section_sha256": section_sha, "review_page_sha256": page_sha,
             "review_packet_version": VERSION, "program_boundary_status": boundary["status"]}
    (output / "review_form.html").write_text(
        make_form(fixed, review_index["table_ids"] + extra_review_ids,
                  identity["ticker"], section_sha is not None),
        encoding="utf-8")
    (output / "review_index.json").write_text(json.dumps(review_index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (output / "review_form.md").write_text(
        f"# {identity['ticker']} 10-Q Item 2 MD&A 审阅表\n\n"
        "状态：PENDING_HUMAN_REVIEW\n\n"
        f"原始申报 SHA-256：`{fixed['raw_sha256']}`\n"
        f"章节 SHA-256：`{section_sha}`\n"
        f"审阅页 SHA-256：`{page_sha}`\n\n"
        "开头：[ ] ACCEPT [ ] REVISE [ ] REJECT\n\n结尾：[ ] ACCEPT [ ] REVISE [ ] REJECT\n\n"
        + "".join(f"{tid}：[ ] ACCEPT [ ] REVISE [ ] REJECT\n\n" for tid in review_index["table_ids"])
        + "".join(f"{tid}：[ ] ACCEPT [ ] REVISE [ ] REJECT\n\n" for tid in extra_review_ids)
        + "正文及异常：[ ] ACCEPT [ ] REVISE [ ] REJECT\n\n"
        "审阅人：\n\n审阅时间：\n\n意见与位置：\n\n总体决定：\n",
        encoding="utf-8")
    (output / "START_HERE.md").write_text(
        f"# {identity['ticker']} 10-Q MD&A 审阅交接\n\n"
        "先看 `index.html` 的身份、开头、结尾、表格、期间和异常，再在空白 `review_form.html` "
        "逐项填写并导出 JSON，选择导出文件回读核对。来源和页面版本已绑定；打开页面或导出草稿不构成人工接受。"
        "若程序边界为 NEEDS_REVIEW，必须先解决歧义，不能让表单越过程序门槛。"
        "本包没有网络请求；本地浏览器实际渲染及导出/回读须另行验证。\n",
        encoding="utf-8")
    outputs = [{"path": p.name, "bytes": p.stat().st_size, "sha256": sha(p)}
               for p in sorted(output.iterdir()) if p.is_file()]
    result = {"kind": "M8_LOCAL_10Q_MDA_REVIEW_PACKET", "ticker": identity["ticker"],
              "status": "PENDING_HUMAN_REVIEW", "program_boundary_status": boundary["status"],
              "raw_sha256": fixed["raw_sha256"], "section_sha256": section_sha,
              "review_page_sha256": page_sha, "form_version": VERSION,
              "version_adjudication": data["version_adjudication"],
              "source_code_sha256": sha(Path(__file__)),
              "inputs_rehashed": {**data["hashes"], "source_identity_sha256": sha(ingest / "source_identity.json"),
                                  "extraction_receipt_sha256": sha(extraction / "extraction_receipt.json"),
                                  "version_adjudication_sha256": (data["version_adjudication"] or {}).get("adjudication_sha256")},
              "outputs": outputs, "table_count": len(data["tables"]),
              "boundary_layout_table_count": len(data["boundary_layout_tables"]),
              "numeric_layout_candidate_count": len(data["numeric_layout_candidates"]),
              "human_reviewer": None, "human_review_time": None, "human_decision": None,
              "browser_render_status": "NOT_VERIFIED", "browser_export_import_status": "NOT_VERIFIED",
              "network_calls": "NONE"}
    (output / "receipt.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def build_overview(run_dir: Path, output: Path, extraction_name: str = "extraction_v4") -> dict:
    need(not output.exists(), "Overview exists; do not overwrite")
    need(bool(re.fullmatch(r"extraction(?:_v[1-9][0-9]*)?", extraction_name)),
         "Extraction directory name must be a local versioned extraction folder")
    output.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for ticker in FIXED:
        ingest = run_dir / "ingest" / ticker
        extraction = run_dir / extraction_name / ticker
        review = output.parent / ticker
        identity = read_json(ingest / "source_identity.json") if (ingest / "source_identity.json").is_file() else {}
        ex = read_json(extraction / "extraction_receipt.json") if (extraction / "extraction_receipt.json").is_file() else {}
        packet = read_json(review / "receipt.json") if (review / "receipt.json").is_file() else {}
        tables = read_json(extraction / "table_index.json") if (extraction / "table_index.json").is_file() else {}
        periods = read_json(extraction / "period_ledger.json") if (extraction / "period_ledger.json").is_file() else {}
        if packet:
            need(packet.get("inputs_rehashed", {}).get("source_identity_sha256") ==
                 sha(ingest / "source_identity.json") and
                 packet.get("inputs_rehashed", {}).get("extraction_receipt_sha256") ==
                 sha(extraction / "extraction_receipt.json"),
                 f"{ticker}: packet and overview inputs differ")
        reason = identity.get("reason") or ex.get("reason") or ex.get("error")
        if not packet and not reason and identity.get("status") == "NEEDS_VERSION_REVIEW":
            reason = "Raw-embedded primary differs from saved prior primary snapshot; version review required."
        if not reason and ex.get("status") == "NEEDS_REVIEW":
            reason = "Program could not identify one reliable Item 2 boundary."
        row = {"ticker": ticker, "accession": FIXED[ticker][1],
                     "acquisition_status": identity.get("status", "NOT_AVAILABLE"),
                     "acquisition_scope": identity.get("acquisition_status"),
                     "version_adjudication_status": (packet.get("version_adjudication") or {}).get("adjudication_status"),
                     "historical_snapshot_equivalence": (packet.get("version_adjudication") or {}).get("historical_snapshot_equivalence"),
                     "extraction_status": ex.get("status", "NOT_RUN"),
                     "table_count": len(tables["tables"]) if isinstance(tables.get("tables"), list) else None,
                     "boundary_layout_table_count": len(tables["boundary_layout_tables"])
                         if isinstance(tables.get("boundary_layout_tables"), list) else None,
                     "numeric_layout_candidate_count": len(tables["numeric_layout_candidates"])
                         if isinstance(tables.get("numeric_layout_candidates"), list) else None,
                     "period_status": periods.get("status", "NOT_RUN"),
                     "packet_status": packet.get("status", "NOT_READY"),
                     "human_review_status": packet.get("status", "NOT_READY_FOR_HUMAN_DECISION"),
                     "failure_or_blocker": reason,
                     "review_page_sha256": packet.get("review_page_sha256"),
                     "review_href": f"{ticker}/index.html" if packet else f"{ticker}/status.html"}
        if not packet:
            status_dir = output.parent / ticker
            status_dir.mkdir(parents=True, exist_ok=True)
            status_page = status_dir / "status.html"
            need(not status_page.exists(), f"{ticker}: existing status page would be overwritten")
            status_page.write_text(
                "<!doctype html><html lang='zh'><head><meta charset='utf-8'>"
                "<meta http-equiv='Content-Security-Policy' content=\"default-src 'none'; style-src 'unsafe-inline'; connect-src 'none'\">"
                f"<title>{ticker} 10-Q 接入状态</title><style>{STYLE}</style></head><body>"
                f"<h1>{ticker} 固定 10-Q 接入状态</h1>"
                "<p class='warn'>未形成可供研究者接受的章节审阅包；没有提取章节或人工决定。</p>"
                f"<pre>{display_json(row)}</pre></body></html>", encoding="utf-8")
            status_receipt = status_dir / "status_page_receipt.json"
            need(not status_receipt.exists(), f"{ticker}: existing status receipt would be overwritten")
            status_receipt.write_text(json.dumps({"status": "NOT_READY_FOR_HUMAN_DECISION",
                "page_sha256": sha(status_page), "source_identity_sha256":
                    sha(ingest / "source_identity.json") if (ingest / "source_identity.json").is_file() else None,
                "extraction_receipt_sha256":
                    sha(extraction / "extraction_receipt.json") if (extraction / "extraction_receipt.json").is_file() else None,
                "reason": reason}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        rows.append(row)
    index = {"kind": "M8_FOUR_COMPANY_REVIEW_OVERVIEW", "extraction_directory": extraction_name, "rows": rows,
             "human_review": "PENDING_HUMAN_REVIEW_FOR_ALL_AVAILABLE_PACKETS",
             "batch_representation": "NOT_RUN", "formal_financial_experiment": "NOT_RUN"}
    overview_style = (".overview-cards{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}"
                      ".firm-card{padding:16px;border:1px solid #9ba8ba;border-radius:8px;min-width:0;overflow-wrap:anywhere}"
                      ".firm-card dl{display:grid;grid-template-columns:minmax(100px,35%) minmax(0,1fr);gap:8px}"
                      ".firm-card dt{font-weight:700}.firm-card dd{margin:0;min-width:0;overflow-wrap:anywhere}"
                      "@media(max-width:760px){.overview-cards{grid-template-columns:1fr}.firm-card dl{grid-template-columns:1fr}}")
    body = ["<!doctype html><html lang='zh'><head><meta charset='utf-8'>",
            "<meta name='viewport' content='width=device-width,initial-scale=1'>",
            "<meta http-equiv='Content-Security-Policy' content=\"default-src 'none'; style-src 'unsafe-inline'; connect-src 'none'\">",
            "<title>四家公司 10-Q MD&amp;A 审阅入口</title><style>", STYLE, overview_style, "</style></head><body>",
            "<h1>四家公司 10-Q Part I Item 2 MD&amp;A 审阅入口</h1>",
            "<p class='warn'>固定 accession；本页只显示获取、程序提取和待审状态。没有研究者决定，不代表五家公司语料已验收。</p>",
            "<div class='overview-cards'>"]
    for row in rows:
        label = esc(row["ticker"])
        if row["review_href"]:
            label = f"<a href='{esc(row['review_href'])}'>{label}</a>"
        details = (("固定 accession", row["accession"]),
                   ("原 ingest 状态", row["acquisition_status"]),
                   ("附加版本核验", row["version_adjudication_status"]),
                   ("历史快照等价", row["historical_snapshot_equivalence"]),
                   ("程序提取", row["extraction_status"]),
                   ("正文 HTML 表数", row["table_count"]),
                   ("标题布局表数", row["boundary_layout_table_count"]),
                   ("非 table 数字候选", row["numeric_layout_candidate_count"]),
                   ("期间线索", row["period_status"]),
                   ("人工审阅", row["human_review_status"]),
                   ("失败或阻塞原因", row["failure_or_blocker"]))
        body.append(f"<section class='firm-card'><h2>{label}</h2><dl>")
        body.extend(f"<dt>{esc(key)}</dt><dd>{esc(value)}</dd>" for key, value in details)
        body.append("</dl></section>")
    body += ["</div><p>各公司若无可用审阅页，请查看其获取与提取失败记录，不替换既定文件。</p></body></html>"]
    output.write_text("".join(body), encoding="utf-8")
    metadata = output.parent / "overview.json"
    need(not metadata.exists(), "Overview metadata exists; do not overwrite")
    metadata.write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    result = {"kind": "M8_FOUR_COMPANY_REVIEW_OVERVIEW_RECEIPT", "page_sha256": sha(output),
              "overview_sha256": sha(metadata), "rows": len(rows), "source_code_sha256": sha(Path(__file__)),
              "status": "PENDING_HUMAN_REVIEW", "network_calls": "NONE"}
    receipt = output.parent / "overview_receipt.json"
    need(not receipt.exists(), "Overview receipt exists; do not overwrite")
    receipt.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ingest", type=Path)
    parser.add_argument("--extraction", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--run-dir", type=Path, help="Build four-company overview instead of one packet")
    parser.add_argument("--extraction-name", default="extraction_v4",
                        help="Versioned extraction folder used by the four-company overview")
    args = parser.parse_args()
    if args.run_dir:
        result = build_overview(args.run_dir.resolve(), args.output.resolve(), args.extraction_name)
    else:
        need(args.ingest is not None and args.extraction is not None,
             "--ingest and --extraction are required for a company packet")
        result = build_packet(args.ingest.resolve(), args.extraction.resolve(), args.output.resolve())
    print(json.dumps({k: result.get(k) for k in ("ticker", "status", "program_boundary_status", "table_count", "page_sha256", "review_page_sha256")},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
