# Shortfacts v2：契约修正与同六段有界复测——2026 年 11 月 15 日

执行时区：**Asia/Shanghai**。本开发任务检查本机固定版本 Qwen 能否从短官方证据提取结构化数值事实。六段原文保持不变，分别来自已保存的 [NVIDIA 财报](https://nvidianews.nvidia.com/news/nvidia-announces-financial-results-for-second-quarter-fiscal-2027)、[CoreWeave 财报及资本支出指引](https://investors.coreweave.com/news/news-details/2026/CoreWeave-Reports-Strong-Third-Quarter-2026-Results/default.aspx)和 [BLS 2026 年 10 月 CPI 公告](https://www.bls.gov/news.release/archives/cpi_11102026.htm)，每个来源两段。这些是重复使用的开发材料，不是留出测试集。[基线回读](../results/news_shortfacts_v2_20261115/baseline_readback.json)核对六段文本与保存原件的哈希；此前的[脱敏输入清单](../results/news_shortfacts_20261115/input_manifest_redacted.json)登记来源位置和版本。

## 契约与旧响应离线重评

新的[提示词](../scripts/news_shortfacts_v2/prompt.txt)与 [Schema](../scripts/news_shortfacts_v2/output.schema.json)在复测前冻结。没有主体依据时，`entity` 允许 `null`；两段 CPI 的私人修正参考将原文点名的指数作为被衡量主体，发布机构仅为来源元数据。`metric` 表示数值指标并区分当前／此前指引；`basis_text` 仅表示有证据的会计、调整或季调口径。数值保留范围、正负号、近似词与容差。事前固定的规则每段至多选前三项目标；六段分别为 **3、1、2、3、2、3** 项，共 **14** 项，无超额段落。[契约回执](../results/news_shortfacts_v2_20261115/contract_receipt.json)绑定未变输入、提示词、Schema 与私人参考哈希。

[离线诊断回执](../results/news_shortfacts_v2_20261115/offline_diagnosis_receipt.json)区分**四处旧参考错误、一处输入上下文缺口、一处 Schema／任务冲突和一处 9 月季调范围无法裁定**。这些是设计问题，不能算作七处模型错误。私人逐字段表保留旧响应哈希、原值、证据位置、判断、原因及参考版本。冻结的原参考 v1、原始响应和[原版评分](../results/news_shortfacts_20261115/public_validation_summary_v2.json)均未覆盖。修正参考在旧版运行后、v2 新运行前冻结，标记为 `CODEX_REFERENCE_DRAFT`，不是独立人工标准答案。

[旧响应在修正规则下的回顾性评分](../results/news_shortfacts_v2_20261115/retrospective_summary_redacted.json)重新作出 `CODEX_REVIEW` 字段判断。旧六份响应中，严格原始 JSON 为 **1/6**，完整单层围栏适配为 **5/6**；六份视图均通过 v2 Schema。旧模型输出六条事实，一对一对应 14 个参考目标中的六条，另八条完全未输出；新规则下完整正确为 **0/14**。历史原版评分使用旧参考，记录六条模型事实中全字段正确 **0/6**；两者分母和参考版本不同。五份适配视图仍属于原始格式失败。

## 冻结复测与同规则比较

[运行前冻结回执](../results/news_shortfacts_v2_20261115/pre_run_freeze_receipt.json)将未变的六段文本哈希绑定至唯一提示词、Schema、私人修正参考、评估器和本机固定 [Qwen/Qwen2.5-3B-Instruct 版本](https://huggingface.co/Qwen/Qwen2.5-3B-Instruct/tree/aa8e72537993ba99e69dfaafa59ed015b17504d1) `aa8e72537993ba99e69dfaafa59ed015b17504d1`。复用既有 Python 环境与 CUDA，没有下载新模型、升级依赖或使用约束解码。六项完整提示词为 **536–575 tokens**，低于 2,048 输入上限。固定 batch size 1、贪心生成、seed 1729、最多 768 个新 token、生成阶段 180 秒、加载阶段 240 秒和零重试。[针对性检查](../results/news_shortfacts_v2_20261115/focused_tests.log)为 16 个测试函数及其六个子测试通过；这是软件检查，不是 22 项独立实验。另有[公开回执](../results/news_shortfacts_v2_20261115/public_receipts_tests_final.log)与[交接隐私](../results/news_shortfacts_v2_20261115/handoff_tests_final.log)检查分别 5 项和 4 项通过，均不是模型质量验证。

[脱敏运行回执](../results/news_shortfacts_v2_20261115/run_receipt_redacted.json)记录**计划六项、尝试六项、开始生成六项、保存响应六份**，按 A1–C2 固定顺序每段一次，执行时段为 **Asia/Shanghai 11:14:48–11:18:42**。所有工作进程正常退出并由 EOS 停止；没有记录超时、OOM、原生崩溃或重试。逐项总耗时 **30.438–47.516 秒**，生成 **109–290 tokens**。回执保留各进程可观测的 CUDA 显存、请求的 SDPA 与实际 dtype/device；有效注意力内核仍未知。私人原始响应和日志原样保存，未修补内容。

[三版本比较](../results/news_shortfacts_v2_20261115/comparison_redacted.json)、[旧响应重评](../results/news_shortfacts_v2_20261115/retrospective_summary_redacted.json)及[新运行脱敏评分](../results/news_shortfacts_v2_20261115/new_run_summary_redacted.json)分别记录：

| 版本 | 完成调用 | 严格原始 JSON/Schema | 围栏适配 Schema | 输出事实 | 一对一对应参考 | 完整正确 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 历史 v1 原参考／原评分 | 6/6 | 1/6 | 5/6 | 6 | 6/14 | 模型六条事实中全字段正确 0/6 |
| 旧 v1 响应按修正 v3 参考重新审阅 | 无新调用；旧六份 | 1/6 | 5/6 | 6 | 6/14 | 参考目标中 0/14 |
| 新 v2 运行按同一修正参考／规则 | 6/6 | 6/6 | 0/6 | 12 | 12/14 | **参考目标中 1/14** |

新输出的 **12/12** 个证据 ID 和引用片段均能在冻结输入中机械找到；这只证明字面位置。B1 此前指引与 C2 九月两条的引用没有包含支持其数值所需的表达。同系统内容自审认为 **1/12** 条输出事实完整得到来源支持，另 11 条部分支持。唯一全字段正确的是 A1 第一条营收事实；其余 11 条已对应事实含字段或引用问题。两个参考目标完全未输出，分别是 B2 设施金额与 C2 八月变化。未见重复输出或无对应的额外事实，本轮未出现空 `facts`。修正规则下“完整正确召回”为 **1/14**，只用于开发参考比较，不是总体准确率估计。

| 字段（`CODEX_REVIEW`） | 旧响应正确／错误／未知；分母 6 | 新响应正确／错误／未知；分母 12 |
| --- | ---: | ---: |
| 主体 | 3 / 3 / 0 | 10 / 2 / 0 |
| 指标 | 3 / 3 / 0 | 2 / 10 / 0 |
| 数值表达 | 5 / 1 / 0 | 10 / 2 / 0 |
| 单位 | 4 / 2 / 0 | 7 / 5 / 0 |
| 期间 | 3 / 3 / 0 | 6 / 6 / 0 |
| 口径 | 5 / 1 / 0 | 12 / 0 / 0 |

下一项工程重点是**指标／期间绑定及引用支持**，尤其要分清实际值、未来指引和融资计划上限。在这六段上，新运行不再添加围栏，目标对应数增加，但多数已对应事实仍有错误。下一候选应事先定义，并继续按分层规则检查。C1 九月季调口径的适用范围仍有歧义，需要保留，不得悄悄算作模型错误或替换参考。

| 阶段 | 计划 | 实际与证据 | 状态 |
| --- | --- | --- | --- |
| P0–P1 | 保留基线，离线修正契约／参考 | [基线](../results/news_shortfacts_v2_20261115/baseline_readback.json)、[契约](../results/news_shortfacts_v2_20261115/contract_receipt.json)、[原因诊断](../results/news_shortfacts_v2_20261115/offline_diagnosis_receipt.json) | 完成 |
| P2–P3 | 测试完整围栏适配，冻结唯一配置 | [旧输出重评](../results/news_shortfacts_v2_20261115/retrospective_summary_redacted.json)、[运行前冻结](../results/news_shortfacts_v2_20261115/pre_run_freeze_receipt.json) | 完成 |
| P4–P5 | 最多六次调用；同规则比较新旧输出 | [运行回执](../results/news_shortfacts_v2_20261115/run_receipt_redacted.json)、[比较](../results/news_shortfacts_v2_20261115/comparison_redacted.json) | 完成 |
| P6–P7 | 双语文档、选择性同步和交接 | 详见[完整计划—实际台账](../results/news_shortfacts_v2_20261115/plan_execution.csv)；远端提交见分支历史与交接 README | 远端回读后完成 |

## 解释与边界

模型、tokenizer、六段字节、生成预算、seed 和任务顺序保持固定；输出契约、提示词与参考同时改变。差异属于**工程组合修正**，不能归因于某一句提示词。机械 JSON、Schema、证据 ID 和逐字引用检查与同系统 `CODEX_REVIEW` 语义判断分别报告；引用找到不代表整条事实正确。本轮不能证明一般抽取能力、历史预测能力或金融有效性。独立人工标准、正式金融准入、收益标签、拟合、预测和回测仍为 **NOT_RUN**。

[计划—实际台账](../results/news_shortfacts_v2_20261115/plan_execution.csv)逐项记录 P0–P7 与证据。原文、原始响应、参考行、权重及私人字段判断留在 Git 忽略的本机 `artifacts/`；公开范围仅包含脱敏汇总和可重复执行的代码。
