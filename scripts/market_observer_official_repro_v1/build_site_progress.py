"""Build a static, redacted Site handoff from immutable public run receipts."""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "results/market_observer_official_repro_20261115/site_progress_redacted.json"
BASE = ROOT / "results/market_observer_official_repro_20261115"
BRANCH_URL = (
    "https://github.com/wuzhuheng007-spec/financial-text-embedding-stability/"
    "blob/codex/sync-research-progress-20261113/"
)


def read(name: str) -> dict:
    return json.loads((BASE / name).read_text(encoding="utf-8"))


def main() -> None:
    gate = read("gate_redacted.json")
    audit = read("text_input_audit_redacted.json")
    inc = read("incremental_redacted.json")
    validation = read("incremental_validation_redacted.json")
    if gate["gate_status"] != "PASS" or validation["status"] != "PASS":
        raise ValueError("reproduction and incremental validation must pass first")
    if inc["test"] != "NO_JUNE_JULY_LABEL_READ_FIT_OR_SCORE":
        raise ValueError("sealed interval status changed")
    symbols = []
    for symbol in ("US.NVDA", "US.MU"):
        row = inc["symbols"][symbol]
        branches = row["branches"]
        metrics = {name: branches[name]["selected_validation"]
                   for name in ("B", "B_count", "B_text", "B_both")}
        v = row["validation_announcement_reuse"]
        short = symbol.removeprefix("US.")
        summary = (
            f"验证 {row['validation_n']} 日，含公告 {v['days_with_announcement']} 日，"
            f"涉及 {v['unique_event_groups']} 个不同事件组。"
            f"平衡准确率 B / B_count / B_text / B_both = "
            f"{metrics['B']['balanced_accuracy']:.3f} / "
            f"{metrics['B_count']['balanced_accuracy']:.3f} / "
            f"{metrics['B_text']['balanced_accuracy']:.3f} / "
            f"{metrics['B_both']['balanced_accuracy']:.3f}；"
            f"Brier = {metrics['B']['brier_score']:.3f} / "
            f"{metrics['B_count']['brier_score']:.3f} / "
            f"{metrics['B_text']['brier_score']:.3f} / "
            f"{metrics['B_both']['brier_score']:.3f}。"
        )
        if short == "MU":
            summary += " 四组在验证期均只预测上涨，概率并不相同。"
        else:
            summary += " 文本分支分类指标有变化，但概率误差更大。"
        symbols.append({
            "symbol": short,
            "summary": summary,
            "validation_dates": row["validation_n"],
            "announcement_dates": v["days_with_announcement"],
            "distinct_event_groups": v["unique_event_groups"],
            "selected_balanced_accuracy": {
                name: metrics[name]["balanced_accuracy"] for name in metrics
            },
            "selected_brier": {
                name: metrics[name]["brier_score"] for name in metrics
            },
        })
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    data = {
        "schema": "market_observer_official_repro_site_progress_v1",
        "updated_at_asia_shanghai": now.isoformat(),
        "role": "STATIC_REDACTED_RETROSPECTIVE_DEVELOPMENT_SNAPSHOT",
        "site_live_feed": False,
        "dot_connected": False,
        "strict_historical_availability": "UNPROVEN",
        "sealed_june_july_labels": "NOT_READ_LABELED_OR_SCORED_THIS_ROUND",
        "source_receipts": {
            "old_config": "configs/market_observer_official_text_v1.json",
            "new_plan": "configs/market_observer_official_repro_v1.json",
            "reproduction": "results/market_observer_official_repro_20261115/gate_redacted.json",
            "text_audit": "results/market_observer_official_repro_20261115/text_input_audit_redacted.json",
            "incremental": "results/market_observer_official_repro_20261115/incremental_redacted.json",
            "validation": "results/market_observer_official_repro_20261115/incremental_validation_redacted.json",
        },
        "reproduction": {
            "status": "COMPARABLE_OUTPUTS_REPRODUCED",
            "pipeline_runs": gate["full_pipeline_executions"],
            "estimator_fits": gate["estimator_fit_calls"],
            "summary": (
                "旧 A/B/C 的可比输出逐字节复现；本轮一次复现流程做了 "
                f"{gate['estimator_fit_calls']} 次逻辑回归拟合。"
            ),
            "limitations": (
                "旧运行未同时保存当时的代码哈希、逐行概率或特征矩阵，"
                "这些项目无法直接做旧新对照。"
            ),
        },
        "text_audit": {
            "status": "INPUT_AUDITED_CODEX_REVIEW",
            "event_groups": audit["participating_event_groups"],
            "documents": audit["participating_documents"],
            "clipped_documents": audit["participating_documents_clipped"],
            "summary": (
                f"{audit['participating_event_groups']} 个事件组、"
                f"{audit['participating_documents']} 份参与文档中有 "
                f"{audit['participating_documents_clipped']} 份截断。"
                "九份参与建模的财报附件都在 10,000 字符处截断，但"
                "检索到的营收、净利润和指引短语仍保留在输入中。"
            ),
            "limitations": (
                "词表前列含常用词和 SEC 套话；独立数值词不进入原 TF-IDF。"
                "短语可见不等于全部财务事实被正确表示。"
            ),
        },
        "incremental": {
            "status": "REUSED_VALIDATION_DEVELOPMENT_ONLY",
            "pipeline_runs": inc["full_incremental_pipeline_runs"],
            "estimator_fits": inc["estimator_fit_count"],
            "symbols": symbols,
            "limitations": (
                "相邻日期复用同一公告；验证集已重复使用。"
                "不同正则化参数下的选参差异不能单独归因于文本。"
            ),
        },
        "limitations": [
            "严格历史首次可见时间未证明，公告日期只是回顾性代理。",
            "六月至七月曾被先前探索接触，本轮未重新评分；没有独立测试结论。",
            "模型词项系数在旧版与复现中均未保存；本文词表审计不等于预测权重审计。",
            "本页面无云端实时采集或 dot 连接；正式 M5 与交易均未运行。",
        ],
        "report_zh_url": BRANCH_URL + "reports/2026-11-15_market_observer_official_repro_v1.zh.md",
        "report_en_url": BRANCH_URL + "reports/2026-11-15_market_observer_official_repro_v1.en.md",
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
