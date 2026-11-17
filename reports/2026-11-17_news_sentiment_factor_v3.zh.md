# 情绪因子 v3：官方财报语气开发比较

执行于 2026-11-17（Asia/Shanghai）。本轮是对 v2 不可执行门槛的**协议修订**，沿用已接触的训练期材料，不是独立测试，也不是完整媒体新闻情绪、可交易信号或金融有效性认证。旧版 163 块评分、8 个事件的收益及相关性均未覆盖；新闻抽取 v1.8.2、个人门户和采集保持原状。

## 计划与实际

| 阶段 | 实际结果 | 证据 |
| --- | --- | --- |
| P0 冻结与保护 | 从实际 HEAD `90d9f091cf60de23860b3fd03a711b16be1183c3` 继续；先登记 v2 的 8 对 30、每公司 4 对 10、无拟合代码、数量退化及半日步进问题。新配置先于来源请求与新收益读取冻结。 | [问题登记](../results/news_sentiment_factor_v3_20261117/baseline_issues.json)、[预检哈希](../results/news_sentiment_factor_v3_20261117/preflight_redacted.json)、[v3 配置](../configs/news_sentiment_factor_v3_20261117.json) |
| P1 样本可行性 | 固定 12 只关注池普通股；SEC 元数据筛出 48 个 Item 2.02 候选，逐项核源后 46 个财报附件、35 个接受日期。两条 Tesla 交付公告被排除且没有用之后的财报补位。SNDK 在本窗口有供应商日线和 SEC 发行人申报；仅凭今日代码索引仍不能完整证明所有历史证券关系。 | [元数据可行性](../results/news_sentiment_factor_v3_20261117/feasibility_redacted.json)、[来源审阅](../results/news_sentiment_factor_v3_20261117/source_review_redacted.json) |
| P2 来源与时间 | 冻结预算 SEC 115、moomoo 30、零重试；实际 SEC 109 次、约 57.8 MB，moomoo 30 次。48 个 SEC 申报索引与完整头回查，8 个最初附件歧义保留后按 EX-99.1 来源解决。新增 10 只证券均取得 2025-04-01 至 2026-03-31 日线、公司行动与股息响应；NVDA/MU 复用旧原件。SEC 接受时刻不是网站首次公开时刻。 | [SEC 归档](../results/news_sentiment_factor_v3_20261117/archive_collection_redacted.json)、[附件修正](../results/news_sentiment_factor_v3_20261117/source_resolution_redacted.json)、[行情请求](../results/news_sentiment_factor_v3_20261117/price_acquisition_redacted.json)、[价格预检](../results/news_sentiment_factor_v3_20261117/price_preflight_redacted.json) |
| P3 文本覆盖与真实评分 | 旧选择器在 8 个事件略过 100 个预算块和 22 个长块。新选择器在 46 个事件提取并逐字定位 1,728 个模型可见块，按财务回顾、经营讨论、指引、风险四类聚合；仍有 793 个未分类块与 55 个表格/目录样式块未送入评分。先后保留两份来源驱动的未评分候选（1,962、1,899 块），最后一版才评分。LM 与固定 FinBERT 对 1,728 块真实运行，108 批、失败 0，CPU 推理循环约 237 秒。 | [选择修订](../results/news_sentiment_factor_v3_20261117/text_selector_revision_redacted.json)、[覆盖回查](../results/news_sentiment_factor_v3_20261117/text_coverage_audit_redacted.json)、[真实评分](../results/news_sentiment_factor_v3_20261117/score_run_redacted.json) |
| P4 可执行方案 | 使用两项滞后量价特征和固定 `Ridge(alpha=10)`，训练折内标准化；冻结门槛为总体至少 24 事件/18 日期，每折训练至少 16、测试至少 4。每事件恰好一个附件，文档数量恒为 1，故 **B=A、D=C**；主配对是 A 对 C。F2 有 22 个事件，不达到总体拟合门槛。 | [配置](../configs/news_sentiment_factor_v3_20261117.json)、[因子覆盖](../results/news_sentiment_factor_v3_20261117/factor_coverage_redacted.json) |
| P5 真实检验 | 46 个 F1 事件均有 1 日标签，39 个有 5 日标签；5 日窗口 7 个因公司行动排除。3 个扩展日期折对 LM/FinBERT 的 F1、两个期限实际执行 **24 次 fit**，保存 84 条模型别逐折预测（每期限分别 23、19 个配对事件）。F2 只做描述性相关，不拟合。旧 8 事件另做不删样本的逐事件敏感性诊断。 | [对齐](../results/news_sentiment_factor_v3_20261117/alignment_redacted.json)、[配对评价](../results/news_sentiment_factor_v3_20261117/development_evaluation_redacted.json)、[半日对照](../results/news_sentiment_factor_v3_20261117/calendar_comparison_redacted.json) |
| P6 新闻与前瞻边界 | 单独登记英文媒体档案缺口；moomoo 历史新闻仅一次 10 条首页探测且训练窗口命中 0，金十仍是中文私人流，不能混为完整英文新闻档案或直接输入英文 FinBERT。本轮未新增前瞻影子信号或成熟收益。 | [英文来源覆盖](../results/news_sentiment_factor_v3_20261117/english_news_coverage.json) |
| P7 同步交接 | 代码、配置、脱敏回执与中英报告选择性提交；私人原文、行情、逐行预测、凭据和数据库只留本机。具体 SHA 与远端回读另见同步回执。 | [计划—实际台账](../results/news_sentiment_factor_v3_20261117/plan_execution.csv) |

## 真实开发结果

| 候选与期限 | 来源事件 / 独立日期 | 折外配对事件 | A 均方误差 | C 均方误差 | 结论 |
| --- | ---: | ---: | ---: | ---: | --- |
| LM F1，1 日 | 46 / 35 | 23 | 0.0031555 | 0.0032283 | 文本较差 |
| FinBERT F1，1 日 | 46 / 35 | 23 | 0.0031555 | 0.0031018 | 均方误差略低，绝对误差反而较高；日期块区间跨零 |
| LM F1，5 日 | 39 / 32 | 19 | 0.0074721 | 0.0075821 | 文本较差 |
| FinBERT F1，5 日 | 39 / 32 | 19 | 0.0074721 | 0.0074868 | 文本略差 |
| F2，1 / 5 日 | 22 / 17；18 / 15 | 0 | — | — | 未达到 24 事件门槛，未拟合 |

这里的 C 是**相同量价特征加文本**。FinBERT 1 日的配对均方误差差值 C−A 为 `−0.0000537`，但平均绝对误差由 `0.0375722` 增至 `0.0377215`，日期组移动块的描述性差值区间覆盖零。5 日的独立日期组更少，没有报告精细区间。F1 与 1 日收益的合并 Pearson 为 LM `+0.0026`、FinBERT `−0.2359`；这是同一已接触样本的描述，不是截面 IC。全部候选、Spearman、逐折门槛、各公司误差及失败状态见[机器回执](../results/news_sentiment_factor_v3_20261117/development_evaluation_redacted.json)。Tesla 两个入选财报都位于折外评估开始之前，**没有 Tesla 折外预测**，不能将 12 只来源公司说成 12 只均完成预测评价。

## 时间、来源和自审限制

SEC 完整申报头的接受时间与 submissions API UTC 一致；输入仍只达到 `SEC_ACCEPTANCE_ANCHOR`，不能证明网页首次可见或当时投资者实际收到。规则是接受日期后先等一个完整交易时段收盘，再从下一交易时段开盘进入；1/5 日退出按真实交易时段步进，包含 3 个半日。相同旧 8 事件中有 3 个 5 日退出日期因计入半日而改变，旧结果未重写。价格使用未复权开盘价，窗口内有来源登记的拆股/分红则排除；公司行动来源未确认不能当作无行动。参见[moomoo 官方历史 K 线说明](https://open.moomoo.com/api/quote/basic-data/history-kline)、[SEC 数据访问说明](https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data)。

评分输入是官方财报**叙述语气代理**。关键字章节分类仍可能误归类；不是每个块都有人审语义，表格数值也没有因评分通过而获事实认证。原文定位机械回查 1,728/1,728；来源标题及有限片段在看新收益前同团队核过，正式全块定位回查于对齐后执行，**不能冒称全部盲审**。两个源驱动选择修订均留痕，未按收益或模型分数挑段落。旧 v2 原件与本轮从完整申报提取的附件字节哈希不同，块数增加不等于同一原文的信息完整率提高。LM 词典版本与 FinBERT 旧权重的每个历史信号日可用性未证明；[版本/许可登记](../results/news_sentiment_factor_v2_20261117/literature_register.json)沿用 v2，FinBERT 本机研究用途受其模型卡许可约束。

本轮只在目的性关注池的季度财报上做 pooled 开发。公司事件与同日事件有关联；24 次 fit 不是 24 个独立实验。旧 8 事件的逐一排除诊断最大 Pearson 变动绝对值约 `1.037`，说明小样本描述高度敏感，但没有据此删事件。没有完整英文媒体档案、独立未见测试、严格历史可交易时间或前瞻收益配对，所以**文本相对量价和数量的稳定增量未建立**。财务输入 v2.5 仍为 `REJECTED_CANDIDATE`；没有 Qwen、微调、交易或收益策略上线。
