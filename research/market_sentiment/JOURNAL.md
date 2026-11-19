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

## R004 · BW构造、可用性与公开版本审核

**状态：** PARTIALLY_SUPPORTED；记录UTC：2026-11-19T00:20:45.145374+00:00

**计划：** 按已同步冻结方案审核作者源码、版本与五代理数值构造；不做收益拟合。

**实际：** 固定作者commit7a0165f3；重建726个共同月SENT并检查四个拟合截止；SVD自检通过；检查五个工作簿版本，含README-only及截断文件。

**结论：** 当前维护BW为低频水平序列，不以简单差分解释变化；当前版本SENT数值可高度匹配，但不能据此恢复当时可用性。历史分数随版本/拟合截止变化。

**限制：** 发布日历及长历史vintage缺失；变化混合标准化、数据和方法影响；无收益检验或独立审计。

证据：[phase2/PLAN_zh.md](phase2/PLAN_zh.md)、[phase2/Phase2_BW_Availability_Report_zh.md](phase2/Phase2_BW_Availability_Report_zh.md)、[phase2/source_registry_complete.json](phase2/source_registry_complete.json)、[phase2/results/bw_diagnostic/summary.json](phase2/results/bw_diagnostic/summary.json)、[phase2/results/bw_diagnostic/self_check.json](phase2/results/bw_diagnostic/self_check.json)、[phase2/results/revision_audit/workbook_inventory.csv](phase2/results/revision_audit/workbook_inventory.csv)、[phase2/results/revision_audit/published_version_comparison.csv](phase2/results/revision_audit/published_version_comparison.csv)、[phase2/results/availability_matrix.csv](phase2/results/availability_matrix.csv)

## R005 · 官方政策新闻时间与预期字段试点

**状态：** METADATA_PILOT_ONLY；记录UTC：2026-11-19T00:20:45.146170+00:00

**计划：** 按冻结计划检查2019FOMC、2020FOMC和2024中国发布会三条官方资料。

**实际：** 实际获取三个当前官方页面和哈希；两条美国声明有明确发布安排，中国仅有会议开始时刻；预期等缺失字段保留null。

**结论：** 字段合同在真实来源上完成有界演示；会议开始、全文发布时间与本轮抓取时间不可混同。

**限制：** 手工选取的三条事件不代表完整新闻库；没有预期差、反应残差、金融拟合或效力结论。

证据：[phase2/results/news_field_pilot/events.json](phase2/results/news_field_pilot/events.json)、[phase2/results/news_field_pilot/summary.json](phase2/results/news_field_pilot/summary.json)、[phase2/news_field_pilot.py](phase2/news_field_pilot.py)

## R006 · 平台工程基线：QQQ a1 日线 20 日动量多/现金（mom20@1）

**状态：** ENGINEERING_BASELINE_COMPLETE_NOT_EVIDENCE_OF_EFFICACY；记录UTC：2026-11-19T01:38:27.937616+00:00

**计划：** 用已审计的 moomoo 研究数据集 v0.1（从两个原始 ZIP 按仓库代码重建，哈希与回执一致，自检 PASS）冻结 QQQ a1 日线快照；在计算前提交冻结协议 configs/quant_research_mom20_v1.json：20 日动量>0 持有否则现金，收盘信号次日开盘执行，0/5/10 bps 单边成本，开发 1999–2012 / 验证 2013–2019 / 留出 2020–2026 分段报告，持有基准同口径。

**实际：** 通过平台网页提交两次相同实验，由无网络、限 CPU/内存的作业容器执行；两次 results_digest 相同；独立复算程序从下载账本重算 0 处时序/数值问题，指标最大差 3e-14。三种成本、三个区间全部保存。留出段 5 bps：策略总收益 +151.1%，持有基准 +222.3%；策略最大回撤 -24.1%，基准 -35.1%。

**结论：** 仅支持：在声明的数据版本、协议和成本假设下，该规则的历史回放数值如汇总所列；三个区间均未超过持有基准的总收益，回撤较小。

**限制：** 单标的、单因子、单一参数，不推出普遍有效性；a1 为供应商前复权价格收益，分红处理方法未经文档确认，不称总收益；非 point-in-time 数据；现金收益按 0；成本为工程假设；开盘价可成交性未验证；同团队自检，非独立审计。

证据：[platform_baseline/mom20_qqq_a1_v1_summary.json](platform_baseline/mom20_qqq_a1_v1_summary.json)
