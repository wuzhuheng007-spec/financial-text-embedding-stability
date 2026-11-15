# moomoo + 金十只读市场观察 v1：开发进度

执行日期为 2026-11-15（Asia/Shanghai），在既有 `codex/sync-research-progress-20261113` 分支开展。[逐项计划—实际](../results/market_observer_v1_20261115/plan_execution.csv)、[运行配置](../configs/market_observer_v1.example.json)与[证据索引](../results/market_observer_v1_20261115/evidence_index.json)绑定本报告的数字和输入版本。原始行情、新闻正文、账号材料和有数据的本机页面留在私人目录。

## 研究目标与本轮边界

目标是保存有来源的报道、只读行情、评价窗口开始前已持久化的判断和到期结果，使抽取错误与市场判断错误分别可审。**本轮交付本机工程原型和一次有界、已认证的 moomoo 查询；持续行情推送、真实事前判断、到期结果、自学习效果及正式金融研究准入均未成立。** 未交易，未读取持仓、余额或订单。

## 计划—实际

| 阶段 | 计划 | 实际及证据 | 状态与偏差 |
| --- | --- | --- | --- |
| P0 基线 | 核对分支、约束、shortfacts v2 与时间配置 | 本地和远端起点均为 `90e293e`；无关文件保留。旧六段 Qwen v2 仍仅是开发材料。[台账](../results/market_observer_v1_20261115/plan_execution.csv)。 | 完成 |
| P1 moomoo | 限定授权、读取工具、核证券并取一笔报价 | 保留原金十配置，用户仅批准 `quote:read`。新启动的原生 Codex CLI 发现 moomoo 工具；本桌面会话未动态加载。一次 `quote_stock_basicinfo` 返回 12 条匹配的美股证券，一次 `quote_stock_quote` 返回一条 `US.NVDA` 报价。[连接回执](../results/market_observer_v1_20261115/moomoo_connection_receipt.json)、[能力表](../results/market_observer_v1_20261115/moomoo_capability_matrix.json)。 | 部分完成：持续推送和账户配额未核实。 |
| P2 采集与存储 | 实现可恢复、受预算限制的私人服务 | 已实现单实例、启动／状态／停止、检查点、缺口、版本化 SQLite 与本机页面。`NO_FEED` 进程启动、心跳、停止；该进程未调用来源，合格的一分钟行情柱仍为 0。12 条官方证券代码与原候选行另表关联；上周五报价只存为有界探测记录，不作合格行情柱。离线回放既有金十快讯 40 个版本，保留分页不全和两条本机缺口记录。[服务](../results/market_observer_v1_20261115/service_lifecycle_redacted.json)、[证券及探测导入](../results/market_observer_v1_20261115/moomoo_post_probe_import_redacted.json)、[回放](../results/market_observer_v1_20261115/jin10_offline_replay_redacted.json)。 | 部分完成：需独立推送凭据、权限、配额和保存条件。 |
| P3 来源核验 | 区分原报道、独立证据、转载和冲突 | 已实现分版本报道和事实层，以及官方确认、独立交叉、同源转载、单源、冲突、未核实状态。既有金十快讯**没有**因此升级为独立核实事实。[来源登记](../docs/news_research/market_observer/source_access.md)、[软件日志](../results/market_observer_v1_20261115/focused_tests.log)。 | 部分完成：未对新事件做真实跨来源裁定。 |
| P4 判断与结果 | 固化事前判断和美股常规时段 30 分钟评价 | 不可改写的预测／结果记录和“持久化之后首根完整一分钟柱”规则通过合成检查；缺柱、跨收盘、延迟和价格口径不一致分开标记。真实判断与结果均为 0。[评价代码](../scripts/market_observer_v1/evaluation.py)、[测试](../tests/test_market_observer_v1_eval.py)。 | 部分完成：实际日历及价格／复权协议仍待冻结。 |
| P5 学习队列 | 分类错误、保留回滚路径 | 已实现审阅队列及授权、可用时间、标签来源门槛；抽取正确性与价格变动分开。无训练、模型晋升或新模型调用。[学习代码](../scripts/market_observer_v1/learning.py)。 | 部分完成：尚无实时事件反馈及独立人工裁定。 |
| P6 验收同步 | 聚焦检查、页面检查、脱敏交接 | 合并软件检查 37 项通过；其中模块检查有重叠，不是 37 次市场实验。已用保存数据在 1440×900 和精确 390×844 CSS 视口打开页面，全页无横向溢出。[测试](../results/market_observer_v1_20261115/focused_tests.log)、[视觉回执](../results/market_observer_v1_20261115/visual_acceptance_redacted.json)。 | 部分完成：未达到推送权限门槛，故 15 分钟实时验收未运行。 |

## 实际查询能说明什么

固定 12 个代码的一次证券信息查询、核实后 `US.NVDA` 的一次报价查询，业务结果均成功，分别返回 12 条和 1 条。报价自身数据时间为 `2026-11-13T21:00:00Z`；本轮在上海时区周日执行，因此这条上周五报价不能证明有持续更新。响应没有单独延迟标志，账户行情延迟状态仍未知。封装程序只机械记录了整段 CLI 会话的起止时间；单个工具调用时间未被机械记录，回执标为不可用。原始 `initialize`、`tools/list` RPC 回执亦不可得；104 个工具绑定来自新 CLI 可见注册表，而非已归档的原始工具列表。[脱敏回执](../results/market_observer_v1_20261115/moomoo_connection_receipt.json)。

moomoo 官方 [MCP 快速入门](https://open.moomoo.com/mcp-docs/quick-start)及[授权说明](https://open.moomoo.com/mcp-docs/authentication)支持端点和权限范围；单独的 [WebSocket 行情订阅](https://open.moomoo.com/api/quote/push/subscribe)仍需独立认证、账户行情权限与订阅额度。MCP 一次读取成功不能替代持续推送验收。账户适用的[行情权限](https://open.moomoo.com/api/overview/api-authority)和[订阅配额](https://open.moomoo.com/api/quote/quotas/subscription)尚未实测。金十连续轮询和留存条件亦未确认，所以只使用已保存的私人数据。

## 使用与下一步

在仓库根目录执行 `python -m scripts.market_observer_v1.cli start --config configs/market_observer_v1.example.json`，将 `start` 改为 `status` 或 `stop` 即可查询和停止。示例配置是 `NO_FEED`，可以查看服务和旧数据，**不会持续采集实时行情**。本轮验收进程已停止，见[服务回执](../results/market_observer_v1_20261115/service_lifecycle_redacted.json)。[公开空白页面](../results/market_observer_v1_20261115/dashboard_public_template.html)不含授权正文。

下一步先核实一种可用于独立服务的只读推送认证方式、账户权限、额度、保存条件和证券订阅规则；冻结真实美股日历、价格及复权口径后，在开市时做有界连接验收。金十轮询需先确认调用条件。只有未来在评价窗口前采集并持久化的新事件，才能形成真实事前判断，随后以到期数据做描述性评价。旧六段开发材料和本轮保存数据回放不能证明普遍抽取能力、因果 alpha 或盈利能力。正式 M5 准入、收益标签和回测仍为 `NOT_RUN`。
