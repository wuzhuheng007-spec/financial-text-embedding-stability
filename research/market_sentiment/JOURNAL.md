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

## R006 · 全样本大涨大跌与风险情绪代理研究冻结

**状态：** PROTOCOL_FROZEN_BEFORE_FINANCIAL_ANALYSIS；记录UTC：2026-11-19T01:42:18.123617+00:00

**计划：** 枚举20%主口径和10%敏感性事件，保留失败信号，检查风险代理的增量关联；先冻结再计算。

**实际：** 核对SQLite及三个FRED当前版本输入哈希；保存完整参数、时序对齐、标签成熟规则、模型比较与验收合同。尚未计算第三阶段金融结果。

**结论：** 只确认输入身份与已固定研究口径，尚无新效力结论。

**限制：** QQQ/STAR历史长度不足30年；当前版本非PIT；缺中国本土长历史情绪/完整新闻首次发布与预期数据。

证据：[phase3/config.json](phase3/config.json)、[phase3/freeze_receipt.json](phase3/freeze_receipt.json)、[phase3/input_manifest.json](phase3/input_manifest.json)、[phase3/PROTOCOL_zh.md](phase3/PROTOCOL_zh.md)

## R007 · 全样本首轮计算与边界问题登记

**状态：** INITIAL_OUTPUT_RETAINED_BOUNDARY_CHECK_FAILED；记录UTC：2026-11-19T01:56:43.821780+00:00

**计划：** 按R006冻结合同枚举完整行情与候选信号并训练固定时序基线。

**实际：** 实际完成v1：656个双阈值行情腿、600个Ridge拟合；边界检查发现6处跨SOX片段注释与8个前态未知信号。登记修正，保留原始结果、代码和回执。

**结论：** 首轮已执行；受影响的信号/注释不能作最终效力证据。

**限制：** 发现问题后没有调整阈值或筛选最好参数；保留失败记录。

证据：[phase3/audit/analyze_v1.py](phase3/audit/analyze_v1.py)、[phase3/results/run_manifest.json](phase3/results/run_manifest.json)、[phase3/results/events_all_thresholds.csv](phase3/results/events_all_thresholds.csv)、[phase3/results/alert_summary.csv](phase3/results/alert_summary.csv)、[phase3/AMENDMENT_v1_1.json](phase3/AMENDMENT_v1_1.json)

## R008 · 修正边界后全量重跑与原始输入自查

**状态：** EXECUTED_SAME_AUTHOR_CHECKED_CURRENT_VINTAGE；记录UTC：2026-11-19T01:56:43.823140+00:00

**计划：** 只修正前态未知和跨片段注释，保留冻结科学口径，全量重跑并核对原始证据。

**实际：** v1_1输出139个20%腿、517个10%腿、1391个唯一候选信号及12519个信号期限障碍单元、600个拟合和35575行逐期预测；从原SQLite核对23749个价格与68993个前向标签；检查信号和重新拟合600个模型。关联/模型比较与v1相同。

**结论：** 固定规则内全事件已枚举；主关联/主增量比较没有确立多重比较调整后稳定预测；确认滞后与误报保留。

**限制：** 当前版本、四条相关市场代理、一个固定模型族；STAR短样本；不是独立复制、完整情绪识别或可交易收益回测。

证据：[phase3/analyze.py](phase3/analyze.py)、[phase3/results_v1_1/run_manifest.json](phase3/results_v1_1/run_manifest.json)、[phase3/results_v1_1/self_check.json](phase3/results_v1_1/self_check.json)、[phase3/results_v1_1/event_counts.csv](phase3/results_v1_1/event_counts.csv)、[phase3/results_v1_1/associations.csv](phase3/results_v1_1/associations.csv)、[phase3/results_v1_1/model_contrasts.csv](phase3/results_v1_1/model_contrasts.csv)、[phase3/results_v1_1/alert_summary.csv](phase3/results_v1_1/alert_summary.csv)、[phase3/results_v1_1/model_fit_coverage.csv](phase3/results_v1_1/model_fit_coverage.csv)

## R009 · 美联储30年官方声明链接索引

**状态：** OFFICIAL_ARCHIVE_LINK_METADATA_ONLY；记录UTC：2026-11-19T01:56:43.824176+00:00

**计划：** 遍历1996-2020年度档案和2021-2026日历，只登记真实声明链接与失败覆盖。

**实际：** 26个官方索引页面获取成功，提取238条唯一声明URL；保存年份覆盖、原网页哈希及未获得字段。按事后行情起点前后20自然日匹配政策线索。

**结论：** 该公开档案范围内的声明链接清单已提取，不是正文/首次发布时刻或完整新闻因果数据库。

**限制：** 90年代不变息会议未必发声明；档案外非例会新闻可能缺失；中国本土政策资料与预期数据待补齐。

证据：[phase3/collect_official_calendar.py](phase3/collect_official_calendar.py)、[phase3/results/official_calendar/index_fetch_inventory.csv](phase3/results/official_calendar/index_fetch_inventory.csv)、[phase3/results/official_calendar/statement_links.csv](phase3/results/official_calendar/statement_links.csv)、[phase3/results/official_calendar/year_coverage.csv](phase3/results/official_calendar/year_coverage.csv)、[phase3/results/official_calendar/summary.json](phase3/results/official_calendar/summary.json)、[phase3/results_v1_1/event_policy_date_links.csv](phase3/results_v1_1/event_policy_date_links.csv)

## R010 · 逐次事件卡、情绪轨迹与全部结果核对

**状态：** PARTIALLY_SUPPORTED_RESEARCH_ATLAS_COMPLETE_WITHIN_SCOPE；记录UTC：2026-11-19T01:56:43.825427+00:00

**计划：** 生成全部20%事件卡和原始结果可追溯报告，核验全部主展示单元与引用，发布结论边界。

**实际：** 139条事件卡逐条对应；生成聚合轨迹及4/8/12周全结果。核对225行模型汇总、159行对照、756行信号汇总、968行轨迹及60行重叠窗口关联；核验7条一手来源的对应论述。

**结论：** 价格与风险代理的重叠窗口关联不等于中期预测；当前有限诊断没有建立统一可靠反转因子。

**限制：** 仍缺完整中国本土情绪、历史可得版本、新闻首次时间/预期与独立复制；不为每次涨跌编造单一原因。

证据：[phase3/Phase3_Event_Atlas_Report_zh.md](phase3/Phase3_Event_Atlas_Report_zh.md)、[phase3/ALL_EVENTS_20pct_zh.md](phase3/ALL_EVENTS_20pct_zh.md)、[phase3/results_v1_1/display_cells.json](phase3/results_v1_1/display_cells.json)、[phase3/results_v1_1/overlapping_window_associations.csv](phase3/results_v1_1/overlapping_window_associations.csv)、[phase3/results_v1_1/event_aligned_medians.csv](phase3/results_v1_1/event_aligned_medians.csv)、[phase3/results_v1_1/report_reconciliation.json](phase3/results_v1_1/report_reconciliation.json)、[phase3/citation_audit.json](phase3/citation_audit.json)、[phase3/evidence_ledger.json](phase3/evidence_ledger.json)、[phase3/AUDIT_FINAL.json](phase3/AUDIT_FINAL.json)

## R011 · 第三阶段公开载荷同步与远端核对

**状态：** SYNCED_REMOTE_READBACK_VERIFIED；记录UTC：2026-11-19T02:00:37.708114+00:00

**计划：** 本地校验后非强制快进同步，精确读回关键文件，记录已公开范围。

**实际：** 提交c65fe925包含56个新增/更新公开文件，带预期父提交非强制更新成功；报告、展示原数值、自查、逐步日志和总清单5个远端文件逐字节与本地一致。

**结论：** 第三阶段公开代码、全事件与聚合结果及负面记录已在指定研究分支可读；P35同步验收通过。

**限制：** 该核对只证明发布一致性，不是独立科学验证；原始行情、逐行风险与预测未公开。

证据：[phase3/PUBLICATION_ACK_20261119.json](phase3/PUBLICATION_ACK_20261119.json)

## R012 · 历史牛熊复盘图片学习与局部事实审核

**状态：** PARTIALLY_SUPPORTED_IMAGE_REVIEW_AND_HYPOTHESES_ONLY；记录UTC：2026-11-19T02:59:03.931763+00:00

**计划：** 读取用户材料，核对可见矛盾，登记可检验机制，不直接采纳图示统计或因果。

**实际：** 打开四个一手来源，核对四日本地OHLC；登记极值/收盘口径、1996详情复制错误、政策日期及纳指周期内部不一致；提出六组候选变量，未运行新金融实验。

**结论：** 图片提供机制线索；只有局部事实已核验，新因子效力和图中未注明口径的统计均未确立。

**限制：** 行业/个股涨幅、开户与宏观实时版本未完整核验；未获得新广度/资金数据库；没有独立验证。

证据：[image_review/Historical_Images_Review_zh.md](image_review/Historical_Images_Review_zh.md)

## R013 · 纳指补充图与盈利利率预期机制登记

**状态：** PARTIALLY_SUPPORTED_HYPOTHESES_ONLY；记录UTC：2026-11-19T03:03:02.416763+00:00

**计划：** 核对新增图片中的政策时序，区分盈利、利率、情绪与成分变化。

**实际：** 实际打开2020-03-15和2022-03-16美联储声明；指出实际加息与预期重定价不能混同；登记盈利修订、叙事兑现差与广度等候选模块，未运行新金融实验。

**结论：** 两条具体政策事实已核验，其余机制作为待检验假说，尚无新预测效力结论。

**限制：** 没有新增历史盈利预期/成分/新闻数据；非独立验证。

证据：[image_review/Nasdaq_Supplement_Review_zh.md](image_review/Nasdaq_Supplement_Review_zh.md)

## R014 · 参与结构与实际利率检验冻结

**状态：** PROTOCOL_FROZEN_BEFORE_PHASE4_FINANCIAL_ANALYSIS；记录UTC：2026-11-19T03:13:49.795444+00:00

**计划：** 先核对新增数据覆盖，再固定广度代理、利率变量、强基线和完整比较，计算前记录。

**实际：** 实际下载NASDAQNDXE、NASDAQ100、DFII10当前版本；阅读Nasdaq官方方法及变更说明；冻结三个新增模块和alpha敏感性、同样本时序检验及多重比较规则。尚未计算第四阶段收益对照或模型。

**结论：** 取得可追溯的等权/市值指数与TIPS输入，可开展有限覆盖检验；新闻和中国本土广度未取得。

**限制：** 等权相对表现不是全市场广度；当前版本非历史PIT，指数编制变化存在；不支持预测效力声明。

证据：[phase4/PLAN_zh.md](phase4/PLAN_zh.md)、[phase4/config.json](phase4/config.json)、[phase4/freeze_receipt.json](phase4/freeze_receipt.json)、[phase4/input_manifest.json](phase4/input_manifest.json)

## R015 · 参与结构与实际利率全比较实际执行

**状态：** EXECUTED_CURRENT_VINTAGE_LIMITED_DIAGNOSTIC；记录UTC：2026-11-19T03:21:30.904119+00:00

**计划：** 按R014冻结规则计算新增代理、状态关联与强基线增量检验，保存全部敏感性和失败样本。

**实际：** 完成1200个拟合和70082行预测，覆盖两个alpha、三个期限、所有预定市场；等权/市值源的两个单边不一致日期拆成三段，禁止补价；保存360行模型汇总、318行对照、180行状态汇总及60行状态对照。

**结论：** 固定规则内QQQ/SOX缩窄队列未来均值较低，但多重比较后未建立稳健效应；强基线之外没有确立新增模块的预测改善。

**限制：** 当前版本，纳指内部结构代理，不是完整广度或心理情绪；中国仅外部环境；独立危机数有限；一个确定性模型族。

证据：[phase4/analyze.py](phase4/analyze.py)、[phase4/results/run_manifest.json](phase4/results/run_manifest.json)、[phase4/results/coverage.json](phase4/results/coverage.json)、[phase4/results/model_coverage.csv](phase4/results/model_coverage.csv)、[phase4/results/model_summary.csv](phase4/results/model_summary.csv)、[phase4/results/model_contrasts.csv](phase4/results/model_contrasts.csv)、[phase4/results/state_summary.csv](phase4/results/state_summary.csv)、[phase4/results/state_contrasts.csv](phase4/results/state_contrasts.csv)

## R016 · 原始输入复核与第四阶段结果报告

**状态：** PARTIALLY_SUPPORTED_SAME_AUTHOR_SELF_CHECKED；记录UTC：2026-11-19T03:21:30.905962+00:00

**计划：** 从源重算代理及日期，重拟合全部模型，核对所有汇总；记录新结论边界和未取得数据。

**实际：** 核对23749行前阶段输入保持不变、47498个来源日期、118745个新特征、68993个未来路径最小值；增广系统重拟合1200个模型，核对280328个预测单元及全部汇总；完整报告和7条一手来源审核已保存，24个主展示结果单元另与原表核对。

**结论：** 数据及结果在本轮口径下可追溯；未形成可靠反转标准，未开展新闻预期差效力检验。

**限制：** 独立复核和PIT版本未取得；A股本土广度、新闻首次时间/市场预期和历史盈利预测仍缺。

证据：[phase4/check.py](phase4/check.py)、[phase4/results/self_check.json](phase4/results/self_check.json)、[phase4/Phase4_Participation_Real_Rates_Report_zh.md](phase4/Phase4_Participation_Real_Rates_Report_zh.md)、[phase4/results/display_cells.json](phase4/results/display_cells.json)、[phase4/citation_audit.json](phase4/citation_audit.json)、[phase4/evidence_ledger.json](phase4/evidence_ledger.json)、[phase4/AUDIT_FINAL.json](phase4/AUDIT_FINAL.json)

## R017 · 第四阶段同步与远端核对

**状态：** SYNCED_REMOTE_READBACK_VERIFIED；记录UTC：2026-11-19T03:23:05.571535+00:00

**计划：** 校验公开载荷后带预期父提交非强制同步，读回关键文件。

**实际：** 结果提交3a68e0b3非强制更新成功；报告、主展示原数值、自查、日志和公开清单5项内容逐字节读回一致。

**结论：** P44公开同步验收通过，第四阶段结果可在研究分支读回。

**限制：** 发布一致性核对不是独立科学验证；商业原始序列和逐行面板/预测不公开。

证据：[phase4/PUBLICATION_ACK.json](phase4/PUBLICATION_ACK.json)

## R018 · 熊市机制与九步因子清单适用性审核

**状态：** PARTIALLY_SUPPORTED_REVIEW_ONLY；记录UTC：2026-11-19T03:43:43.263193+00:00

**计划：** 阅读新增六图，核对关键时序/口径，将横截面清单适配为市场择时检查项。

**实际：** 实际打开三篇官方历史/政策分析，核对QQQ同区间24个报价观测和23个收益区间；保留失败论文访问；登记多标签危机机制，逐项辨别九步流程适用性。未运行新因子或策略实验。

**结论：** 图片提供机制与流程线索，统计阈值及因果不能直接采纳；时间序列择时与横截面选股分开评价。

**限制：** 历史图具体数据仅局部核验；三篇来源不认证所有图片；尚无新金融效力或独立验证。

证据：[image_review/Bear_Markets_and_Factor_Checklist_zh.md](image_review/Bear_Markets_and_Factor_Checklist_zh.md)

## R019 · R006 勘误：分段比较、协议时间与口径表述（原拟 R012，并发提交已占用该编号，改记 R019）

**状态：** ERRATUM_APPENDED_ORIGINAL_RECORDS_UNCHANGED；记录UTC：2026-11-19T03:45:17.980723+00:00

**计划：** 按外部审查意见，从原运行结果文件重新生成比较，核对首次真实计算、代码提交、入队/开始/结束时间，纠正分红、日历与后段表述；不修改原配置与原记录。

**实际：** 由结果文件生成：9 个分段格中策略高于基准 1 格（0 bps 开发段 44.55% 对 24.87%），低于 8 格；R006 的“全部低于”表述错误。首次真实计算为本机演练 01:16:08Z，早于配置首次 Git 提交 01:25:31Z，且演练结果在提交前已被查看；frozen_at_utc=01:40:00Z 为手填错误值。配置内容各次一致（哈希 63a8993d…），但不能证明在首次计算前冻结，运行不属于预注册。

**结论：** 仅纠正 R006 的表述与时间元数据；数值结果未变。

**限制：** 勘误不改变单标的、单因子工程基线的性质；后段已被查看，不是未见留出集；a1 分红处理未确认；日期序列未与完整交易所日历独立核对。

证据：[platform_baseline/R006_errata_E001.json](platform_baseline/R006_errata_E001.json)

## R020 · 四张因子阈值与终检图学习及现有研究验收对照

**状态：** PARTIALLY_SUPPORTED_METHOD_REVIEW_NOT_FACTOR_ACCEPTANCE；记录UTC：2026-11-19T03:52:59.267599+00:00

**计划：** 核验统计含义、区分时序择时与横截面因子，对照第三四阶段真实证据，保留并发勘误。

**实际：** 实际阅读四图并保存输入哈希；直接打开三条一手方法来源；运行只读核对脚本，校验旧文件身份、1200个保存拟合的标签/特征元数据与12个主对照行，生成10项状态矩阵；未重拟合或重跑旧自查，未开展新策略实验。远端R019原样保留。

**结论：** 图示阈值和中性化变化不能单独认证真假因子；当前证据支持有限历史诊断，不支持第三四阶段实盘因子准入。

**限制：** 非独立验证；旧自查未在本轮重跑；没有新增净收益、容量、真正未见留出或PIT证据；其他平台基线按自身勘误处理。

证据：[image_review/factor_acceptance_v1/PLAN_zh.md](image_review/factor_acceptance_v1/PLAN_zh.md)、[image_review/factor_acceptance_v1/REVIEW_zh.md](image_review/factor_acceptance_v1/REVIEW_zh.md)、[image_review/factor_acceptance_v1/input_manifest.json](image_review/factor_acceptance_v1/input_manifest.json)、[image_review/factor_acceptance_v1/citation_audit.json](image_review/factor_acceptance_v1/citation_audit.json)、[image_review/factor_acceptance_v1/review_existing.py](image_review/factor_acceptance_v1/review_existing.py)、[image_review/factor_acceptance_v1/snapshot.json](image_review/factor_acceptance_v1/snapshot.json)、[image_review/factor_acceptance_v1/readiness.json](image_review/factor_acceptance_v1/readiness.json)、[image_review/factor_acceptance_v1/AUDIT_FINAL.json](image_review/factor_acceptance_v1/AUDIT_FINAL.json)

## R021 · 新闻情绪均值、时间波动与乘积候选的学习及数学辨识检查

**状态：** PARTIALLY_SUPPORTED_METHOD_REVIEW_REAL_NEWS_FACTOR_PENDING_INPUTS；记录UTC：2026-11-19T04:08:59.746955+00:00

**计划：** 阅读十张截图，有限检索原研究并核对API定义，使用明确人造数组检查测量含义，形成市场择时与横截面分开的候选合同，追加研究记录。

**实际：** 十图输入哈希已保存；三条官方方法文档已直接打开；有限检索未定位到能逐项核对数值的原研究。实际运行formula_audit.py，通过五项同作者确定性数学诊断并保存输入、代码与输出身份。形成候选合同和证据台账；未训练金融模型、未采集真实新闻面板、未复制截图IC。原有发布与证据哈希核对通过；完整发布校验及远端读回另在同步操作核对。

**结论：** 均值×标准差不保留窗口内时间先后顺序，时间波动不等于同日文章分歧；这些数学限制已用人工数组检查。可将其登记为待验收候选，不能宣称已发现市场情绪拐点预测能力。

**限制：** 截图经验数字全部USER_REPORTED_NOT_VERIFIED；原研究身份与原始结果未定位；没有新真实市场回测、PIT新闻验收、净收益或独立验证。

证据：[image_review/news_mean_vol_v1/AUDIT_FINAL.json](image_review/news_mean_vol_v1/AUDIT_FINAL.json)、[image_review/news_mean_vol_v1/CANDIDATE_CONTRACT_zh.md](image_review/news_mean_vol_v1/CANDIDATE_CONTRACT_zh.md)、[image_review/news_mean_vol_v1/PLAN_zh.md](image_review/news_mean_vol_v1/PLAN_zh.md)、[image_review/news_mean_vol_v1/REVIEW_zh.md](image_review/news_mean_vol_v1/REVIEW_zh.md)、[image_review/news_mean_vol_v1/evidence_ledger.json](image_review/news_mean_vol_v1/evidence_ledger.json)、[image_review/news_mean_vol_v1/formula_audit.py](image_review/news_mean_vol_v1/formula_audit.py)、[image_review/news_mean_vol_v1/formula_results.json](image_review/news_mean_vol_v1/formula_results.json)、[image_review/news_mean_vol_v1/formula_run_manifest.json](image_review/news_mean_vol_v1/formula_run_manifest.json)、[image_review/news_mean_vol_v1/input_manifest.json](image_review/news_mean_vol_v1/input_manifest.json)、[image_review/news_mean_vol_v1/source_audit.json](image_review/news_mean_vol_v1/source_audit.json)、[image_review/news_mean_vol_v1/toy_config.json](image_review/news_mean_vol_v1/toy_config.json)

## R022 · 五篇投资者情绪、新闻收益期限及分歧论文的正文学习

**状态：** FIVE_PAPER_FOCUSED_BODY_REVIEW_COMPLETE_NOT_EMPIRICAL_REPLICATION；记录UTC：2026-11-19T04:21:20.358777+00:00

**计划：** 定向网络检索并选择五篇，核对一手论文身份，阅读关键构造/结果/限制，记录来源版本及对市场中期转向研究的启发。

**实际：** 取得并提取五份公开PDF；核对作者/机构/出版商来源及五条Crossref元数据；阅读主要方法、结果和限制，保存实际PDF哈希与页码。Heston/Sinha明确阅读2016工作论文正文，另核对2017期刊身份；FEARS区分2014 Advance Access稿与2015卷期。执行来源身份同作者核对并通过；有限访问失败与页码/版本差异保留。未重新拟合任何金融模型或复算盈利。

**结论：** 所读文献的测量、预测对象和期限不同；区分短期恐慌反转、个股中期信息延续与市场共同风险状态。具体新增候选为后续研究提议，不是已验证因子。

**限制：** 主要为美国历史证据，非系统综述；原始市场结果未独立复制；未认证PIT或A股迁移；五篇不验证截图均值乘波动的IC，也不证明本项目市场顶底或净收益可预测。

证据：[literature/five_papers_v1/AUDIT_FINAL.json](literature/five_papers_v1/AUDIT_FINAL.json)、[literature/five_papers_v1/PLAN_zh.md](literature/five_papers_v1/PLAN_zh.md)、[literature/five_papers_v1/REVIEW_zh.md](literature/five_papers_v1/REVIEW_zh.md)、[literature/five_papers_v1/acquire.py](literature/five_papers_v1/acquire.py)、[literature/five_papers_v1/acquisition_manifest.json](literature/five_papers_v1/acquisition_manifest.json)、[literature/five_papers_v1/citation_registry.json](literature/five_papers_v1/citation_registry.json)、[literature/five_papers_v1/reading_ledger.json](literature/five_papers_v1/reading_ledger.json)、[literature/five_papers_v1/self_check.json](literature/five_papers_v1/self_check.json)、[literature/five_papers_v1/source_audit.json](literature/five_papers_v1/source_audit.json)

## R023 · 首轮行情策略协议与执行代码预运行锁定

**状态：** PRE_RUN_SPECIFICATION_SYNTHETIC_CHECKED_HISTORY_ALREADY_SEEN；记录UTC：2026-11-19T04:43:17.401313+00:00

**计划：** 在真实行情策略计算前固定三价格策略、两项文本阻碍、成交成本与主检验；先完成人工路径和因果前缀检查。

**实际：** 固定config/PROTOCOL及run/check代码，人工路径成本核算与未来扰动/前缀检查通过；尚未运行真实行情策略，新闻候选保持BLOCKED。配置计划经Git预运行提交后才执行。

**结论：** 规则与计算代码已写出，同作者人工检查已执行；不支持金融功效或未见预注册声明。

**限制：** 已看过的历史；只使用当前版本OHLC；理论指数单位；假设成本；新闻数据缺失。

证据：[phase5_strategy_v1/PROTOCOL_zh.md](phase5_strategy_v1/PROTOCOL_zh.md)、[phase5_strategy_v1/check.py](phase5_strategy_v1/check.py)、[phase5_strategy_v1/config.json](phase5_strategy_v1/config.json)、[phase5_strategy_v1/run.py](phase5_strategy_v1/run.py)、[phase5_strategy_v1/unit_check.json](phase5_strategy_v1/unit_check.json)

## R024 · 三项moomoo行情策略首轮实际模拟及Claude复核交接

**状态：** EXECUTED_SELF_CHECKED_NO_RETURN_INCREMENT_SCREEN_PASSED；记录UTC：2026-11-19T04:49:16.346750+00:00

**计划：** 按预运行锁定协议计算三候选、两基准和三成本情景；对账逐日净值并输出可复核指令，不以价格冒充新闻情绪。

**实际：** 冻结提交1cfe1acb在真实运行开始前完成并读回；使用指定SHA的真实moomoo SQLite，得到360行汇总，其中330份有足够区间的实际账本。12项主收益增量检验均未通过。330份账本以现金/单位数和费用独立方程作同作者核对，6个实际行情前缀检查通过；输出全部时期、成本、区块/seed敏感性及Claude三层复核指令。T1/T2新闻策略因缺真实输入未运行。

**结论：** 仅支持此固定配置与当前价格输入下的探索性策略模拟：没有候选通过预设收益增量门槛；部分P3风险指标改善同时伴随较低暴露，不能据此证明alpha。

**限制：** 非独立验证；历史已见、当前版本、假设费用与零现金收益；QQQ总回报未认证；其他指数非可直接交易；真实文本与事件因子仍BLOCKED；Claude尚未执行。

证据：[phase5_strategy_v1/AUDIT_FINAL.json](phase5_strategy_v1/AUDIT_FINAL.json)、[phase5_strategy_v1/CLAUDE_CODE_TASK.md](phase5_strategy_v1/CLAUDE_CODE_TASK.md)、[phase5_strategy_v1/RESULTS_zh.md](phase5_strategy_v1/RESULTS_zh.md)、[phase5_strategy_v1/build_report.py](phase5_strategy_v1/build_report.py)、[phase5_strategy_v1/environment.json](phase5_strategy_v1/environment.json)、[phase5_strategy_v1/evidence_ledger.json](phase5_strategy_v1/evidence_ledger.json)、[phase5_strategy_v1/freeze_receipt.json](phase5_strategy_v1/freeze_receipt.json)、[phase5_strategy_v1/requirements.txt](phase5_strategy_v1/requirements.txt)、[phase5_strategy_v1/results/contrasts.csv](phase5_strategy_v1/results/contrasts.csv)、[phase5_strategy_v1/results/display_cells.json](phase5_strategy_v1/results/display_cells.json)、[phase5_strategy_v1/results/factor_diagnostics.csv](phase5_strategy_v1/results/factor_diagnostics.csv)、[phase5_strategy_v1/results/input_inventory.json](phase5_strategy_v1/results/input_inventory.json)、[phase5_strategy_v1/results/metrics.csv](phase5_strategy_v1/results/metrics.csv)、[phase5_strategy_v1/results/private_output_hashes.json](phase5_strategy_v1/results/private_output_hashes.json)、[phase5_strategy_v1/results/run_manifest.json](phase5_strategy_v1/results/run_manifest.json)、[phase5_strategy_v1/results/self_check.json](phase5_strategy_v1/results/self_check.json)、[phase5_strategy_v1/results/text_candidates.json](phase5_strategy_v1/results/text_candidates.json)
