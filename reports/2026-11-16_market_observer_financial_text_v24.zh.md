# 财务表格语义内核 v2.4：四表来源回归

**执行日期：2026 年 11 月 16 日，Asia/Shanghai（UTC+08:00）。** 本轮只修复表头、期间、单位与会计口径的结构绑定。使用四份已保存的训练期 NVIDIA/Micron 官方财报附件；未下载新语料、读取 2026 年四至七月正文或收益标签，也未运行模型或修改个人版。研究者此前接触过验证期结果，这仍是开发回归，不是独立测试。

## 结论与口径

六个 v2.3 已知 NVIDIA 错误见证在冻结的 v2.4 回归中 **6/6 完整绑定**；v2.3 同六项的来源完整数为 **0/6**，且此前 12/12 个列头链接指向数值数据行。v2.4 四表回归中，数值行误判表头 **0**、假表头链接 **0**、错误期间链接 **0**、错误单位作用范围 **0**、错误完整声明 **0**。这是预先冻结的[四表回归回执](../results/market_observer_financial_text_v24_20261116/evaluation_frozen_v1_redacted.json)与旧版[失败诊断](../results/market_observer_financial_text_v23_20261116/postrun_false_header_diagnosis_redacted.json)之间、只针对六个同源见证的比较；旧版 71 条候选的连续片段指标不能与新版结构指标直接比较。

Micron 所选表格的 **48/48 个数值单元格在来源已知字段上绑定完整**，但全部字段完整数是 **0/48**：30 个金额格没有足以确认币种的单元格或单位表头证据，48 个脚注标记没有直接 HTML 定义链接。评价器将这 78 个字段决定记录为**正确弃答**，并未补写币种或脚注。Micron 因此通过预定的“已知字段正确、歧义正确保留”小范围门槛，不能称为 48 条完全核实的财务事实。[逐单元格对照](../results/market_observer_financial_text_v24_20261116/evaluation_frozen_v1_redacted.json)的详细值和关系留在本机私人 JSON/UTF-8 BOM CSV，不进入仓库。

四张表共 **138 个预期数值单元格**：NVIDIA 三表各 30，Micron 一表 48；均被定位，已知字段完整为 138/138，全部字段完整为 90/138。36 个 NVIDIA Q/Q、Y/Y 变化列按比较标签处理，不被猜作新的财务期间。冻结后同团队[逐格来源后审](../results/market_observer_financial_text_v24_20261116/postrun_source_audit_redacted.json)回查保存原件中的 138 个数值定位与 1,104 条显式来源边，未发现额外错误链接；它没有重跑解析器或评分器，也不是独立人工认证。这个通过仅适用于已看过的四张表和预定规则。[参考冻结](../results/market_observer_financial_text_v24_20261116/reference_freeze_redacted.json)、[只读参考审计](../results/market_observer_financial_text_v24_20261116/reference_audit_redacted.json)、[冻结哈希](../results/market_observer_financial_text_v24_20261116/freeze_frozen_v1_redacted.json)绑定原件、DOM、配置、代码和评价器版本。

## 来源与修订记录

四份保存原件的发布者和附件身份在 SEC 页面只读核对：[NVIDIA 2025-08-27](https://www.sec.gov/Archives/edgar/data/1045810/000104581025000207/q2fy26pr.htm)、[2025-11-19](https://www.sec.gov/Archives/edgar/data/1045810/000104581025000228/q3fy26pr.htm)、[2026-02-25](https://www.sec.gov/Archives/edgar/data/1045810/000104581026000019/q4fy26pr.htm)，以及[Micron 2025-06-25](https://www.sec.gov/Archives/edgar/data/723125/000072312525000019/a2025q3ex991-pressrelease.htm)。网页身份核对不替代保存字节哈希或财务语义审阅；见[来源回执](../results/market_observer_financial_text_v24_20261116/source_identity_check_redacted.json)。Micron 按公告日期、文档 ID、DOM 顺序选到第一张符合多期间和单位/例外/口径条件的表，未按结果挑样本。

生产解析先建立逻辑网格与单元格角色，再识别表头和作用范围，最后才给指标主题作注释。金额行中的 `$` 不会单独变成表头；单位拆成币种、倍率和测量种类；重复期间表头保留仍适用的明确单位/口径。无证据字段保持未知，解析器从不读取参考答案、金额清单或收益标签。v2.3 代码与冻结产物未覆盖；其此前被误用的章节 billions 与表格 millions 的范围问题在本四表回归中未再出现。

[开发尝试日志](../results/market_observer_financial_text_v24_20261116/development_attempts_redacted.jsonl)保留一次调用路径错误、重复表头丢失单位/口径的合成失败、季度 `Q1 2025` 写法导致的四项测试失败，以及后续修订。公开合成套件修订前 **13/17**、修订后 **17/17**，属于同一组重叠测试方法，不能相加；私人六例开发探针也不是独立来源样本。[失败测试回执](../results/market_observer_financial_text_v24_20261116/dev_synthetic_pre_quarter_fix_redacted.json)、[修订后测试回执](../results/market_observer_financial_text_v24_20261116/dev_synthetic_post_quarter_fix_redacted.json)与[冻结前审计解决回执](../results/market_observer_financial_text_v24_20261116/pre_freeze_code_audit_resolution_redacted.json)记录软件和契约检查。冻结前另有一次未即时保存代码哈希的内存开发评分，已[单独说明](../results/market_observer_financial_text_v24_20261116/pre_freeze_development_score_audit_redacted.json)，不充作正式运行。正式冻结后来源评分仅执行一次，解析器调用四次；八事件输入构建 **0**、20,000 字符装包 **0**、模型拟合 **0**、模型推理 **0**、收益评价 **0**。

## 计划—实际与限制

逐阶段计划、实际、证据、偏差及下一步见 [P0–P5 台账](../results/market_observer_financial_text_v24_20261116/plan_execution.csv)。本轮小范围门槛为 **PASS**，但 78 个正确弃答是来源证据限制，不是解析器生成了完整事实。可变 HTML、其他公司、其他表格、完整八事件、固定预算下的模型可见文本均未检验；v2.4 不自动替换个人页面事实卡。后续应先对剩余训练期表格做独立版本的开发回查，再决定是否构建完整输入；Micron 币种与脚注须有更明确的原件关系才能升级。正式金融实验继续为 `NOT_RUN`。

**同步状态：** 报告写作时 Git 推送、远端回读及交接 ZIP 尚待执行，以下游同步回执为准。
