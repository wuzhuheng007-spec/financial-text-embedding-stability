"""Offline source readback and static privacy checks for event-impact dashboard v1."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.build_event_impact_dashboard_v1 import DATA, ROOT, calculate, load_and_validate, read_json
from scripts.jin10_mcp.engine import unwrap_mcp_payload


PRIVATE_ROOT = ROOT / "artifacts"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--public-html", type=Path, required=True)
    parser.add_argument("--private-html", type=Path, required=True)
    parser.add_argument("--flash-captures", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    public_html, private_html, flashes, receipt = (
        path.resolve() for path in (args.public_html, args.private_html,
                                    args.flash_captures, args.receipt))
    if not public_html.is_relative_to(DATA) or not receipt.is_relative_to(DATA):
        raise ValueError("Public outputs must stay in impact results")
    if not private_html.is_relative_to(PRIVATE_ROOT) or not flashes.is_relative_to(PRIVATE_ROOT):
        raise ValueError("Private inputs must stay in ignored artifacts")
    if receipt.exists():
        raise FileExistsError(receipt)
    manifest, bundles, facts = load_and_validate()
    specs = read_json(DATA / "calculation_specs.json")["calculations"]
    derived = calculate(specs, facts)
    published_derived = read_json(DATA / "calculations_final.json")["calculations"]
    published_edges = read_json(DATA / "transmission_edges.json")["edges"]
    if derived != published_derived:
        raise AssertionError("Published calculations do not replay")
    if published_edges != [dict(edge, case=bundle["case"])
                           for bundle in bundles for edge in bundle["edges"]]:
        raise AssertionError("Combined edges differ from case sources")
    public_page = public_html.read_text(encoding="utf-8")
    private_page = private_html.read_text(encoding="utf-8")
    for page in (public_page, private_page):
        if page.count('class="impact-card card"') != 3:
            raise AssertionError("Expected three impact cards")
        if not all(label in page for label in ("事件时间依据", "材料获取", "分析制作",
                                            "来源事实 / SOURCE_FACT", "来源缺口 / SOURCE_MISSING",
                                            "分析假设", "待验证", "开发分析草稿，非预测结果",
                                            "新闻语气: NOT_SCORED", "实际价格反应: NOT_RUN")):
            raise AssertionError("Page omitted event-time or evidence-state labels")
        if "CPI_F08" not in page or "未知或未公布 / unknown" not in page:
            raise AssertionError("Missing CPI year-on-year value was not preserved")
    if "PUBLIC_TEMPLATE_PRIVATE_FEED_OMITTED" not in public_page:
        raise AssertionError("Public page does not mark omitted private feed")
    if "LOCAL_CAPTURE_WITH_COVERAGE_GAP" not in private_page:
        raise AssertionError("Private page does not mark partial flash coverage")
    captures = json.loads(flashes.read_text(encoding="utf-8"))
    flash_bodies = [item.get("content") for capture in captures
                    for item in unwrap_mcp_payload(capture["response"])[0]["data"]["items"]]
    leaks = sum(bool(body and len(body) >= 20 and body in public_page) for body in flash_bodies)
    if leaks:
        raise AssertionError("Provider flash body leaked into public page")
    files = [DATA / "selection_manifest.json", DATA / "calculation_specs.json"]
    files.extend(DATA / case / name for case in ("a_nvidia", "b_coreweave", "c_cpi")
                 for name in ("facts.json", "card.zh.json", "card.en.json", "edges.json"))
    result = {
        "schema": "event_impact_static_source_validation_v1",
        "source_manifest_sha256": sha256(ROOT / "results/news_earnings_macro_20261115/source_manifest.json"),
        "selection_manifest_sha256": sha256(DATA / "selection_manifest.json"),
        "input_file_sha256": {path.relative_to(ROOT).as_posix(): sha256(path) for path in files},
        "official_source_files_read_back": sum(len(event["source_files"]) for event in manifest["events"]),
        "source_bound_facts": len(facts),
        "transmission_edges": len(published_edges),
        "code_derived_calculations": len(derived),
        "public_html_sha256": sha256(public_html),
        "private_html_sha256": sha256(private_html),
        "captured_flash_bodies_compared": len(flash_bodies),
        "captured_flash_body_matches_in_public_html": leaks,
        "private_html_published": False,
        "source_readback": "PASS",
        "static_display_and_privacy": "PASS",
        "browser_visual_check": "SEPARATE_RECEIPT",
        "formal_financial_admission": False,
        "model_or_financial_evaluation": "NOT_RUN",
    }
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({key: result[key] for key in ("official_source_files_read_back",
                                                   "source_bound_facts", "transmission_edges",
                                                   "code_derived_calculations", "source_readback",
                                                   "static_display_and_privacy")}))


if __name__ == "__main__":
    main()
