# 科技财报、宏观与重大新闻输入进度｜2026-11-15

时区：Asia/Shanghai。开发材料的截止时点固定为 **2026-11-15 07:47:54 +08:00**（UTC 2026-11-14 23:47:54）。输入提交、任务包哈希和精确截止时刻见 [`intake_config.json`](../results/news_earnings_macro_20261115/intake_config.json)。原件是在截止时点之后从官方页面或存档取得的当前副本，不能当作发行当时网页的冻结快照。

## 目标与计划—实际

目标是为“重大新闻＋宏观指标＋六家科技公司财报＋条件式连锁影响”建立可核查的开发输入，供后续时间安全的金融实验使用。

| 阶段 | 计划 | 实际 | 状态 |
| --- | --- | --- | --- |
| P0 | 核对包、仓库并冻结截止时点 | ZIP CRC、10/10 包内哈希通过；沿用协作分支，保留无关未跟踪文件 | 完成 |
| P1 | 六家财报与同口径比较 | 六张卡、72 个必需字段检查：57 个原文值、3 个有公式的派生值、12 个明确缺口；核验 35 份公司来源原件；36 项限定增长比较中计算 32 项 | 完成，有缺口 |
| P2 | 三张宏观卡和最多六条独立公司新闻 | 完成三张官方宏观卡、六条公司事件；市场观察表为空 | 完成，有缺口 |
| P3 | 本机三栏页面与传导示例 | 生成财报／宏观／新闻页面和两条 `AI_ANALYST_DRAFT` 条件路径；未做浏览器视觉验收 | 部分完成 |
| P4 | 原件回读、测试、双语同步 | 六份输入草稿、41 份来源原件及结果一致性回读通过；四项针对性测试通过；远端同步在核验后另行记录 | 报告创建时进行中 |

逐项证据见 [`plan_execution.csv`](../results/news_earnings_macro_20261115/plan_execution.csv)；数字由 [`assembly_receipt.json`](../results/news_earnings_macro_20261115/assembly_receipt.json)、[`source_manifest.json`](../results/news_earnings_macro_20261115/source_manifest.json) 和 [`growth_calculations.json`](../results/news_earnings_macro_20261115/growth_calculations.json) 绑定输入版本与来源。这些是开发覆盖数，不是正式研究样本数。

## 有限结果

六张卡对应 NVIDIA FY2027 Q2、AMD、Intel、CoreWeave、三星电子及 SK 海力士的 2026 Q3。逐字段值、单位、财期、GAAP／调整口径、定位和哈希见 [`company_cards.json`](../results/news_earnings_macro_20261115/company_cards.json)、[`earnings_metrics.jsonl`](../results/news_earnings_macro_20261115/earnings_metrics.jsonl) 和 [`coverage.csv`](../results/news_earnings_macro_20261115/coverage.csv)。官方财报入口：[NVIDIA](https://nvidianews.nvidia.com/news/nvidia-announces-financial-results-for-second-quarter-fiscal-2027)、[AMD](https://ir.amd.com/news-events/press-releases/detail/1303/amd-reports-third-quarter-2026-financial-results)、[Intel](https://www.intc.com/news-events/press-releases/detail/1786/intel-reports-third-quarter-2026-financial-results)、[CoreWeave](https://investors.coreweave.com/news/news-details/2026/CoreWeave-Reports-Strong-Third-Quarter-2026-Results/default.aspx)、[三星材料](https://images.samsung.com/is/content/samsung/assets/global/ir/docs/2026_3Q_conference_eng.pdf)、[海力士披露](https://www.sec.gov/Archives/edgar/data/2120882/000119312526434994/d162547d6k.htm)。

各公司 12 项必需字段中有来源值的数量依次为 12、12、12、10、8、6；其余保留空值和原因。三星与海力士的 Q3 材料注明**初步、外部审阅未完成**。CoreWeave 的公司定义资本支出包括租赁取得资产，不能与现金购置固定资产混写；Intel 的自由现金流为调整后指标。四项增长率因前期基数非正而未计算。韩国材料部分比较来自同一表格，但调整口径未单独说明，故派生增长率仍属指示性描述。已算出的 32 项不代表“超预期”或股价收益。

宏观卡来自 [美国 10 月 CPI](https://www.bls.gov/news.release/archives/cpi_11102026.htm)、[10 月就业报告](https://www.bls.gov/news.release/archives/empsit_11062026.htm) 和 [10 月 28 日美联储声明](https://www.federalreserve.gov/newsevents/pressreleases/monetary20261028a.htm)，分开保存实际值、当时前值与修订，见 [`macro_cards.json`](../results/news_earnings_macro_20261115/macro_cards.json)。六条独立公司新闻见 [`news_events.json`](../results/news_earnings_macro_20261115/news_events.json)。[本机展示页](../results/news_earnings_macro_20261115/dashboard.html)及两条条件式传导草稿仅用于字段和解释框架检查；视觉验收未运行。

## 尚未完成与下一步

没有带时间戳的合格公布前共识，因此预期差、beat/miss 都**未计算**。多数材料的首次实际可见时间仍未知；历史公司—证券身份链、价格／日历／调整／退出及共同样本协议尚未接受。CoreWeave 11 月 12 日 10-Q 中的债务值不能倒填为 11 月 10 日财报发布时已知。正式金融准入、此新输入上的模型推理、收益标签、预测及回测均为 **NOT_RUN**。

下一步需要取得真正的时点来源和共识快照、补全有日期依据的历史身份链并确定完整协议，然后做有界公司事件抽取质量检查。旧公司新闻模型失败记录和 M5 准入边界没有因本轮卡片而改变。原始材料留在本机忽略目录；公开部分只含短引用值、自写摘要、来源定位及校验信息。
