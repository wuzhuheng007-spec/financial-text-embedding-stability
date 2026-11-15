# 市场观察：观察池 v2 与独立行情推送门槛

执行日期：2026-11-15（Asia/Shanghai）；既有分支 `codex/sync-research-progress-20261113`，起点 `5950bf963e90027788f044908f30ff7ace79ea45`。数字和状态分别绑定[计划—实际台账](../results/watchlist_live_20261115/plan_execution.csv)、[观察池配置](../configs/market_observer_watchlist_v2_20261115.json)、[证券映射回执](../results/watchlist_live_20261115/security_mapping_redacted.json)、[先前宽权限回执](../results/watchlist_live_20261115/authorization_failure_redacted.json)、[有界连接回执](../results/watchlist_live_20261115/live_probe_redacted.json)与[NO_FEED 服务回执](../results/watchlist_live_20261115/service_lifecycle_redacted.json)。私人行情、新闻正文、账户标识、凭据和有数据的页面均留在本机。

## 目标与实际结果

目标是固化你关注的科技股、存储、航天及 ETF，并把独立服务接到只读行情推送。**单标的 WebSocket 在美股休市时完成了有界连接；开市更新与持续采集尚未验证。** 先前含 `quote:write`、`trade:read` 的 WebAPI 令牌已被拒绝并丢弃。用户随后同意继续授权流程，新取得的令牌实际仅含 `quote:read` 与脱敏账户选择项，已通过 DPAPI 在本机加密保存。一次配额 GET 返回 HTTP 404，账户限额仍为 UNKNOWN。初版适配器没有识别服务端不带请求 ID 的 `ret_code` 控制帧；完成结构诊断和修复后，两次有界 TQQQ 运行均通过认证并收到订阅确认。私人库新增一条去重后的报价，但来源时刻在周五，延迟状态未知；周日美国市场状态码为 `11`（[官方枚举：`AFTER_HOURS_END`](https://open.moomoo.com/api/quote/naming-dictionary)）。没有验证开市更新、完整一分钟柱、新预测或市场结果。原 Codex MCP 的 `quote:read` 凭据没有挪用。

## 计划—实际

| 阶段 | 计划 | 实际 | 状态 |
| --- | --- | --- | --- |
| P0 基线 | 核对旧分支并保留私人数据 | 本地与远端起点均为 `5950bf9`；旧回执和无关未跟踪文件保留。 | 完成 |
| P1 观察池 | 分开公司、证券、ETF 与指数 | 六组共 20 个展示对象、19 个拟观察美股证券。`GOOG` 是 Alphabet 的未订阅类股别名；`KR.000660` 与 `US.SKHY` 不拼接。见[配置](../configs/market_observer_watchlist_v2_20261115.json)。 | 完成 |
| P2 身份 | 官方核实新增代码 | moomoo `quote_stock_basicinfo` 只读调用 2 次，返回 9 条精确匹配（美股 8、韩国 1）；新 9 条当前映射导入私人库，加上旧 12 条共 21 条。`US.SPCX` 对应 SpaceX；海力士 ADR 与韩国原股分开。见[回执](../results/watchlist_live_20261115/security_mapping_redacted.json)。 | 当前身份完成；历史身份未完成 |
| P3 授权 | 取得仅 `quote:read` 的独立 WebAPI 凭据和配额 | [先前宽权限令牌](../results/watchlist_live_20261115/authorization_failure_redacted.json)已丢弃。新令牌实际仅含 `quote:read` 与脱敏账户选择项，使用 DPAPI 本机加密保存。配额 GET 一次返回 HTTP 404，账户配额 UNKNOWN。见[有界连接回执](../results/watchlist_live_20261115/live_probe_redacted.json)。 | 只读凭据完成；配额未明 |
| P4 采集 | 官方推送、有界验收、订阅台账 | 初次结构探测暴露不带请求 ID 的 `ret_code` 控制帧，适配器已修复。后两次单标的有界运行均完成认证，`US.TQQQ` 订阅确认码 0。最终运行状态为 `CONNECTED_MARKET_CLOSED`，美国市场码 11（`AFTER_HOURS_END`）；新增一条来源时间较旧且延迟 UNKNOWN 的报价。最终取消订阅获得确认，各次运行均停止，当前活跃订阅 0。[有界回执](../results/watchlist_live_20261115/live_probe_redacted.json)；独立的[NO_FEED 回执](../results/watchlist_live_20261115/service_lifecycle_redacted.json)。 | 休市连接通过；开市更新待验 |
| P5 页面 | 显示分组、时效、来源、判断与结果 | 旧保存数据页面通过 Edge 1440px／390px 检查；新增报价后的私人停机页面也在 Chrome 1440px 与模拟 390px 打开，无全页横向溢出。页面显示 `STOPPED_BY_USER`／`DISCONNECTED`、旧来源时刻、分开的接收上界及 UNKNOWN 延迟。最终页面另将 `US AFTER_HOURS_END (11)` 标为上次供应商信号，注明信号时间未单独记录；这不是连接中的页面验收。见[初次](../results/watchlist_live_20261115/visual_acceptance_redacted.json)、[探测后](../results/watchlist_live_20261115/visual_dashboard_probe_v3_redacted.json)及[最终页面](../results/watchlist_live_20261115/visual_dashboard_poststop_v4_redacted.json)回执。 | 保存及停机页面展示完成 |
| P6 评价 | ETF 实际收益与公司行动安全门槛 | TQQQ 只用自身行情，不用 `3×QQQ`。拆股、分红、ADR 比例、汇率或覆盖证据不足时，不把潜在收益写成成熟标签；韩国原股与美国 ADR 分流。见[代码](../scripts/market_observer_v1/price_safety.py)。 | 软件完成；真实标签 NOT_RUN |
| P7 交付 | 定向测试、中英记录、同步和交接 | 控制帧修复及配额回执门槛加入后的[软件测试日志](../results/watchlist_live_20261115/focused_tests.log)为 66 项通过，含合成检查，不是市场实验。写作本报告时，GitHub 同步与脱敏交接仍待完成。 | 进行中 |

[SpaceX 官方上市公告](https://ir.spacex.com/updates/releases-details/2026/Space-Exploration-Technologies-Corp--Announces-Closing-of-Initial-Public-Offering-Including-Full-Exercise-of-Underwriters-Option-to-Purchase-Additional-Shares-2026-RgoR-Y1Vwh/default.aspx)支持 2026-06-12 起使用 `SPCX`。[SK hynix 投资者页面](https://www.skhynix.com/ir/UI-FR-IR03/)区分 `000660` 普通股和 `SKHY` ADR，说明 1 份 ADR 对应 10 股普通股。[Alphabet 10-K](https://www.sec.gov/Archives/edgar/data/1652044/000165204426000018/goog-20251231.htm)支持 `GOOG`、`GOOGL` 同一发行人；其 `GOOG` 上市起始日与 moomoo 当前基本信息相差七天，历史日期暂不裁定。供应商本次未返回具名交易所、报价币种或 ISIN；发行人来源提供的字段另行注明。当前映射不能回填历史。

[ProShares 官方 TQQQ 页面](https://www.proshares.com/our-etfs/leveraged-and-inverse/tqqq)给出的是**每日**目标杠杆，不能替代 TQQQ 实际收益。没有带日期的官方 ETF 持仓权重，就不做加权传导。财报的实际值、上期值、指引与公布前共识分开；没有可靠共识就不宣称“超预期”。存储产品价格按 DRAM、NAND、HBM 等具体品类处理，新闻事实、分析假设和市场涨跌不互相充当证明。

## 当前运行状态与下一步

拟订阅美股顺序：`TQQQ、SOXX、SPY、QQQ、AAPL、MSFT、NVDA、AMZN、GOOGL、META、TSLA、SNDK、MU、SKHY、SPCX、AMD、INTC、CRWV、AVGO`。**两次成功的有界运行各确认一条 `US.TQQQ` 订阅；停止后活跃订阅 0。** 私人库当前有 21 条证券信息映射、旧保存金十快讯 40 个版本、1 条隔离的旧报价探测记录，以及本轮新保存的一条去重 WebSocket 报价。该报价来源时刻为 `2026-11-13T22:00:00.063Z`；记录的本机接收上界为 `2026-11-15T04:51:38.144527Z`，并非经独立证实的精确到达瞬间。延迟标志 `UNKNOWN`。服务端市场状态帧报告美国市场码 11（`AFTER_HOURS_END`），最终运行状态为 `CONNECTED_MARKET_CLOSED`。完整合格一分钟柱仍为 0，正式结果标签未生成。[NO_FEED 回执](../results/watchlist_live_20261115/service_lifecycle_redacted.json)与[有界连接回执](../results/watchlist_live_20261115/live_probe_redacted.json)记录不同阶段。当前无服务进程运行。

在仓库根目录使用以下已验证命令启动、查询、停止**本机保存数据页面**：

```powershell
python -m scripts.market_observer_v1.cli start --config configs/market_observer_v1.watchlist_v2.example.json
python -m scripts.market_observer_v1.cli status --config configs/market_observer_v1.watchlist_v2.example.json
python -m scripts.market_observer_v1.cli stop --config configs/market_observer_v1.watchlist_v2.example.json
```

上述公开命令配置明确为 `NO_FEED`；有界 live 探测配置与凭据留在本机。[live 模板](../configs/market_observer_v1.live.template.json)尚不能直接作为 19 标的启动配置：配额接口返回 HTTP 404，账户容量、保存条件和开市时效仍待核实。moomoo [OAuth 官方说明](https://open.moomoo.com/api/overview/getting-started)指出令牌 `scope` 是实际授权范围；[WebSocket 认证说明](https://open.moomoo.com/api/quote/push/auth)要求独立令牌，MCP 单次报价不能替代。下一步先澄清配额接口和行情权限，再于美股常规交易时段做一次有上限的探测，核验来源时间与延迟状态，最后按真实配额决定能够订阅的标的数量。复权分钟柱和正式判断评价仍待完成。本轮无交易、持仓／余额／订单查询、自动训练、正式金融标签或回测。
