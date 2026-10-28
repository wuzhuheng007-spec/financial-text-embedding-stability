"""Create a version-bound, importable AAPL review form from a frozen M7 packet.

The parent packet and all SEC sources are read-only. No network call is made.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FIXED_KEYS = ("company", "cik", "accession", "form", "report_period_end",
              "filing_date", "raw_sha256", "html_sha256", "primary_text_sha256",
              "section_sha256")


def sha(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def checked(path: Path, expected: dict) -> dict:
    assert path.is_file(), f"Missing frozen input: {path}"
    actual = {"path": str(path), "bytes": path.stat().st_size, "sha256": sha(path)}
    assert actual["bytes"] == expected["bytes"] and actual["sha256"] == expected["sha256"], (
        f"Frozen input mismatch: {path}")
    return actual


def js_for_form(fixed: dict, table_ids: list[str]) -> str:
    fixed_json = json.dumps(fixed, ensure_ascii=False, sort_keys=True).replace("<", "\\u003c")
    tables_json = json.dumps(table_ids)
    return """<script>
'use strict';
const fixed = FIXED_JSON;
const tableIds = TABLE_IDS;
const checks = ['start','end','body',...tableIds];
const fieldNames = [...checks,'issues','reviewer','review_time','decision'];
const required = [...checks,'reviewer','review_time','decision'];
let previousRecord = null;
function statusFor(fields) {
  return required.every(k => String(fields[k] || '').trim())
    ? 'HUMAN_DECISION_RECORDED' : 'PENDING_HUMAN_REVIEW';
}
function gateFor(fields) {
  return statusFor(fields) === 'HUMAN_DECISION_RECORDED' &&
    fields.decision === 'ACCEPT' && checks.every(k => fields[k] === 'YES')
    ? 'ELIGIBLE_FOR_SEPARATE_PROJECT_VALIDATION' : 'NOT_MET';
}
function validateRecord(record) {
  if (!record || record.schema_version !== 4 || !record.file_and_section || !record.fields) {
    throw Error('不是 v4 审阅记录');
  }
  for (const [key, value] of Object.entries(fixed)) {
    if (record.file_and_section[key] !== value) throw Error('文件或页面版本不匹配：' + key);
  }
  const fields = record.fields;
  if (Object.keys(fields).sort().join('|') !== [...fieldNames].sort().join('|')) {
    throw Error('审阅字段集合不匹配');
  }
  for (const key of checks) {
    if (!['','YES','NO','UNCLEAR'].includes(fields[key])) throw Error('无效判断：' + key);
  }
  if (!['','ACCEPT','REVISE','REJECT'].includes(fields.decision)) throw Error('无效总决定');
  for (const key of ['issues','reviewer','review_time']) {
    if (typeof fields[key] !== 'string') throw Error('无效文本字段：' + key);
  }
  if (fields.issues.length > 10000 || fields.reviewer.length > 200) throw Error('文本字段过长');
  if (fields.review_time && Number.isNaN(Date.parse(fields.review_time))) throw Error('审阅时间格式错误');
  if (record.status !== statusFor(fields) || record.acceptance_gate !== gateFor(fields)) {
    throw Error('记录状态与逐项决定不一致');
  }
  if (typeof record.exported_at_utc !== 'string' || Number.isNaN(Date.parse(record.exported_at_utc))) {
    throw Error('导出时间缺失或无效');
  }
  return fields;
}
document.getElementById('save').addEventListener('click', () => {
  const fields = Object.fromEntries(new FormData(document.getElementById('review')).entries());
  const now = new Date();
  const payload = {
    schema_version: 4, file_and_section: fixed, fields,
    status: statusFor(fields), acceptance_gate: gateFor(fields),
    reviewer_entered_local_time: fields.review_time || null,
    browser_time_zone: Intl.DateTimeFormat().resolvedOptions().timeZone || null,
    exported_at_utc: now.toISOString(), previous_record: previousRecord
  };
  const blob = new Blob([JSON.stringify(payload, null, 2)], {type: 'application/json'});
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = 'AAPL_10Q_MDA_review_v4_' + now.toISOString().replace(/[:.]/g, '-') + '.json';
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 30000);
  document.getElementById('save-status').textContent =
    '已请求下载 ' + a.download + '；请在下载目录确认文件，再用下方按钮重新载入。项目验收状态未自动改变。';
});
document.getElementById('load').addEventListener('click', async () => {
  const message = document.getElementById('load-status');
  const selected = document.getElementById('import-file').files[0];
  if (!selected) { message.textContent = '请先选择已导出的 JSON。'; return; }
  if (selected.size > 250000) { message.textContent = '文件超过 250 KB，拒绝载入。'; return; }
  try {
    const record = JSON.parse(await selected.text());
    const fields = validateRecord(record);
    const form = document.getElementById('review');
    for (const key of fieldNames) form.elements.namedItem(key).value = fields[key];
    previousRecord = record;
    message.textContent = '已核对原文、章节和页面哈希并回填字段；保存的意见未被自动认定为人工验收。';
  } catch (error) {
    message.textContent = '载入失败：' + String(error.message || error);
  }
});
</script>""".replace("FIXED_JSON", fixed_json).replace("TABLE_IDS", tables_json)


def build(parent: Path, output: Path) -> dict:
    assert not output.exists(), "Use a new output version; never overwrite the parent"
    receipt_path = parent / "receipt.json"
    old = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert old["kind"] == "M7_LOCAL_10Q_HUMAN_REVIEW_PACKET"
    assert old["status"] == "PENDING_HUMAN_REVIEW" and old["table_count"] == 6
    inputs = [checked(ROOT / row["path"], row) for row in old["input_files_rehashed"]]
    inputs.extend(checked(parent / row["path"], row) for row in old["outputs"])
    index = parent / "index.html"
    index_hash = sha(index)
    old_form = (parent / "review_form.html").read_text(encoding="utf-8")
    index_data = json.loads((parent / "table_review_index.json").read_text(encoding="utf-8"))
    identity = index_data["source_identity"]
    table_ids = [row["table_id"] for row in index_data["tables"]]
    assert table_ids == [f"MDA-{i}" for i in range(1, 7)]
    fixed = {key: identity[key] for key in FIXED_KEYS}
    fixed["review_page_sha256"] = index_hash
    fixed["review_packet_version"] = "v4"
    assert old_form.count("<script>") == 1 and old_form.count("</script>") == 1
    assert old_form.count("</table></div><form id='review'>") == 1
    assert old_form.count("<button type='button' id='save'>") == 1
    assert "<input name='reviewer' autocomplete='off'>" in old_form
    form = old_form.replace("<meta charset='utf-8'>", "<meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>", 1)
    form = form.replace("</table></div><form id='review'>",
                        "<tr><th>review_page_sha256</th><td><code>" + index_hash +
                        "</code></td></tr><tr><th>review_packet_version</th><td><code>v4</code></td></tr>"
                        "</table></div><form id='review'>", 1)
    form = form.replace("<button type='button' id='save'>下载本地 JSON</button>",
                        "<button type='button' id='save'>下载带版本哈希的 JSON</button>"
                        "<label>重新载入已导出的 JSON<input id='import-file' type='file' "
                        "accept='.json,application/json'></label>"
                        "<button type='button' id='load'>校验并回读 JSON</button>"
                        "<p id='load-status' aria-live='polite'></p>", 1)
    script_start = form.index("<script>")
    script_end = form.index("</script>", script_start) + len("</script>")
    form = form[:script_start] + js_for_form(fixed, table_ids) + form[script_end:]
    form = form.replace("本表只在本地导出 JSON，不向网络发送。",
                        "本表只在本地导出及回读 JSON，不向网络发送；新导出使用唯一文件名。", 1)
    assert "PENDING_HUMAN_REVIEW" in form and "review_page_sha256" in form

    output.mkdir(parents=True)
    for name in ("index.html", "table_review_index.json"):
        shutil.copyfile(parent / name, output / name)
    (output / "review_form.html").write_text(form, encoding="utf-8")
    md = (parent / "review_form.md").read_text(encoding="utf-8")
    assert "- Item 2 候选 SHA-256：" in md
    md = md.replace("- Item 2 候选 SHA-256：", "- 审阅页面 SHA-256：`" + index_hash + "`\n"
                    "- 审阅包版本：`v4`\n- Item 2 候选 SHA-256：", 1)
    (output / "review_form.md").write_text(md, encoding="utf-8")
    (output / "START_HERE.md").write_text(
        "# AAPL 10-Q MD&A researcher handoff v4\n\n"
        "The original v3 packet is preserved. `index.html` is byte-identical to v3 and its SHA-256 is `" +
        index_hash + "`. `review_form.html` starts blank. Inspect start, end, each of six tables, and anomalies. "
        "Use the form to export a uniquely named local JSON; confirm the downloaded file exists, then select it with "
        "the import control and verify the fields reload. The form checks exact raw/HTML/primary/section/page hashes. "
        "Do not enter a reviewer name, time, or decision on someone else's behalf. Exporting does not change project "
        "acceptance. Send the original JSON for separate validation and append-only archival.\n\n"
        "Browser rendering and save/reload still require a real local browser check; generator/static tests alone "
        "cannot establish that gate. All six tables remain PENDING_HUMAN_REVIEW.\n",
        encoding="utf-8")
    outputs = [{"path": path.name, "bytes": path.stat().st_size, "sha256": sha(path)}
               for path in sorted(output.iterdir()) if path.is_file()]
    result = {"kind": "M7_LOCAL_10Q_HUMAN_REVIEW_PACKET_V4",
              "status": "PENDING_HUMAN_REVIEW", "generated_utc": datetime.now(timezone.utc).isoformat(),
              "parent_packet": str(parent), "parent_receipt_sha256": sha(receipt_path),
              "review_page_sha256": index_hash, "form_version": "v4",
              "upgrade_source_sha256": sha(Path(__file__)), "input_files_rehashed": inputs,
              "outputs": outputs, "table_count": 6, "human_reviewer": None,
              "human_review_time": None, "human_decision": None,
              "browser_render_status": "NOT_VERIFIED", "browser_export_import_status": "NOT_VERIFIED",
              "network_calls": "NONE"}
    (output / "receipt.json").write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parent", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = build(args.parent.resolve(), args.output.resolve())
    print(json.dumps({"output": str(args.output), "page_sha256": result["review_page_sha256"],
                      "input_files_rehashed": len(result["input_files_rehashed"]),
                      "outputs": len(result["outputs"]), "status": result["status"]}))


if __name__ == "__main__":
    main()
