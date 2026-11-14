# N1 定向修复：字段诊断与一次有界开发运行

日期：2026-11-15，Asia/Shanghai（UTC+08:00）。本报告仍使用此前 N1 的 FNSPID 固定顺序前十篇**开发材料**，不构成独立抽取质量或金融表现评价。此前 [v4 报告](2026-11-14_news_grounding_runtime.zh.md)和已审计回执保持原状。

## 研究目标与计划—实际

眼前的工程目标是得到一份完整通过机械校验的公司事件输出。较远的研究目标仍是形成公司级新闻事件测量，用于未来风险与表示稳定性研究；质量判断和金融准入另有前提。[A–E 台账](../results/news_targeted_repair_20261115/plan_execution.csv)逐项记录任务、实际和依据。

| 阶段 | 计划 | 实际与证据 | 状态 |
| --- | --- | --- | --- |
| A · 诊断保存的 v4 失败 | 不修复旧响应、不调用模型，读取原保存的响应与正文，定位字段和引用失败，核查原因未明的中断。 | [离线诊断](../results/news_targeted_repair_20261115/wire_failure_diagnostic.json)与[对账](../results/news_targeted_repair_20261115/wire_failure_reconciliation.json)同冻结 v4 聚合一致。九份保存响应包括八份合法 JSON、七份 wire 不合法、一份 wire 合法、一份 JSON 不合法。七份 wire 不合法响应共产生五组、14 个 Schema **错误实例**，见下文。唯一 wire 合法响应请求八条引用，其中七条唯一、一条不存在。逐条错误值、正文和生成文本只留本机。[私人证据复查回执](../results/news_targeted_repair_20261115/native_interruption_recheck.json)确认中断调用没有保存 stderr 或退出码；原因仍为 **UNKNOWN**。 | 证据复查完成；原因未明 |
| B · 冻结运行停止规则 | 保留 v4 证据；新运行遇 OOM、原生崩溃、非零退出或缺响应即停，不重试、不自动恢复。 | [v5 配置](../configs/news_n1_smoke_v5_targeted.json)声明了比旧版更严格的生命周期规则，这是与提示词变化不同的一项变化。[运行前检查](../results/news_targeted_repair_20261115/runtime_prerun_validation.log)通过九项聚焦合成测试。[已审计运行](../results/news_targeted_repair_20261115/inference_aggregate.json)只作十次单次尝试，没有恢复；记录三次逐次超时，无 OOM 或原生故障停止状态。实际运行没有触发新的异常退出停止分支。 | 完成；停止分支只作合成验证 |
| C · 只做一个定向提示词候选 | 保持模型、输入、语义标签、wire 与 canonical Schema、适配器、校验器及生成预算，针对实际失败字段绑定一个新提示词。 | [v4 定向提示词](../docs/news_research/company_event_prompt_v4_targeted.md)及[针对性回执](../results/news_targeted_repair_20261115/prompt_targeting_receipt.json)将五组字段、缺失引用与 JSON 长度线索对应到新指令。允许值表由固定 Schema 生成，提示词中没有示例，本设计步骤未调用模型。[运行前门槛](../results/news_targeted_repair_20261115/prerun_gate_receipt.json)绑定实际提示词、配置、模型和输入哈希，十篇全文均符合上下文预算。配置差异还包含标识与生命周期字段，因此不是严格单因素比较。 | 运行前设计完成 |
| D · 验证并只运行一次 | 重放有关合成测试，预检同十篇全文；按冻结 v5 契约每篇最多调用一次，合计最多十次，分别审计原始与派生阶段。 | [合并运行前测试](../results/news_targeted_repair_20261115/pre_run_validation.log)通过 37 项合成测试，包含另跑的诊断、提示词和 runner 检查。其后六项[审计器/比较器合成测试](../results/news_targeted_repair_20261115/audit_comparison_synthetic_test_log.txt)也通过。[运行前门槛](../results/news_targeted_repair_20261115/prerun_gate_receipt.json)显示十篇全文均符合上下文预算；当时 GPU 空闲 7,895 MiB，另有七个计算进程，无本轮新闻 worker。独立[审计的 v5 运行](../results/news_targeted_repair_20261115/inference_aggregate.json)对固定十篇均开始生成，保存七份响应，保留三次逐次超时。七份响应中六份 JSON 合法；六份均未通过 wire Schema，canonical 构造为零。 | 有界运行完成；审计通过；无 wire 合法输出 |
| E · 准备人工审阅并同步 | 将以原文为先的空白审阅字段与模型草稿分开，向既有分支发布脱敏证据和双语进度。 | [审阅准备回执](../results/news_targeted_repair_20261115/human_review_preparation.json)绑定十篇开发材料的本机原文优先入口，**零条**预填裁定，也未嵌入模型草稿。[配对比较](../results/news_targeted_repair_20261115/paired_comparison.json)覆盖固定十个位置并通过，但只有六个位置两版都保存响应，且这六个位置两版 wire 合法数均为零。[首个发布提交](https://github.com/wuzhuheng007-spec/financial-text-embedding-stability/commit/c3d6a4143bec96ad4dbdbc1cba6500db45e2646e)已推送既有分支；远端 SHA 与五份关键文件的 blob 均和本机一致。脱敏交接文件夹及 ZIP 已通过 CRC 检查。 | 发布已核实；人工裁定 NOT_RUN |

## 保存的失败具体说明了什么

[离线诊断](../results/news_targeted_repair_20261115/wire_failure_diagnostic.json)中的五组 Schema 错误是 `/article_disposition`（4 个 enum 实例）、`/article_evidence/*/role`（4 个 enum）、`/records/*/evidence_spans/*/role`（4 个 enum）、`/market_outcome_direction`（1 个 const）和 `/records/*/unknown_reason`（1 个 not）。这些是**错误实例**，不是 14 篇文章。12 个 enum 实例均归类为未列出的字符串；具体私人原值不发布。其余两项为跨字段约束。定向提示词明确允许标签、市场结果状态与方向的关系、未知影响与理由的关系，并要求短且逐字的正文引用。`event_type` 仍是自由文本。旧响应中那条不存在的引用依然阻止 canonical 构造，未暗中修复。

旧 [v4 聚合](../results/news_grounding_runtime_20261114/inference_aggregate.json)记录一份 JSON 不合法响应及达到输出 token 预算的截断线索，尚不能据此确定 JSON 无效原因。第 2 个位置的[原生中断复查](../results/news_targeted_repair_20261115/native_interruption_recheck.json)确认已提交发起调用且有 worker 创建阶段的 CUDA 快照，但没有保存 stderr、退出码、模型加载完成、开始生成或响应。Windows 同期记录 `python.exe`/`c10.dll`（`0xc0000005`）及 `codex.exe`（`0xc0000409`）应用错误；二者因果关系 **UNKNOWN**。v4 未记录 OOM，不足以解释 GPU 或注意力后端的作用。新停止规则与旧恢复方式不同，比较覆盖范围时须考虑。

[v5 配置](../configs/news_n1_smoke_v5_targeted.json)绑定原作者 FNSPID 修订版和样本哈希、固定 Qwen2.5-3B-Instruct 修订版、FP16、SDPA 请求、seed 1729、wire/canonical 契约，以及最多十次调用、每次 1,024 个新 token、180 秒、总计 1,800 秒、零重试的预算。来源与产物哈希留在配置及回执。十篇均为重复使用的开发材料；逐字匹配、主机计算位置及机械有效都不能证明受影响公司、事件或方向在语义上正确。

## 结果与下一门槛

[v5 独立审计](../results/news_targeted_repair_20261115/inference_aggregate.json)通过。运行在本地时间 03:51:56–04:11:13 完成。分阶段数量为：固定十篇**10/10** 已尝试且开始生成；**7/10** 保存响应；保存的响应中 **6/7** JSON 合法；JSON 合法响应中 **0/6** 符合 wire Schema。其余位置是**三次 `CALL_TIMEOUT`**、**一次 `JSON_PARSE_ERROR`**（达到 1,024 输出 token 上限，是截断线索而非已证实原因）及**六次 `WIRE_SCHEMA_INVALID`**。六份 wire 不合法响应产生**九个 Schema 错误实例**：`market_outcome_direction` const 四个、`records[].evidence_spans` minItems 两个、`records[].unknown_reason` enum 两个、`records` maxItems 一个。错误实例不是文章数。由于没有 wire 合法响应，引用未进入核查，引用成功为 **NOT_EVALUATED（0/0 条引用）**；canonical 构造的条件分母同样为零，记 **NOT_EVALUATED（0/0 份 wire 合法响应）**。本轮没有完整通过机械校验的公司事件输出；这些数值不构成语义准确率。

[配对比较](../results/news_targeted_repair_20261115/paired_comparison.json)绑定相同样本、模型修订版、wire/canonical Schema、适配器和校验器。v4 与 v5 均尝试固定十个位置；v4 保存九份，有一份 wire 合法但引用未解决；v5 保存七份，wire 合法为零。只有**六个**位置两版均保存响应，在这一配对子集上两版 wire 合法数均为 **0/6**。其余缺失位置保留在转移表。定向提示词在每篇固定输入上多 644 个 token；异常退出停止策略也发生变化。错误实例总数和总体 wire 覆盖的响应分母不同，不能据此声称任何字段错误已改善。v5 三次超时均未重试；未记录 OOM 或原生崩溃。实际 SDPA kernel 仍为 **UNKNOWN**。在这一批重复使用的开发材料上，无法把运行差异单独归因于提示词或运行规则。

本机人工审阅入口仍为空白。独立人工裁定、抽取质量指标、按事件簇隔离的评价、历史公司—证券身份、已接受的市场时间与价格规则、正式金融标签、模型拟合、预测和收益评价，分别仍为 **NOT_RUN / 尚未准入**。下一质量步骤是以原文为先的独立人工裁定，再建立按底层事件和重复报道隔离的留出集。若要启动另一个模型版本，须先单独判断新的 wire 字段约束错误、超时和 JSON 失败，保留本轮冻结运行原状。JSON 或 canonical 机械通过本身不构成金融结论。

交接包的五份聚焦合成测试在未安装 `torch` 的 Python 3.11.9 环境下 **27/27 通过**；原生退出测试改用临时模块模拟。[验证日志](../results/news_targeted_repair_20261115/torchless_handoff_validation.log)仅证明交接包可无模型运行，与先前工程测试重叠，不是新增模型试验。本机交接包只含公开报告、代码、测试与聚合回执。
