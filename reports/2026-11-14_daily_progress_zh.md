# 研究简报：2026 年 11 月 14 日（Asia/Shanghai，UTC+08:00）

本项目要检验文本嵌入构建方式的变化，是否会传导到公司相似关系、预测信号和金融评估。今天完成的是已有向量的诊断、评估契约修复、真实申报候选登记及来源缺口定位。候选发现回执的 `2026-11-13T19:38:18Z` 换算为本地 **11 月 14 日 03:38**；本简报按该时区及今天的三轮运行记录界定日期。另一段 ChatGPT 对话中有 `M5_source_response_handoff_20261114.zip` 链接，但其内容未作为本次可核验的本机输入取得；不能据此声称已收到 WRDS 来源答复。

## 已完成与有限发现

- [保存向量清单](../results/m5_20261114_public/diagnostic/input_inventory_public.json)核对了五份 Item 1、固定的 `all-MiniLM-L6-v2` 版本及两轮向量配置。[Top-2 诊断](../results/m5_20261114_public/diagnostic/saved_vector_diagnostic_public.json)中，MSFT 在四项切块长度/池化比较更换邻居：等权池化的 128→123、133、138，以及正文 token 加权的 128→123。这是读取旧向量的描述性诊断，本轮没有重新编码；不能推及总体、其他模型或股票收益。
- [评估器](../src/m5_bridge/evaluate.py)修复了时区顺序、首个后续交易日与日历覆盖、训练标签结束时间、市场相对目标的来源绑定（F1–F4）。[合并测试日志](../results/m5_20261114_public/evaluator/independent_all_tests.log)为 **54 项通过**，其中已包含六项候选发现测试；后来隔离重放的六项是依赖可移植性核验，不能相加为 60 项独立测试。[探针](../results/m5_20261114_public/evaluator/after_probe_final.json)及回读均为合成验证。
- 冻结的 **32 个 CIK** 范围登记了 **133 条非开发原始 10-K 候选**，**30 条开发记录**另列；取得 **20 份原件**，其中 **16 份**通过候选 Item 1 抽取。[发现摘要](../results/m5_20261114_public/discovery/candidate_discovery_summary_safe.json)与[来源回读](../results/m5_20261114_public/discovery/candidate_discovery_verify_safe.json)保留输入版本和核验结果。这不是正式样本。
- 在当前候选证券 ID 内，**20 条**有有限端点区间字面覆盖申报日且未命中未知端点；与 **16 条**候选 Item 1 的交集为 **5 条**（[联合覆盖](../results/m5_20261114_public/source/joint_coverage_safe.json)）。按固定顺序复查其中 **3 条**，历史公司—证券有效关系和主证券规则仍不足（[匿名状态](../results/m5_20261114_public/source/s3_identity_chain_safe.json)）。**4 份**歧义原件中，**2 份**为双申报主体，**2 份**为其他 Item 1 标题形式（[原件复查](../results/m5_20261114_public/source/s3_ambiguity_cases_safe.json)）。
- 已备好一条单字母 `LINKENDDT` 与一条有限日期对照的**私人** WRDS 包，尚未发送。[安全状态](../results/m5_20261114_public/source/s1_wrds_pair_safe.json)不含原行。

## 当前状态与下一步

候选台账的正式准入记录为 **0**，完整 M5 样本尚未定义，协议仍为 `PROPOSED / researcher_accepted=false`。原查询 SQL、release 与 `LINKENDDT` CSV 转换语义仍为 `SOURCE_PENDING`；正式金融标签、拟合、预测和收益评估均 `NOT_RUN`。今天没有金融有效性结论。

下一步先取得原 WRDS 查询资料或对确切导出的支持说明，同时建立有日期依据的历史公司—证券身份链。若另一条来源链足够完整，可以采用，不必强求旧 Header 连接。再把证券范围、主证券、文本准入、日历、价格调整、退出和共同样本写成明确协议；有新证据或决定后，按条件式交接推进，不重复全量扫描、下载或编码。详细证据与限制见[英文日报](2026-11-14_daily_progress.md)及其所列的本机运行记录。

进度：今日准备与核验 `██████████`；来源解释 `SOURCE_PENDING`；正式金融实验 `NOT_RUN`。
