# 新闻情绪因子试点 v2：真实文本评分与有限开发检验

执行日期：2026-11-17（Asia/Shanghai）。本轮只使用保存的 2025-04-01 至 2026-03-31 NVDA、MU 官方财报附件。它们是此前已接触的训练期材料；官方披露语气不是完整媒体新闻档案，也不等于报道真实、未来收益方向或可交易信号。2026 年 4—7 月价格标签未读取或重新评分。

## 可回读的实际结果

| 项目 | 实际 | 证据 |
| --- | --- | --- |
| 来源 | 8 个独立财报事件，NVDA 与 MU 各 4 个；每事件一个 SEC 附件，主申报不重复计权 | [冻结输入](../results/news_sentiment_factor_v2_20261117/freeze_redacted.json)、[来源登记](../results/news_sentiment_factor_v2_20261117/literature_register.json) |
| 评分 | 163 个完整叙述块，LM 词典与固定 FinBERT 都实际运行；FinBERT 11 批、失败 0、首次缓存命中 0 | [预检](../results/news_sentiment_factor_v2_20261117/score_preflight_redacted.json)、[运行回执](../results/news_sentiment_factor_v2_20261117/score_run_redacted.json) |
| 候选 | F1 事件均值 8/8 可计算；F2 与同公司此前两个同类事件比较，4/8 可计算 | [因子覆盖](../results/news_sentiment_factor_v2_20261117/factor_coverage_redacted.json) |
| 价格对齐 | 仅回顾性公告日期代理；1 日标签 8 个、5 日标签 6 个，另 2 个因公司行动排除 | [评价回执](../results/news_sentiment_factor_v2_20261117/development_evaluation_redacted.json) |
| A/B/C/D 量价与文本配对拟合 | **0 次**：独立事件 8 个，低于预先冻结的 30 个门槛，且每公司仅 4 个，低于各 10 个门槛 | [配置](../configs/news_sentiment_factor_v2_20261117.json)、[因子覆盖](../results/news_sentiment_factor_v2_20261117/factor_coverage_redacted.json) |

LM 将每块的词典正负词数差除以英文词数；FinBERT 保存三个类别分数，并用 positive 减 negative。事件 F1 对块等权平均，F2 扣除同公司此前两个同类事件的 F1 平均。不存在的历史基准保持缺失。完整 163 行评分、8 行事件因子及逐事件价格对齐均留本机私人目录，公开回执仅含哈希与汇总。

按事先固定的描述性规则，F1 与 1 个交易日开盘至开盘收益的合并 Pearson 值为 LM −0.555、FinBERT −0.491（均为 8 个事件）；5 日分别为 −0.036、+0.073（均为 6 个事件）。F2 的 1 日最多 4 个、5 日最多 3 个，低于预设 6 个的相关性门槛，故不计算。小事件数与重复使用的训练材料使分块区间极不稳；这些数值不支持显著性、因果、收益或推广结论，也不允许看结果后翻转情绪方向。**由于 A/B/C/D 没有拟合，无法回答文本相对量价或公告数量是否有增量。**

严格历史首见时间没有独立证据。代理规则在公告日期之后等待一个完整美股常规交易日，再从下一交易日开盘进入；退出取其后 1 或 5 个交易日开盘。使用保存的未复权开盘价，窗口内有已登记拆股或分红日期则排除。该保守代理不证明当时内容版本、实时可获得性或真实可执行性。2026 年 3 月观察到的 LM 词典版本也未证明在 2025 年每个信号日可用。没有计算有推广意义的截面 IC。

## 来源、许可和自审

文献与工具身份在[来源登记](../results/news_sentiment_factor_v2_20261117/literature_register.json)逐项记录：[Zhou 作者稿](https://kangyingzhou.com/files/JMP_KangyingZhou.pdf)、[Ke–Kelly–Xiu 作者稿](https://carey.jhu.edu/sites/default/files/2023-01/finance-conference-june-2019-dacheng-xiu-paper-min.pdf)、[Heston–Sinha 联储稿](https://www.federalreserve.gov/econresdata/feds/2016/files/2016048pap.pdf)、[LM 官方词典](https://sraf.nd.edu/loughranmcdonald-master-dictionary/)及 [ProsusAI/finbert 模型卡](https://huggingface.co/ProsusAI/finbert)。没有将论文中的收益数字当作本项目目标。FinBERT 固定旧 revision `54bddcea2cca580dd1df6a88d33242dcf4c61a71`，权重 SHA 和 `0=positive, 1=negative, 2=neutral` 均核对；模型权重标注为 CC-BY-NC-SA-4.0，本轮只在本机研究。Polymarket 仅登记未来核查方案，没有数据。

来源原件与下载哈希核对；8 个 SEC 附件 URL 本轮重新打开，确认分别是对应公司的财报附件。事先要求的“不看收益先做语义核对”**没有完成**：实际先查看了描述性相关性，随后才对固定顺序每事件前两个较长段落做 16/163 块同团队来源抽查。[事后抽查](../results/news_sentiment_factor_v2_20261117/source_spot_review_redacted.json)标为 `CODEX_REVIEW_AFTER_OUTCOME`，发现管理层意见、反事实调整口径和同段混合正负信息；没有用该抽查修改输入、分数或方向，也不能补称盲审。余下 147 块尚无逐块语义审阅，严格研究准入仍未通过。

运行中还发生一次实际软件错误：隔离推理环境缺 `bs4`，在零模型调用前将导入移入来源提取函数；重新生成的输入 ID/文本哈希与冻结版完全一致，见[修订回执](../results/news_sentiment_factor_v2_20261117/postfreeze_import_fix_redacted.json)。首次价格预检误将半日交易从原始条数中排除，保留[失败回执](../results/news_sentiment_factor_v2_20261117/evaluation_attempt1_failure_redacted.json)后改为读取训练期全部 251 个日线对象，其中 248 个完整交易日参与对齐；没有读取后期价格对象。原失败不是额外模型实验。评分回执中的 1.563 秒只覆盖模型加载后的推理循环，不代表含加载的进程总耗时。

[未来影子评分入口](../scripts/news_sentiment_factor_v2/shadow.py)为本机英文原文版本准备独立记录：首次本机收到时间、实际评分完成时间、权重与规则版本、输入哈希和待成熟结果。它没有启动持续任务；中文金十原文不会直接送入英文 FinBERT，没有合格的新影子收益标签。门户、采集与既有已拒绝财务输入保持原状态。软件测试、真实来源抽查、真实模型推理和价格描述性检验分别列在[计划—实际台账](../results/news_sentiment_factor_v2_20261117/plan_execution.csv)。
