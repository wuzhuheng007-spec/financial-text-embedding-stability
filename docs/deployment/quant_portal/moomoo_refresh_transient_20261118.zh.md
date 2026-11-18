# moomoo 令牌续期：暂时性故障改为有限重试（2026-11-18，北京时间）

本说明已脱敏，只记录时间、状态、错误类别和指纹前缀，不含令牌、授权码或账户标识。

## 三条事实，分开记录

1. **旧生产版本的真实续期成功。** 生产 checkout `61b5f44`（未打本补丁）在 11-18 13:35:12 自动续期成功，无人工干预。
   - 旧到期 13:42:25，新到期 15:35:12；令牌文件指纹前缀 `A85CA6EF4DD4` → `ED140E1840E2`。
   - 续期后第一条业务记录：13:35:12.985 写入 `session_quotes`（TQQQ OVERNIGHT）。
   - 这说明正常续期路径可用，与本补丁无关。
2. **异常分支补丁只通过了隔离测试。** 网络错误、超时、HTTP 429 和 5xx 的处理只在合成测试里验证过，没有遇到过真实的供应商故障，也没有在生产运行过。
3. **上午故障的底层异常未知。** 11-18 10:58:38，moomoo 线程在网络恢复前 43 ms 内被记为 `AUTH_EXPIRED / AUTHORIZATION`，此后永久退出。
   - 当时的原始异常没有保留：stderr 已被覆盖，`error_class` 只有 `AUTHORIZATION`。
   - "网络错误被当成授权失败"是可能的解释，但**未证实**。本补丁修的是这一类错误归类，并不证明上午就是这个原因。

## 改动

| 文件 | 改动 |
|---|---|
| `scripts/market_observer_v1/moomoo_webapi_oauth.py` | 新增 `OAuthTransientError(OAuthError)`。令牌端点遇到 URLError、超时、OSError、HTTP 429 或 HTTP ≥500 时抛出它。400、401、403、439 等其他 HTTP 码和非法响应仍是 `OAuthError`。 |
| `scripts/market_observer_v1/moomoo_ws.py` | `connect()` 和 `_refresh_if_due()` 把 `OAuthTransientError` 转成 `ConnectionError`，走采集器原有的有限重连（`max_reconnect_attempts`，指数退避）。其他 `OAuthError` 仍转成 `AuthorizationError`。 |
| `deploy/linux/supervisor.py` | `classify_feed` 改读真实缓存库的列名 `last_error_class`，旧测试夹具里的 `error_class` 仍兼容。原来读不到这一列，配额（`SUBSCRIPTION_QUOTA`）和 `USER_STOP` 识别不到，配额停止的线程可能被重启。 |

因为 `OAuthTransientError` 是 `OAuthError` 的子类，现有的 `except OAuthError` 行为不变，包括 v44/v45 的 REST 调用、support 和 probe。REST 路径的 439 规则不经过这段代码，保持原样。

## 行为对照

| 情况 | 补丁前 | 补丁后 |
|---|---|---|
| 续期时网络不可用、超时、429 或 5xx | 一次就记为 `AUTH_EXPIRED / AUTHORIZATION`，线程永久退出；Linux 监督器 HOLD | 按 `max_reconnect_attempts` 次（生产配置为 3）有限重连。用尽后记为 `DISCONNECTED / ConnectionError`，`worker_alive=0`。Windows 上线程结束，其他线程不受影响；Linux 监督器等 180 s 宽限后重启进程，退避，每小时最多 6 次，超过后 HOLD |
| 令牌端点返回 400、401、403 或 439 | `AUTH_EXPIRED`，停止 | 不变：一次就停止，监督器 HOLD |
| 订阅配额耗尽 | 线程以 `RATE_LIMITED / SUBSCRIPTION_QUOTA` 停止；Linux 监督器因列名问题可能误判为可重启 | 线程行为不变；监督器 HOLD |
| moomoo 故障期间的金十线程 | 独立线程，继续运行 | 不变，有测试覆盖 |

## 测试

- 新增 `tests/test_moomoo_refresh_transient.py`，覆盖错误映射：7 种暂时性错误，4 种拒绝（400、401、403、439），以及 connect 和定期刷新。
- 新增 `tests/test_collector_refresh_transient.py`，用真实采集器循环和真实缓存库（合成数据，无网络），覆盖重试次数、耗尽后的状态、监督器判定，以及 moomoo 故障时金十仍成功轮询。
- 日志放在私人交付目录，不入库，路径见 PR 说明。

## 另行发现，本次未改

`PersonalFeedStore._initialize` 先检查列是否存在，再 `ALTER TABLE ADD COLUMN`。如果两个线程同时打开一个**全新**缓存库，可能报 `duplicate column name`。新测试里约 4/15 次复现。

生产库早已迁移完成，不会遇到。云端新卷第一次启动时可能导致某一线程退出，由监督器在 180 s 后重启。建议单独修：启动线程前先初始化一次，或者忽略重复列错误。

## 上线步骤（维护窗口内，需另行授权）

1. 本机：记录当前 feed PID、`provider_state` 和 `session_quotes` 的行数和最大时间（只读）。保留 `moomoo_webapi_oauth.dpapi` 及其备份。
2. 选择在令牌刚续期之后、距下次续期还有超过 30 分钟的时间点，正常停止 feed。确认没有残留写入进程（单写入方）。
3. 把本提交的 `moomoo_webapi_oauth.py` 和 `moomoo_ws.py` 带到生产 checkout：cherry-pick，或 `git apply` 交付补丁，先 `--check`。
4. 用原命令 `apps/quant_portal/ops/feed_personal.ps1` 启动，stderr 改为追加写或带时间戳的文件，以保留下次的底层异常。
5. 验收：moomoo 和金十都进入 CONNECTED；`session_quotes` 继续增长；下一次自动续期后到期时间前移，状态不是 `AUTH_EXPIRED`。
6. 云端：等 PR 合并、重新构建镜像之后，按购机后的验收清单执行，不在本轮。

## 回退步骤

1. 正常停止 feed。
2. 把这两个文件恢复到生产原版（`61b5f44`：`moomoo_webapi_oauth.py` 哈希前缀 `4a66265ba5a8384b`，`moomoo_ws.py` 哈希前缀 `7d7c61a47b7a1312`），或 `git revert` 本提交。
3. 用原命令重启，确认 moomoo 进入 CONNECTED。
4. 令牌文件和缓存库都不需要回退：补丁不改存储格式，也不改表结构。
