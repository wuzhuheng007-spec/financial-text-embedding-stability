# 宏观与科技事件：有来源的开发候选

日期：2026-11-15，Asia/Shanghai（UTC+08:00）。本报告对应 `docs/news_research/macro_tech/01_CODEX_TASK.txt` 与[现行技术路线](../docs/news_research/macro_tech/roadmap.zh.md)。研究目标是先将可能影响大盘和科技股的宏观、政策、地缘、融资及关键科技事件拆成可核验事实、公布前预期、条件式传导路径和有日期依据的目标暴露，再研究表示稳定性和金融相关性。本轮只准备事件输入，没有做收益评价。

## 计划—实际

[P0–P4 台账](../results/news_macro_tech_20261115/plan_execution.csv)记录逐项执行。[候选目录输入](../results/news_macro_tech_20261115/candidate_events.json)、[构建回执](../results/news_macro_tech_20261115/catalog_build_receipt.json)、[逐条来源索引](../results/news_macro_tech_20261115/source_evidence_index.csv)、[点选页回执](../results/news_macro_tech_20261115/preference_ui_receipt.json)及[机制草案](../results/news_macro_tech_20261115/analyst_drafts.json)分别绑定下列数量、版本、检索时间与输入。所有数量只描述本次有界事后检索。

| 阶段 | 计划 | 实际与证据 | 状态 |
| --- | --- | --- | --- |
| P0 · 绑定范围并保留现有工作 | 获取既有协作分支，读取共享约束与状态，将旧十篇从当前主线转为历史工程材料。 | 本地分支快进至远端基线 `9e81ba3`，无关未跟踪内容保留。原十篇仍是 `LEGACY_ENGINEERING_DEV`；LM Format Enforcer token-prefix 兼容性失败继续标为 `PAUSED_LEGACY_BACKEND`，未改写成抽取成功。 | 完成 |
| P1 · 发现真实候选事件 | 在 2026-05-15 至 2026-11-15 的有界窗口内，六类各最多两张，核验官方/申报原件、合并同一事件、记录时间与版本局限。 | 得到 **12 张开发候选卡**、**12 个不同底层事件簇**、**24 条带具体原件定位的简短事实**（每卡两条），六类各两张。审计发现 G01 的 6 月 14 日卡曾混入 6 月 17 日里程碑；已改为 6 月 14 日英国官方声明中当时可知的两项事实，避免把后续事实提前。 | 候选发现完成；非正式准入 |
| P2 · 制作简单点选页 | 每卡展示日期、事实、未知项和科技传导假设；仅需点选是否关注、1–5 分相关度或跳过、优先对象，无默认选择。 | 已建立本机 12 卡点选页及[一页说明](../docs/news_research/macro_tech/preference_review_start.md)。每卡三题，无文字栏、无预填答案；页面初始保存偏好 **0/12**、事实 gold **0**。已做静态/脚本检查；浏览器视觉验收 **NOT_RUN**。 | 页面已建；用户点选待做 |
| P3 · 分开事实、预期与机制 | 使用四个独立对象；有带时点的公布前共识才计算 surprise；只写两条条件式示例，不启动新模型批次。 | [契约](../docs/news_research/macro_tech/event_contract.md)分别定义 `FACT_CARD`、`EXPECTATION`、`CHANNEL_HYPOTHESES`、`TECH_EXPOSURES`。F02、T01 两条 `AI_ANALYST_DRAFT` 均至多两跳，含反向情景及待观测信号；[12 项聚焦契约检查](../results/news_macro_tech_20261115/contract_validation.log)通过。12 张卡均没有已核验的公布前共识，也没有独立观测到的精确首次公开时刻；未计算 surprise、上涨概率或金融标签。 | 设计与草案完成；无推理/金融运行 |
| P4 · 发布与交接 | 更新中英进度和脱敏元数据，推送既有分支并远端回读，制作小型交接 ZIP。 | 逐项审阅后将 28 个相关文件以提交 [`38b5363`](https://github.com/wuzhuheng007-spec/financial-text-embedding-stability/commit/38b53633f035422ae1acb44d8cfa4892dde8eaf6) 推送至既有分支；远端分支 SHA 一致，关键文件经 GitHub 回读。本机可转发文件夹及 ZIP 已建立，ZIP CRC 通过。私人选择、原始授权新闻、无关未跟踪文件、Slack 和 Overleaf 均未纳入。 | GitHub 推送/回读及本机交接完成 |

## 得到的有限结论

本轮选择了[美国劳工统计局](https://www.bls.gov/news.release/archives/cpi_11102026.htm)、[美联储](https://www.federalreserve.gov/newsevents/pressreleases/monetary20260916a.htm)、[BIS](https://www.bis.gov/media/documents/bis-guidance-may-31-2026.pdf)、[能源部](https://www.energy.gov/articles/energy-department-supports-fercs-call-pjm-protect-ratepayers-and-build-more-reliable-power)、[财政部](https://home.treasury.gov/news/press-releases/sb0653/)、[SEC 申报](https://www.sec.gov/Archives/edgar/data/1769628/000176962826000357/crwv-20260807.htm)及公司投资者关系页面中的具体原件；[双语目录](../docs/news_research/macro_tech/candidates.zh.md)与[来源登记](../docs/news_research/macro_tech/SOURCE_REGISTER.md)列出每张卡的证据与边界。例子：[BLS 10 月 CPI 报告](https://www.bls.gov/news.release/archives/cpi_11102026.htm)给出季调环比 CPI-U 0.4%、核心 CPI 0.3%；[就业报告](https://www.bls.gov/news.release/archives/empsit_11062026.htm)给出新增非农 16.5 万、失业率 4.1%。这些是该版本公布值，没有公布前共识就不能称为“超预期”。[美联储压力测试](https://www.federalreserve.gov/newsevents/pressreleases/bcreg20260624a.htm)是**假设情景**，不是已实现银行损失。[CoreWeave 8-K](https://www.sec.gov/Archives/edgar/data/1769628/000176962826000357/crwv-20260807.htm)中的 5.50 个百分点是特定 SOFR 贷款的利差，不是总借款利率。

筛选依据是事件类别与原始来源，不是事件后的股价涨跌。但手工挑选的 12 张事后开发卡仍不是连续、代表性的金融事件面板。BLS 解禁时间或 Fed 标注的发布时刻，与本项目独立观测到的首次公开时刻分开；后者 12 张均未知。12 张也都缺少有时点依据的公布前共识，因此 `actual − consensus` 尚不能计算。机构判断、公司意向、分析员推断与实现结果必须分开。ticker 只是候选证券 ID，历史公司—证券关系尚未核验。

## 下一步

1. 用户用[本机点选页](../docs/news_research/macro_tech/preference_review_start.md)筛出感兴趣事件；偏好不作事实 gold。
2. 对入选材料独立裁定事实、时间、预期、传导边与公司关系；需要公司层结论时补有日期依据的身份链。
3. 在产生金融标签前，制定连续的事前事件准入规则，并冻结发布时间、交易日历、价格调整、退出与共同样本协议。
4. 来源与相关性门槛明确后，才设计小型事实抽取原型；旧解码器失败仍保留在历史工程记录。

**状态进度：** 候选发现 `██████████ 12/12 开发配额`｜用户偏好初始状态 `░░░░░░░░░░ 0/12`｜事实 gold `0`｜新宏观模型调用 `0`｜金融标签、拟合、预测及收益评价 `NOT_RUN`。12 张只是工作配额，不是统计充分性标准。
