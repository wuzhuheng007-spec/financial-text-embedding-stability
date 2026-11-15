# News research — current scope / 新闻研究当前方向

Updated 2026-11-15 (Asia/Shanghai), watchlist v2 / status 1.20.

**Current priority / 当前优先：重大新闻 + 宏观指标 + 科技公司财报及传导。**

- [Watchlist v2 and bounded quote-push English report](../../reports/2026-11-15_market_observer_watchlist_live.en.md) · [中文报告](../../reports/2026-11-15_market_observer_watchlist_live.zh.md) · [逐项计划—实际](../../results/watchlist_live_20261115/plan_execution.csv) · [证券映射回执](../../results/watchlist_live_20261115/security_mapping_redacted.json) · [有界连接回执](../../results/watchlist_live_20261115/live_probe_redacted.json). The pool has 20 display entities and 19 requested US securities. Nine new current security codes were checked; SpaceX `SPCX` and SK hynix ADR `SKHY` have separate issuer crosschecks. An earlier broad WebAPI grant was rejected; a later `quote:read` grant enabled two acknowledged one-key TQQQ subscriptions during a closed US market. One old-timestamp quote was stored, the quota request returned HTTP 404, and zero subscriptions remain active. The saved-data page was checked at desktop and 390px; market-open freshness, ongoing collection and financial results are unverified. 休市单标的有界连接已通过，长期采集和正式金融评价未运行。

- [Read-only market observer v1 / 只读市场观察](market_observer/README.md) · [English report](../../reports/2026-11-15_market_observer_v1.en.md) · [中文报告](../../reports/2026-11-15_market_observer_v1.zh.md) · [plan–actual](../../results/market_observer_v1_20261115/plan_execution.csv). That earlier baseline separated scoped moomoo MCP discovery and a bounded quote check from the private `NO_FEED` service and saved Jin10 replay. The local page was opened at desktop and 390px widths. Standalone push was then untested; account quota, real pre-window decisions and outcomes remain unverified. 旧版完成本机只读存储、页面和评价契约；上方 v2 回执更新了休市连接状态，正式评价仍未运行。

- [Shortfacts v2 bilingual report / v2 中英报告](../../reports/2026-11-15_news_shortfacts_v2.en.md) · [中文](../../reports/2026-11-15_news_shortfacts_v2.zh.md) · [plan–actual / 计划—实际](../../results/news_shortfacts_v2_20261115/plan_execution.csv) · [contract receipt / 契约回执](../../results/news_shortfacts_v2_20261115/contract_receipt.json) · [retrospective v1 score / 旧输出修正规则重评](../../results/news_shortfacts_v2_20261115/retrospective_summary_redacted.json) · [new-run score / 新运行评分](../../results/news_shortfacts_v2_20261115/new_run_summary_redacted.json). The v2 contract permits an unknown entity, treats the measured index rather than the publisher as CPI subject, and keeps guidance state out of accounting basis. The unchanged six passages received six new one-call responses with the same pinned model and budget: strict raw JSON was 6/6; only 1/14 corrected-reference facts was fully correct. Original v1 scoring remains historical, while both retrospective and new-run scores use the corrected `CODEX_REFERENCE_DRAFT`, not human gold. v2 同时修改契约和提示词，是工程组合修正；不据此主张一般抽取能力或金融有效性。

- [First real short-evidence Qwen run / 首次真实短证据 Qwen 试验](../../reports/2026-11-15_news_shortfacts_qwen.en.md) · [中文报告](../../reports/2026-11-15_news_shortfacts_qwen.zh.md) · [plan–actual / 计划—实际](../../results/news_shortfacts_20261115/plan_execution.csv) · [redacted run receipt / 脱敏运行回执](../../results/news_shortfacts_20261115/run_receipt_redacted.json) · [separate validation gates / 分层验证](../../results/news_shortfacts_20261115/public_validation_summary_v2.json). Six frozen short passages produced six real local responses. Strict raw JSON passed for one; five fenced responses were saved and parsed only as separate views. Six quotes matched mechanically, while content review found field errors and omissions. Its dated next step was strict raw JSON formatting; the v2 record above supersedes that plan. 六项开发调用已完成，但不能写成抽取质量或金融有效性通过。

- [Three event-impact development cards / 三张事件影响分析样板](../../reports/2026-11-15_event_impact_cards.en.md) · [中文报告](../../reports/2026-11-15_event_impact_cards.zh.md) · [selection manifest / 选择清单](../../results/news_event_impact_20261115/selection_manifest.json) · [plan–actual / 计划—实际](../../results/news_event_impact_20261115/plan_execution.csv) · [public page / 公开页面](../../results/news_event_impact_20261115/dashboard_public_v3.html). Saved official NVIDIA, CoreWeave and BLS materials support three conditional `AI_ANALYST_DRAFT` cards. Their facts, hypotheses, unknowns and timing are separate; the saved-data private page passed desktop and 390px visual checks. These are analysis templates **awaiting evaluation**, with no new model call, financial label or backtest. 三张有来源的开发卡已制作并完成本机页面验收，内容质量和金融有效性均未验证。

- [Jin10 desktop acceptance English report](../../reports/2026-11-15_jin10_desktop_acceptance.en.md) · [中文报告](../../reports/2026-11-15_jin10_desktop_acceptance.zh.md) · [本轮计划—实际](../../results/news_jin10_desktop_20261115/plan_execution.csv) · [原生桌面调用回执](../../results/news_jin10_desktop_20261115/native_calendar_receipt.json) · [脱敏视觉回执](../../results/news_jin10_desktop_20261115/visual_acceptance_redacted.json). The current desktop session loaded Jin10 and made one native calendar call; a saved private page was opened at desktop and 390px width, with no provider download for page QA. 桌面工具与本机页面验收完成；旧新进程探测和本轮桌面调用分别记录。
- [Jin10 MCP bounded-intake English report](../../reports/2026-11-15_jin10_mcp_intake.en.md) · [中文进度](../../reports/2026-11-15_jin10_mcp_intake.zh.md) · [逐项计划—实际](../../results/news_jin10_20261115/plan_execution.csv) · [source-rights boundary / 来源权限](jin10/source_rights.md)
- Jin10 MCP configuration has been written using `JIN10_MCP_TOKEN`. The earlier [configuration baseline](../../results/news_jin10_20261115/configuration_baseline.json) lacked a credential; the later [bounded live receipt](../../results/news_jin10_20261115/probe_redacted_run2.json) verifies fresh-process authentication, one calendar call and two flash pages that both still have more. The captured pages were [imported offline into private SQLite](../../results/news_jin10_20261115/store_validation_v2.json), with no new provider call; the [v2 display template](../../results/news_jin10_20261115/dashboard_v2_template.html) is a development view. 金十新进程有界调用与私人离线导入已完成，但快讯分页未完。
- [Guidance timing v2](../../results/news_jin10_20261115/guidance_v2/README.md) keeps 19 guidance rows separate from the earlier 72 core metric checks; independent first availability remains unknown. 指引时间已另版修复，不更改旧冻结财报卡或正式金融准入状态。
- [财报扩展中文路线](earnings/roadmap.zh.md) · [English earnings extension](earnings/roadmap.en.md)
- [本轮英文报告 / English intake report](../../reports/2026-11-15_earnings_macro_intake.en.md) · [中文简报](../../reports/2026-11-15_earnings_macro_intake.zh.md) · [执行台账](../../results/news_earnings_macro_20261115/plan_execution.csv)
- 核心公司：NVIDIA、AMD、Intel、CoreWeave、Samsung Electronics、SK hynix。已形成六张有来源财报开发卡、三张宏观卡和六条独立公司新闻；正式金融准入与收益实验仍为 `NOT_RUN`。旧兴趣页不是前置条件。
- Six source-bound issuer development cards, three macro release cards and six separate issuer news inputs are available. This is descriptive intake, with no formal financial admission or market-return evaluation. The old preference form is not a prerequisite.

以下为此前宏观候选阶段记录，保留其证据与状态；旧“先点选”的顺序由上述任务替代。The historical macro stage below is preserved; its preference-first sequencing is superseded.

**Previous macro-stage priority: macro/systemic events and transmission to the broad market and technology firms. 当前优先：宏观/系统性事件对大盘和科技股的传导。** The user has clarified the scope. The prior first-ten corpus is legacy engineering material; its manual screening and LMFE debugging are paused as the main task. Old failures remain recorded. 用户无需继续筛旧十篇；本轮已收集真实候选，下一步做人审与分层抽取准备。

- [中文新路线](macro_tech/roadmap.zh.md) · [English roadmap](macro_tech/roadmap.en.md)
- [Executed Codex task / 本轮执行任务](macro_tech/01_CODEX_TASK.txt) · [Sources / 来源边界](macro_tech/SOURCE_REGISTER.md)
- [Current machine status / 当前状态](STATUS.json) · [Macro progress report / 宏观进度报告](../../reports/2026-11-15_macro_tech_candidates.en.md) · [中文报告](../../reports/2026-11-15_macro_tech_candidates.zh.md)

The bounded first deliverable is now complete: [12 source-backed development candidates](macro_tech/candidates.en.md) in six categories, with [24 fact-to-source rows](../../results/news_macro_tech_20261115/source_evidence_index.csv). A [private click-only preference page](macro_tech/preference_review_start.md) and two conditional `AI_ANALYST_DRAFT` paths are prepared. The page starts with zero saved choices; human fact gold remains zero; new macro model inference and financial experiments are **NOT_RUN**. Pre-release consensus, exact first-public instants, historical security identity and full financial protocol acceptance remain unresolved. 已完成 12 条开发候选和 24 条来源定位事实，不等于正式事件面板或金融有效性结果。

## Historical engineering record / 历史工程记录（以下旧“下一步”已由上述新任务替代）

# News research roadmap / 新闻研究技术路线

Last updated / 更新日期：2026-11-15（Asia/Shanghai） · Version / 版本：1.8

**Status / 状态：PROPOSED。** 对 v4 保存的九份响应完成离线逐字段诊断：七份 wire 不合法响应中的 14 个错误实例落在五组字段；唯一 wire 合法响应仍因一条引用缺失而无法构造 canonical。另立一个定向提示词及 v5 配置后，对固定十篇开发材料进行一次有界运行，独立审计通过：十篇均尝试并开始生成，保存七份响应，三次超时；七份中六份 JSON 合法，**wire 合法 0/6，完整公司事件输出 0**。这不是抽取质量或金融有效性结论。Offline field-level diagnosis of saved v4 responses found 14 error instances across five field groups in the seven wire-invalid responses; the sole wire-valid response still lacked one quote and could not be canonically constructed. A single targeted prompt and separate v5 config were then used for one bounded run on the same ten development articles. The independent audit passed: all ten were attempted and generation-started, seven responses were saved, three calls timed out, six of seven responses were JSON-valid, **zero of six were wire-valid and no complete company-event output was obtained**. These engineering observations do not establish extraction quality or financial validity. Independent human adjudication and financial experiments remain **NOT_RUN**.

**Current constrained-decoding gate / 当前约束解码门槛：** 固定 wire-v2 候选编译器在 4,573 个有限合成对象上与原 Schema 判定一致；LM Format Enforcer 0.11.3 在显式设置下的 63 个字符级测试也一致，但固定 Qwen tokenizer 的实际前缀回调拒绝三条原规则认可的测试序列。因此门槛为 **FAIL_TOKEN_PREFIX_COMPATIBILITY**，**未启动新模型运行**；替代序列化可达性仍未知。The pinned candidate matches 4,573 finite synthetic decisions, and the configured backend matches 63 character-level cases. The actual pinned-Qwen prefix callback disallowed three tested source-valid sequences, so the gate failed and **no new model run began**. Alternative serialization reachability is unknown. No extraction-quality or financial claim follows.

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
| [Targeted repair English report](../../reports/2026-11-15_news_targeted_repair.en.md) · [中文报告](../../reports/2026-11-15_news_targeted_repair.zh.md) | v4 失败字段诊断、唯一提示词候选及 v5 有界复测状态 |
| [A–E targeted ledger](../../results/news_targeted_repair_20261115/plan_execution.csv) · [offline diagnostic](../../results/news_targeted_repair_20261115/wire_failure_diagnostic.json) · [reconciliation](../../results/news_targeted_repair_20261115/wire_failure_reconciliation.json) | 逐项计划—实际、14 个错误实例的字段组及旧聚合对账；原值留本机 |
| [Native interruption recheck](../../results/news_targeted_repair_20261115/native_interruption_recheck.json) | 旧第 2 次调用无 stderr/退出码/响应，原生错误关系仍 UNKNOWN |
| [Targeted prompt](company_event_prompt_v4_targeted.md) · [v5 config](../../configs/news_n1_smoke_v5_targeted.json) · [targeting receipt](../../results/news_targeted_repair_20261115/prompt_targeting_receipt.json) | 一个针对性候选，保留固定模型、输入、Schema、校验器及生成预算；停止规则另版记录 |
| [V5 pre-run gate](../../results/news_targeted_repair_20261115/prerun_gate_receipt.json) · [synthetic suite](../../results/news_targeted_repair_20261115/pre_run_validation.log) | 十篇全文上下文适配，合并 37 项合成测试通过；尚非模型结果 |
| [Audited v5 run](../../results/news_targeted_repair_20261115/inference_aggregate.json) · [v4–v5 paired comparison](../../results/news_targeted_repair_20261115/paired_comparison.json) | 十篇均发起并开始生成；七份响应、三次超时，wire 合法 0/6；六个位置两版均有响应 |
| [Blank human review preparation](../../results/news_targeted_repair_20261115/human_review_preparation.json) | 十篇原文优先入口；尚无人工裁定或抽取质量结果 |
| [Torchless handoff check](../../results/news_targeted_repair_20261115/torchless_handoff_validation.log) | Python 3.11.9 无 `torch` 环境下五份聚焦合成测试 27/27 通过；与先前工程测试重叠，不是模型试验 |
| [Constrained-decoding English report](../../reports/2026-11-15_news_constrained_decode.en.md) · [中文报告](../../reports/2026-11-15_news_constrained_decode.zh.md) | 固定原协议的解码兼容性门槛、有限编译检查、模型运行条件及证据边界 |
| [A–E decode ledger](../../results/news_constrained_decode_20261115/plan_execution.csv) · [compiler receipt](../../results/news_constrained_decode_20261115/compiler_gate_run1/compiler_validation.json) · [v5 evidence recheck](../../results/news_constrained_decode_20261115/v5_evidence_recheck.json) | 逐项计划—实际、4,573 个有限合成判定与旧失败/资源证据 |
| [Pinned LMFE lock](../../configs/news_n1_lmfe_backend_v1.lock.json) · [backend compatibility receipt](../../results/news_constrained_decode_20261115/backend_compatibility_receipt.json) | 固定版本、字符级和 token 级门槛反例；无新模型调用 |
| [Human review start / 人工审阅说明](human_review_start_20261115.md) · [choice UI receipt](../../results/news_constrained_decode_20261115/human_choice_ui_receipt.json) | 十篇私人原文的点选式文章初筛，保存选择 0/10；不是完整公司事件 gold 或模型评估结果 |

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

金十开发分支已完成一次原生桌面日历调用及保存页面的浏览器视觉验收；桌面宏观表长字段名仍略拥挤。确认配额及保存/展示条款前不启动持续采集。研究准入下一步仍需有日期依据的来源／公布前共识快照、历史公司—证券身份链和完整金融协议。The Jin10 desktop call and saved-page browser acceptance are complete, with a desktop macro-table readability note. Recurring collection awaits confirmed quota and storage/display terms. Formal research admission still requires point-in-time source and prerelease consensus evidence, dated issuer-to-security identity, and the complete financial protocol. A bounded company-event extraction quality study may use the cards as development input; it must preserve the earlier model-failure record.
