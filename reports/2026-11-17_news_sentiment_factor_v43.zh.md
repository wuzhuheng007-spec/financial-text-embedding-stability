# 新闻情绪 v4.3：日度窗口、实际可用时间与首次快照验收

执行日为 2026-11-17（Asia/Shanghai）；实际起始 HEAD 为 `5b85882c40b29455eab0f5982344f95411295153`，与用户给定基线相同。研究仍在 `codex/sync-research-progress-20261113`。模型 revision `e91b1a3af10e1e8c9c03429d3cd7d5e9a1c8000d`、中文分句、公司别名、原始分数与每日 500 次尝试上限未改；财务 v2.5 仍为 `REJECTED_CANDIDATE`。

## 计划与实际

| 阶段 | 冻结计划 | 实际执行与界限 |
| --- | --- | --- |
| P0 | 核对基线、备份、更正旧包计数 | 三份 SQLite 在线备份并通过 `integrity_check`，保存在本机忽略目录。旧 v4.2 ZIP 哈希及清单回读：ZIP 总条目 45、文件 45、唯一路径 45；旧清单 14 行、唯一路径 13，`snapshot_checks_redacted.json` 重复。追加更正回执，旧包未改。先保存配置和台账，再运行；运行前代码、配置和私人输入清单哈希见锁定回执。 |
| P1 | 交易日 16:15 左开右闭日度窗口 | 新版与 v4.2 累计回放分开。窗口按上一个合格纽约交易日 16:15 到当日 16:15，首窗起点早于研究起点时标 `PARTIAL_INITIAL_WINDOW`。事件按首次完整评分可用时间入窗；同窗取截至截止已完成的最新修订，每个来源内算法事件组只增一次 N2。跨日修订另记 `update_count`；无新增事件时 N1 缺失。金十分页连续性未证实，N2 完整数量缺失。 |
| P2 | 完成与发布分离、幂等、严格分数身份 | 数据、完成回执、发布门槛、提交见证依次追加；页面只读可用时间已到且见证未晚于门槛的快照。重复 ID 保留原类型和时间，输入清单冲突则拒绝。要求文章全部预期片段在相同模型、分句规则、评分配置哈希下成功，部分失败整篇排除。锁定后发现“见证晚于门槛”读取风险，追加独立修订回执和第 13 项回归检查，未改旧快照。 |
| P3 | 公司新闻与证券收益映射分开 | 保留 15 个观察实体。独立映射表记录 13 个已由本机回执核对的当前美股供应商代码；命名交易所、币种、历史身份和合格开收盘价格仍未证实。SK hynix 和 Samsung Electronics 保留新闻观察，收益标签标 `BLOCKED_SECURITY_MAPPING`，不推测 ADR 或套用美股价格。 |
| P4 | 明确标签阶段、不提前读价格 | 预定下一合格常规交易日开盘及当日／第五合格交易日收盘；队列可区分等入场、等退出、等合格价格、映射阻塞、错过入场和就绪。只在合格快照发布后建任务；当前真实任务 0，真实收益标签 0。报价缓存不能代替开盘／收盘及公司行动。 |
| P5 | 边界、故障与配置定向检查 | 隔离合成检查最终 13/13，通过旧新闻排除、边界、迟评分、修订、休市、重复、未完成发布、不同评分配置、部分失败、无新闻与中性、映射阻塞及错过入场等。开发初期两次测试运行因 Windows 临时 SQLite 句柄未关闭失败，修复后通过。Python 编译、JS 语法和 Django system check 通过；Django 既有 4 项测试通过。以上不是新闻内容或收益有效性验证。 |
| P6 | 受控部署和首次真实截止 | 研究 worker 与门户受控重启；研究状态回读显示 `RUNNING`、规则 `news_sentiment_factor_v43_protocol_1`、固定研究起点、积压 0、每日上限 500。纽约当日 16:15 的真实截止为 2026-11-17 21:15 UTC（上海次日 05:15），执行时未到：真实 v4.3 日度快照 0，首次到点验收 `PENDING_MARKET_CUTOFF`。保留三份 v4.2 开发回放，不改称日度前瞻。登录后中英桌面及 390px 研究区实看，分别测得页面宽 1009≤1024 与 375≤390，无整页横向溢出。 |
| P7 | 报告、远端和脱敏交接 | 本报告、英文摘要、计划—实际台账、定向检查、修订和运行回执分别保存。最终远端 SHA 与 ZIP 校验见独立交接回执；私人新闻、逐段分数、快照成员、价格、数据库和凭据均排除。 |

## 关键口径与已知限制

2026 年休市与半日安排按 [NYSE 官方交易时间](https://www.nyse.com/trade/hours-calendars)及 [Nasdaq 市场日程](https://www.nasdaq.com/market-activity/stock-market-holiday-schedule)核对。研究冻结的半日截止仍为纽约时间 16:15，**不是**交易所半日收盘。首窗因研究启动较晚只能称部分观察。来源内分组是算法代理，跨来源转载未核实；完整新闻数量不能从不连续的最新页缓存推断。

真实 worker 在本轮受控重启之间有一次 `ModuleNotFoundError` 导致评分任务失败，失败记录保留。单独的本机模型装载复查成功；该次异常的具体导入模块未被记录，故原因仍未查明。后续重启的任务实际评分成功。这个故障与合成检查分开报告，不能写成所有运行均无错。

当前供应商连接、研究 worker 和门户是回执时点状态，不构成全天稳定性证明。v4.3 日度代码自身模型推理 0、收益标签 0、收益拟合 0、IC 0；持续的 v4.2 中文评分 worker 继续独立运行。任何现有历史材料回放均只能称开发检查，不能回填为前瞻信号或独立评价。

## 复查入口

代码与定义：`scripts/news_sentiment_factor_v43/daily.py`、`configs/news_sentiment_factor_v43_20261117.json`、`configs/news_sentiment_factor_v43_security_mapping_20261117.json`。哈希和私人输入清单摘要见 `results/news_sentiment_factor_v43_20261117/run_lock_redacted.json`；锁定后发布门槛与逐证券日历字段的补充修订分别见 `postfreeze_revision_redacted.json`、`postfreeze_mapping_revision_redacted.json`，当时尚无 v4.3 快照。旧 ZIP 计数更正见 `v42_zip_count_correction_redacted.json`，定向检查见 `targeted_checks_redacted.json`。这些文件是脱敏元数据，不提供私人逐条输入。

仓库根目录运行：

```powershell
& 'artifacts/news_sentiment_factor_v3_20261117/.venv/Scripts/python.exe' -m scripts.news_sentiment_factor_v42.worker status
& 'apps/quant_portal/ops/local.ps1' status
& 'apps/quant_portal/ops/feed_personal.ps1' status
```
