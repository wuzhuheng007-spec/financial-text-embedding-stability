# moomoo 云端切换清单（尚未启动；收到供应商答复后才执行）

当前状态（2026-11-19）：云端 `PROVIDER_GATE_MOOMOO=PENDING_SUPPORT_REPLY`，没有云端 moomoo 授权和采集。本机 Windows 采集照常运行。

## 0. 先核对答复（每项要有原文依据，不凭推断）

| 核对项 | 通过条件 |
|---|---|
| 答复覆盖的账户主体 | 与 app 中"我的 → 设置 → 协议"显示的主体一致 |
| 云服务器调用 WebAPI 行情 | 明确允许从租用的云服务器（美国硅谷）调用 `quote:read`，仅本人使用 |
| 设备 / 会话 / 客户端限制 | 写明能否同时存在两个 OAuth 客户端、两个 WebSocket 会话；新授权是否使旧授权失效 |
| 额度 | 推送订阅和 REST 日线额度是否在两个客户端之间共享 |
| 地域、IP | 美国数据中心 IP 是否允许 |

任一项不明确，就不启动，并补问。

## A. 答复允许本地和云端同时运行

1. 服务器：执行 P6 授权（你本人通过 SSH 隧道完成登录和验证），确认 `QUOTE_READ_AUTHORIZED`。
2. 仍然只保留一个 moomoo 写入方：本机切到 `feed_personal.ps1 -FeedProfile jin10_only`，云端才启动 feed。云端和本机的缓存是两份独立数据，不能双写。

## B. 答复不允许同时运行（默认按这种情况处理）

1. 本机：`feed_personal.ps1 status`，记录 PID 和最近一次推送时间。
2. 本机：`feed_personal.ps1 stop`，确认进程已退出，并确认 moomoo 连接已经释放。连接释放的判断方式包括：
   - 本机 `provider_state` 中 moomoo 为 `DISCONNECTED/USER_STOP`；
   - 若 moomoo 提供查询会话的方式，也按其方式确认。
3. 本机：`feed_personal.ps1 start -FeedProfile jin10_only`，确认金十正常、本机没有 moomoo 线程。
4. 服务器：你本人完成授权（P6）以及任何必要的验证码或设备确认。
5. 服务器：**这一步不撤下等待提示**。先用隔离配置做一次小批量试采：1 个代码、限时 10 分钟、写入隔离库。检查推送、来源时间、币种和延迟属性。
6. 试采通过后，在正式库启动 feed，订阅 6 个自选，检查行情卡、报价曲线和 `session_quotes` 增长。
7. 数据验收通过（6 个代码都有带来源时间的报价，且没有第二个写入方）之后，**才**把 `PROVIDER_GATE_MOOMOO` 设为空，重建 web 容器，页面显示"已接通"。

## 失败处理

- 第 5 或第 6 步失败：停止云端 feed，保留 `PROVIDER_GATE_MOOMOO`。如有需要，可新增门控代码，在页面写明失败状态和时间。
- 本机执行 `feed_personal.ps1 stop`，再执行 `feed_personal.ps1 start`（默认 full），恢复本机 moomoo。
- 如果 Windows 令牌已失效，由你本人重新登录。
- 不撤下等待提示，不显示部分数据为"已接通"。
