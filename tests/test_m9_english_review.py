"""Software and saved-source checks for the English M9 review delivery."""

from __future__ import annotations

from html import escape
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from scripts.m8_build_review import checked_inputs, sha
from scripts.m9_build_review import FIXED, VERSION, make_form, render_guide_markdown


ROOT = Path(__file__).resolve().parents[1]
M8_RUN = ROOT / "artifacts/m8/20261025T230645Z-four-10q"


def test_english_form_rejects_wrong_source_and_page_versions():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is unavailable for the form JavaScript validation check")
    fixed = {
        "company": "Software Test Company", "cik": "0000000001", "accession": "0000000001-25-000001",
        "raw_sha256": "a" * 64, "section_sha256": "b" * 64,
        "review_page_sha256": "c" * 64, "review_packet_version": VERSION,
        "program_boundary_status": "PROGRAM_BOUNDARY_PASS",
    }
    form = make_form(fixed, ["MDA-1", "PERIODS"], "TEST")
    assert "lang='en'" in form
    assert "PENDING_HUMAN_REVIEW" in form
    assert "<option value='ACCEPT'>ACCEPT</option>" in form
    script = form.split("<script>", 1)[1].split("</script>", 1)[0]
    program = r"""
const vm=require('node:vm');
const assert=require('node:assert/strict');
const context={document:{getElementById:()=>({addEventListener:()=>{}})}};
vm.runInNewContext(SOURCE+';globalThis.testApi={validateRecord,statusFor,gateFor};',context);
const api=context.testApi;
const fields={start:'',end:'',body:'','MDA-1':'',PERIODS:'',issues:'',reviewer:'',review_time:'',decision:''};
assert.equal(api.statusFor(fields),'PENDING_HUMAN_REVIEW');
assert.equal(api.gateFor(fields),'NOT_MET');
const record={schema_version:5,file_and_section:FIXED,fields,
  status:'PENDING_HUMAN_REVIEW',acceptance_gate:'NOT_MET',exported_at_utc:'2026-10-27T00:00:00Z'};
assert.equal(api.validateRecord(record),fields);
for (const key of ['raw_sha256','section_sha256','review_page_sha256','review_packet_version']) {
  const altered={...record,file_and_section:{...FIXED,[key]:'wrong'}};
  assert.throws(()=>api.validateRecord(altered),/Filing or page version mismatch/);
}
const stale={...record,schema_version:4};
assert.throws(()=>api.validateRecord(stale),/schema-version-5/);
""".replace("SOURCE", json.dumps(script)).replace("FIXED", json.dumps(fixed))
    result = subprocess.run([node, "-e", program], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_saved_real_source_tables_and_page_bindings():
    output_env = os.environ.get("M9_REVIEW_OUTPUT")
    if not output_env or not M8_RUN.is_dir():
        pytest.skip("Local M8 source and M9 review paths were not supplied for the real-source check")
    output = Path(output_env)
    overview = json.loads((output / "overview.json").read_text(encoding="utf-8"))
    entry = (output / "index.html").read_text(encoding="utf-8")
    opening = (output / "START_HERE.html").read_text(encoding="utf-8")
    guide = (output / "M9_ENGLISH_REVIEW_GUIDE.html").read_text(encoding="utf-8")
    overview_receipt = json.loads((output / "overview_receipt.json").read_text(encoding="utf-8"))
    assert "href='START_HERE.html'" in entry
    assert "href='M9_ENGLISH_REVIEW_GUIDE.html'" in entry
    assert "href='START_HERE.md'" not in entry
    assert "href='M9_ENGLISH_REVIEW_GUIDE.md'" not in entry
    assert "MSFT/index.html#start" in opening
    assert "Scope and source versions" in guide
    assert sha(output / "START_HERE.html") == overview_receipt["opening_html_sha256"]
    assert sha(output / "M9_ENGLISH_REVIEW_GUIDE.html") == overview_receipt["guide_html_sha256"]
    assert [row["ticker"] for row in overview["rows"]] == list(FIXED)
    total_tables = total_layout = 0
    for ticker, (_, accession) in FIXED.items():
        data = checked_inputs(M8_RUN / "ingest" / ticker, M8_RUN / "extraction_v4" / ticker)
        packet_dir = output / ticker
        index = json.loads((packet_dir / "review_index.json").read_text(encoding="utf-8"))
        receipt = json.loads((packet_dir / "receipt.json").read_text(encoding="utf-8"))
        page = (packet_dir / "index.html").read_text(encoding="utf-8")
        form = (packet_dir / "review_form.html").read_text(encoding="utf-8")
        assert data["identity"]["accession"] == accession
        assert index["source_hashes"]["original_submission_sha256"] == receipt["raw_sha256"]
        assert index["source_hashes"]["section_sha256"] == receipt["section_sha256"]
        assert sha(packet_dir / "index.html") == receipt["review_page_sha256"]
        assert receipt["review_page_sha256"] in form
        assert receipt["status"] == index["status"] == "PENDING_HUMAN_REVIEW"
        assert receipt["human_decision"] is None
        for row in data["tables"]:
            assert f"id='{row['table_id']}'" in page
            assert escape(row["flattened_text"]) in page
            for note in row["adjacent_text_note_candidates"]:
                assert escape(note["text"]) in page
        for row in data["boundary_layout_tables"]:
            assert f"BOUNDARY-{row['source_table_index']}" in page
        total_tables += len(data["tables"])
        total_layout += len(data["boundary_layout_tables"])
    assert total_tables == 44
    assert total_layout == 2


def test_restricted_guide_rendering_escapes_source_markup():
    rendered = render_guide_markdown("# Guide\n\n| Item | Status |\n| --- | --- |\n| <script> | **Pending** |\n")
    assert "<h1>Guide</h1>" in rendered
    assert "&lt;script&gt;" in rendered
    assert "<strong>Pending</strong>" in rendered
    assert "<script>" not in rendered
