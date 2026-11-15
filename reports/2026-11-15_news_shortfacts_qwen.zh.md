# 首次短证据段落到结构化事实的 Qwen 有界开发试验——2026 年 11 月 15 日

时区：**Asia/Shanghai**。本轮实际运行本机固定版本 Qwen2.5-3B-Instruct，检验它能否把简短官方原文转换为事实对象。六项任务依次取自已保存的 [NVIDIA 财报](https://nvidianews.nvidia.com/news/nvidia-announces-financial-results-for-second-quarter-fiscal-2027)、[CoreWeave 财报及资本支出指引](https://investors.coreweave.com/news/news-details/2026/CoreWeave-Reports-Strong-Third-Quarter-2026-Results/default.aspx)和 [BLS CPI 公告](https://www.bls.gov/news.release/archives/cpi_11102026.htm)，每类两段。[计划—实际台账](../results/news_shortfacts_20261115/plan_execution.csv)逐阶段记录。**六项是调用配额和开发材料，不是统计样本或盲测集。**

## 冻结输入与真实运行

[脱敏输入清单](../results/news_shortfacts_20261115/input_manifest_redacted.json)记录固定顺序、三份官方保存原件的哈希、原文位置、提取方式、六段文本哈希及 tokenizer 版本。完整段落、原始 HTML 和运行前建立的 14 条 `CODEX_REFERENCE_DRAFT` 留在本机。生成前，原件、段落和参考短引用均通过回读。模型只看到固定段落和同一份[提示词](../scripts/news_shortfacts_v1/prompt.txt)，没有输入此前的事实表、分析卡、传导边、计算结果、参考答案、收益或市场共识。

[环境回读](../results/news_shortfacts_20261115/environment_receipt.json)将本机 11 个模型文件逐一核对至既有官方快照完整性回执，包括权重分片和 tokenizer；revision 为 `aa8e72537993ba99e69dfaafa59ed015b17504d1`。[官方固定版本](https://huggingface.co/Qwen/Qwen2.5-3B-Instruct/tree/aa8e72537993ba99e69dfaafa59ed015b17504d1)及[研究许可](https://huggingface.co/Qwen/Qwen2.5-3B-Instruct/blob/aa8e72537993ba99e69dfaafa59ed015b17504d1/LICENSE)已核验，本轮仅作本机非商业研究。检查时 CUDA 可用、空闲显存 11,026 MiB；这不保证之后每刻均有同等显存。[脱敏运行配置](../results/news_shortfacts_20261115/run_config_redacted.json)固定 batch size 1、贪心生成、seed 1729、CUDA float16、请求 SDPA、最多 768 个新 token、生成阶段 180 秒上限和零重试；实际所用注意力内核未知。固定 tokenizer 对六项**完整提示词**计数为 **263–302 tokens**，低于 2,048 上限，没有静默截断。运行前通过聚焦软件检查。

[真实运行回执](../results/news_shortfacts_20261115/run_receipt_redacted.json)和[脱敏尝试台账](../results/news_shortfacts_20261115/attempt_ledger_redacted.jsonl)记录：**计划 6、尝试 6、开始生成 6、保存响应 6**，顺序为 A1、A2、B1、B2、C1、C2。每项仅调用一次普通生成，并使用独立可终止进程。本轮没有超时、OOM、原生崩溃、重试、换模型或安装新依赖。六份原始响应、工作进程日志、token 计数和 43 文件输出冻结回执保存在 Git 忽略的本机 `artifacts/`；审阅后再次验证未改动。

## 格式、证据与内容分别检查

[脱敏验证汇总](../results/news_shortfacts_20261115/public_validation_summary_v2.json)保留不同阶段的实际分母：

| 检查 | 结果 | 含义 |
| --- | ---: | --- |
| 严格原始 JSON／严格原始 Schema | 1/6／1/6 | 另外五份带 Markdown 围栏，违反原始输出契约。 |
| 单独保存的围栏解析视图 | 5/5 JSON 与 Schema 合法 | 不把解析视图冒充严格原始 JSON；加上唯一严格响应，六份对象仅在明确解析步骤后符合 Schema。 |
| 段落 ID 与连续逐字引用 | 6/6 条模型事实 | 仅为机械吻合，不保证所有字段正确。 |
| Codex 语义自审 | 1/6 条数值事实主张完整得到来源支持；5/6 条部分支持 | 这是 `CODEX_REVIEW`，不是独立人工金标准。 |
| 全字段正确 | 0/6 条模型事实 | 数字和引用吻合时，其他字段仍可能有错。 |
| 预设参考覆盖 | 匹配 6/14，漏抽 8/14 | 冻结的开发参考草稿不能当作准确率基准。 |

同一事实可带多个错误标签，不能相加为独立失败数。记录到 **5 次原始格式失败、8 条参考事实漏抽、5 处指标名称错误或过于笼统、实体 3 处、期间 3 处，以及数值、单位、口径各 1 处**。六条模型事实中未发现新增无依据数值或重复。逐字段自审判定 `value_text` 有 5 条正确，指标名称仅 1 条正确；四项字段判断保留 `UNRESOLVED`，其中三项来自输入／参考设计，一项来自单位表达判断。来源支持数不等于完整结构化事实正确数。

运行前参考 v1 **未被改写**。[分版本更正回执](../results/news_shortfacts_20261115/reference_correction_redacted.json)指出：v1 把 A2/B1 的“当前／此前指引”放进会计／调整 `basis_text`，且 C2 输入没有写出 BLS 机构名称却要求该实体。私人 v2 仅用于透明记录未来敏感性，**没有用于提高本次主结果分数**；受影响的 A2/B1 口径及 C2 实体判断按证据标为 `UNRESOLVED`。

## 下一步与边界

**下一轮只优先修严格原始 JSON 格式。** 六份中五份虽然围栏内可解析，却违反原始输出契约。可在固定模型、输入和预算下只提出一个去围栏提示词候选，并继续分别检查格式、引用和事实。漏抽与字段不精确已保留；格式改善本身不会使它们自动正确。

最终[聚焦软件检查](../results/news_shortfacts_20261115/focused_tests_release.log)为 **12/12 通过**，验证的是运行器与评估器的风险，不是抽取准确率。本轮没有新增页面、新闻采集、模型下载、收益标签、拟合、预测、回测或交易策略；正式金融实验为 `NOT_RUN`。历史证券身份、公布前时点共识及正式金融准入仍未解决。六项开发任务不能证明模型的一般抽取能力、历史预测能力或金融有效性。
