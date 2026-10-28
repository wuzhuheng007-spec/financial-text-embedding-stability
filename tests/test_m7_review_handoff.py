"""Software-only checks for the version-bound human-review handoff."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess

import pytest

from scripts.m7_upgrade_10q_review import build, js_for_form


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _synthetic_parent(tmp_path):
    parent = tmp_path / "parent"
    parent.mkdir()
    fixed = {key: "synthetic" for key in (
        "company", "cik", "accession", "form", "report_period_end",
        "filing_date", "raw_sha256", "html_sha256", "primary_text_sha256",
        "section_sha256")}
    (parent / "index.html").write_text("<html>synthetic software fixture</html>", encoding="utf-8")
    (parent / "review_form.html").write_text(
        "<html><head><meta charset='utf-8'></head><body><table>"
        "</table></div><form id='review'><input name='reviewer' autocomplete='off'>"
        "本表只在本地导出 JSON，不向网络发送。"
        "<button type='button' id='save'>下载本地 JSON</button>"
        "<script>old synthetic handler</script></form></body></html>", encoding="utf-8")
    (parent / "review_form.md").write_text(
        "- Item 2 候选 SHA-256：`synthetic`\n", encoding="utf-8")
    (parent / "table_review_index.json").write_text(json.dumps({
        "source_identity": fixed,
        "tables": [{"table_id": f"MDA-{i}"} for i in range(1, 7)]}), encoding="utf-8")
    (parent / "START_HERE.md").write_text("synthetic", encoding="utf-8")
    outputs = [{"path": p.name, "bytes": p.stat().st_size, "sha256": _sha(p.read_bytes())}
               for p in parent.iterdir()]
    (parent / "receipt.json").write_text(json.dumps({
        "kind": "M7_LOCAL_10Q_HUMAN_REVIEW_PACKET",
        "status": "PENDING_HUMAN_REVIEW", "table_count": 6,
        "input_files_rehashed": [], "outputs": outputs}), encoding="utf-8")
    return parent


def test_new_version_preserves_parent_and_rejects_tamper(tmp_path):
    parent = _synthetic_parent(tmp_path)
    before = {p.name: _sha(p.read_bytes()) for p in parent.iterdir()}
    output = tmp_path / "v4"
    receipt = build(parent, output)
    assert receipt["status"] == "PENDING_HUMAN_REVIEW"
    assert receipt["review_page_sha256"] == _sha((parent / "index.html").read_bytes())
    assert (output / "index.html").read_bytes() == (parent / "index.html").read_bytes()
    form = (output / "review_form.html").read_text(encoding="utf-8")
    assert "id='import-file'" in form and "id='load'" in form
    assert "review_page_sha256" in form and "schema_version: 4" in form
    assert {p.name: _sha(p.read_bytes()) for p in parent.iterdir()} == before
    (parent / "index.html").write_text("tampered", encoding="utf-8")
    with pytest.raises(AssertionError, match="Frozen input mismatch"):
        build(parent, tmp_path / "invalid")


def test_synthetic_js_import_rejects_wrong_page_and_false_status():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node unavailable for standalone JavaScript software check")
    fixed = {"raw_sha256": "a" * 64, "section_sha256": "b" * 64,
             "review_page_sha256": "c" * 64, "review_packet_version": "v4"}
    script = js_for_form(fixed, [f"MDA-{i}" for i in range(1, 7)])
    assert script.startswith("<script>") and script.endswith("</script>")
    source = script[len("<script>"):-len("</script>")]
    program = """
const assert = require('node:assert/strict');
const vm = require('node:vm');
const source = SOURCE;
const context = {document: {getElementById: () => ({addEventListener: () => {}})}};
vm.runInNewContext(source + ';globalThis.api={validateRecord,statusFor,gateFor};', context);
const api = context.api;
const fixed = FIXED;
const fields = {start:'YES',end:'YES',body:'YES',issues:'',reviewer:'Synthetic Tester',
  review_time:'2026-10-25T10:30',decision:'ACCEPT'};
for (let i=1;i<=6;i++) fields['MDA-'+i]='YES';
const record = {schema_version:4,file_and_section:fixed,fields,
  status:'HUMAN_DECISION_RECORDED',acceptance_gate:'ELIGIBLE_FOR_SEPARATE_PROJECT_VALIDATION',
  exported_at_utc:'2026-10-25T02:30:00.000Z'};
assert.equal(api.validateRecord(record).reviewer,'Synthetic Tester');
const wrong = JSON.parse(JSON.stringify(record));
wrong.file_and_section.review_page_sha256='d'.repeat(64);
assert.throws(() => api.validateRecord(wrong));
const falseStatus = JSON.parse(JSON.stringify(record));
falseStatus.fields.end='';
assert.throws(() => api.validateRecord(falseStatus));
assert.equal(api.statusFor(falseStatus.fields),'PENDING_HUMAN_REVIEW');
assert.equal(api.gateFor(falseStatus.fields),'NOT_MET');
console.log('synthetic software validation passed');
""".replace("SOURCE", json.dumps(source)).replace("FIXED", json.dumps(fixed))
    done = subprocess.run([node, "-e", program], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    assert "synthetic software validation passed" in done.stdout


def test_synthetic_js_export_then_reload_fields():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node unavailable for standalone JavaScript software check")
    fixed = {"raw_sha256": "a" * 64, "section_sha256": "b" * 64,
             "review_page_sha256": "c" * 64, "review_packet_version": "v4"}
    script = js_for_form(fixed, [f"MDA-{i}" for i in range(1, 7)])
    source = script[len("<script>"):-len("</script>")]
    program = """
const assert = require('node:assert/strict');
const vm = require('node:vm');
const handlers = {};
const inputNames = ['start','end','body','MDA-1','MDA-2','MDA-3','MDA-4','MDA-5','MDA-6',
  'issues','reviewer','review_time','decision'];
const inputs = Object.fromEntries(inputNames.map(k => [k,{value:''}]));
const form = {elements:{namedItem:k=>inputs[k]}};
const saveStatus = {textContent:''}, loadStatus = {textContent:''}, picker = {files:[]};
let outputBlob = null, outputName = null;
const document = {body:{appendChild:()=>{}},createElement:()=>({
  click(){outputName=this.download;}, remove(){}}),getElementById(id){
  if (id==='review') return form;
  if (id==='save-status') return saveStatus;
  if (id==='load-status') return loadStatus;
  if (id==='import-file') return picker;
  return {addEventListener(_event,fn){handlers[id]=fn;}};
}};
class FormDataMock {entries(){return inputNames.map(k=>[k,inputs[k].value]);}}
const context = {document,FormData:FormDataMock,Blob,Date,Intl,
  URL:{createObjectURL(blob){outputBlob=blob;return 'blob:synthetic';},revokeObjectURL(){}},
  setTimeout(){}};
vm.runInNewContext(SOURCE,context);
for (const k of ['start','end','body','MDA-1','MDA-2','MDA-3','MDA-4','MDA-5','MDA-6']) inputs[k].value='YES';
inputs.reviewer.value='Synthetic Tester'; inputs.review_time.value='2026-10-25T10:30';
inputs.decision.value='ACCEPT';
(async () => {
  handlers.save();
  const json = await outputBlob.text();
  const record = JSON.parse(json);
  assert.equal(record.schema_version,4);
  assert.equal(record.file_and_section.review_page_sha256,FIXED.review_page_sha256);
  assert.match(outputName,/AAPL_10Q_MDA_review_v4_.*\\.json/);
  for (const key of inputNames) inputs[key].value='';
  picker.files=[{size:Buffer.byteLength(json),text:async()=>json}];
  await handlers.load();
  assert.equal(inputs.reviewer.value,'Synthetic Tester');
  assert.equal(inputs['MDA-6'].value,'YES');
  assert.equal(inputs.decision.value,'ACCEPT');
  assert.match(loadStatus.textContent,/已核对/);
  console.log('synthetic export reload passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
""".replace("SOURCE", json.dumps(source)).replace("FIXED", json.dumps(fixed))
    done = subprocess.run([node, "-e", program], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    assert "synthetic export reload passed" in done.stdout
