# 三张科技股事件影响分析开发卡——2026 年 11 月 15 日

时区：**Asia/Shanghai**。本轮以已保存的官方材料制作三张 **`AI_ANALYST_DRAFT`** 样板，[计划—实际台账](../results/news_event_impact_20261115/plan_execution.csv)逐项记录执行。起点分支提交为 `1d654b6052f271bb803d84ef3e6d9986ce5c2de6`；无关文件和上一版页面保留。未再次调用金十、开启定时采集、重下原件、调用本地 Qwen 或付费模型，也未生成收益标签或回测。

## 固定选材与时间边界

[选材清单](../results/news_event_impact_20261115/selection_manifest.json)按 A/B/C 顺序记录六份已保存官方原件、SHA-256、材料版本、获取时间、分析制作时间和开发用途。没有按事后股价选材，也无需替换指定类别。

| 卡片 | 官方材料与事件时间依据 | 不能由此证明的时间 |
| --- | --- | --- |
| A · NVIDIA | [FY2027 第二季度财报公告](https://nvidianews.nvidia.com/news/nvidia-announces-financial-results-for-second-quarter-fiscal-2027)，页面日期为 **2026-08-26**；已保存的[第一季度公告](https://nvidianews.nvidia.com/news/nvidia-announces-financial-results-for-first-quarter-fiscal-2027)只用于比较此前公司指引。 | 日期级公告和本次保存页不证明独立首见时刻或公布前市场共识。 |
| B · CoreWeave | [2026-11-10 公司公告](https://investors.coreweave.com/news/news-details/2026/CoreWeave-Reports-Strong-Third-Quarter-2026-Results/default.aspx)及同日[第三季度演示材料](https://s205.q4cdn.com/133937190/files/content_files/CoreWeave-3Q26-Earnings-Presentation-Final.pdf)，重点是全年资本支出指引更新。 | 同日两份材料的分钟先后和独立首见时间未知；未用之后的 10-Q 充当事件当时事实。 |
| C · 美国 CPI | [BLS 2026 年 10 月存档公告](https://www.bls.gov/news.release/archives/cpi_11102026.htm)，官方禁发解除时刻为 **2026-11-10 08:30 ET**；[9 月存档公告](https://www.bls.gov/news.release/archives/cpi_10142026.htm)提供此前原公布值。 | 官方禁发时刻不是独立观察到的首发时刻；后来保存的网页不是首发时点快照。 |

每张卡都分列**事件时间依据、材料获取时间、分析制作时间**。本次分析写于材料获取之后，不能表述为事件当时的预测。原件哈希、短摘录与本机保存字节已回读；在线仅核验官方页面身份，没有另存完整原件。

## 事实、计算与条件式传导

独立的 [A](../results/news_event_impact_20261115/a_nvidia/facts.json)、[B](../results/news_event_impact_20261115/b_coreweave/facts.json)、[C](../results/news_event_impact_20261115/c_cpi/facts.json) 事实表分别为 **18、17、9 条**。每条带期间、单位/口径、官方链接、文件哈希、定位、短摘录、公布时间精度与证据状态；缺口单列。[静态来源回执](../results/news_event_impact_20261115/static_source_validation_release.json)核对六份原件和 **44 条事实**。这是有界开发材料，不是正式研究样本。

- **NVIDIA：**公司报告 FY2027 第二季度 GAAP 营收 **96,221 百万美元**、净利润 **59,688 百万美元**、毛利率 **75.0%**；第三季度管理层营收展望中心值为 **1,080 亿美元，上下 2%**。现金购买与公司定义的非 GAAP 自由现金流分列。此前对本季度的 **910 亿美元**为**公司指引**，不是市场共识。代码用经营现金流、现金购买及相关本金支付回算已报告的自由现金流口径。
- **CoreWeave：**当期公告将 FY2026 公司定义资本支出指引列为 **370–390 亿美元**，并在本公告中复述此前的 **350–390 亿美元**；原始旧指引材料尚未独立回读。演示材料的第三季度公司定义资本支出 **14,511 百万美元**与现金流表的现金购买 **9,441 百万美元**口径不同。前者定义包含融资租赁取得的资产，但该部分单独金额未知。公告报告约 **42 亿美元**可转换票据融资及 **26 亿美元**贷款安排；票息、收益率、利差和综合融资成本均未知。第四季度总利息费用展望不能替代这些融资条款。
- **CPI：**BLS 公布 10 月整体 CPI 经季调环比 **0.4%**、核心 **0.3%**、整体未经季调环比 **0.2%**。已保存的 9 月公告原公布同口径整体 **0.6%**、核心 **0.3%**。引用公告未公布 10 月整体／核心同比率，缺失值不显示为 0。没有带日期的公布前市场共识，因此相对市场预期的意外程度为 **UNKNOWN**。

[十项代码计算](../results/news_event_impact_20261115/calculations_final.json)逐项绑定公式、输入 `fact_id`、单位和可比性限制，包括 NVIDIA 自由现金流、CoreWeave 公司定义资本支出回算及 CPI 月率差值；均不称为“超出市场共识”。[A](../results/news_event_impact_20261115/a_nvidia)、[B](../results/news_event_impact_20261115/b_coreweave)、[C](../results/news_event_impact_20261115/c_cpi) 目录各有一张中文和一张英文分析卡。[九条传导边](../results/news_event_impact_20261115/transmission_edges.json)把发行人直接陈述或机械关系与经济推断分开，保留假设、反证条件、观察变量和时间范围；没有虚构具体供应商或客户收益。新闻语气为 `NOT_SCORED`，基本面解释为 `AI_ANALYST_DRAFT`，实际价格反应为 `NOT_RUN`。

## 自审、页面与验证

先保存了[13 文件初稿冻结回执](../results/news_event_impact_20261115/draft_freeze_receipt.json)，再做一次[Codex 逐条反证自审](../results/news_event_impact_20261115/review_findings.json)。记录并修复五项字段、时间和展示问题；检查无来源数字与关系、后见信息、增长与超预期混淆、可能性写成确定性、期间/币种/GAAP 口径，以及不同期限方向。**这是同一执行者自审，不是独立人工裁定或因果识别。**

新版[公开页面模板](../results/news_event_impact_20261115/dashboard_public_v3.html)在既有财报、宏观和金十布局中增加“事件影响分析”。私人版只从已有 SQLite 与官方材料生成，保留在 Git 忽略的 `artifacts/`。Chrome **157.0.8103.51** 通过 `127.0.0.1` 真正打开页面，检查 **1440×900** 与 **390×844**。[脱敏浏览器回执](../results/news_event_impact_20261115/visual_acceptance_release.json)确认三卡顺序、数字与未知值、可展开证据、无全页横向溢出；DOM 中 **194 个外部链接**均为 HTTPS 且带 `noopener`，浏览器验收期间**未请求外网或服务商**。桌面三栏较密但可读。公开页面与此前保存的 40 条金十快讯正文比对，命中 **0 条**；私人页面、数据库、响应及截图均未发布。

最终[聚焦软件检查](../results/news_event_impact_20261115/focused_tests_release.log)为 **8/8 通过**，包含三个已有金十页面测试，与历史软件覆盖有重叠。合成软件检查、原件字节/短摘录回读、真实浏览器检查分别记录。三张开发卡不能用于估计抽取准确率或推断金融有效性。

## 待解决事项

合格的公布前共识、独立首见时刻、历史公司—证券身份、来源权限和完整金融协议仍未解决。若下一步评价内容质量，需要单独设计人工裁定。正式金融准入、收益标签、拟合、预测、收益评价和回测均 **NOT_RUN**。
