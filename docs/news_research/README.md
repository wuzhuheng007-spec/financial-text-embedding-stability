# News research roadmap / 新闻研究技术路线

Last updated / 更新日期：2026-11-14（Asia/Shanghai） · Version / 版本：1.4

**Status / 状态：PROPOSED。** 本轮已在独立环境核验官方固定版本 Qwen 3B，并对已有前 10 篇开发材料执行真实有界抽取：9 份响应保存并回读，1 次超时，完整 Schema 通过数为 0。人工裁定、抽取准确率与金融实验仍 **NOT_RUN**。An official pinned Qwen 3B snapshot and isolated CUDA environment were verified. The bounded run on the existing first ten development articles saved and read back nine responses, had one timeout, and yielded zero schema-valid responses. Human adjudication, extraction accuracy and financial experiments remain **NOT_RUN**.

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

回读首次真实开发冒烟的私有响应与失败原因，需要调整时另建版本；独立完成人工裁定后，才在独立样本上评价抽取质量。历史身份、时间与完整金融协议继续单列。Review the private responses and failures from the first real development smoke, version any contract changes, and complete independent human adjudication before evaluating extraction quality on a separate sample. Historical identity, timing and the full financial protocol remain separate.
