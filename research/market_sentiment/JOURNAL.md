# 逐步研究日志

记录时间为本条实际写入时间；历史运行时间仅在原回执中存在时引用。不是后台自动研究记录。

## R000 · 历史机制综述与公开月度初筛归档

**状态：** ARCHIVED_DESCRIPTIVE_ONLY；记录UTC：2026-11-19T00:08:21.084180+00:00

**计划：** 整理此前已执行的历史资料和OECD/FRED探索性筛选。

**实际：** 原报告、协议、20行初筛结果和运行回执导入本目录；本次没有重跑旧实验。

**结论：** 固定旧输入上的描述性筛选及其负面/稀疏结果保留；不同市场未呈一致改善。

**限制：** 不同于moomoo价格数据；非PIT、非独立未知测试，不与后续结果合并。

证据：[phase0/Market_Sentiment_Turning_Points_20261119.md](phase0/Market_Sentiment_Turning_Points_20261119.md)、[phase0/pilot_run_manifest.json](phase0/pilot_run_manifest.json)、[phase0/pilot_summary.csv](phase0/pilot_summary.csv)、[phase0/self_check.json](phase0/self_check.json)

## R001 · moomoo历史数据验收与已知问题隔离

**状态：** MERGED_WITH_UNRESOLVED_LIMITS；记录UTC：2026-11-19T00:08:21.084948+00:00

**计划：** 验收原始包及定向补抓，保留异常证据和缺失。

**实际：** 既有5段补抓新增1227行，数据库与原响应逐行核对；原回执导入GitHub。

**结论：** 声明版本内的文件和派生数据对账通过，7个SOX候选缺口未填。

**限制：** 源行情私有；没有逐日跨供应商独立核价，不认证总回报或完整交易日历。

证据：[data_audit/merge_receipt.json](data_audit/merge_receipt.json)、[data_audit/series_registry.json](data_audit/series_registry.json)、[data_audit/audit/post_merge_self_check.json](data_audit/audit/post_merge_self_check.json)、[data_audit/audit/unresolved_dates.json](data_audit/audit/unresolved_dates.json)

## R002 · 第一阶段价格状态基线及历史拐点对照

**状态：** PARTIALLY_SUPPORTED；记录UTC：2026-11-19T00:08:21.085829+00:00

**计划：** 固定规则后比较四条代理的4/8/12自然周价格分布，并区分极值日与确认日。

**实际：** 此前实际运行270行统计、108行状态差和401条两阈值极值；输入回溯和未来扰动自检通过，本次导入既有结果。

**结论：** 在指定后期段的8周主展示，弱势反弹相对全部周的均值差方向不同，四个主区间都覆盖零；局部20%确认不代表下跌结束。

**限制：** 当期版本价格、相关标的、重叠窗口、同团队自检，未验证情绪或可交易收益。

证据：[phase1/PROTOCOL_zh.md](phase1/PROTOCOL_zh.md)、[phase1/config.json](phase1/config.json)、[phase1/results/run_manifest.json](phase1/results/run_manifest.json)、[phase1/results/baseline_summary.csv](phase1/results/baseline_summary.csv)、[phase1/results/self_check.json](phase1/results/self_check.json)、[phase1/Phase1_Research_Report_zh.md](phase1/Phase1_Research_Report_zh.md)

## R003 · 第二阶段BW可用性审核启动

**状态：** IN_PROGRESS；记录UTC：2026-11-19T00:08:21.086276+00:00

**计划：** 核对作者版本、发布滞后、宏观修订和全样本拟合；再做固定原始代理数值诊断。

**实际：** 已查阅作者README和Stata源码，冻结诊断方案；尚未执行本阶段数值诊断。

**结论：** 研究目标及执行边界已记录；尚无新增收益或预测结果。

**限制：** 没有历史发布版本，不把当前SENT一阶差分当作情绪变化。

证据：[phase2/PLAN_zh.md](phase2/PLAN_zh.md)、[phase2/freeze_receipt.json](phase2/freeze_receipt.json)
