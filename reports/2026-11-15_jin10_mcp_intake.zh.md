# 金十 MCP 有界接入与指引时间修复：开发进度

2026-11-15，**Asia/Shanghai**。本报告对应既有 `codex/sync-research-progress-20261113` 分支和新建的 `news_jin10_20261115` 开发结果。[计划—实际台账](../results/news_jin10_20261115/plan_execution.csv)逐项列出证据、偏差和下一步。旧[财报组装截止时点](../results/news_earnings_macro_20261115/assembly_receipt.json)为 `2026-11-14T23:47:54.6036273Z`，本轮新数据另标版本。

## 目标与实际完成

研究目标是及时发现宏观与六家科技公司（NVIDIA、AMD、Intel、CoreWeave、Samsung Electronics、SK hynix）的重大消息，保存来源及接收时点，待协议确定后再研究公司级传导和金融结果。本轮完成连接、可恢复采集与可追溯展示的准备，尚不证明新闻覆盖完整、历史证券身份、预测质量或金融有效性。

编辑前核对了远端协作分支与参考提交 `3620fc97f707ea6a5ff8818b092900e9dbacd14c`，未重置历史，也未动无关未跟踪文件和旧原始结果。核对 Codex CLI `0.147.0`，将金十 URL 与 `JIN10_MCP_TOKEN` 环境变量配置写入 `~/.codex/config.toml`，保留原有 `node_repl`。较早的[配置基线](../results/news_jin10_20261115/configuration_baseline.json)检查时，当前进程及 User 环境没有 Token。后续[连接状态回执](../results/news_jin10_20261115/connection_status.json)确认 User 环境及新子进程能读取，已运行的 Codex 桌面进程未继承，也未热加载金十工具。下述直接协议认证成功；桌面重启后的连接器认证仍 **NOT_TESTED**。仓库外已备隐藏输入脚本，报告不包含凭据。

[金十官方 MCP 指南](https://mcp.jin10.com/app/doc.html)给出了快讯和资讯示例，但示例中的 ID、游标不能代替本机 `tools/list` 和真实响应；公开指南没有列日历工具，也未列明频率、配额或留存期。下述实际工具清单包含 `list_calendar`。[用户协议](https://flash.jin10.com/agreement)及[版权声明](https://www.jin10.com/about/index.html)限制未经授权的复制和再分发。[来源权限登记](../docs/news_research/jin10/source_rights.md)把 MCP 专项存储及展示权列为待确认。本轮未启动定时轮询或无人值守 24 小时任务。

新代码在 SQLite 中分开保存原始 MCP 页面、逻辑记录、追加版本和每次获取，设置请求预算、重叠去重、分页重放断点、候选公司/事件标签及采集错误状态。HTTP 客户端实现 `initialize`、`notifications/initialized`、`tools/list`，后续参数应由服务端实际 Schema 决定。代码见 [`scripts/jin10_mcp/`](../scripts/jin10_mcp/) 与 [`scripts/jin10_mcp_http.py`](../scripts/jin10_mcp_http.py)。最终[聚焦 pytest 日志](../results/news_jin10_20261115/final_validation_v2.log)为 **29/29 通过**，[原页面回归](../results/news_jin10_20261115/legacy_dashboard_regression.log)为 **4/4 通过**，本轮变化的 Jin10 Python 文件还通过 `py_compile`。这些是软件检查，真实连接探测和离线导入另列证据；早期指引日志中的六项检查与聚焦测试重叠，测试运行也不是独立研究实验。

[指引时间 v2](../results/news_jin10_20261115/guidance_v2/README.md)读取旧[六家公司卡](../results/news_earnings_macro_20261115/company_cards.json)，不覆盖旧结果。[覆盖回执](../results/news_jin10_20261115/guidance_v2/guidance_coverage_v2.json)计 **19 条指引**，与旧[组装回执](../results/news_earnings_macro_20261115/assembly_receipt.json)的 **72 项核心指标检查**分列。Intel 三条 `first_available_at` 置空，Q4 日期明确标为财年规则推断；三星和海力士各两条只补官方页面**日期**，未猜时间。19 条均缺独立首次可见证据。[验证日志](../results/news_jin10_20261115/guidance_v2/guidance_validation.log)记录六项聚焦合成检查及官方来源回放。

[新版页面模板](../results/news_jin10_20261115/dashboard_v2_template.html)把旧冻结的财报、宏观卡与采集状态、日历和快讯区域分开显示；公司关联仍是候选，来源报道、核实事实、模型假设及市场观察分层。带正文的本机页面已从 Git 忽略目录中的私有数据库生成。[静态验收](../results/news_jin10_20261115/dashboard_static_validation_v2.json)记录：40 条导出快讯中显示 **12 条关键词相关候选**，213 条已捕获日历中页面按上限显示 **50 条**；这不是已核实公司事件或完整日历。公开模板对 40 条快讯正文的泄露检查为零。静态检查通过，**浏览器视觉验收 NOT_RUN**。

## 有限真实连接结果、缺口与下一步

在历史[配置基线](../results/news_jin10_20261115/configuration_baseline.json)之后，用户通过本机隐藏输入填写 Token。新子进程完成 `initialize`（HTTP 200）、`notifications/initialized`（202）、`tools/list`（200），协商协议为 `2025-11-25`，列出八个工具，包括 `list_flash` 和 `list_calendar`。这证明**该进程中认证与工具调用可行**。已运行的 Codex 桌面会话在 `ALL_TOOLS` 中仍**没有金十工具**；正常重启后的连接器认证 **NOT_TESTED**。首次探测已发出一次快讯 HTTP 请求，但本地解析器在保存页面回执前出错；[事件更正回执](../results/news_jin10_20261115/probe_initial_failure_correction.json)记录了 `AttributeError` 和原回执低报工具调用的事实，不把该页计为完成检查。

修复后的[脱敏真实回执](../results/news_jin10_20261115/probe_redacted_run2.json)绑定本机私人原始捕获：首个 `list_flash` 页面返回 **20 条**，字段为 `content/time/url`；一次 `list_calendar` 返回 **213 条**，字段为 `actual/affect_txt/consensus/previous/pub_time/revised/star/title`；按游标仅再取一个快讯页面，返回 **20 条**，字段为 `content/time/title/url`。三次工具调用的 HTTP 与业务状态均为 200。两页快讯均为 `has_more=true` 且有下一游标，因此**分页覆盖未完成**。实际快讯项未观察到独立 `id` 字段，存储须优先核验 URL，后备组合键标潜在碰撞。这些是本次返回行数，**不是不同新闻数或全库覆盖率**。日历 `consensus` 可能为空；本次获取的值不能追认成公布前快照。一次成功会话也不证明长期稳定或历史首发时间。

[结构画像](../results/news_jin10_20261115/probe_profile_run2.json)用 SHA-256 绑定私人原始文件。在这两页内，40 条快讯均有非空正文及已检查的详情页 URL 形式，无独立 ID；时间字符串有秒和时区偏移。213 条日历记录中，`consensus` 为 null 的有 **129 条**，`actual` 为 null 的有 **5 条**。工具把 `pub_time` 描述为**理论公布时间**；本次 213 个时间字符串均为**分钟精度且无时区**，故不转换为确定 UTC 时刻。上述数字只描述已捕获页面。[形状绑定回执](../results/news_jin10_20261115/shape_binding_receipt.json)保存实际 `tools/list` 和响应见证哈希，供导入器核验。

随后将已捕获页面**离线**导入 Git 忽略目录内的 SQLite。[回读审计](../results/news_jin10_20261115/store_validation_v2.json)确认保留三份原始响应、**253 条逻辑记录及 253 个初始版本／获取记录**；40 条快讯按检查过的 URL 建键，213 条日历采用标有潜在碰撞风险的组合键。253 个定位及三份原始响应哈希均回读。快讯为 `CAPTURED_PARTIAL` 且有重放断点；日历只对本次请求范围标记完成。离线导入**没有增加服务请求**。数据库与正文留本机，汇总数不是正式研究样本。

下一步不增加服务请求，待有可用且获准的本机浏览器表面时做视觉验收；仅发布审核后的无正文回执。定时频率要等账户实际配额及保存/展示条款明确。研究准入还需有日期依据的公司—证券身份、公布前共识快照、独立首次可见证据，以及日历、价格、退出和共同样本协议。正式标签、新模型推理、拟合、预测、收益与回测均 **NOT_RUN**。

| 阶段 | 计划 | 本报告实际 | 状态 |
| --- | --- | --- | --- |
| 配置 | 合并官方地址并保留其他 MCP | 已写入；新进程凭据与认证核验通过 | 新进程完成；桌面重启未测 |
| 真实连接 | 协商协议并有界取快讯、日历 | 保留首次解析失败；修复后返回 20 + 213 + 20 行 | 本轮探测完成；快讯分页部分覆盖 |
| 存储与页面 | 可恢复采集及新版展示 | 三份捕获页面已私有导入；公开／私人页面静态检查通过；视觉未运行 | 静态工程完成；视觉待做 |
| 指引时间 | 修正首见与目标财期口径 | 单列 v2 与来源回放完成 | 完成 |
| 发布 | 选择性推送与远端回读 | 待最终审查 | 未运行 |

这是开发进度。授权正文、原始 API 响应、Token、私人配置和数据库均不进入 GitHub；本轮不发 Slack、不修改 Overleaf。
