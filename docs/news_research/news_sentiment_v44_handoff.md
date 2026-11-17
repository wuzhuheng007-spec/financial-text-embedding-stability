# v4.4 交接：前瞻日度快照与价格标签

本轮代码在 `codex/sync-research-progress-20261113` 上继续。私人数据库、新闻正文、原始行情和凭据不随代码分发。请先读 `reports/2026-11-17_news_sentiment_factor_v44.zh.md` 与同名英文摘要，以及 `results/news_sentiment_factor_v44_20261117/plan_execution.csv`。

截至本轮实际运行回执，2026-11-17 21:15 UTC 的首次真实日度截止尚未到点。v4.3 的真实快照、收益标签和因子—收益配对均为 0，不能用 v4.2 的三份累计开发快照顶替。快照规则、中文模型、分句、公司关联和 N1/N2 不变。

NVDA 与 MU 各有两个已完成交易日的 moomoo 常规日 K 私人原件，但日 K `open` 未核实为开盘竞价成交价、报价币种字段未取得。v4.4 在独立价格收益契约下新增追加式标签引擎，只有未来信号和合格价格／公司行动证据满足后才能写入。`scripts/news_sentiment_factor_v44/qualification.py` 可以从本机私人证据重算不含价格的状态回执。

定时 REST 快照最后失败为 `HTTP_439`，供应商文档归类为 WebAPI 安全策略阻断。新线程保持存活并安全暂停；它没有新的成功响应。WebSocket 推送、金十和研究评分 worker 分别运行。不要靠重复重启请求被阻断端点；供应商明确解决该限制后，先在有界请求预算内单次复查，再设计明确的人工恢复入口。

在仓库根目录检查：

```powershell
& 'apps/quant_portal/ops/local.ps1' status
& 'apps/quant_portal/ops/feed_personal.ps1' status
& 'artifacts/news_sentiment_factor_v3_20261117/.venv/Scripts/python.exe' -m scripts.news_sentiment_factor_v42.worker status
python -m scripts.news_sentiment_factor_v44.audit
python -m scripts.news_sentiment_factor_v44.verify
```

到首个真实截止后，按运行回执逐项核对日度数据行、完成与发布见证、实际可用时间、公司行和标签队列。迟到结果保留迟到身份。下一次价格接入需先取得明确币种、常规开收盘口径和持有区间公司行动证据；不得把旧试点价格与后来前瞻信号拼接。本轮页面桌面与 390px 实看未运行，因为电脑操作工具无法确认 Edge 网址；需在可靠浏览器控制环境中补验。
