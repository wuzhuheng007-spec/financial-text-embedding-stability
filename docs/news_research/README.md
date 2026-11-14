# News research roadmap / 新闻研究技术路线

Last updated / 更新日期：2026-11-14（Asia/Shanghai） · Version / 版本：1.6

**Status / 状态：PROPOSED。** 旧 v3 开发复测尝试三次后因 OOM 停止，保存两份机械检查未通过的响应。v4 另版采用模型给原文引用、主机严格唯一匹配后计算位置，并请求 SDPA。真实有界 v4 运行尝试固定十篇、保存九份响应，其中一次原生进程中断；八份 JSON 可解析，一份符合 wire Schema，八条引用中七条唯一匹配、一条未找到，**canonical 可构造 0/1**。这是重复开发材料的工程结果，不是抽取质量或金融有效性结论。The earlier v3 retest stopped at OOM after three attempts and saved two mechanically invalid responses. The separate v4 version asks the model for body quotes, computes offsets only after unique exact matches, and requests SDPA. The bounded v4 run attempted the fixed ten articles and saved nine responses, with one native-process interruption; eight parsed as JSON, one passed the wire Schema, and seven of eight quotes were unique while one was missing, so **0/1** wire-valid response was canonically constructible. These are reused-development engineering results, not extraction-quality or financial-validity findings. Human adjudication and financial experiments remain **NOT_RUN**.

| 入口 / Entry | 内容 / Content |
| --- | --- |
| [中文技术路线](roadmap.zh.md) | 完整架构、模型、前视偏差、实验和阶段安排 |
| [English technical roadmap](roadmap.en.md) | Matching full English roadmap |
| [来源接入 / Source access](source_access.md) | Hugging Face、金十与 WRDS 的访问和准入边界 |
| [Machine-readable status / 机器状态](STATUS.json) | 当前文档与执行状态、下一步、固定路径 |
| [Source evidence / 来源依据](sources.json) | 已读来源、版本和证据边界 |
| [Change history / 修改记录](CHANGELOG.md) | 中英同步的变更记录 |
| [N1 annotation field template / N1 标注字段模板](event_annotation_template.md) | 公司—事件、证据位置及无法判断原因；不是人工金标准或模型输出 |
| [Bounded sample receipt / 有界样本回执](../../results/news_n1_20261114/source_sample_receipt.json) | 来源版本、限额、哈希和本机原文边界 |
| [Aggregate profile / 脱敏字段概况](../../results/news_n1_20261114/sample_profile.json) | 仅描述本轮扫描和保存的记录 |
| [Field-check receipt / 字段检查回执](../../results/news_n1_20261114/field_review_receipt.json) | 固定顺序前 10 条的 Codex 草稿检查，不是人工金标准 |
| [N1 output schema / N1 输出 Schema](company_event_output.schema.json) · [prompt / 提示词](company_event_prompt.md) | 公司事件、市场结果、未知状态和原文 Unicode 证据位置的工程契约 |
| [Prior N1 engineering progress](../../reports/2026-11-14_news_n1_next.en.md) · [此前中文进度](../../reports/2026-11-14_news_n1_next.zh.md) | 前一阶段的解析器、样本回读和下载前模型门槛 |
| [P0–P4 execution ledger](../../results/news_n1_next_20261114/plan_execution.csv) | 每项计划、实际、证据、状态和偏差 |
| [Input replay](../../results/news_n1_next_20261114/input_replay.json) · [pre-download model gate](../../results/news_n1_next_20261114/model_gate.json) | 输入哈希/画像对照与下载前的模型缺口记录 |
| [First inference English report](../../reports/2026-11-14_news_first_inference.en.md) · [中文报告](../../reports/2026-11-14_news_first_inference.zh.md) | 独立环境、官方模型、首次真实开发冒烟与有限结论 |
| [A–F execution ledger](../../results/news_first_inference_20261114/plan_execution.csv) · [aggregate smoke receipt](../../results/news_first_inference_20261114/smoke_receipt.json) | 逐项计划—实际和脱敏调用/机械检查状态 |
| [Model source plan](../../results/news_first_inference_20261114/model_source_plan.json) · [local integrity](../../results/news_first_inference_20261114/model_integrity_receipt.json) · [CUDA environment](../../results/news_first_inference_20261114/environment_receipt.json) | 固定版本来源、文件哈希、运行环境的不同证据层 |
| [Schema repair English report](../../reports/2026-11-14_news_schema_repair.en.md) · [中文报告](../../reports/2026-11-14_news_schema_repair.zh.md) | 旧错误离线归类、冻结候选与配对开发复测 |
| [A–D execution ledger](../../results/news_schema_repair_20261114/plan_execution.csv) · [old error audit](../../results/news_schema_repair_20261114/old_error_aggregate.json) | 逐项计划—实际与旧响应的错误实例／唯一响应数 |
| [Prompt v2](company_event_prompt_v2.md) · [candidate config](../../configs/news_n1_smoke_v3_prompt_complete.json) · [offline candidate replay](../../results/news_schema_repair_20261114/candidate_validation_replay.json) | 新提示词与实验配置的固定哈希和离线检查 |
| [Audited v3 aggregate](../../results/news_schema_repair_20261114/new_inference_aggregate.json) · [paired comparison](../../results/news_schema_repair_20261114/paired_comparison.json) | OOM 停止后的尝试、完成响应、格式和机械检查分母及聚合配对状态 |
| [Sanitized OOM receipt](../../results/news_schema_repair_20261114/oom_failure_receipt.json) · [final validation log](../../results/news_schema_repair_20261114/final_validation.log) | 第 3 次尝试的 CUDA 内存不足阶段与来源绑定；33 项最终合成测试及只读重放，和运行前 27 项有重叠 |
| [Final audit closure](../../results/news_schema_repair_20261114/final_audit_closure_receipt.json) | 运行前及运行后 auditor 版本、冻结的模型输入/runner、只读重放一致性 |
| [Grounding runtime English report](../../reports/2026-11-14_news_grounding_runtime.en.md) · [中文报告](../../reports/2026-11-14_news_grounding_runtime.zh.md) | 新 wire、严格引用定位、GPU 运行及实际结果边界 |
| [A–E grounding ledger](../../results/news_grounding_runtime_20261114/plan_execution.csv) · [old failure diagnosis](../../results/news_grounding_runtime_20261114/old_failure_diagnosis.json) | 计划—实际、旧三条引用与 OOM 的只读定位 |
| [Wire v2 Schema](company_event_wire_v2.schema.json) · [quote prompt](company_event_prompt_v3_quotes.md) · [v4 config](../../configs/news_n1_smoke_v4_grounded.json) | 候选输出接口、输入版本、注意力实现请求与有界预算 |
| [V4 pre-run synthetic validation](../../results/news_grounding_runtime_20261114/pre_run_validation.log) · [candidate replay](../../results/news_grounding_runtime_20261114/candidate_validation_replay.json) | 52 项 pytest 测试含旧 33 项；候选 15 项断言单列，均不是新的真实模型结果 |
| [Audited v4 run](../../results/news_grounding_runtime_20261114/inference_aggregate.json) · [paired status comparison](../../results/news_grounding_runtime_20261114/paired_comparison.json) | 十篇固定开发材料的尝试、响应、wire、引用和 canonical 分阶段数量；仅一篇有新旧完整配对响应 |
| [Native interruption observation](../../results/news_grounding_runtime_20261114/native_interruption_observation.json) · [recovery validation](../../results/news_grounding_runtime_20261114/recovery_validation.log) | 第 2 次调用中断与恢复版本证据；进程崩溃因果关系 UNKNOWN |

## Stable entry and future updates / 固定入口与后续更新

- Repository: `wuzhuheng007-spec/financial-text-embedding-stability`
- Working branch: `codex/sync-research-progress-20261113`
- Entry path: `docs/news_research/README.md`
- This branch is the current publication location; it is not a claim that the documents have been merged to the default branch.
- 当前文件发布在上述工作分支，不代表已经合并到默认分支。

后续处理“继续新闻项目/修改技术路线”时，先读取当前远端分支与本入口，再读取 STATUS.json 和需要修改的语言版本；不要仅依赖聊天记忆、旧 ZIP 或默认分支。若分支迁移，更新这里的路径。

For future continuation or edits, fetch the current remote branch and this entry, then STATUS.json and the relevant documents. Do not rely solely on conversation memory, old ZIPs or the default branch. Update this pointer if the branch moves.

中英两份保持相同的章节编号、研究含义和状态；改动时同时维护两份、状态和修改记录。一次工程完成不自动改变金融准入、协议接受或实验状态。

Keep section numbers, research meaning and statuses aligned across languages. Update both documents, the status and change history together. Engineering completion does not automatically establish financial admission, protocol acceptance or experiment execution.

本入口支持后续按请求读取和更新，不启动后台监控、自动采集或定时同步。This entry supports retrieval and updates on request; it creates no background monitoring, collection or scheduled synchronization.

## Next bounded task / 下一项有限任务

复核 OOM 诊断并另立后续运行决定；在独立人工裁定和隔离事件簇样本上评价抽取质量前，不解释两份开发响应为泛化表现。历史身份、时间与完整金融协议继续单列。Review the OOM diagnostics and make a separate decision about any later run. Obtain independent human adjudications and an event-grouped holdout before assessing extraction quality; the two development responses do not establish general performance. Historical identity, timing and the full financial protocol remain separate.
