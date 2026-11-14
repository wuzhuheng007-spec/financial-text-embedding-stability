"""Build a private, click-only screening page from the frozen first-ten manifest.

The page references existing local article HTML files. It embeds only ordinals,
hashes, and filenames; article text and model responses never enter this script's
public output. A saved choice export is private and is not an event gold set.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DIR = ROOT / "artifacts/news_targeted_repair_20261115/human_review_original_first10_v1"
DEFAULT_RECEIPT = ROOT / "results/news_constrained_decode_20261115/human_choice_ui_receipt.json"

PAGE = r'''<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>前十篇新闻 · 点选式人工筛查（本机私有）</title>
<style>
  :root { color-scheme: light; font-family: system-ui, "Microsoft YaHei", sans-serif; }
  body { margin: 0; color: #17212d; background: #f4f7fa; }
  header { padding: 1rem 1.2rem; background: #12304a; color: #fff; }
  h1 { font-size: 1.3rem; margin: 0 0 .4rem; }
  header p { margin: .25rem 0; max-width: 80rem; }
  .toolbar { display: flex; flex-wrap: wrap; gap: .5rem; align-items: center; padding: .8rem 1rem; background: #e5edf4; }
  button, .file-button { font: inherit; border: 1px solid #51718b; border-radius: .4rem; background: white; padding: .45rem .75rem; cursor: pointer; }
  button:hover, .file-button:hover { background: #edf5fb; }
  button:focus-visible, .file-button:focus-visible, input:focus-visible { outline: 3px solid #f0a429; outline-offset: 2px; }
  button[aria-current="page"] { color: white; background: #145b89; border-color: #145b89; }
  .done { border-color: #19704c; }
  #nav { display: flex; flex-wrap: wrap; gap: .35rem; }
  #nav button { min-width: 2.4rem; }
  #progress { font-weight: 700; }
  #save-status { font-size: .9rem; }
  main { display: grid; grid-template-columns: minmax(0, 3fr) minmax(20rem, 2fr); gap: 1rem; padding: 1rem; }
  .panel { min-width: 0; background: white; border: 1px solid #ccd8e2; border-radius: .5rem; overflow: hidden; }
  .panel h2 { font-size: 1.08rem; margin: 0; padding: .7rem 1rem; background: #eaf1f7; }
  iframe { display: block; width: 100%; height: 73vh; border: 0; background: white; }
  #question-list { max-height: 73vh; overflow-y: auto; padding: .7rem 1rem 1.2rem; }
  fieldset { border: 1px solid #d2dfe9; border-radius: .45rem; margin: 0 0 .85rem; padding: .65rem; }
  legend { font-weight: 700; padding: 0 .3rem; }
  .hint { color: #516478; font-size: .88rem; margin: .15rem 0 .55rem; }
  .options { display: flex; flex-wrap: wrap; gap: .4rem; }
  .options label { border: 1px solid #b8cbd9; border-radius: .4rem; padding: .38rem .52rem; cursor: pointer; }
  .options label:has(input:checked) { border-color: #145b89; background: #e1f0fa; }
  .options input { margin-right: .2rem; }
  .optional legend::after { content: " · 可跳过"; font-size: .8rem; font-weight: 400; color: #5b6b79; }
  .muted { color: #526579; }
  #import-file { display: none; }
  @media (max-width: 860px) { main { grid-template-columns: 1fr; } iframe { height: 52vh; } #question-list { max-height: none; } }
</style>
</head>
<body>
<header>
  <h1>前十篇新闻 · 点选式人工筛查</h1>
  <p>先读左侧原文，再点右侧选项。新闻语气评分与对公司的经营影响是两道不同问题。没有文字输入框；不确定就选“无法判断”。</p>
  <p>这里只做文章级筛查，尚不能作为完整公司—事件金标准，也不评估股票收益。原文、选择结果和下载文件均留在本机。</p>
</header>
<div class="toolbar">
  <span id="nav" aria-label="选择文章"></span>
  <button id="previous" type="button">上一篇</button>
  <button id="next" type="button">下一篇</button>
  <strong id="progress">0/10 篇完成核心题</strong>
  <button id="download" type="button">下载当前选择 JSON</button>
  <label class="file-button" for="import-file">导入已保存选择 JSON</label>
  <input id="import-file" type="file" accept=".json,application/json">
  <span id="save-status" role="status" aria-live="polite"></span>
</div>
<main>
  <section class="panel" aria-label="新闻原文"><h2 id="article-heading">文章 01 · 原文</h2><iframe id="article-frame" title="新闻原文" sandbox=""></iframe></section>
  <section class="panel" aria-label="点选题目"><h2>您的选择</h2><div id="question-list"></div></section>
</main>
<script>
"use strict";
const META = __META_JSON__;
const STORAGE_KEY = "n1-choice-screen-v1-" + META.sample_sha256;
const QUESTIONS = [
  {key:"company_link", title:"正文能识别受影响公司吗？", required:true, options:[["ONE","能，一家公司"],["MULTIPLE","能，多家公司"],["NONE","不能"],["UNKNOWN","无法判断"]]},
  {key:"event_count", title:"正文有具体公司事件吗？", required:true, options:[["ONE","有，一个主要事件"],["MULTIPLE","有，多个事件"],["NONE","没有明确事件"],["UNKNOWN","无法判断"]]},
  {key:"news_tone", title:"新闻语气评分", hint:"只评文章表述的语气，不是收益预测。", required:true, options:[["-2","明显负面"],["-1","偏负面"],["0","中性"],["1","偏正面"],["2","明显正面"],["UNKNOWN","无法判断"]]},
  {key:"business_impact", title:"对所涉公司的经营影响方向", hint:"根据正文判断；多个公司方向不同可选“混合”。", required:true, options:[["POSITIVE","正面"],["NEGATIVE","负面"],["MIXED","混合"],["NO_CLEAR_IMPACT","无明确方向"],["UNKNOWN","无法判断"]]},
  {key:"evidence_support", title:"原文是否支持您上面的公司、事件和方向判断？", required:true, options:[["CLEAR","清楚支持"],["PARTIAL","只支持一部分"],["NO","不支持"],["UNKNOWN","无法判断"]]},
  {key:"confidence", title:"您对本篇判断的把握程度", required:true, options:[["1","1 很低"],["2","2"],["3","3"],["4","4"],["5","5 很高"]]},
  {key:"impact_path", title:"影响路径", required:false, options:[["DIRECT","直接"],["INDIRECT","间接"],["BOTH","两者都有"],["UNKNOWN","无法判断"],["NOT_APPLICABLE","不适用"]]},
  {key:"statement_status", title:"影响判断来自哪里？", required:false, options:[["EXPLICIT","正文明确陈述"],["INFERENCE","我根据正文推断"],["MIXED","两者都有"],["UNKNOWN","无法判断"]]}
];
const EXPOSURE = [["NO","此前未看模型输出"],["YES","此前看过模型输出"],["UNSURE","不确定"]];
const allowed = Object.fromEntries(QUESTIONS.map(q => [q.key, new Set(q.options.map(x => x[0]))]));
let answers = Object.fromEntries(META.articles.map(a => [a.ordinal, {}]));
let exposure = null;
let current = 1;
let storageOk = true;

function validatePayload(x) {
  if (!x || x.schema !== "n1_article_choice_screen_v1" || x.sample_sha256 !== META.sample_sha256 ||
      x.review_manifest_sha256 !== META.review_manifest_sha256 || !Array.isArray(x.rows) || x.rows.length !== 10) throw new Error("样本或格式与本页不一致");
  if (x.prior_model_exposure !== null && !EXPOSURE.some(v => v[0] === x.prior_model_exposure)) throw new Error("模型接触状态不合法");
  const next = {};
  for (let i=0; i<10; i++) {
    const expected = META.articles[i], row = x.rows[i];
    if (!row || row.ordinal !== expected.ordinal || row.body_sha256 !== expected.body_sha256 || !row.choices || typeof row.choices !== "object" || Array.isArray(row.choices)) throw new Error("文章顺序或哈希不匹配");
    for (const [key,value] of Object.entries(row.choices)) if (!allowed[key] || !allowed[key].has(value)) throw new Error("含有未知选项");
    next[expected.ordinal] = {...row.choices};
  }
  return {answers:next, exposure:x.prior_model_exposure};
}
function payload() {
  return {schema:"n1_article_choice_screen_v1", sample_sha256:META.sample_sha256,
    review_manifest_sha256:META.review_manifest_sha256, prior_model_exposure:exposure,
    exported_at:new Date().toISOString(),
    rows:META.articles.map(a => ({ordinal:a.ordinal,body_sha256:a.body_sha256,choices:{...answers[a.ordinal]}}))};
}
function persist() {
  try { localStorage.setItem(STORAGE_KEY, JSON.stringify(payload())); storageOk=true; }
  catch (_) { storageOk=false; }
  document.getElementById("save-status").textContent = storageOk ? "已暂存在此浏览器；仍建议下载 JSON 备份" : "浏览器无法自动保存；请及时下载 JSON";
}
function completed(a) { return QUESTIONS.filter(q => q.required).every(q => Object.hasOwn(a,q.key)); }
function updateProgress() {
  const count = META.articles.filter(a => completed(answers[a.ordinal])).length;
  document.getElementById("progress").textContent = count + "/10 篇完成核心题";
  for (const button of document.querySelectorAll("#nav button")) {
    const ordinal = Number(button.dataset.ordinal);
    button.classList.toggle("done", completed(answers[ordinal]));
    if (ordinal === current) button.setAttribute("aria-current","page"); else button.removeAttribute("aria-current");
  }
}
function renderExposure() {
  const fieldset = document.createElement("fieldset"), legend = document.createElement("legend");
  legend.textContent = "此前是否看过这些文章的模型输出？（只选一次）"; fieldset.append(legend);
  const box = document.createElement("div"); box.className = "options";
  for (const [value,label] of EXPOSURE) {
    const item = document.createElement("label"), input = document.createElement("input");
    input.type="radio"; input.name="prior_model_exposure"; input.value=value; input.checked=(exposure===value);
    input.addEventListener("change",()=>{ exposure=value; persist(); });
    item.append(input,document.createTextNode(label)); box.append(item);
  }
  fieldset.append(box); return fieldset;
}
function renderQuestions() {
  const root = document.getElementById("question-list"); root.replaceChildren(renderExposure());
  const note = document.createElement("p"); note.className="muted"; note.textContent="标有“可跳过”的两题用于后续研究准备；核心题答完会显示完成。多个事件或多家公司将来还需更细的人工标注。"; root.append(note);
  for (const q of QUESTIONS) {
    const fieldset=document.createElement("fieldset"), legend=document.createElement("legend"), box=document.createElement("div");
    if (!q.required) fieldset.className="optional";
    legend.textContent=q.title; box.className="options"; fieldset.append(legend);
    if (q.hint) { const hint=document.createElement("p"); hint.className="hint"; hint.textContent=q.hint; fieldset.append(hint); }
    for (const [value,label] of q.options) {
      const item=document.createElement("label"), input=document.createElement("input");
      input.type="radio"; input.name=q.key; input.value=value; input.checked=(answers[current][q.key]===value);
      input.addEventListener("change",()=>{ answers[current][q.key]=value; persist(); updateProgress(); });
      item.append(input,document.createTextNode(label)); box.append(item);
    }
    fieldset.append(box); root.append(fieldset);
  }
}
function go(n) {
  current=Math.max(1,Math.min(10,n));
  const article=META.articles[current-1];
  document.getElementById("article-heading").textContent="文章 "+String(current).padStart(2,"0")+" · 原文";
  document.getElementById("article-frame").src=article.page;
  document.getElementById("previous").disabled=current===1;
  document.getElementById("next").disabled=current===10;
  renderQuestions(); updateProgress();
}
function download() {
  const text=JSON.stringify(payload(),null,2)+"\n";
  const blob=new Blob([text],{type:"application/json;charset=utf-8"});
  const url=URL.createObjectURL(blob), link=document.createElement("a");
  link.href=url; link.download="news_review_choices_20261115.json"; document.body.append(link); link.click(); link.remove();
  setTimeout(()=>URL.revokeObjectURL(url),1000);
}
async function importFile(file) {
  if (!file) return;
  try {
    const parsed=validatePayload(JSON.parse(await file.text()));
    answers=parsed.answers; exposure=parsed.exposure; go(current); persist();
    document.getElementById("save-status").textContent="已导入并核对样本哈希；请继续选择，完成后重新下载 JSON";
  } catch (error) { document.getElementById("save-status").textContent="导入失败："+error.message; }
}
function init() {
  const nav=document.getElementById("nav");
  for (const article of META.articles) {
    const button=document.createElement("button"); button.type="button"; button.textContent=String(article.ordinal).padStart(2,"0");
    button.dataset.ordinal=String(article.ordinal); button.addEventListener("click",()=>go(article.ordinal)); nav.append(button);
  }
  try { const saved=localStorage.getItem(STORAGE_KEY); if (saved) { const parsed=validatePayload(JSON.parse(saved)); answers=parsed.answers; exposure=parsed.exposure; } }
  catch (_) { storageOk=false; }
  document.getElementById("previous").addEventListener("click",()=>go(current-1));
  document.getElementById("next").addEventListener("click",()=>go(current+1));
  document.getElementById("download").addEventListener("click",download);
  document.getElementById("import-file").addEventListener("change",event=>importFile(event.target.files[0]));
  go(1); persist();
}
init();
</script>
</body>
</html>
'''


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build(manifest_path: Path, output_path: Path, receipt_path: Path) -> dict:
    if output_path.exists() or receipt_path.exists():
        raise FileExistsError("Use new output paths; preserve existing review work")
    folder = manifest_path.parent.resolve()
    if output_path.parent.resolve() != folder:
        raise ValueError("Private review page must stay beside the source articles")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    rows = manifest["rows"]
    if len(rows) != 10 or [row["ordinal"] for row in rows] != list(range(1, 11)):
        raise ValueError("Expected the fixed first ten articles")
    meta = {
        "sample_sha256": manifest["sample_sha256"],
        "review_manifest_sha256": sha256(manifest_path),
        "articles": [],
    }
    for row in rows:
        page_name = row["page"]
        if page_name != f'article_{row["ordinal"]:02d}.html':
            raise ValueError("Unexpected article page mapping")
        page = folder / page_name
        if not page.is_file() or sha256(page) != row["page_sha256"]:
            raise ValueError("Article page missing or changed")
        meta["articles"].append({
            "ordinal": row["ordinal"], "body_sha256": row["body_sha256"],
            "page": page_name, "page_sha256": row["page_sha256"],
        })
    meta_json = json.dumps(meta, ensure_ascii=True, separators=(",", ":")).replace("<", "\\u003c")
    content = PAGE.replace("__META_JSON__", meta_json)
    output_path.write_text(content, encoding="utf-8")
    receipt = {
        "schema": "n1_choice_review_ui_receipt_v1", "model_calls": 0,
        "sample_sha256": manifest["sample_sha256"],
        "review_manifest_sha256": meta["review_manifest_sha256"],
        "article_pages": len(rows), "question_count": 8, "core_question_count": 6,
        "initial_human_adjudications": 0, "ai_draft_embedded": False,
        "article_text_embedded": False, "free_text_inputs": 0,
        "private_page_sha256": sha256(output_path),
        "interpretation": "Click-only article screening, not full company-event gold; private exports stay local.",
    }
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_DIR / "review_manifest.json")
    parser.add_argument("--out", type=Path, default=DEFAULT_DIR / "review_choices.html")
    parser.add_argument("--receipt", type=Path, default=DEFAULT_RECEIPT)
    args = parser.parse_args()
    print(json.dumps(build(args.manifest, args.out, args.receipt)))


if __name__ == "__main__":
    main()
