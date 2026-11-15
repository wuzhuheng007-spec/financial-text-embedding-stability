# 市场观察系统：首轮有界历史价格基线

执行日期为 **2026-11-15（Asia/Shanghai）**，起点分支 `codex/sync-research-progress-20261113`、提交 `8c30dcb776b64b3174fee0c55bc8b77e05a55b76`。本机未找到所称 `next_codex_task.txt`；用户随后在聊天中补齐 P0—P7 全文，本轮按补充文本执行。[计划—实际台账](../results/market_observer_historical_20261115/plan_execution.csv)逐项对应。旧成果、私人数据库和无关未跟踪文件均保留。本轮是独立于正式 M5 和实时观察服务的**回顾性开发试验**。

## 目标与实际数据

冻结的来源窗口为 **2025-04-01 至 2026-07-31**。实际调用 moomoo 官方[历史 K 线接口](https://open.moomoo.com/api/quote/basic-data/history-kline)：TQQQ、NVDA、MU 各取得 335 个美股交易日的未复权（`autype=0`）及前复权（`autype=1`）日线，均为完整单页。试点成功后，QQQ、SOXX、SPY 各取得 335 条未复权日线。六只证券的日期均与取回的 335 日美股交易日历一致；六次历史证券查询仅核实了 2025-04-01 当日代码，不能证明之后每天的身份连续性。[价格回执](../results/market_observer_historical_20261115/price_probe_redacted.json)、[ETF 扩展](../results/market_observer_historical_20261115/etf_expansion_redacted.json)及[身份回执](../results/market_observer_historical_20261115/historical_identity_redacted.json)绑定了原始响应、参数和哈希。

价格端共进行 **23 次有界业务调用，零重试**。官方[历史额度接口](https://open.moomoo.com/api/quote/quotas/history-kline)返回业务码 `-3`，服务把 `quotas` 解析成证券代码，因此账户剩余额度仍未知；扩展止于上述六只证券。其余观察对象，包括闪迪、海力士和 SpaceX，**本轮均未取得历史价格序列**，也没有拼接其他公司的行情。[SpaceX 官方公告](https://ir.spacex.com/updates/releases-details/2026/Space-Exploration-Technologies-Corp--Announces-Closing-of-Initial-Public-Offering-Including-Full-Exercise-of-Underwriters-Option-to-Purchase-Additional-Shares-2026-RgoR-Y1Vwh/default.aspx)说明 SPCX 从 2026-06-12 开始交易，此前价格应保持缺失。响应中的 `time_key` 对应交易日当地午夜，不能当作收盘或首次可见时刻；`time_zone` 实际为 `-4/-5`，与现行文档的“分钟偏移”描述不一致。本轮按交易日期对齐。

[公司行动回执](../results/market_observer_historical_20261115/corporate_actions_redacted.json)及[日期核对](../results/market_observer_historical_20261115/action_alignment_redacted.json)保留了三只试点证券的拆股、分红记录。TQQQ 在 2025-11-20 开盘前生效的 2:1 拆股另有 [ProShares 官方公告](https://www.proshares.com/press-releases/proshares-announces-etf-share-splits5)支持。直接用其未复权跨日价格会产生机械跌幅。本轮模型使用同一证券单日开盘至收盘方向，以及事后取得的前复权序列所构造的比率特征；**不计算含分红总收益**。公司行动完整性和当时价格可见性仍待核实。原始行情依据 [moomoo 数据条款](https://www.moomoo.com/terms)仅留本机。

## 新闻与官方来源

金十 MCP 的实际有界检索得到 **7 条美光关键词候选**，其供应商日期在窗口内；一条详情调用也成功。关键词接口没有日期筛选。TQQQ 首页检索为零，NVIDIA 首页仅有近期报道，均不能据此断言整个来源没有历史新闻或覆盖完整。moomoo REST 新闻检索实际返回 10 条 NVDA 结果，但日期均晚于 2026-07-31，**没有进入本轮冻结输入**。原生 moomoo MCP 调用在业务工具前因 Codex CLI `model_not_found` 中止；REST 调用只证明当前检索可访问。[新闻来源回执](../results/market_observer_historical_20261115/news_official_redacted.json)分开记录调用、错误与覆盖缺口。

官方 [SEC submissions API](https://www.sec.gov/search-filings/edgar-application-programming-interfaces)给出窗口内 NVDA 20 条、MU 19 条 8-K/10-Q/10-K 元数据。另有 2 条属于多系列信托的 TQQQ 关联候选，**尚不能作为 TQQQ 专属事件准入**。有界下载的三份官方原件分别为 NVIDIA 财报附件、美光财报附件和 ProShares 拆股公告。[BEA GDP 公告](https://www.bea.gov/news/2025/gross-domestic-product-1st-quarter-2025-advance-estimate)与[美联储声明](https://www.federalreserve.gov/newsevents/pressreleases/monetary20250507a.htm)按固定日期顺序取得；一份 [BLS CPI 档案页](https://www.bls.gov/news.release/archives/cpi_07152025.htm)的程序请求返回 HTTP 403，官方网页另经只读方式核对。这些是定向样本，尚非完整的公司或宏观新闻库。公告／申报时间与可独立证明的历史首次公开时刻分开，当前下载时间不能回填为当时的接收时间。

[时间对齐诊断](../results/market_observer_historical_20261115/event_alignment_redacted.json)把 41 条 SEC 元数据、7 条金十候选和 2 条可读取的官方宏观发布时刻映射到来源日期后的首个美股交易日。宏观时刻是**不得早于发布**的时间，不是已观察的首次可见时刻；BLS 程序请求失败，未纳入。**50 条均为 `PUBLICATION_PROXY_ONLY`，严格事前可用性准入为 0**；两条信托记录的 TQQQ 关系仍有歧义。未取到的历史报道标记为未知，不能写成零新闻。2026 年 7 月之后的新闻、现时总结和既有 8 月／11 月开发材料均未进入模型输入。

## 冻结的首轮学习

目标为**下一交易日从开盘至收盘的方向**。对于标签交易日 *t*，价格和成交量特征仅用至 *t−2*，整段前一交易日 *t−1* 均不使用，确保没有误用其收盘数据。方法 A 为各证券训练期多数方向；方法 B 为各证券 L2 逻辑回归，特征为过去 1/5/20 个交易日价格收益、20 日波动及 1/5 日成交量变化。标准化只在训练期拟合；预先冻结的参数格 `{0.1, 1, 10}` 只按验证期平衡准确率选择，三只证券均选中 `10`。按**标签日期**划分：训练截至 2026-03-31，验证为 4—5 月，测试为 6—7 月。每只试点证券有 **训练 229、验证 41、测试 43 条**可评估记录。公开[冻结配置](../configs/market_observer_historical_v1.json)与私人运行配置哈希完全一致；[v3 脱敏结果](../results/market_observer_historical_20261115/price_direction_v3_redacted.json)记录准确分母、类别数、来源／配置哈希和私人逐行预测哈希。

| 证券 | 测试条数 | A 准确率 | A 平衡准确率 | B 准确率 | B 平衡准确率 |
| --- | ---: | ---: | ---: | ---: | ---: |
| TQQQ | 43 | 39.5% | 50.0% | 39.5% | 49.0% |
| NVDA | 43 | 46.5% | 50.0% | 55.8% | 57.7% |
| MU | 43 | 46.5% | 50.0% | 44.2% | 47.5% |

这些只是**三只相关开发证券**的描述结果。验证期中，A 的平衡准确率均为 50.0%，B 在 TQQQ、NVDA、MU 分别为 44.8%、37.6%、47.8%，均未超过 A。被污染的测试期里，B 在 NVDA 的平衡准确率高于 A，在 TQQQ、MU 则没有；不能据此推断一般预测能力。方法 C（B 加新闻文本）因未取得完整且历史可用时间合格的新闻语料而 **NOT_RUN**；未观察到的新闻没有被编码成中性或零。

[污染记录](../results/market_observer_historical_20261115/test_contamination_redacted.json)说明：用户补充精确分类协议前，先行且已被替代的收盘到收盘回归查看了 6—7 月标签。第一版分类实现也曾让每个参数候选生成测试预测，虽未用测试标签选参，但不符合“最后只评价一次”。该 [v2 结果](../results/market_observer_historical_20261115/price_direction_v2_redacted.json)及代码保留。v3 已改为候选只看训练与验证、选定模型才预测测试一次；**6—7 月区间仍已污染，不能称独立测试**。旧[探索回执](../results/market_observer_historical_20261115/price_baseline_redacted.json)保留原状态，没有冒充本轮结果。

## 完成状态与下一步

合成软件检查覆盖日期过滤、*t−2* 特征隔离、时间分界、只用验证选参、新闻时间对齐和重复事件；它们与真实 API 请求、真实日线回顾性建模分别报告。实时观察服务本轮没有新增开市更新；没有下单、大模型微调、正式 M5 准入、新闻因果效应或盈利结论。[dot 脱敏交接数据](../results/market_observer_historical_20261115/dot_progress_redacted.json)仅为文件，当前无 dot 实际连接工具。

下一步需要有明确使用权限、原始发布者、稳定覆盖和历史首次可用证据的新闻档案；核实历史行情额度后才扩展观察池；补足公司—证券关系连续性及公司行动口径。此后应冻结一个**真正未查看过**的未来评价期，再重新比较 A/B/C。
