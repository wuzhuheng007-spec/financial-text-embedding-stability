# 中美市场中期情绪拐点研究

目标：研究市场如何从恐慌、反弹或拥挤转向中期趋势，并检验新闻反应与情绪是否在价格基线之外提供信息。观察窗口为4/8/12自然周；QQQ、上证综指、科创50、SOX分别分析。

## 当前入口

- [第四阶段实际检验报告](phase4/Phase4_Participation_Real_Rates_Report_zh.md)：等权参与结构与TIPS利率，强基线增量比较及完整负面结果。
- [第四阶段冻结计划](phase4/PLAN_zh.md)、[模型全对照](phase4/results/model_contrasts.csv)与[同作者复核](phase4/results/self_check.json)。

- [第三阶段完整报告](phase3/Phase3_Event_Atlas_Report_zh.md)：全样本20%/10%行情、候选信号误报、风险代理与价格增量比较。
- [全部139条主要事件卡](phase3/ALL_EVENTS_20pct_zh.md)及[656条双阈值事件CSV](phase3/results_v1_1/events_all_thresholds.csv)。
- [第三阶段冻结口径](phase3/PROTOCOL_zh.md)、[边界修正](phase3/AMENDMENT_v1_1.json)与[同作者自查](phase3/results_v1_1/self_check.json)。

- [逐步研究日志](JOURNAL.md)和[结构化日志](steps.json)：每一步的计划、输入身份、实际执行、结果、限制和证据。
- [当前状态](STATUS.json)和[结论登记表](claims.json)：区分已核验的窄结论、假设、负面结果和未通过的数据条件。
- [第一阶段完整报告](phase1/Phase1_Research_Report_zh.md)：四条价格代理的基线和历史拐点对照。
- [第一阶段冻结协议](phase1/PROTOCOL_zh.md)与[完整汇总](phase1/results/baseline_summary.csv)。
- [第二阶段报告](phase2/Phase2_BW_Availability_Report_zh.md)：BW源码、实际数值对照、五个公开文件版本及官方新闻字段试点。
- [第二阶段计划](phase2/PLAN_zh.md)及[汇总结果](phase2/results/bw_diagnostic/summary.json)。
- [新闻反应数据合同](phase1/NEWS_DATA_CONTRACT_zh.md)。
- [最初的历史机制与公开月度初筛](phase0/Market_Sentiment_Turning_Points_20261119.md)：历史记录，输入与后续moomoo研究不同，不混合为独立复制。
- [行情数据验收说明](data_audit/README_zh.md)：包括未解决的SOX缺口、异常隔离和当前版本限制。

## 已执行结果的范围

第一阶段固定价格状态在后期时间检查段中没有显示一致的弱势反弹改善；主展示四个均值差区间均覆盖零。20%方向变化确认也可能在后续再次下跌前发生。以上只支持声明输入、参数和分段下的描述性结果，尚未验证情绪预测、策略收益或因果解释。

当前发布BW工作簿属于可修订的低频水平序列；作者明确反对以发布序列简单差分代表情绪变化。本项目尚未取得其完整历史发布版本及逐期发布时间，不把当前全历史数据当成实时历史输入。

## 记录与复现

阶段完成时，先保存结果、输入/代码/参数哈希和负面证据，再更新日志、结论状态和GitHub提交。修正写入新记录，不覆盖原始运行结果。遵循本目录[AGENTS.md](AGENTS.md)。

代码和汇总在此版本化；授权行情数据库、原始商业文本、逐行市场面板和第三方作者原件不公开，符合根目录SHARING_SCOPE。文件哈希标明输入身份，不授予数据再分发权。复现价格阶段需要自有权限下的上一轮数据库；公开代码不会虚构缺失输入。

本目录与UCLA嵌入稳定性实验、现有个人平台运行和短期新闻评分分别记录。本次同步不改变服务器或平台参数，也不重新认定其他实验的执行状态。

公开副本的图表链接与价格行级字段适配见[PUBLICATION_ADAPTATIONS.json](PUBLICATION_ADAPTATIONS.json)。原先完整文件保持不动。当前公开载荷校验清单为PUBLICATION_MANIFEST.json；旧阶段回执中所列私有输入不因此变成公开文件。

第三阶段已在固定规则与可用历史内完成139个20%方向腿及517个10%方向腿的枚举；不是所有可能行情定义的全集。VIX/融资变量的当前版本诊断没有建立统一中期预测因子，完整中国本土情绪/新闻归因仍待补齐。全部结果和负面证据保留。
