# News research roadmap / 新闻研究技术路线

Last updated / 更新日期：2026-11-14（Asia/Shanghai） · Version / 版本：1.5

**Status / 状态：PROPOSED。** 首次固定开发冒烟保存并回读 9 份响应，1 次超时，Schema 通过 0/9。旧响应已离线归类。完整 Schema 加虚构示例的新提示词复测在第 3 次尝试时因 OOM 停止：2 份响应均符合 Schema，但最终机械检查均未通过；其余 7 篇未尝试。这是重复开发材料的有限格式结果。人工裁定、抽取准确率与金融实验仍 **NOT_RUN**。The first fixed development smoke saved nine responses, had one timeout and passed the Schema on 0/9 responses. Its errors were classified offline. The complete-Schema-plus-invented-example retest stopped at OOM on attempted call 3: both saved responses passed Schema but failed the final mechanical check; seven articles were not attempted. This is a limited format result on reused development material. Human adjudication, extraction accuracy and financial experiments remain **NOT_RUN**.

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
