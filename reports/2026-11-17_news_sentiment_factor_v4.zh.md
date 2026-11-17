# 新闻情绪因子 v4：英文媒体新闻来源与前瞻评分试点

执行日期：2026-11-17（Asia/Shanghai）。基线 `563b8fc00e221ab7ade1fbe26e9fbdc276dc124f`；旧 v3 结果原样保留。本轮没有读取收益标签、运行收益拟合、Qwen 或交易。所有数字来自[脱敏实际回执](../results/news_sentiment_factor_v4_20261117/actual_summary_redacted.json)、[在线观察回执](../results/news_sentiment_factor_v4_20261117/online_observation_redacted.json)、[覆盖回执](../results/news_sentiment_factor_v4_20261117/coverage_redacted.json)和[冻结配置](../configs/news_sentiment_factor_v4_20261117.json)；逐条原文与响应仅在本机私人目录。

## 实际结果

| 指标 | 实际 | 口径 |
| --- | ---: | --- |
| 新供应商请求 | 9/30 | 均为 moomoo WebAPI；8 次业务成功，1 次因缺必填 keyword 失败；无自动重试 |
| 返回行／去重文章版本 | 80／30 | 当前关键词页，含重复；不能代表完整历史档案 |
| 可得正文版本 | 15/30 | 但所有保存标题、摘要及可得正文都不符合英文主输入条件 |
| 冻结的 2025 年三个窗口内文章 | 0 | 未验证历史日期筛选参数；没有通过搜索结果替换窗口 |
| 来源版本时间证据 | 30 个发布时间或更新代理；历史当时版本可证明 0 | 当前下载时间没有当成历史首见时间 |
| 经核实独立事件簇／有效公司日 | 0／0 | 30 个文章 ID 仅是待核对的分组代理 |
| 英文 LM／FinBERT 实际评分 | 0／0 | 固定权重和词典哈希已核对；真实新闻评分流程因合格英文输入为 0 未装载模型，另有单列的合成冒烟推理 |
| 前瞻合格评分／A–D 拟合 | 0／0 | 新收到版本 0；没有读取任何收益标签 |

按 NVDA、MU、MSFT 顺序探测时，moomoo `news/search` 的 `keyword` 请求可返回当前新闻，`Accept-Language: en-US` 对 NVDA 返回相同响应哈希。官方[更新日志](https://open.moomoo.com/changelog)记载 2026-10-20 的新闻搜索变化和 2026-10-26 暂不在公开文档中列出新闻端点；本轮未找到可核实的历史日期、分页和英文来源参数，因此没有猜测参数进行九个历史窗口请求。返回项有标题、摘要、部分正文、新闻 ID、来源链接及时间整数，但没有足以证明原始版本当时可见的记录。`publish_time` 被作为供应商时间代理，未冒称文章修订时间或历史首次可见时间。

冻结的三个证券 × 三个 2025 年窗口共九个单元均标为“历史查询能力未核实，未查询”，而非“该日无新闻”；纽约本地半开区间和对应 UTC 边界见[窗口覆盖表](../results/news_sentiment_factor_v4_20261117/historical_window_coverage_redacted.json)。

WRDS 的[RavenPack 产品页](https://wrds-www.wharton.upenn.edu/pages/about/data-vendors/ravenpack/)能证实产品存在；本机没有可用的程序化凭据，浏览器能打开 WRDS 数据目录，但未证明账户拥有 RavenPack 权限或可导出文章正文，故 0 次查询。Massive 的[股票新闻接口](https://massive.com/docs/rest/stocks/news)和[Benzinga 新闻接口](https://massive.com/docs/rest/partners/benzinga/news)有官方说明；本机未发现配置的 API key，故 0 次请求、未购买计划。两者访问条件和正文许可仍需具体核实，详见[来源能力表](../results/news_sentiment_factor_v4_20261117/source_capability_redacted.json)。

## 前瞻观察与工程修正

第一次 240 秒观察的 4 个轮次均在本地 OAuth 解密阶段失败，未发供应商请求；第二次在同一环境复现 1 次后立即停止。根因是研究虚拟环境缺少 Windows 凭据解密组件；两次失败回执永久保留。采集改用已验证的本机 Python，未来评分仍由隔离研究环境的子进程执行。第三次 125 秒观察发出 3 次成功请求，收到 30 行，均为旧文章版本；新增版本、英文候选与前瞻评分均为 0。这是成功轮询和去重的实际证据，不是英文新闻链路或连续前瞻评分通过的证据。已有 moomoo 行情和金十采集独立运行，旧自动可信解释队列未恢复。

私人 SQLite 将原始响应哈希、文章 ID、正文版本、发布时间代理、本机收到时间及评分时间分列；相同 ID 同内容幂等，内容变化追加版本。初版把入库时间写入了名为 `first_received_at_utc` 的字段；发现后保留原值，另增实际读取完供应商响应的时间字段，并凭请求日志为 30/30 个版本补齐。[迁移回执](../results/news_sentiment_factor_v4_20261117/response_time_migration_redacted.json)记录修正，不把入库时间冒充网络首次接收。历史回放不能标记为前瞻，合格信号的最早时间定义为评分完成时间。软件合成检查覆盖重复、修订、旧发布时间和所有者接口，不能替代供应商内容审阅。[来源审阅冻结回执](../results/news_sentiment_factor_v4_20261117/review_selection_redacted.json)的英文样本数为 0，语义审阅 `NOT_RUN`，没有以零错误宣称内容合格。

## 后续评价协议与限制

冻结的 N1 是公司日去重事件簇平均净文本语气，N2 是公司日去重事件簇数量；无新闻、实测中性、采集失败分别保存。A 量价、B 量价加 N2、C 量价加 N1、D 量价加 N2/N1 必须用相同公司日、时间划分和收益目标。主目标为信号后 1 个常规交易时段，5 个为次要目标；信号截止为纽约常规交易日 16:00，只有此前实际评分完成的文章可用，之后首个符合来源及公司行动要求的开盘为候选入场。跨划分标签须清除。当前没有合格面板，四组和日度 IC 均 `NOT_RUN`；缓存报价不能冒充可成交价格。详见[就绪回执](../results/news_sentiment_factor_v4_20261117/factor_readiness_redacted.json)。

软件检查为 4 项 SQLite／时间规则测试、2 项 Django 所有者隔离测试，以及 1 条明确标为合成的 LM／FinBERT 管线冒烟输入；最后一次均通过。合成推理不计入真实新闻评分。首次测试发现 Windows SQLite 文件未关闭，首次研究虚拟环境冒烟发现缺少 IANA 时区数据；修复及重跑次数见[修订回执](../results/news_sentiment_factor_v4_20261117/implementation_revisions_redacted.json)，[时区文件回执](../results/news_sentiment_factor_v4_20261117/timezone_data_redacted.json)记录其版本和哈希。

个人门户仅新增所有者可见、默认折叠的“研究情绪”区域，从本机私有评分库每 5 秒只读刷新。当前正确显示 0 条评分。它不是已验证预测，代码路径不会触发供应商或模型调用。已使用现有所有者登录在 Edge 实看中文、英文桌面与 390 CSS 像素响应式视图；面板显示 30 个版本、0 个英文候选、0 条评分及明确的空状态，面板没有观察到横向溢出。此项是浏览器响应式实看，未在实体手机上验收；详见[脱敏视觉回执](../results/news_sentiment_factor_v4_20261117/visual_acceptance_redacted.json)。

**唯一主要阻塞：** 尚未核实一个可按冻结历史窗口查询、且当前有英文标题与版本时间证据的授权媒体新闻源。取得 WRDS RavenPack entitlement 或已有 Massive 授权后，先做同样有界的来源试探与时间核对，再启动实际 LM/FinBERT 评分；不能拿 v3 财报语气或中文机器译文填补本轮媒体样本。
