# N1 约束解码：兼容性门槛与有界运行决定

日期：2026-11-15，Asia/Shanghai（UTC+08:00）。对象仍是 FNSPID 固定顺序前十篇开发材料。本轮检查能否在生成时执行既有公司事件 wire 契约；独立事件质量和金融表现不属于这道工程门槛。

## 目标与计划—实际

较远的研究目标是建立公司级新闻事件测量，用于未来风险和表示稳定性研究。当前须先确认解码器确实执行原 wire 对象集合，且不改变[原 wire Schema](../docs/news_research/company_event_wire_v2.schema.json)、[canonical Schema](../docs/news_research/company_event_output.schema.json)、[逐字引用适配器](../scripts/exact_quote_adapter.py)、[校验器](../scripts/validate_news_company_event.py)或 [v5 提示词](../docs/news_research/company_event_prompt_v4_targeted.md)。[A–E 台账](../results/news_constrained_decode_20261115/plan_execution.csv)逐项列出计划、实际及证据。

| 阶段 | 计划 | 实际与证据 | 状态 |
| --- | --- | --- | --- |
| A · 绑定基线并复查旧失败 | 核对既有分支和冻结 v5 文件；私人逐条检查错误与资源快照。 | 基线为提交 [`d1663d3`](https://github.com/wuzhuheng007-spec/financial-text-embedding-stability/commit/d1663d365693f6efe9d32e5fbe86298fca50b4cc)。[v5 复查回执](../results/news_constrained_decode_20261115/v5_evidence_recheck.json)绑定原样本、配置、Schema 和保存台账。六份合法 JSON 中有四组、九个 Schema 错误实例，实际原值仅在私人位置核查。三次超时均到 `GENERATION_STARTED`，未保存中间 token 计时。七份生成后 CUDA API 快照均返回可用字节零，但超时原因仍未确定。 | 已复查保存证据；原因未明 |
| B · 无模型编译解码候选 | 将固定 Schema 条件展开为显式分支；有限合成对象逐项比较，并拒绝源哈希变化。 | [编译器回执](../results/news_constrained_decode_20261115/compiler_gate_run1/compiler_validation.json)记录 4,573 个合成判定：原规则与候选规则均接受 1,235、拒绝 3,338；源哈希改变会被拒绝。[候选 Schema](../results/news_constrained_decode_20261115/compiler_gate_run1/decoding_schema_candidate.json)含 12 个 root 和八个 record 分支。这不能证明后端支持或完整等价。 | 有限检查完成 |
| C · 检查单一后端与固定 tokenizer | 锁定 LM Format Enforcer 官方版本及依赖；测试字符级、token 级约束和全部必要分支、限制。 | [0.11.3 锁定文件](../configs/news_n1_lmfe_backend_v1.lock.json)绑定官方 [PyPI 发布](https://pypi.org/project/lm-format-enforcer/0.11.3/)、三份 wheel 哈希及未改动的 GPU 运行栈。显式设置官方数组/空白上限后，[后端回执](../results/news_constrained_decode_20261115/backend_compatibility_receipt.json)在 63 个字符级用例上与原 Schema 一致；固定 Qwen 的实际前缀回调拒绝三条经原规则判为合法的测试序列，其中一条候选 token 集为空。另一个序列化方式能否到达同一对象仍为 **UNKNOWN**，有界探查未完成。 | 已测试路径 **FAIL_TOKEN_PREFIX_COMPATIBILITY** |
| D · 门槛通过后只运行一次 | 全部兼容性通过才对原十篇各尝试一次；预算仍为十次、每次 1,024 个新 token、180 秒，总 1,800 秒。逐阶段审计原始响应→wire→引用→canonical。 | 门槛未通过，没有启动 v6 生成配置或 runner；新增模型调用、响应和金融标签均为 **NOT_RUN**。旧 v5 结果保持冻结。 | **NOT_RUN_BY_GATE** |
| E · 启动人工审阅并交接 | 给已认领的真人提供点选式原文优先入口；发布脱敏双语证据与最小交接包。 | 本机入口是仓库根目录下的 `artifacts/news_targeted_repair_20261115/human_review_original_first10_v1/review_choices.html`。[一页说明](../docs/news_research/human_review_start_20261115.md)和[界面回执](../results/news_constrained_decode_20261115/human_choice_ui_receipt.json)绑定十篇原文页、六道核心选项和两道可选题，无手写栏或预填答案。真人已认领，但保存选择仍为 **0/10**。这只是文章级初筛，不是完整公司事件 gold。GitHub 与交接包核验：**待完成**。 | 审阅者已认领；初筛 0/10 |

## 编译检查的证据边界

附件列出的九个文件在解压后均符合 SHA-256 和字节数清单。集成的[编译器代码](../scripts/news_constrained_decode_v1/compile_wire_v2.py)只接受固定 wire-v2 原文件字节（SHA-256 `afc517b5…`）。它将 root 的四种 disposition 与三种市场结果状态展开，并按 record 的三个条件谓词形成八种分支；候选保留必需字段、有限枚举、数组长度及 `additionalProperties`。候选 SHA-256 为 `f7bc9df682ca365131c0b97592bfa4bc0beaf23ad63a6c90dc1d67007cec4c5f`。4,573 个对象只覆盖有限组合和突变；[37 个后端哨兵](../results/news_constrained_decode_20261115/compiler_gate_run1/backend_sentinels.json)是测试输入，不是新闻或模型输出。

原 Schema 与候选的这轮比较使用 `jsonschema`，尚不能说明 token 过滤库是否真正执行每项 `anyOf`、`$ref`、数组/字符串长度或跨字段限制。最终仍由原 wire 校验器及引用/canonical 检查验收。若后端接受原 wire 拒绝的对象，即为门槛失败；不得修补或改名旧响应以取得通过。

## 后端兼容性决定

[LM Format Enforcer 0.11.3 锁定候选](../configs/news_n1_lmfe_backend_v1.lock.json)来自[官方 PyPI 发布](https://pypi.org/project/lm-format-enforcer/0.11.3/)，装在隔离依赖目录；固定的 `torch`、`transformers`、`tokenizers` 和模型权重未变。[默认限制探查](../results/news_constrained_decode_20261115/backend_probe_limits_default.json)观察到 20 项数组和 12 个连续空白的设置，并记录五种原规则认可的序列化形式被拒。显式调整数组/空白上限并采用固定 tokenizer 字符集后，[兼容性回执](../results/news_constrained_decode_20261115/backend_compatibility_receipt.json)记录字符级 **63/63** 与原 Schema 判定一致，其中原规则接受 29、拒绝 34，无不支持关键字告警。这只是字符级结果。

固定 Qwen tokenizer 的 Transformers 实际前缀回调让一条合法无记录序列的 **74/74** 个 token 通过并允许两个 EOS ID，也在第 **40/182** 个 token 拒绝非法市场方向序列；但它还拒绝三条原规则认可的合成序列：公司事件第 **79/184** 个 token、含中文/转义的序列第 **75/196** 个 token（允许集合为空）、21 条证据的序列第 **363/468** 个 token。五条测试序列的 token 解码均能还原原输入。这些反例证明当前测试的回调路径未过门槛，尚不能证明这些对象在所有等价 JSON 序列化下不可达。带空格/转义的替代序列化探查约 165 秒没有得到结果后停止，未调用模型，因此替代可达性仍为 **UNKNOWN**。原协议在这道门槛内未获可执行证明，故没有启动 v6 模型运行。

一条另立版本的设计候选是先抽文章级 disposition，再抽公司事件与证据，最后组合成完整 wire 对象并交给未改动的原校验器。它会改变抽取流程，必须另行检验对象覆盖、tokenizer 和成本，才能考虑模型调用；目前不是已实现修复或放宽的验收规则。

## 有限解释与下一决定

旧 v5 的九个错误实例依[审计聚合](../results/news_targeted_repair_20261115/inference_aggregate.json)分别为：`market_outcome_direction:const` 四个、`records[].evidence_spans:minItems` 两个、`records[].unknown_reason:enum` 两个、`records:maxItems` 一个。这些是六份合法 JSON 中的实例，不是九篇文章。[保存证据复查](../results/news_constrained_decode_20261115/v5_evidence_recheck.json)确认四个市场方向原值是字符串，但具体标签保留本机；两条证据数组各只有一项、原要求至少两项；两个 unknown reason 为 JSON null；出错的 records 数组有一项而应为空。第 4、7、9 篇超时前最后进入 `GENERATION_STARTED`；含启动及超时开销的尝试时长分别为 181.406、181.516、182.406 秒。生成前记录的 CUDA 可用字节分别为 968,671,232、1,183,363,072 和 1,595,129,856；没有保存中间 token 数或阶段内计时。七份完成响应的 `AFTER_GENERATION` 快照均返回 `free_bytes=0`。稍后本机检查显示空闲 9,373 MiB、五条计算进程记录。这些值不能证明 OOM 或超时原因；新后端也可能增加固定时限内的处理时间。

即使某版约束解码输出符合格式，其含义也限于这批重复使用的开发材料及本条管线，不能推断公司身份、事件语义、影响方向、引用充分性或预测价值。已认领的真人现可使用点选式文章初筛页，保存选择仍为零；它未覆盖逐个公司—事件与引用 gold，后续仍需版本化真人复核。抽取质量指标仍为 **NOT_RUN**。历史证券身份、已接受的市场时间及价格规则、按事件簇隔离的独立留出评价、金融标签、拟合、预测和收益评估分别仍为 **NOT_RUN / 尚未准入**。
