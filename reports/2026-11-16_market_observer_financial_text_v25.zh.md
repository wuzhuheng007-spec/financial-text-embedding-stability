# 财务输入 v2.5：八事件扩展与定额装包

**执行日期：2026 年 11 月 16 日，Asia/Shanghai（UTC+08:00）。** 使用已保存的 NVIDIA、Micron 八个训练期财报事件与 16 份官方原件，公告日期介于 2025-05-28 和 2026-03-18。工作区基线与远端均为 `75133b055f4238eff5af3a4ed16115c8b717a01a`；v2.4 表格语义内核保持冻结。本轮没有下载、读取 2026 年四至七月正文或收益标签、调用模型、拟合模型、评价收益、改动个人版事实卡。此前已看过验证期结果，因此这是已接触训练材料上的工程开发检查，不是独立测试。

## 先回答本轮四个问题

1. **实际处理八个事件。** 16/16 份原件完成解析。源文档中发现 154 张表格：78 张由 v2.4 内核处理，68 张列为布局或嵌套表，8 张结构不支持；逐表原因保存在私人解析清单，脱敏计数见[一次冻结解析回执](../results/market_observer_financial_text_v25_20261116/parse_frozen_v1_redacted.json)。
2. **60 条原来源支持候选中，解析来源完整 15 条、最终装包来源完整 15 条。** 16 条表格候选达到机械完整且进入最终文本；同团队[逐项来源审阅](../results/market_observer_financial_text_v25_20261116/source_audit_frozen_v1_redacted.json)发现其中 15 条完整、1 条脚注错误。[最终评价](../results/market_observer_financial_text_v25_20261116/evaluation_frozen_v1_redacted.json)将余下 55 条候选标为未完成来源关系审阅，不能把 15/60 当作总体抽取准确率。另有 16 条来源支持候选关系未知、20 条属于独立段落分支、8 条因原定位与新数值格无法唯一对应；这些分母来自[回归审计](../results/market_observer_financial_text_v25_20261116/postrun_regression_audit_redacted.json)与冻结的[71 条候选参考](../results/market_observer_financial_text_v25_20261116/reference_freeze_redacted.json)。
3. **损失来源分开。** 已支持的 16 条关系未知是语义绑定缺口，其中 8 条仍可见、8 条因预算未装入。20 条来源支持的段落候选已登记，却全部在装包预算外；它们不能因段落存在就算作表格完整事实。8 条未唯一定位属于匹配或解析不确定，不能直接判作原文丢失。旧版连续片段完整的 34 条中，22 条在新版最终文本可见、4 条明确因预算遗漏、8 条未能唯一定位；旧片段指标与 v2.5 结构指标不可直接等同比较。[回归审计](../results/market_observer_financial_text_v25_20261116/postrun_regression_audit_redacted.json)。
4. **存在错误的机械完整声明，输入不晋升。** 同团队来源回查发现较早 NVIDIA 财报中的每股数据脚注适用于一条 EPS 候选，冻结内核却把脚注标为 `NOT_APPLICABLE`。[来源审阅回执](../results/market_observer_financial_text_v25_20261116/source_audit_frozen_v1_redacted.json)记录了被审的 16 条候选、15 条完整与 1 条错误，另有 84 条显式来源路径核对（含重复路径）；这不是 84 个独立事实。这是新范围暴露的语义错误，不改变 v2.4 四表既有结论，也不能在本轮按结果改内核。八个最终文本均保留 `REJECTED_CANDIDATE`，不是已核实事实或可投入模型的正式输入。评价文件对错误行的 `pack_status` 仍写“引用可见、待语义核对”，必须与同一行的 `source_binding_status=ERROR` 合看，不能将其解释为待定或通过。[最终评价](../results/market_observer_financial_text_v25_20261116/evaluation_frozen_v1_redacted.json)。

## 三层证据与定额结果

冻结的[输入、配置和代码哈希](../results/market_observer_financial_text_v25_20261116/preflight_frozen_v1_redacted.json)绑定八事件、16 份原件、71 条候选、27 个依赖文件及每事件 20,000 Unicode 字符预算；配置在北京时间 08:39 冻结。生产选表依据 HTML 网格，不读取候选答案或参考路径。完整关系组先于缺失字段组入选，之后沿固定主题、附件优先和文档顺序；未知字段明确写 `UNKNOWN`，冲突组不装入。段落保持单独清单，未沿用 v2.3 错误关系兜底。[配置](../configs/market_observer_financial_text_v25_20261116.json)与[适配器](../scripts/market_observer_financial_text_v25/adapter.py)给出规则。

| 事件公告日 | 证券 | 最终文本字符 | 表格证据组 | 独立段落片段 |
| --- | --- | ---: | ---: | ---: |
| 2025-05-28 | NVDA | 19,722 | 17 | 0 |
| 2025-06-25 | MU | 19,959 | 13 | 2 |
| 2025-08-27 | NVDA | 19,715 | 17 | 0 |
| 2025-09-23 | MU | 19,677 | 13 | 1 |
| 2025-11-19 | NVDA | 19,715 | 17 | 0 |
| 2025-12-17 | MU | 19,973 | 13 | 2 |
| 2026-02-25 | NVDA | 19,713 | 17 | 0 |
| 2026-03-18 | MU | 19,641 | 13 | 1 |

这些字符数按最终可见字符串的 Python `len()` 计算，字段名、编号、引用、分隔符和状态均计入；每条长度和文本哈希见[一次冻结装包回执](../results/market_observer_financial_text_v25_20261116/pack_frozen_v1_redacted.json)。八包合计选择 120 个表格证据组和 6 个未结构化段落片段；对 298 个实际可见来源片段的原件回查和 8/8 包的引用闭合均通过。[回归审计](../results/market_observer_financial_text_v25_20261116/postrun_regression_audit_redacted.json)。这些机械检查不证明语义正确；单元格原值、全文与逐条预测均未公开。

v2.4 四表小范围来源回归在新解析路径下保持：138/138 个预定数值格的已知字段完整，90/138 个全部字段完整；NVIDIA 六个旧错误见证 6/6 解析并 6/6 装入。Micron 四表回归中的一张表仍为 48/48 已知字段完整、0/48 全字段完整；其中 4 格选入、32 格因预算省略、12 格非目标主题。其未知币种及未解脚注没有被擅自补全。四表结果不能代表新发现的其余 74 张已处理表格。[原 v2.4 报告](2026-11-16_market_observer_financial_text_v24.zh.md)与[本轮回归审计](../results/market_observer_financial_text_v25_20261116/postrun_regression_audit_redacted.json)。

## 计划—实际

| 阶段 | 计划 | 实际与证据 | 状态 |
| --- | --- | --- | --- |
| P0 | 核对基线、16 份原件与冻结哈希 | 基线一致，参考与 27 项依赖冻结；见[参考](../results/market_observer_financial_text_v25_20261116/reference_freeze_redacted.json)、[预检](../results/market_observer_financial_text_v25_20261116/preflight_frozen_v1_redacted.json) | 完成 |
| P1 | 独立全量接口与排除记录 | 未改 v2.4 内核；154 张表各有状态，段落另列；见[解析回执](../results/market_observer_financial_text_v25_20261116/parse_frozen_v1_redacted.json) | 完成 |
| P2 | 动态评价与反例测试 | 四表分母由参考生成，脚注分状态，额外错误路径会被拒绝；24 项合成测试通过，不能当作来源审阅 | 完成 |
| P3 | 一次八事件解析与来源复核 | 16/16 原件解析；16 条机械完整的来源支持候选全数审阅，15 完整、1 脚注错误；其他输出未审阅 | 已执行，语义门槛失败 |
| P4 | 一次 20,000 字符装包 | 8/8 诊断包未超预算、引用闭合；均为 `REJECTED_CANDIDATE`；见[装包回执](../results/market_observer_financial_text_v25_20261116/pack_frozen_v1_redacted.json) | 诊断完成，未晋升 |
| P5 | 三层比较及旧见证回退 | 四表及六见证复现；71/60/11 和旧 34 分开登记；来源审阅覆盖 16 条机械完整候选，其余 55 条候选未完成语义关系审阅；见[最终评价](../results/market_observer_financial_text_v25_20261116/evaluation_frozen_v1_redacted.json) | 已完成有界比较；全量语义未审阅 |
| P6 | 错误完整、错误关系或悬空引用时停止晋升 | 无悬空引用，但已发现每股数据脚注错误；输入停止晋升 | 触发停止条件 |
| P7 | 中英报告、台账、同步与脱敏交接 | 研究提交 `2f40ac3` 已推送，远端 SHA 与五个关键文件回读相同；脱敏 ZIP 已在本机按成员哈希、CRC 与中文编码核验，见[同步回执](../results/market_observer_financial_text_v25_20261116/git_sync_initial_redacted.json) | 完成；最终元数据提交另行回读 |

本轮软件检查 24 项合成测试、真实解析 16 份原件、装包 8 个事件和同团队来源审阅 16 条候选分别计数；分母不同，不能相加成独立实验数。其余 55 条候选没有完成新增关系的来源审阅，其中包括原有 11 条歧义候选。模型拟合、推理及收益评价各为 **0**。下一步应独立版本修复脚注作用范围，并解决段落事实在固定预算下全部被排除的问题；先做来源回归，再决定是否尝试固定小批量抽取，不得把已看过的训练材料当新测试集。
