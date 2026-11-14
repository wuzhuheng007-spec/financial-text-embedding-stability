# N1 输出契约修复：旧错误离线归类与固定提示词复测

日期：2026-11-14，Asia/Shanghai（UTC+08:00）。本轮继续使用**同一批前十篇开发材料**做回顾性文本工程检查，不是独立抽取质量样本或当年可执行的金融预测。[首次真实推理报告](2026-11-14_news_first_inference.zh.md)及未改动的[聚合回执](../results/news_first_inference_20261114/inference_aggregate.json)是旧版本基线。

| 阶段计划 | 实际与证据 | 状态 |
| --- | --- | --- |
| A 离线定位旧输出 | 用原 Schema 与校验器重验九份原始响应，检查旧请求与超时阶段；逐条实际值留本机。[错误聚合](../results/news_schema_repair_20261114/old_error_aggregate.json)记录 **87 条 Schema 错误实例**，分布在九份响应中，不能当作 87 篇文章。`input_binding` 在 9/9 中是字符串而非对象，`market_outcome_reported` 在 9/9 中是布尔值而非三态字符串，`article_disposition` 在 9/9 中使用非标准字符串。实际发给模型的 System 消息缺完整 Schema 和 `COMPANY_EVENTS_FOUND` 字面值。超时调用已启动、完成模型加载并进入生成，但没有响应或 token 数。新增模型调用为 0。 | 完成，离线诊断 |
| B 冻结单一候选 | [v2 提示词](../docs/news_research/company_event_prompt_v2.md)及[v3 实验配置](../configs/news_n1_smoke_v3_prompt_complete.json)与交接包候选逐字节一致。[离线回读](../results/news_schema_repair_20261114/candidate_validation_replay.json)证实实际 System 消息包含与原文件深度相等的完整 Schema 和一个机械校验通过的虚构格式示例；前十篇 User 消息不含示例。模型、样本顺序、Schema、validator 与全部生成预算不变；两项聚焦合成测试通过。完整 Schema 和示例是**同时加入**的，不能分别归因。 | 完成，离线候选检查 |
| C 一次有界配对复测 | [v3 审计聚合回执](../results/news_schema_repair_20261114/new_inference_aggregate.json)和[脱敏失败回执](../results/news_schema_repair_20261114/oom_failure_receipt.json)记录第 3 次尝试时发生 CUDA 内存不足，模型已完成加载并进入生成。两份 EOS 响应保存并回读，其余七篇未尝试；本轮没有重试或另开运行。两份响应的 JSON、Schema 和输入绑定均通过，但最终机械检查都未通过。 | 部分完成 · 终止性 OOM_STOP |
| D 比较并脱敏同步 | [配对比较](../results/news_schema_repair_20261114/paired_comparison.json)对全部十个目标位置的状态完成有界审计，只有**两篇**在新旧两轮均有完整响应，因此不存在十篇完整响应的配对比较。中英报告及进度文件已记录审计结果；远端同步和不含私人数据的 GPT 交接包另在[逐项台账](../results/news_schema_repair_20261114/plan_execution.csv)核对。 | 部分完成 · 只有两篇完整配对 |

[A–D 逐项计划—实际台账](../results/news_schema_repair_20261114/plan_execution.csv)列明每项证据与偏差。旧错误回执同时报告错误**实例数**和出现该错误的**唯一响应数**：例如 `REQUIRED` 共 45 条，涉及八份响应；`ENUM` 共 18 条，涉及全部九份。这些是格式诊断，不能作为独立的模型质量观测。旧提示词接口不完整是直接可见的问题，但现有证据不能证明它解释了全部失败。

[运行前门槛](../results/news_schema_repair_20261114/prerun_gate_receipt.json)在新增模型调用之前绑定输入、模型、提示词、配置、Schema、validator 和 runner 哈希，同时登记当时的 auditor 版本。模型运行**之后**，auditor 为终止状态检查进行了加固；提示词、配置、Schema、validator 和 runner 始终未变。[最终审计闭环回执](../results/news_schema_repair_20261114/final_audit_closure_receipt.json)登记两个 auditor 版本和无新增模型调用的只读重放一致性。[真实 tokenizer 预检](../results/news_schema_repair_20261114/prompt_token_preflight.json)显示候选提示词使十篇输入各增加恰好 **1,831 个 token**；在不变的 32,768 token 上下文上限、预留 1,024 个输出 token 下，十篇仍全部容纳。[运行前验证日志](../results/news_schema_repair_20261114/pre_run_validation.log)记录运行前代码状态的 **27 项不同合成测试**（原有 25 项加两项提示词测试）；这不是抽取准确率测试。

[最终验证日志](../results/news_schema_repair_20261114/final_validation.log)记录 auditor、comparator 加固后 **33 项不同合成测试**通过，旧、新及配对回执只读重放一致。运行前 27 项与最终 33 项存在重复覆盖，不能相加为 60 项独立测试。两份测试日志都不测抽取准确率。

## 复测结果与边界

旧轮尝试 **10/10**，保存 **9** 份响应，完成响应中 **0/9 符合 Schema**。v3 轮尝试 **3/10**，保存 **2** 份响应；完成响应中 **2/2 符合 Schema**（按尝试为 **2/3**，按固定目标为 **2/10**）。这只描述两篇重复使用的开发材料的格式合规，不能推广为一般改善率。两份 v3 输出的**最终机械检查均未通过（0/2）**：一份为 `ARTICLE_REASON_INCONSISTENT`，一份为 `SPAN_SLICE_MISMATCH`；核验的 **3 处证据切片均未与原正文完全吻合（0/3）**。两份 Schema 合法输出的文章状态分别为一条 `COMPANY_EVENTS_FOUND`（一条事件记录）和一条 `NO_SUPPORTED_COMPANY`（零条记录）。严格定义的合成示例复制标志为 **0/2**，不代表排除所有形式的复制或语义错误。

十个固定文章位置的配对状态为：旧 `SCHEMA_INVALID` → 新 `MECHANICAL_INVALID` **2** 篇；旧 `SCHEMA_INVALID` → 新 `NOT_ATTEMPTED_AFTER_OOM_STOP` **6** 篇；旧 `SCHEMA_INVALID` → 新 `OOM_STOP` **1** 篇；旧 `CALL_TIMEOUT` → 新 `NOT_ATTEMPTED_AFTER_OOM_STOP` **1** 篇。提示词使每篇输入增加 **1,831 个 token**，并同时加入完整 Schema 和虚构示例。新轮仅有两份完整响应且因 OOM 终止，不能据此估计稳定吞吐量或提示词的因果效果。公开比较仅保留聚合转移，逐条文本和配对细节留本机。

前十篇此前用于字段及提示词设计，即使配对开发材料上的机械合规改善，也不能推出一般抽取质量，更无法区分完整 Schema 与合成示例各自的作用。人工裁定栏仍为空。JSON 可解析、Schema 合法或证据切片吻合，都还需要独立人工判断事件、公司和影响方向是否正确。新闻历史可用时间、公司—证券身份及完整金融协议未解决；金融标签、预测和收益评价均 **NOT_RUN**。

## 下一步

检查已保存的 OOM 诊断和两份响应的机械检查失败，再决定是否值得另立版本、采用资源可承受的后续运行；本轮未重试。抽取质量结论之前，需要独立公司—事件人工裁定和隔离事件簇的新样本。历史可用时间、公司—证券身份和具体且被接受的金融协议，仍是金融标签、预测及收益评价的前提。
