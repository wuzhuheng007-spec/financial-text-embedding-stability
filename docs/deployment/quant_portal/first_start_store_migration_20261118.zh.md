# 云端首次启动：个人缓存库并发迁移（2026-11-18，北京时间）

所有输入都是合成数据：合成账户 `owner@local.invalid`，合成的过期令牌，不使用任何真实供应商凭据，不挂载开发源码。
Windows 生产采集（`61b5f44`）没有改动，一直在运行。

## 1. 谁会同时打开 `personal_feeds.sqlite3`

| 打开方 | 位置 | 是否写库 | 首次启动时是否可能并发 |
|---|---|---|---|
| feed 主线程 | `cli.run` 第 113 行，先于两个采集线程 | 是（迁移和选择） | 是，与 web 并发 |
| moomoo、金十线程 | `collector.run_moomoo` / `run_jin10` | 是 | 正常入口下不会互相并发，因为主线程已先迁移；但线程异常处理 `launch.wrapped` 会再次打开 |
| web | `core/views.py` 的三个 POST 视图（行情选择、新闻关注、事件偏好），gunicorn 2 个 worker，每个 2 线程 | 是，每次请求都打开 | **是**。用户在首次启动期间操作页面即触发 |
| 一次性命令 | `acceptance_synthetic.py seed-feed`、`backfill_v15.py`、运维 run | 是 | 是 |
| research、daily-price、健康检查 | 只读 `mode=ro` | 否 | 不参与迁移 |

`web`、`feed`、`research`、`daily-price` 共享同一个 `personal_var` 卷。

## 2. 基线错误（修复前，保留原始日志）

`_initialize()` 原来是"先检查、后写入"，而且没有加锁：

| 现象 | 位置 | 原因 |
|---|---|---|
| `duplicate column name: …` | `ALTER TABLE provider_state ADD COLUMN` | 两个打开方都看到某列不存在，然后都去添加 |
| `UNIQUE constraint failed: owner_binding.singleton` | 插入 `owner_binding` | 两个打开方都看到没有绑定，然后都去插入 |
| `database is locked` | `PRAGMA journal_mode=WAL` | 新文件切换到 WAL 需要排他锁；这条语句冲突时 SQLite 直接返回，不走 busy 等待 |

| 复现方式 | 失败数 / 分母 |
|---|---|
| Windows，4 进程 × 2 线程，空库，12 轮（54c464f 干净副本） | 11 / 96（重复列 7，owner_binding 1，WAL 锁 3） |
| Linux 容器（旧镜像 c421eaf，store 与 54c464f 相同），空库 12 轮 | 24 / 96（WAL 锁 21，重复列 3） |
| Linux 容器（旧镜像），旧结构库升级 12 轮 | 29 / 96 |
| 全栈首次启动（旧镜像）：feed、research 启动的同时，8 个已登录会话并发 POST，另有一次性容器并发打开 | web **HTTP 500：1 / 1017** |

## 3. 修复（commit `64632d8`）

- 把建表、owner 绑定、补列、计数回填放进同一个 `BEGIN IMMEDIATE` 事务。后来的打开方在 `busy_timeout`（10 s）内等待写锁，拿到锁后看到的是已完成的结构。
  - `executescript()` 会先自动提交当前事务，所以改为逐条执行。
- 只对 `PRAGMA journal_mode=WAL` 的 `locked` 错误做有限重试，最长 10 s。
- 真正的错误仍然回滚并上报：库已绑定其他 owner（ValueError）、文件不是数据库（DatabaseError）、锁始终不释放（10 s 后报 `locked`）。

## 4. 验收

| 场景 | 证据层级 | 结果 |
|---|---|---|
| 空库并发首次打开 | Windows 单元测试 + Linux 容器 | 0 / 96 失败；结构与参考完全一致；owner 绑定 1 行 |
| 已有旧结构库并发升级 | Windows 单元测试 + Linux 容器 | 0 / 96 失败；补齐了列；原有数据不变 |
| 初始化后再次启动 | Windows 单元测试 + Linux 容器 | 8/8 + 8/8 成功；结构和数据不变 |
| 真实错误仍上报 | Windows 单元测试 + Linux 容器 | 其他 owner 被拒绝；非数据库文件报错；持续锁约 14 s 后报错 |
| 两个采集线程在全新库上直接竞争（不预建库） | Windows 单元测试 | 金十成功轮询，moomoo 有限重试后为 `DISCONNECTED / ConnectionError` |
| 全栈首次启动 | **容器实测**（候选镜像） | web 1007/1007 返回 200；一次性容器 344 次并发打开 0 失败；最终结构一致，owner=1，WAL |
| 门户健康、单写入方、正常停止 | **容器实测** | web/feed/research 健康；代理 readyz 200；第二个采集进程被拒绝（"already runs"）；只有一个 feed 容器；全部 `Exited (0)` |
| moomoo 暂时性错误 | **容器实测**（测试覆盖层见下） | 过期合成令牌 → 无出网 → 3 个子进程各重试 3 次（共 9 次 CONNECTING→DISCONNECTED）→ 监督器宽限后重启 2 次；全程没有 `AUTH_EXPIRED` |
| moomoo 授权失败 HOLD | **容器实测** | 删除令牌后进入 `RUNNING_HOLD`，30 s 内不重启 |
| 配额 HOLD | 容器内部分实测 + 单元测试 | 用镜像内真实 store 写入配额停止状态，镜像内 supervisor 判为 HOLD。WebSocket 返回配额码这一段只有单元测试 |
| 门户 Django 测试（personal_feed、timeline） | 容器内（候选镜像） | 9/9（需 `DEBUG=1`；生产设置下测试客户端被 HTTPS 重定向为 301，新旧镜像相同） |

测试覆盖层：Windows Docker Desktop 把文件 secret 挂载成 0777，服务器令牌存储按设计拒绝这样的密钥。
所以 moomoo 容器测试用 `compose.keyfile-test.yaml`，只把 feed 的 `MOOMOO_TOKEN_KEY_FILE` 指向隔离卷里 0600 的测试密钥。Linux 服务器上 secret 保留宿主文件权限，不需要这个覆盖层。

## 5. Windows `test_restart_limit_then_hold` 偶发失败（单列，未修）

- 机制：测试线程在读 `state.json` 时，监督器线程执行 `state.tmp → state.json` 替换。Windows 不允许替换正被打开的文件，监督器线程因 `PermissionError [WinError 5]` 退出，重启计数达不到 3。
- 失败记录：
  - c421eaf：8 次中失败 2 次；此前还有一次整文件循环卡住超过 8 分钟后被终止。
  - 64632d8：Windows 20 次中失败 1 次（第 19 次，日志保留），每次超时上限 120 s。
- 对 Linux 候选镜像：同一测试在镜像内运行 30 次，30 次通过，每次超时上限 120 s。Linux 的 rename 可以覆盖已打开的文件，目前没有证据显示会影响 Linux。
- 不建议用重复运行通过来掩盖；如需修，应改测试读取方式或监督器写入的重试，单独评审。

## 6. 上线和回退

- 生产 Windows 尚未上线本提交。生产库早已迁移完成，正常启动不受这个竞争影响；在维护窗口和令牌刷新补丁一起替换 `store.py` 即可。
- 云端首次启动：用 `64632d8` 或之后的镜像，把本机库在线备份恢复进卷之后再启动 research（见快照保留说明）。
- 回退：恢复 `store.py` 到 `54c464f` 版本。修复没有改变表结构，也不需要迁移数据。
