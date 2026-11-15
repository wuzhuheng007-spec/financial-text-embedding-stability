# 财务证据输入 v2.3：结构关系核对

**执行日期：2026 年 11 月 16 日，Asia/Shanghai。同步状态以报告冻结时点为准。** 本轮仅使用已保存的训练期 NVIDIA 与 Micron 官方材料，公告日期范围为 2025-04-01 至 2026-03-31。旧版、v2.1、v2.2 结果保持不变；[v2.2 报告](2026-11-16_market_observer_financial_text_v22.zh.md)记录此前的负面结果。[v2.3 配置](../configs/market_observer_financial_text_v23_20261116.json)规定每事件 20,000 个 Unicode 字符及一次冻结后的完整构建。本轮规则设计者此前已接触验证期结果，因此这是训练材料上的内容工程检查。

## 规则与运行证据

- 规则冻结：`frozen_r1` 的进程启动后，在写出解析文档、事件输入或构建汇总前因未链接脚注标记的自审风险而终止；它没有评分结果。[中止回执](../results/market_observer_financial_text_v23_20261116/full_build_aborted_frozen_r1_redacted.json)与[旧配置副本](../results/market_observer_financial_text_v23_20261116/config_frozen_r1_aborted_redacted.json)均保留。修正后 `frozen_r2` 于 2026-11-16 07:02:09 Asia/Shanghai 冻结；[预检](../results/market_observer_financial_text_v23_20261116/preflight_frozen_r2_redacted.json)记录 16 份来源和 67 个代码、配置及参考依赖哈希。首次完整构建使用这一冻结版。
- 来源身份：[预检](../results/market_observer_financial_text_v23_20261116/preflight_frozen_r2_redacted.json)记录保存清单和原件哈希。[NVIDIA 2025-05-28 财报附件](https://www.sec.gov/Archives/edgar/data/1045810/000104581025000115/q1fy26pr.htm)与[Micron 2025-06-25 财报附件](https://www.sec.gov/Archives/edgar/data/723125/000072312525000019/a2025q3ex991-pressrelease.htm)只用于 SEC 发布者及文档身份抽查；网页核对不能证明本机保存字节与逐条财务语义均正确。
- 结构调试：按日期选取两家公司各最早一份已保存财报附件。[调试回执](../results/market_observer_financial_text_v23_20261116/structure_debug_r3_redacted.json)记录表格网格、跨行跨列、重复表头、章节财期、段落数值和脚注的冻结前修正。冻结前为 **29 个解析/对抗 + 10 个评估器 = 39 个不同的合成测试方法通过**；评估器后续[窄修正回执](../results/market_observer_financial_text_v23_20261116/evaluator_failure_correction_preflight_frozen_r2_redacted.json)另加两项，当前聚焦套件为 **29 + 12 = 41 项**。前后版本重叠，不累加成 80 项，也不是独立实证实验。其余训练期原件是回归检查，不称未见测试集。
- 完整构建：[构建回执](../results/market_observer_financial_text_v23_20261116/build_frozen_r2_redacted.json)显示一次完成的 `frozen_r2` 构建处理 **16 份文档、8 个事件**，产生 **1,305 组解析候选**，选入 **152 组**、省略 **1,153 组**。省略原因分列为关系未解 **486**、关系冲突 **623**、非主题数值 **20**、完整组超预算 **24**。八个实际序列化输入各为 **10,617–19,605 字符**，均不超过 20,000；**4,561 个**来源 DOM 定位检查通过，构建校验未检出序列化来源片段丢失。它们只是工程定位与装入结果；P6 同团队来源复核发现错误绑定，完整确认仍为零。

## 模型可见输入与核对口径

本轮分别核对：① 片段是否在模型输入；② 公共上下文引用是否可达；③ 数值单元格与行列、期间、单位、会计口径是否精确对应；④ 来源财务语义是否经同一团队回查。前三项通过不能自动升级成第四项。旧版若没有结构关系记录，在新结构指标中标为**不可评估**，不能计作错误绑定。来源会计口径未知时，分开报告已知字段完整与全部字段完整。

| 固定分母与指标 | 旧版 | v2.1 | v2.2 | v2.3 |
| --- | ---: | ---: | ---: | ---: |
| 71 条全部候选：原片段级严格完整 | 34 | 18 | 8 | 0 |
| 60 条来源支持候选：原片段级严格完整 | 34 | 18 | 8 | 0 |
| 60 条来源支持候选：机械可达且精确单元格关系 | 不可评估 | 不可评估 | 不可评估 | 6；来源复核后完整 0 |
| 60 条来源支持候选：已知字段/全部字段完整 | 不可评估 | 不可评估 | 不可评估 | 0 / 0 |

表内旧版、v2.1、v2.2 数字沿用冻结旧评分；v2.3 在实际序列化文本上调用同一段连续片段规则，得到严格完整 **0/71**。[评估回执](../results/market_observer_financial_text_v23_20261116/evaluation_frozen_r2_evalfix1_redacted.json)说明 v2.3 采用拆分的编号引用形式，旧规则的零分**不等于所有数值证据消失**；其中六条来源支持候选的精确数值单元格机械可达，但后续来源关系复核均不完整。[后运行解读](../results/market_observer_financial_text_v23_20261116/postrun_interpretation_frozen_r2_evalfix1_redacted.json)记录逐项状态。分母须分开：71 条候选、60 条同团队判定有来源支持、11 条歧义、1,305 个解析证据组、16 份原件、8 个事件及 483 个字段槽位，不能相加。[配置](../configs/market_observer_financial_text_v23_20261116.json)、[预检](../results/market_observer_financial_text_v23_20261116/preflight_frozen_r2_redacted.json)和上述回执绑定输入及评估版本；详细哈希留在回执。`CODEX_REVIEW` 不称独立人工认证。

## 关键问题与负面结果

1. **期间和单位是否绑定正确数值列：** 没有通过来源核对的完整例子。六条机械可达的 NVDA 记录中，数值单元格和行指标定位正确，但 **12/12 个列头链接均指向同列的其他数值数据行**；真正的期间/单位表头被解析为数据。六条均未把来源表格中的期间、单位及 GAAP 口径完整送入模型。见[结构根因回执](../results/market_observer_financial_text_v23_20261116/postrun_false_header_diagnosis_redacted.json)。
2. **是否仍错误继承表头或会计口径：** 六条均错误连接了“伪列头”；章节的 billions 单位被带入，而表格声明的是 millions。GAAP 表头在模型输入中缺失，不能把可达的错误上下文当作已确认口径。合成反例对某些跨子表冲突会拒绝绑定，但真实材料仍揭示行分类错误。这是六条、三份来源文档的同团队只读复核，不能外推全部材料。
3. **解析完整但模型输入不完整：** 在 60 条来源支持候选中，42 条能定位唯一数值单元格却未选入模型输入，分别因关系冲突 16 条、关系未解 26 条；另有 8 条数值单元格匹配歧义、4 条段落无表格网格关系。六条选入但语义上下文错误。[构建回执](../results/market_observer_financial_text_v23_20261116/build_frozen_r2_redacted.json)中的 **24 组**完整组预算省略以证据组为分母，不能当作 24 条候选事实；此次 42 条未选候选的已查原因均非预算。
4. **相对旧版的回退：** 原来片段级完整的 34 条在 v2.3 同一字面检查中全不完整：[回退解读](../results/market_observer_financial_text_v23_20261116/postrun_interpretation_frozen_r2_evalfix1_redacted.json)分为关系冲突而省略 2、关系未解而省略 18、单元格匹配歧义 8、单元格选入但拆分格式及语义关系错误 6。这是严格字面指标的回退，同时存在真实上下文错误；不能把 0/71 单独解释为所有财务信息消失。
5. **只能作为候选的内容：** 六条机械可达记录均经同团队来源复核否决，完整确认数 **0/6**；其余关系未解、冲突、匹配歧义及无网格段落继续保持候选或不可评估。下一版只优先修一处：先用严格的期间/单位语义表头识别，再判定主题与数值数据行；不得仅凭货币符号把数值行提升为表头。此为[后运行根因](../results/market_observer_financial_text_v23_20261116/postrun_false_header_diagnosis_redacted.json)提出的**未运行候选修订**，不在本轮按结果修改冻结版。

## 计划—实际

| 阶段 | 计划 | 实际与证据 | 状态、偏差与下一步 |
| --- | --- | --- | --- |
| P0 | 冻结 16 份既有训练期原件、规则与哈希；保留旧结果 | `frozen_r1` 在产出前终止；[中止回执](../results/market_observer_financial_text_v23_20261116/full_build_aborted_frozen_r1_redacted.json)保留。`frozen_r2` [预检](../results/market_observer_financial_text_v23_20261116/preflight_frozen_r2_redacted.json)绑定 16 份来源和 67 个依赖哈希。 | 完成；两次尝试只一次完成构建，旧版不覆盖。 |
| P1 | 对齐构建器与评估器的四层定义，旧输出如需重评则版本化 | [定义审查](../results/market_observer_financial_text_v23_20261116/evaluator_contract_review_redacted.json)确认 v2.2 的可见公共引用允许，未发现足以改写旧片段分数的定义缺陷；旧版缺单元格结构时新指标为不可评估。 | 定义已冻结；没有改写旧评分。 |
| P2 | 恢复 HTML 表格坐标、表头、单位、期间与口径范围 | [调试回执](../results/market_observer_financial_text_v23_20261116/structure_debug_r3_redacted.json)记录跨行跨列、嵌套表格、重复表头和冲突拒绝；[构建](../results/market_observer_financial_text_v23_20261116/build_frozen_r2_redacted.json)产生 1,305 个候选组。 | 工程构建完成；大量关系冲突/未解仍保留候选。 |
| P3 | 绑定章节财期与显式脚注，保留冲突和未知 | [调试](../results/market_observer_financial_text_v23_20261116/structure_debug_r3_redacted.json)记录章节声明与未链接脚注的保守处理；`frozen_r1` 因脚注风险[中止](../results/market_observer_financial_text_v23_20261116/full_build_aborted_frozen_r1_redacted.json)，修订后才运行完整材料。 | 规则实现并冻结；来源语义未因软件通过而升级。 |
| P4 | 按固定日期顺序调试两份附件，合成反例通过后冻结 | [两份附件调试](../results/market_observer_financial_text_v23_20261116/structure_debug_r3_redacted.json)和[评估定义审查](../results/market_observer_financial_text_v23_20261116/evaluator_contract_review_redacted.json)分别记录 29 与 10 项冻结前测试；[窄修正](../results/market_observer_financial_text_v23_20261116/evaluator_failure_correction_preflight_frozen_r2_redacted.json)后评估器为 12 项，当前聚焦测试方法合计 41 项。 | 合成测试与真实来源复核分列；旧版本重叠不累加。 |
| P5 | 一次完整构建每事件 20,000 字符输入，核对可见引用及省略 | [构建回执](../results/market_observer_financial_text_v23_20261116/build_frozen_r2_redacted.json)：16 份文档、8 个事件，152/1,305 组选入，1,153 组省略，4,561 次来源定位检查，所有输入 ≤20,000 字符。 | 一次完整构建完成；选入不等于事实确认。 |
| P6 | 分开报告旧片段指标与新结构指标，逐条审阅新版完整事实 | 首次评估因八份主申报未选入可见数值而在输出前失败；[窄修正回执](../results/market_observer_financial_text_v23_20261116/evaluator_failure_correction_preflight_frozen_r2_redacted.json)记录事件映射修复，未改解析或输入。随后[同口径比较](../results/market_observer_financial_text_v23_20261116/evaluation_frozen_r2_evalfix1_redacted.json)完成；[同团队复核](../results/market_observer_financial_text_v23_20261116/postrun_interpretation_frozen_r2_evalfix1_redacted.json)和[结构根因](../results/market_observer_financial_text_v23_20261116/postrun_false_header_diagnosis_redacted.json)否决六条机械可达候选。 | 完成负面结果；完整确认 0/6，不晋升事实卡或模型。 |
| P7 | 更新双语文档、状态与台账，选择性 Git 推送、回读及脱敏交接 | 中英报告、入口、状态和台账已准备；报告冻结时尚未执行 Git 推送、远端回读或最终 ZIP 核验。 | 文档准备完成，同步待核验；以后续追加回执为准。 |

## 限制与下一步

本轮回执记录**新增下载 0、模型拟合 0、模型推理 0**；未读取 2026 年四至七月原文或标签，未做新收益评价、回测或交易，个人应用及其数据库未改动。仅凭片段、DOM 定位或机械可达不能确认财务事实。正式 M5 仍为 `NOT_RUN`；规则制定者此前见过验证期结果，且六条来源复核由同一执行团队完成，不构成独立认证。下一轮只提议先修表头语义分类，另版冻结后再作训练期回归；v2.3 的负面结果永久保留。报告冻结时 Git 推送和脱敏交接 ZIP 尚待执行与核验。
