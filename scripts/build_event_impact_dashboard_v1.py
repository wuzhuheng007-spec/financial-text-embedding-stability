"""Validate three source-bound development cards and render a new dashboard version.

The public variant omits the private Jin10 feed. The private variant reads the
already saved SQLite store and may only be written under ignored artifacts/.
No acquisition or model call is performed here.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import sys
from datetime import datetime
from functools import lru_cache
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.build_jin10_dashboard_v2 import PRIVATE_ROOT, ROOT, render_v2
from scripts.jin10_mcp.export import export_display


DATA = ROOT / "results/news_event_impact_20261115"
SOURCE_INDEX = ROOT / "results/news_earnings_macro_20261115/source_manifest.json"
CASES = ("a_nvidia", "b_coreweave", "c_cpi")
FACT_STATUSES = {"SOURCE_SUPPORTED", "DERIVED", "NOT_FOUND", "NOT_DISCLOSED"}
EDGE_STATUSES = {"SOURCE_SUPPORTED", "AI_ANALYST_DRAFT"}
UNIT_LABELS = {
    "USD millions": "百万美元",
    "USD millions cash outflow magnitude": "百万美元（现金流出额）",
    "USD million": "百万美元",
    "USD million_cash_outflow_magnitude": "百万美元（现金流出额）",
    "USD billions": "十亿美元",
    "USD billion": "十亿美元",
    "USD billion_approximate": "十亿美元（约）",
    "percent": "%",
    "percent of center, plus or minus": "%（中心值上下）",
    "basis points, plus or minus": "基点（上下）",
    "percent_month_over_month": "%（环比）",
    "percent_year_over_year": "%（同比）",
}
REQUIRED_FACT = {
    "fact_id", "description_zh", "description_en", "raw_value", "unit", "period",
    "accounting_basis", "source_url", "source_path", "source_sha256",
    "locator", "short_quote", "source_published_at", "publication_precision",
    "evidence_status",
}
REQUIRED_CARD = {
    "event_id", "title", "analysis_role", "happened", "new_info", "affected",
    "pathways", "conditions", "counterforces", "watch", "unknowns", "key_fact_ids",
}
REQUIRED_EDGE = {
    "from", "to", "channel", "direction", "horizon", "support_fact_ids",
    "assumptions", "falsifiers", "watch", "status",
}


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def esc(value: object) -> str:
    return html.escape(str(value), quote=True)


@lru_cache(maxsize=16)
def source_text(path: Path) -> str:
    raw = path.read_text(encoding="utf-8", errors="replace")
    if path.suffix.lower() in {".html", ".htm"}:
        raw = re.sub(r"(?is)<(script|style)\b[^>]*>.*?</\1>", " ", raw)
        raw = re.sub(r"(?s)<[^>]*>", " ", raw)
        raw = html.unescape(raw)
    return re.sub(r"\s+", " ", raw).strip().casefold()


def source_index() -> dict[str, dict]:
    rows = read_json(SOURCE_INDEX)["sources"]
    return {row["local_ignored_artifact"].replace("\\", "/"): row for row in rows}


def aware_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"Timezone missing from analysis/retrieval time: {value}")
    return parsed


def load_and_validate() -> tuple[dict, list[dict], dict[str, dict]]:
    manifest = read_json(DATA / "selection_manifest.json")
    if manifest.get("schema") != "news_event_impact_selection_v1":
        raise ValueError("Unexpected selection manifest schema")
    events = manifest.get("events")
    if not isinstance(events, list) or len(events) != len(CASES):
        raise ValueError("Selection manifest must have exactly three ordered events")
    indexed = source_index()
    seen_facts: dict[str, dict] = {}
    seen_edges: set[str] = set()
    quote_words: dict[str, int] = {}
    bundles: list[dict] = []
    for case, event in zip(CASES, events, strict=True):
        if event.get("case") != case:
            raise ValueError(f"Selection order mismatch: {case}")
        if not event.get("event_time_note_zh"):
            raise ValueError(f"Missing human-readable event-time basis: {case}")
        event_creation = aware_time(event["analysis_created_at_utc"])
        cutoff = aware_time(event["analysis_cutoff_at_utc"])
        if event_creation > cutoff or cutoff > aware_time(manifest["analysis_cutoff_at_utc"]):
            raise ValueError(f"Event analysis time exceeds frozen cutoff: {case}")
        source_files = event.get("source_files") or []
        if not source_files:
            raise ValueError(f"No source files frozen for {case}")
        selected_paths = {source["path"].replace("\\", "/") for source in source_files}
        for source in source_files:
            path_name = source["path"].replace("\\", "/")
            path = (ROOT / path_name).resolve()
            if not path.is_relative_to(ROOT / "artifacts") or not path.is_file():
                raise ValueError(f"Source path invalid or missing: {path_name}")
            if sha256(path) != source["sha256"]:
                raise ValueError(f"Source hash mismatch: {path_name}")
            if path_name not in indexed or indexed[path_name]["sha256"] != source["sha256"]:
                raise ValueError(f"Source not bound to saved index: {path_name}")
            if indexed[path_name]["url"] != source["url"]:
                raise ValueError(f"Source URL mismatch: {path_name}")
            if aware_time(source["retrieved_at_utc"]) > event_creation:
                raise ValueError(f"Source retrieved after analysis creation: {path_name}")
            if source["published_at"][:10] > event["published_at"][:10]:
                raise ValueError(f"Post-event source used as event-time evidence: {path_name}")
        facts = read_json(DATA / case / "facts.json")
        zh = read_json(DATA / case / "card.zh.json")
        en = read_json(DATA / case / "card.en.json")
        edges = read_json(DATA / case / "edges.json")
        if not (facts.get("event_id") == zh.get("event_id") == en.get("event_id") == event["event_id"]):
            raise ValueError(f"Event ID mismatch: {case}")
        if not isinstance(edges, list):
            raise ValueError(f"Edges must be a list: {case}")
        for fact in facts.get("facts") or []:
            if not REQUIRED_FACT.issubset(fact):
                raise ValueError(f"Fact missing fields: {case}/{fact.get('fact_id')}")
            fact_id = fact["fact_id"]
            if fact_id in seen_facts:
                raise ValueError(f"Duplicate fact ID: {fact_id}")
            if fact["evidence_status"] not in FACT_STATUSES:
                raise ValueError(f"Invalid evidence status: {fact_id}")
            if fact["evidence_status"] in {"NOT_FOUND", "NOT_DISCLOSED"} and fact["raw_value"] is not None:
                raise ValueError(f"Unknown fact must have null value: {fact_id}")
            path_name = fact["source_path"].replace("\\", "/")
            if path_name not in selected_paths or path_name not in indexed:
                raise ValueError(f"Fact source missing from index: {fact_id}")
            source = indexed[path_name]
            if source["sha256"] != fact["source_sha256"] or source["url"] != fact["source_url"]:
                raise ValueError(f"Fact source identity mismatch: {fact_id}")
            if not fact["source_url"].startswith("https://"):
                raise ValueError(f"Fact source URL is not HTTPS: {fact_id}")
            source_path = (ROOT / path_name).resolve()
            if sha256(source_path) != fact["source_sha256"]:
                raise ValueError(f"Fact source byte mismatch: {fact_id}")
            quote = fact["short_quote"].strip()
            if fact["evidence_status"] == "SOURCE_SUPPORTED" and not quote:
                raise ValueError(f"Source-supported fact lacks excerpt: {fact_id}")
            if quote:
                quote_name = fact.get("quote_source_path", path_name).replace("\\", "/")
                quote_path = (ROOT / quote_name).resolve()
                if not quote_path.is_relative_to(ROOT / "artifacts") or not quote_path.is_file():
                    raise ValueError(f"Quote source missing: {fact_id}")
                if quote_name != path_name:
                    candidates = [value for value in (facts.get("sources") or {}).values()
                                  if value.get("quote_source_path") == quote_name]
                    if len(candidates) != 1 or sha256(quote_path) != candidates[0].get("quote_source_sha256"):
                        raise ValueError(f"Quote extraction hash mismatch: {fact_id}")
                normalized_quote = re.sub(r"\s+", " ", html.unescape(quote)).casefold()
                if normalized_quote not in source_text(quote_path):
                    raise ValueError(f"Short excerpt not found in saved source: {fact_id}")
                quote_words[path_name] = quote_words.get(path_name, 0) + len(quote.split())
                if quote_words[path_name] > 25:
                    raise ValueError(f"Source excerpt budget exceeded: {path_name}")
            seen_facts[fact_id] = dict(fact, event_id=event["event_id"])
        local_fact_ids = {fact["fact_id"] for fact in facts["facts"]}
        if not event.get("highlight_fact_ids") or not set(event["highlight_fact_ids"]).issubset(local_fact_ids):
            raise ValueError(f"Invalid highlighted facts: {case}")
        for card in (zh, en):
            if not REQUIRED_CARD.issubset(card) or card["analysis_role"] != "AI_ANALYST_DRAFT":
                raise ValueError(f"Invalid draft card: {case}")
            if aware_time(card["analysis_created_at_utc"]) > event_creation:
                raise ValueError(f"Card created after manifest analysis time: {case}")
            if not set(card["key_fact_ids"]).issubset(local_fact_ids):
                raise ValueError(f"Card references unknown fact: {case}")
        for edge in edges:
            if not REQUIRED_EDGE.issubset(edge) or edge["status"] not in EDGE_STATUSES:
                raise ValueError(f"Invalid edge: {case}")
            if edge.get("event_id") != event["event_id"] or edge.get("edge_id") in seen_edges:
                raise ValueError(f"Edge ID/event mismatch: {case}")
            seen_edges.add(edge["edge_id"])
            if edge["direction"] not in {"POSITIVE", "NEGATIVE", "MIXED", "UNKNOWN"}:
                raise ValueError(f"Invalid edge direction: {case}")
            if not set(edge["support_fact_ids"]).issubset(local_fact_ids):
                raise ValueError(f"Edge references unknown fact: {case}")
            if not edge["support_fact_ids"]:
                raise ValueError(f"Edge lacks source context: {case}")
        bundles.append({"case": case, "event": event, "facts": facts, "zh": zh, "en": en, "edges": edges})
    return manifest, bundles, seen_facts


def calculate(specs: list[dict], facts: dict[str, dict]) -> list[dict]:
    outputs = []
    for item in specs:
        input_ids = item["input_fact_ids"]
        if not all(ident in facts for ident in input_ids):
            raise ValueError(f"Unknown calculation input: {item['calculation_id']}")
        if any(facts[ident]["event_id"] != item["event_id"] for ident in input_ids):
            raise ValueError(f"Cross-event calculation input: {item['calculation_id']}")
        values = [facts[ident]["raw_value"] for ident in input_ids]
        if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in values):
            raise ValueError(f"Non-numeric calculation input: {item['calculation_id']}")
        if item["operation"] == "subtract_three":
            if len(values) != 3:
                raise ValueError(f"Expected three calculation inputs: {item['calculation_id']}")
            value = values[0] - values[1] - values[2]
            formula = f"{input_ids[0]} - {input_ids[1]} - {input_ids[2]}"
        else:
            if len(values) != 2:
                raise ValueError(f"Expected two calculation inputs: {item['calculation_id']}")
            left_id, right_id = input_ids
            left, right = values
            if item["operation"] == "delta":
                value = left - right
                formula = f"{left_id} - {right_id}"
            elif item["operation"] == "percent_change":
                if right == 0:
                    raise ValueError(f"Zero denominator: {item['calculation_id']}")
                value = (left - right) / abs(right) * 100
                formula = f"({left_id} - {right_id}) / abs({right_id}) * 100"
            elif item["operation"] == "range_lower":
                value = left * (1 - right / 100)
                formula = f"{left_id} * (1 - {right_id} / 100)"
            elif item["operation"] == "range_upper":
                value = left * (1 + right / 100)
                formula = f"{left_id} * (1 + {right_id} / 100)"
            elif item["operation"] == "delta_scaled_left":
                divisor = item["left_scale_divisor"]
                if not isinstance(divisor, (int, float)) or divisor <= 0:
                    raise ValueError(f"Invalid left scale divisor: {item['calculation_id']}")
                value = left / divisor - right
                formula = f"{left_id} / {divisor} - {right_id}"
            else:
                raise ValueError(f"Unknown calculation operation: {item['calculation_id']}")
        if "expected_reported_fact_id" in item:
            expected_id = item["expected_reported_fact_id"]
            if (expected_id not in facts or facts[expected_id]["event_id"] != item["event_id"]
                    or not isinstance(facts[expected_id]["raw_value"], (int, float))
                    or abs(value - facts[expected_id]["raw_value"]) > 1e-6):
                raise ValueError(f"Derived value disagrees with reported fact: {item['calculation_id']}")
        outputs.append({"calculation_id": item["calculation_id"], "event_id": item["event_id"],
                        "operation": item["operation"], "formula": formula,
                        "input_fact_ids": input_ids, "value": round(value, 6),
                        "unit": item["unit"], "comparability_note": item["comparability_note"],
                        "status": "DERIVED_BY_CODE"})
    return outputs


def _list(items: list) -> str:
    return "".join(f"<li>{esc(item)}</li>" for item in items) or "<li>Unknown / 未知</li>"


def _fact_value(fact: dict) -> str:
    value = fact["raw_value"]
    if value is None:
        return "文字事实 / qualitative" if fact["evidence_status"] == "SOURCE_SUPPORTED" else "未知或未公布 / unknown"
    display_value = f"{value:,}" if isinstance(value, (int, float)) and not isinstance(value, bool) else str(value)
    unit = UNIT_LABELS.get(fact["unit"], fact["unit"] or "单位未记录")
    return f"{esc(display_value)} {esc(unit)}"


def _fact_tag(fact: dict) -> str:
    return ("来源事实 / SOURCE_FACT" if fact["evidence_status"] == "SOURCE_SUPPORTED"
            else "代码派生 / DERIVED" if fact["evidence_status"] == "DERIVED"
            else "来源缺口 / SOURCE_MISSING")


def render_impact(manifest: dict, bundles: list[dict], calculations: list[dict], feed: dict | None) -> str:
    page = render_v2(feed)
    calc_by_event: dict[str, list[dict]] = {}
    for item in calculations:
        calc_by_event.setdefault(item["event_id"], []).append(item)
    cards_html = []
    for bundle in bundles:
        event, zh, en = bundle["event"], bundle["zh"], bundle["en"]
        fact_by_id = {item["fact_id"]: item for item in bundle["facts"]["facts"]}
        key_facts = "".join(
            f'<li><span class="fact-tag">{_fact_tag(fact_by_id[ident])}</span> {esc(fact_by_id[ident]["description_zh"])}: '
            f'<strong>{_fact_value(fact_by_id[ident])}</strong></li>'
            for ident in event["highlight_fact_ids"])
        fact_details = "".join(
            f'<li id="{esc(item["fact_id"])}"><strong>{esc(item["fact_id"])}</strong> · '
            f'{esc(item["description_zh"])} / {esc(item["description_en"])} · '
            f'{_fact_value(item)} · {esc(item["period"])} · {esc(item["accounting_basis"])} · '
            f'{esc(item["evidence_status"])}<br><a href="{esc(item["source_url"])}" '
            f'target="_blank" rel="noopener noreferrer">官方来源 / Official source</a> · '
            f'{esc(item["locator"])} · short excerpt: {esc(item["short_quote"] or "none")}</li>'
            for item in bundle["facts"]["facts"])
        derived = "".join(
            f'<li>{esc(item["calculation_id"])}: {esc(item["value"])} {esc(item["unit"])} '
            f'({esc(item["formula"])}; {esc(item["comparability_note"])})</li>'
            for item in calc_by_event.get(event["event_id"], []))
        edges = "".join(
            f'<li>{esc(item["from"])} → {esc(item["to"])}: {esc(item["channel"])} '
            f'({esc(item["direction"])}; {esc(item["status"])}; '
            f'facts {esc(", ".join(item["support_fact_ids"]))})</li>'
            for item in bundle["edges"])
        cards_html.append(
            f'<article class="impact-card card" id="impact-{esc(bundle["case"])}">'
            f'<h3>{esc(zh["title"])}</h3>'
            f'<p class="draft-tag">开发分析草稿，非预测结果 · AI_ANALYST_DRAFT</p>'
            f'<p class="muted">新闻语气: NOT_SCORED · 基本面影响: AI_ANALYST_DRAFT · '
            f'实际价格反应: NOT_RUN</p>'
            f'<p class="muted">事件时间依据 / Event time basis: {esc(event["published_at"])} '
            f'({esc(event["event_time_note_zh"])}); 材料获取: {esc(event["retrieved_at_utc"])}; '
            f'分析制作: {esc(event["analysis_created_at_utc"])}. 首次可见时刻未独立证明。</p>'
            f'<p><strong>发生了什么 / What happened</strong><br>{esc(zh["happened"])}</p>'
            f'<p><strong>新增信息 / New information</strong><br>{esc(zh["new_info"])}</p>'
            f'<p><strong>关键事实 / Key facts</strong></p><ul>{key_facts}</ul>'
            f'<p><strong>分析假设：可能影响 / Potentially affected</strong></p><ul>{_list(zh["affected"])}</ul>'
            f'<p><strong>分析假设：传导路径 / Conditional paths</strong></p><ul>{_list(zh["pathways"])}</ul>'
            f'<p><strong>成立条件 / Conditions</strong></p><ul>{_list(zh["conditions"])}</ul>'
            f'<p><strong>反向因素 / Counterforces</strong></p><ul>{_list(zh["counterforces"])}</ul>'
            f'<p><strong>后续观察 / Watch</strong></p><ul>{_list(zh["watch"])}</ul>'
            f'<p><strong>待验证：尚不知道 / Unknown</strong></p><ul>{_list(zh["unknowns"])}</ul>'
            f'<details><summary>English analysis</summary><p>{esc(en["happened"])}</p>'
            f'<p>{esc(en["new_info"])}</p><ul>{_list(en["pathways"])}</ul></details>'
            f'<details><summary>证据字段与公式 / Evidence and calculations</summary>'
            f'<ul>{fact_details}</ul><h4>Derived by code</h4><ul>{derived or "<li>None</li>"}</ul>'
            f'<h4>Transmission edges</h4><ul>{edges or "<li>None</li>"}</ul></details>'
            f'</article>')
    panel = ('<section id="impact" class="impact-board"><h2>事件影响分析 / Event impact analysis</h2>'
             '<p class="flag">开发分析草稿，非预测结果。来源事实、分析假设与待验证事项分开显示；'
             '市场价格反应和金融评估 NOT_RUN。</p><div class="impact-grid">'
             + "".join(cards_html) + '</div></section>')
    page = page.replace('</nav>', '<a href="#impact">事件影响分析 / Event impact</a></nav>', 1)
    page = page.replace('</main>', '</main>' + panel, 1)
    css = ('.impact-board{padding:18px}.impact-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px}'
           '.impact-card{min-width:0}.impact-card ul{padding-left:20px}.impact-card li{margin:6px 0}'
           '.draft-tag{background:#eaf3fa;padding:8px;border-left:4px solid #145b88}'
           '.fact-tag{font-size:11px;font-weight:700;color:#265b35}.impact-card details{overflow-wrap:anywhere}'
           '@media(max-width:1100px){.impact-grid{grid-template-columns:1fr}}'
           '@media(max-width:600px){.impact-board{padding:10px}}')
    return page.replace('</style>', css + '</style>', 1)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--private-db", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--calculation-output", type=Path)
    parser.add_argument("--edges-output", type=Path)
    args = parser.parse_args()
    output = args.out.resolve()
    if output.exists():
        raise FileExistsError(output)
    manifest, bundles, facts = load_and_validate()
    specs = read_json(DATA / "calculation_specs.json")["calculations"]
    calculations = calculate(specs, facts)
    if args.calculation_output:
        calc_path = args.calculation_output.resolve()
        if calc_path.exists():
            raise FileExistsError(calc_path)
        if not calc_path.is_relative_to(DATA):
            raise ValueError("Calculation output must stay in impact results")
        calc_path.write_text(json.dumps({"schema": "event_impact_calculations_v1",
                                         "calculations": calculations}, ensure_ascii=False, indent=2) + "\n",
                             encoding="utf-8", newline="\n")
    if args.edges_output:
        edges_path = args.edges_output.resolve()
        if edges_path.exists():
            raise FileExistsError(edges_path)
        if not edges_path.is_relative_to(DATA):
            raise ValueError("Edges output must stay in impact results")
        all_edges = [dict(edge, case=bundle["case"]) for bundle in bundles for edge in bundle["edges"]]
        edges_path.write_text(json.dumps({"schema": "event_impact_transmission_edges_v1",
                                          "edges": all_edges}, ensure_ascii=False, indent=2) + "\n",
                              encoding="utf-8", newline="\n")
    feed = None
    if args.private_db is not None:
        database = args.private_db.resolve()
        if not database.is_relative_to(PRIVATE_ROOT) or not output.is_relative_to(PRIVATE_ROOT):
            raise ValueError("Private feed/page must stay in ignored artifacts")
        feed = export_display(database, limit=50, include_private_content=True)
    elif not output.is_relative_to(DATA):
        raise ValueError("Public template output must stay in impact results")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_impact(manifest, bundles, calculations, feed), encoding="utf-8", newline="\n")
    print(json.dumps({"case_count": len(bundles), "fact_count": len(facts),
                      "edge_count": sum(len(item["edges"]) for item in bundles),
                      "calculation_count": len(calculations),
                      "private_feed_included": feed is not None,
                      "output": output.relative_to(ROOT).as_posix()}))


if __name__ == "__main__":
    main()
