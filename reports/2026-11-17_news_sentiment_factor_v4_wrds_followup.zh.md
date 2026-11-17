# 新闻情绪因子 v4：WRDS 登录后权限核验补充回执

核验完成于 2026-11-17 14:26（Asia/Shanghai）。本文件是 [v4 原报告](2026-11-17_news_sentiment_factor_v4.zh.md) 的追加证据；原始配置、请求、新闻版本、评分和回执均未覆盖。计划与实际见[独立台账](../results/news_sentiment_factor_v4_wrds_followup_20261117/plan_execution.csv)，数字和页面原文见[脱敏回执](../results/news_sentiment_factor_v4_wrds_followup_20261117/access_receipt_redacted.json)。

在用户完成现有 WRDS 账户登录后，打开官方 [RavenPack RPA 1.0 Dow Jones and PR Equities 查询表](https://wrds-www.wharton.upenn.edu/pages/get-data/ravenpack-news-analytics-40/rpa-10-equities/rpa_1_0-DJPR-edition-equities/)。页面明确显示该查询表不可访问、不能提交查询，并列出所需产品 `ravenpack_dj`。这是**该查询表的实际权限拒绝**；其他 RavenPack 版本没有在拒绝后继续探测，故不能说整个 RavenPack 产品线均不可用。产品目录可见不等于获授权。

本次共打开 4 个 WRDS 顶层页面（主页、数据目录、RavenPack 产品页、查询表），保守地把 4 次都计入冻结的供应商请求预算。连同原 v4 的 9 次 moomoo 请求，预算记账为 **13/30**；WRDS 子预算 **4/9**。浏览器加载的底层 HTTP 子请求数不可观测。提交查询 **0** 次、取得英文新闻 **0** 条、真实 LM/FinBERT 评分各 **0** 次、合格前瞻评分 **0** 条。没有读取收益标签或运行收益拟合。

已用原账户会话打开本机[个人页面](http://127.0.0.1:8765/#panel-research-sentiment)：折叠的研究情绪面板显示 30 个已观察文章版本、0 个英文标题候选、0 个前瞻版本和 0 条评分。[单时点服务回执](../results/news_sentiment_factor_v4_wrds_followup_20261117/runtime_readback_redacted.json)显示门户、个人采集、金十、moomoo 推送与快照仍在运行；旧自动解释保持暂停。此次回查不把页面有数据误写成 RavenPack 数据已接入。

下一步的主要阻塞是**可授权取得英文新闻标题或正文，并能核对其历史版本时间**。若选 WRDS，需由现有机构按其流程开通[查询表所列的 `ravenpack_dj` 产品](https://wrds-www.wharton.upenn.edu/data-dictionary/ravenpack_dj)；即使开通，还要另行核查实际输出字段是否含可用于本地 LM/FinBERT 的英文文本和适用的保存条件。本轮没有申请、购买或使用新权限。
