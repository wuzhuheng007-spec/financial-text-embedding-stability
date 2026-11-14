"""Build a private, click-only preference screen from public macro event cards.

The catalog is a development selection aid. Choices are a user's interests,
not event facts, human gold labels, market outcomes, or model predictions.
The generated page and exported choices stay under ignored local artifacts.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CATALOG = ROOT / "results/news_macro_tech_20261115/candidate_events.json"
DEFAULT_PAGE = ROOT / "artifacts/macro_tech_20261115/preference_v1/index.html"
DEFAULT_RECEIPT = ROOT / "results/news_macro_tech_20261115/preference_ui_receipt.json"


PAGE = r'''<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>宏观与科技事件 · 点选关注度</title>
<style>
  :root { color-scheme: light; font-family: system-ui, "Microsoft YaHei", sans-serif; }
  body { margin: 0; background: #f4f7fb; color: #15283a; }
  header { background: #143650; color: white; padding: 1rem 1.25rem; }
  h1 { font-size: 1.4rem; margin: 0 0 .4rem; }
  header p { margin: .3rem 0; max-width: 70rem; line-height: 1.5; }
  .toolbar { background: #e4eef5; padding: .7rem 1rem; display: flex; gap: .5rem; flex-wrap: wrap; align-items: center; }
  button, .file-button { border: 1px solid #59758a; border-radius: .45rem; padding: .45rem .75rem; background: white; color: #15283a; font: inherit; cursor: pointer; }
  button:hover, .file-button:hover { background: #f2f8fc; }
  button:focus-visible, .file-button:focus-visible, input:focus-visible, a:focus-visible { outline: 3px solid #e59727; outline-offset: 2px; }
  button[aria-current="page"] { background: #12608b; color: white; border-color: #12608b; }
  button.done { border-color: #287653; }
  #nav { display: flex; gap: .3rem; flex-wrap: wrap; }
  #nav button { min-width: 2.5rem; }
  #progress { font-weight: 700; }
  #save-status { font-size: .9rem; }
  main { max-width: 1100px; margin: auto; padding: 1rem; display: grid; grid-template-columns: minmax(0, 3fr) minmax(19rem, 2fr); gap: 1rem; }
  section { min-width: 0; background: white; border: 1px solid #ccd9e3; border-radius: .55rem; padding: 1rem; }
  h2 { margin: .15rem 0 .6rem; font-size: 1.2rem; }
  .meta { color: #53677a; font-size: .93rem; }
  .fact-list { line-height: 1.55; padding-left: 1.3rem; }
  .fact-list li { margin: .45rem 0; }
  .hypothesis { background: #f5f9ec; border-left: 4px solid #7a9b39; padding: .75rem; line-height: 1.55; }
  .sources { overflow-wrap: anywhere; }
  .sources a { display: block; margin: .4rem 0; }
  fieldset { border: 1px solid #ccdae5; border-radius: .45rem; margin: 0 0 .85rem; padding: .7rem; }
  legend { font-weight: 700; padding: 0 .25rem; }
  .options { display: flex; flex-wrap: wrap; gap: .4rem; }
  .options label { border: 1px solid #b5c9d7; border-radius: .45rem; padding: .4rem .55rem; cursor: pointer; }
  .options label:has(input:checked) { border-color: #12608b; background: #e2f1fa; }
  .options input { margin-right: .2rem; }
  #import-file { display: none; }
  @media (max-width: 800px) { main { grid-template-columns: 1fr; } }
</style>
</head>
<body>
<header>
  <h1>宏观与科技事件：先点选你关注的材料</h1>
  <p>每张卡只需选择“是否关注”、相关度 1–5 分或跳过、优先对象。没有文字输入，也没有默认答案。“否”“不确定”“跳过”和“尚未回答”分别保存。</p>
  <p>这是候选事件的<strong>偏好筛查</strong>，不是事实金标准、模型输出或投资判断。事实和传导须再核验；所有选择留在本机，不会上传。</p>
</header>
<div class="toolbar">
  <span id="nav" aria-label="选择事件卡"></span>
  <button id="previous" type="button">上一张</button>
  <button id="next" type="button">下一张</button>
  <strong id="progress"></strong>
  <button id="download" type="button">下载选择 JSON</button>
  <label class="file-button" for="import-file">导入已保存选择 JSON</label>
  <input id="import-file" type="file" accept=".json,application/json">
  <span id="save-status" role="status" aria-live="polite"></span>
</div>
<main>
  <section aria-label="事件事实与来源">
    <div id="event-meta" class="meta"></div>
    <h2 id="event-title"></h2>
    <h3>已核验事实</h3>
    <ol id="fact-list" class="fact-list"></ol>
    <h3>待核验</h3>
    <ul id="unknown-list" class="fact-list"></ul>
    <p id="tech-link" class="hypothesis"></p>
    <h3>核验来源</h3>
    <div id="sources" class="sources"></div>
  </section>
  <section aria-label="点选偏好">
    <h2>你的选择</h2>
    <div id="question-list"></div>
  </section>
</main>
<script>
"use strict";
const CATALOG = __CATALOG_JSON__;
const STORAGE_KEY = "macro-tech-preference-v1-" + CATALOG.sha256;
const CATEGORY_LABELS = {
  MACRO_RELEASE:"宏观数据", MONETARY_POLICY:"货币政策",
  TECH_POLICY:"科技与产业政策", GEO_ENERGY:"地缘与能源",
  FINANCIAL_STABILITY:"金融稳定与杠杆", TECH_CHAIN:"科技产业链"
};
const QUESTIONS = [
  {key:"interest", title:"是否关注这件事？", options:[["YES","是"],["NO","否"],["UNSURE","不确定"]]},
  {key:"relevance", title:"与你想研究的问题有多相关？", options:[["1","1 很低"],["2","2"],["3","3"],["4","4"],["5","5 很高"],["SKIP","跳过"]]},
  {key:"focus", title:"你最想先看哪个对象？", options:[["BROAD_MARKET","大盘"],["CHIPS","芯片"],["CLOUD_DC","云与数据中心"],["OTHER_TECH","其他科技"],["SKIP","跳过"]]}
];
const ALLOWED = Object.fromEntries(QUESTIONS.map(q => [q.key, new Set(q.options.map(x => x[0]))]));
let answers = Object.fromEntries(CATALOG.events.map(e => [e.id, {}]));
let current = 0;
let storageOk = true;

function validatePayload(data) {
  if (!data || data.schema !== "macro_tech_preference_v1" || data.catalog_sha256 !== CATALOG.sha256 ||
      !Array.isArray(data.rows) || data.rows.length !== CATALOG.events.length) throw new Error("选择文件与当前事件集不一致");
  const checked = {};
  for (let i=0; i<CATALOG.events.length; i++) {
    const row = data.rows[i], event = CATALOG.events[i];
    if (!row || row.id !== event.id || !row.choices || typeof row.choices !== "object" || Array.isArray(row.choices)) throw new Error("事件顺序或格式不匹配");
    for (const [key,value] of Object.entries(row.choices)) if (!ALLOWED[key] || !ALLOWED[key].has(value)) throw new Error("含有未知选项");
    checked[event.id] = {...row.choices};
  }
  return checked;
}
function payload() {
  return {schema:"macro_tech_preference_v1", catalog_sha256:CATALOG.sha256,
    exported_at:new Date().toISOString(),
    rows:CATALOG.events.map(e => ({id:e.id, choices:{...answers[e.id]}}))};
}
function persist() {
  try { localStorage.setItem(STORAGE_KEY, JSON.stringify(payload())); storageOk=true; }
  catch (_) { storageOk=false; }
  document.getElementById("save-status").textContent = storageOk ?
    "已暂存在此浏览器；建议定期下载 JSON 备份" : "浏览器无法自动保存；请下载 JSON 备份";
}
function complete(a) { return QUESTIONS.every(q => Object.hasOwn(a, q.key)); }
function updateProgress() {
  const count = CATALOG.events.filter(e => complete(answers[e.id])).length;
  document.getElementById("progress").textContent = count + "/" + CATALOG.events.length + " 张已完成";
  for (const b of document.querySelectorAll("#nav button")) {
    const i = Number(b.dataset.index);
    b.classList.toggle("done", complete(answers[CATALOG.events[i].id]));
    if (i === current) b.setAttribute("aria-current","page"); else b.removeAttribute("aria-current");
  }
}
function renderQuestions(event) {
  const root=document.getElementById("question-list"); root.replaceChildren();
  for (const q of QUESTIONS) {
    const fieldset=document.createElement("fieldset"), legend=document.createElement("legend"), box=document.createElement("div");
    legend.textContent=q.title; box.className="options"; fieldset.append(legend);
    for (const [value,label] of q.options) {
      const item=document.createElement("label"), input=document.createElement("input");
      input.type="radio"; input.name=q.key; input.value=value; input.checked=(answers[event.id][q.key]===value);
      input.addEventListener("change",()=>{ answers[event.id][q.key]=value; persist(); updateProgress(); });
      item.append(input,document.createTextNode(label)); box.append(item);
    }
    fieldset.append(box); root.append(fieldset);
  }
}
function go(index) {
  current=Math.max(0,Math.min(CATALOG.events.length-1,index));
  const e=CATALOG.events[current];
  document.getElementById("event-meta").textContent=(current+1)+"/"+CATALOG.events.length+" · "+e.date+" · "+(CATEGORY_LABELS[e.category]||e.category)+" · "+e.id;
  document.getElementById("event-title").textContent=e.title_zh;
  const facts=document.getElementById("fact-list"); facts.replaceChildren();
  for (const fact of e.facts_zh) { const li=document.createElement("li"); li.textContent=fact; facts.append(li); }
  const unknowns=document.getElementById("unknown-list"); unknowns.replaceChildren();
  for (const item of e.unknowns_zh) { const li=document.createElement("li"); li.textContent=item; unknowns.append(li); }
  const hypothesis=e.tech_link_zh.replace(/^假设：\s*/,"");
  document.getElementById("tech-link").textContent="待检验的科技传导假设："+hypothesis;
  const sources=document.getElementById("sources"); sources.replaceChildren();
  e.source_urls.forEach((url,i)=>{ const a=document.createElement("a"); a.href=url; a.target="_blank"; a.rel="noopener noreferrer";
    a.textContent="来源 "+(i+1)+"："+new URL(url).hostname; sources.append(a); });
  document.getElementById("previous").disabled=current===0;
  document.getElementById("next").disabled=current===CATALOG.events.length-1;
  renderQuestions(e); updateProgress();
}
function download() {
  const blob=new Blob([JSON.stringify(payload(),null,2)+"\n"],{type:"application/json;charset=utf-8"});
  const url=URL.createObjectURL(blob), link=document.createElement("a");
  link.href=url; link.download="macro_tech_preference_choices.json";
  document.body.append(link); link.click(); link.remove();
  setTimeout(()=>URL.revokeObjectURL(url),1000);
}
async function importFile(file) {
  if (!file) return;
  try {
    if (file.size > 1024*1024) throw new Error("文件超过 1 MiB");
    const checked=validatePayload(JSON.parse(await file.text()));
    answers=checked; go(current); persist();
    document.getElementById("save-status").textContent="已核对事件集并导入；完成后请重新下载 JSON";
  } catch (error) { document.getElementById("save-status").textContent="导入失败："+error.message; }
}
function init() {
  const nav=document.getElementById("nav");
  CATALOG.events.forEach((event,i)=>{ const b=document.createElement("button"); b.type="button";
    b.textContent=String(i+1).padStart(2,"0"); b.dataset.index=String(i); b.title=event.title_zh;
    b.addEventListener("click",()=>go(i)); nav.append(b); });
  let restoreError=false;
  try { const saved=localStorage.getItem(STORAGE_KEY); if (saved) answers=validatePayload(JSON.parse(saved)); }
  catch (_) { storageOk=false; restoreError=true; }
  document.getElementById("previous").addEventListener("click",()=>go(current-1));
  document.getElementById("next").addEventListener("click",()=>go(current+1));
  document.getElementById("download").addEventListener("click",download);
  document.getElementById("import-file").addEventListener("change",event=>importFile(event.target.files[0]));
  go(0);
  document.getElementById("save-status").textContent = restoreError ?
    "旧暂存读取失败，原暂存未覆盖；请导入正确的选择 JSON" :
    "尚未写入新选择；点选后会尝试暂存，建议定期下载 JSON";
}
init();
</script>
</body>
</html>
'''


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_catalog(value: object) -> list[dict]:
    if not isinstance(value, dict) or value.get("schema") != "macro_tech_candidate_events_v1":
        raise ValueError("Unexpected catalog schema")
    events = value.get("events")
    if not isinstance(events, list) or not 1 <= len(events) <= 12:
        raise ValueError("Expected 1 to 12 candidate events")
    ids: set[str] = set()
    for event in events:
        if not isinstance(event, dict):
            raise ValueError("Event must be an object")
        for key in ("id", "category", "date", "title_zh", "title_en", "tech_link_zh", "tech_link_en"):
            if not isinstance(event.get(key), str) or not event[key].strip():
                raise ValueError(f"Missing event field: {key}")
        if event["id"] in ids:
            raise ValueError("Duplicate event ID")
        ids.add(event["id"])
        if date.fromisoformat(event["date"]).isoformat() != event["date"]:
            raise ValueError("Event date must be ISO YYYY-MM-DD")
        for key in ("facts_zh", "facts_en"):
            facts = event.get(key)
            if not isinstance(facts, list) or len(facts) != 2 or any(
                not isinstance(fact, str) or not fact.strip() for fact in facts
            ):
                raise ValueError(f"Expected exactly two nonempty facts: {key}")
        unknowns = event.get("unknowns_zh")
        if not isinstance(unknowns, list) or not unknowns or any(
            not isinstance(item, str) or not item.strip() for item in unknowns
        ):
            raise ValueError("Expected at least one pending check: unknowns_zh")
        urls = event.get("source_urls")
        if not isinstance(urls, list) or not urls or any(
            not isinstance(url, str)
            or urlsplit(url).scheme != "https"
            or not urlsplit(url).hostname
            or urlsplit(url).username is not None
            for url in urls
        ):
            raise ValueError("Expected public HTTPS source URLs without credentials")
    return events


def build(catalog_path: Path, page_path: Path, receipt_path: Path) -> dict:
    if page_path.exists() or receipt_path.exists():
        raise FileExistsError("Use new output paths; preserve existing reviewer choices")
    try:
        page_path.resolve().relative_to(ROOT)
    except ValueError:
        pass  # Explicit test output outside the repository.
    else:
        if not page_path.resolve().is_relative_to(ROOT / "artifacts"):
            raise ValueError("In-repository preference page must stay in ignored artifacts")
    value = json.loads(catalog_path.read_text(encoding="utf-8"))
    events = validate_catalog(value)
    embedded = [
        {key: event[key] for key in (
            "id", "category", "date", "title_zh", "title_en", "facts_zh", "facts_en",
            "unknowns_zh", "tech_link_zh", "tech_link_en", "source_urls"
        )}
        for event in events
    ]
    meta = {"sha256": sha256(catalog_path), "events": embedded}
    literal = json.dumps(meta, ensure_ascii=True, separators=(",", ":")).replace("<", "\\u003c")
    page_path.parent.mkdir(parents=True, exist_ok=True)
    page_path.write_text(PAGE.replace("__CATALOG_JSON__", literal), encoding="utf-8")
    receipt = {
        "schema": "macro_tech_preference_ui_receipt_v1",
        "candidate_catalog_sha256": meta["sha256"],
        "candidate_count": len(events),
        "questions_per_card": 3,
        "choice_export_schema": "macro_tech_preference_v1",
        "initial_saved_preferences": 0,
        "free_text_inputs": 0,
        "prefilled_answers": False,
        "human_gold_labels": 0,
        "model_calls": 0,
        "visual_browser_check": "NOT_RUN",
        "private_page_sha256": sha256(page_path),
        "interpretation": "User preference screen only; not fact gold or market outcome.",
    }
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--page", type=Path, default=DEFAULT_PAGE)
    parser.add_argument("--receipt", type=Path, default=DEFAULT_RECEIPT)
    args = parser.parse_args()
    print(json.dumps(build(args.catalog, args.page, args.receipt)))


if __name__ == "__main__":
    main()
