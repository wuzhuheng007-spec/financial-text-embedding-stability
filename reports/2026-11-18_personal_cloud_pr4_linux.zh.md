# PR #4 第二轮：重启恢复、Linux 容器适配与隔离联调（收尾版）

执行时间 2026-11-18 06:41–08:30（Asia/Shanghai）。电脑 06:21:54 由用户重启；本轮未再重启电脑。PR 起点 `61b5f44f2cd67ba353c4d494ba1aeffcbf972fe0`。生产运行目录 `financial-text-embedding-stability-sync-20261113`（分支 `codex/sync-research-progress-20261113`，HEAD 同上）本轮**未改代码**。所有检查的命令、退出码、配置、输入类型和日志见 [verification_matrix.csv](../results/personal_cloud_pr4_linux_20261118/verification_matrix.csv)（42 行，42 行退出码与预期一致）。

## 验收类别（不可互相替代）

| 类别 | 含义 | 本轮状态 |
|---|---|---|
| SYNTHETIC | 合成数据或合成账户经过真实代码路径 | 通过（W1–W2、L1–L4、S1–S12、B1–B11） |
| REAL_MODEL_SYNTHETIC_INPUT | 真实评分模型，输入为合成中文文本 | 通过：4 个版本、9 个分段 DONE；**不是金融有效性检验** |
| LOCAL_RUNTIME_READONLY | 本机真实服务的只读状态 | 通过（P1–P5），短时观察 |
| REAL_SUPPLIER | 容器内真实供应商连通 | **BLOCKED**：Linux 无 moomoo 授权（S8 返回 `NOT_INSTALLED`）；金十云端许可未取得 |
| REAL_RETURN_LABEL | 真实收益标签 | 未运行：无到期标签 |
| CLOUD | 云服务器部署 | 未运行：未购买 |

## 1. 本机重启恢复（5 项业务、4 个进程）

服务计数口径：门户（进程 1）、金十与 moomoo 推送（同一个采集进程 2 的两个线程）、v42 中文研究（进程 3）、v45 日线（进程 4），共 **5 项业务、4 个进程**。重启后 5/5 项业务、4/4 个进程全部停止；本轮各启动一次，5/5 恢复。按设计**不启动**：v16 旧 AI 解读队列（`PAUSED_FOR_V17`）、moomoo REST 快照线程（HTTP 439，`SECURITY_POLICY_BLOCKED`）。

修改前用 SQLite 在线备份接口备份 8 个私人库，`quick_check` 与 `integrity_check` 全部 ok，完整 SHA-256 见 [runtime_restart_redacted.json](../results/personal_cloud_pr4_linux_20261118/runtime_restart_redacted.json)。账户 1、自选 6、设置 1、历史事实卡 14 的一致性摘要在结束时与基线相同；门户启动按既有逻辑新增 1 条邀请记录。

| 时间 UTC | 观察 |
|---|---|
| 22:48 | 金十最近成功 22:48:55，缓存 412；moomoo 推送 22:48:54；v42 积压 7→0 |
| 23:23 | 门户 readyz/CSS 200；金十 23:23:01，失败累计保持 22；缓存 419；REST 仍阻塞 |
| 00:18–00:20 | 矩阵 P1–P5 退出码 0：门户 RUNNING、采集 RUNNING、v42 RUNNING、v45 RUNNING、分析 PAUSED_FOR_V17 |

重启后无新 SQLite 锁（最后一次 2026-11-17T22:06:18Z）。重启期间 22:21–22:43 UTC 的金十缺口没有补抓，分页衔接仍 `POSSIBLE_GAP_NO_PAGE_OVERLAP`。所有者浏览器视觉验收 `NOT_RUN`（未使用所有者密码）；以回滚事务测试客户端验证所有者页面 200、第二账户 404。

## 2. 开机启动与异常恢复（检查结果，未重启电脑）

见 [boot_recovery_redacted.json](../results/personal_cloud_pr4_linux_20261118/boot_recovery_redacted.json)。

- **Windows 本机**：任务计划、Run 键、启动文件夹、Windows 服务中都**没有**门户、采集、v42、v45 的开机启动项，进程整体退出后也没有外部守护进程。唯一相关的任务 `QuantFirstSnapshotReadback20261118` 是 05:17 一次性只读回执（结果 0，无下次运行）。Docker Desktop 有 Run 键，但其设置 `AutoStart=False`，开机 20 分钟后引擎未运行。
- **进程内恢复**：采集的两个供应商互不连带；moomoo 最多重连 3 次（带退避），金十锁冲突按 60/120/240/300 秒退避；v42 对任务错误标记重试或失败，非暂时性评分错误进入 `ERROR_PAUSED` 并退出；v45 捕获循环错误后继续。
- **Linux 容器（S9）**：向 web、feed 发送 SIGTERM，向 research 发送 SIGINT，三者各自被 `unless-stopped` 重启一次（RestartCount 0→1），3.2 秒内恢复 healthy。`docker kill -s KILL` 后容器**未被自动重启**（Docker 视为手动停止），手动 `up` 后 6.3 秒恢复。宿主或守护进程重启 `NOT_RUN`。

## 3. Docker 与 Linux 引擎

首次检查：WSL2 正常，Docker 只有客户端。启动 Docker Desktop 后 18 秒就绪；`wsl --status`、`wsl -l -v`、`docker version`、`docker info`、`docker compose version` 退出码全 0，`OSType=linux`，Engine 29.6.2，内核 6.18.33.2-microsoft-standard-WSL2。见 [docker_checks_redacted.json](../results/personal_cloud_pr4_linux_20261118/docker_checks_redacted.json)。

## 4. 代码变更

- `deploy/linux/`：白名单构建（上下文 2.17 MB；app 321 MB、research 1.71 GB），前台入口与业务健康检查，Compose（db 无端口、backend internal、research 无网络、仅 feed/daily-price 出站），Caddy，隔离测试叠加层，合成验收脚本，复跑脚本 `verify_matrix.py`，运行手册。
- `scripts/quant_personal_feed/owner.py`：PostgreSQL 下经 Django ORM 解析 owner；Windows 保持只读 SQLite 查询。
- `moomoo_webapi_oauth.py`：`ServerFileTokenStore`（AES-256-GCM，独立密钥文件，拒收 DPAPI 密文与非 `quote:read` 范围）与平台工厂；服务器授权经 SSH 转发回环回调。采集、日线、REST 模块改用工厂，Windows 行为不变。
- `settings.py`：仅 `/healthz/`、`/readyz/` 豁免 HTTPS 跳转。
- `configs/quant_portal_personal_feeds_linux_v1.json`：云端默认**关闭金十**，直到金十书面确认（见第 6 节）；测试配置两个供应商都关闭。

隔离联调中发现并修复 5 个 Linux 运行时缺陷：migrate 在只读根目录建目录失败；健康探针 Host 不在 ALLOWED_HOSTS（400）；gunicorn 控制套接字写只读 `/app`；v41 目录属主错误；`/readyz/` 被 HTTPS 跳转。

## 5. 隔离容器验收（逐项）

| 检查 | 矩阵行 | 退出码 | 输入 | 结果 |
|---|---|---|---|---|
| Windows 单元测试 | W1、W2 | 0、0 | 合成夹具 | 通过 |
| 镜像私人文件扫描 | L1 | 0 | 构建镜像 | 无匹配 |
| Linux 测试套件（40 pytest + 10 v45） | L2 | 0 | 合成夹具 | 通过 |
| v43 日度窗口/快照/见证/标签 | L3 | 0 | 合成临时库 | 13/13 |
| Compose 配置 | L4 | 0 | 测试 env | 通过 |
| 启动 db/migrate/web/proxy/feed/research | S1 | 0 | 测试卷 | healthy |
| 容器内生产标志 / check --deploy | S2、S3 | 0、0 | 运行环境 | DEBUG 关、PERSONAL_MODE 开；仅 2 条 HSTS 建议 |
| 业务健康 web/feed/research | S4a–c | 0 | 测试卷 | feed 为 `NO_PROVIDER_ENABLED`，未测供应商业务 |
| HTTPS 登录、隔离、静态资源 | S5 | 通过 | 合成账户 | 第二账户 404；Caddy 本地 CA（非公网信任） |
| 单实例 | S6 | 1（预期 1） | 测试卷 | 第二个采集被锁拒绝 |
| 合成数据去重 | S7 | 0 | SYNTHETIC 行 | 再次写入全部 DUPLICATE |
| Linux 凭据状态 | S8 | 0 | 无真实凭据 | `NOT_INSTALLED` → 真实供应商 BLOCKED |
| 异常重启 | S9 | 通过 | 信号 | 见第 2 节 |
| 持久化 | S10–S12 | 0 | 测试卷 | 计数一致，不重复评分 |
| 备份恢复到另一项目 | B1–B11 | 0 | 测试库备份 | 计数一致，恢复后可登录 |
| 本机只读状态 | P1–P5 | 0 | 真实本机 | 见第 1 节 |
| 测试结束停止 | Z1、Z2 | 0 | — | 测试容器已停，测试卷保留 |

真实模型（修订 `e91b1a3af10e1e8c9c03429d3cd7d5e9a1c8000d`，6 个文件按清单哈希核对）在无网络容器中对 4 个合成版本产生 9 个分段评分，评分后内存约 254 MiB。新测试队列没有冻结的 `association_revisions`，计划快照被拒绝生成（失败即停）。桌面 1440 与 390px 截图为合成账户，无横向溢出。

## 6. 供应商云端条款与真实授权

原文链接、引文和判断见 [supplier_cloud_terms_20261118.zh.md](../docs/deployment/quant_portal/supplier_cloud_terms_20261118.zh.md)。要点：moomoo 开发文档写明 Linux 云服务可安全保存 `refresh_token` 并自动刷新；用户条款限定行情仅个人使用、禁止再分发（适用主体待用户在账户中确认）。金十用户协议限定资料“仅可作为私人和非商业用途而保存在某台计算机内”，且未经书面授权不得“在非金十数据APP所属的服务器上做镜像”，因此**云端金十采集在取得书面确认前保持关闭**。缺少的授权与逐步命令列在该文档末尾。

## 7. 候选服务器容量（只读估算）

候选：阿里云轻量应用服务器，新加坡，4 vCPU、8 GiB、70 GiB 系统盘、Ubuntu 22.04，用户页面报价 ¥156/月（未核实结账价，未购买）。计算依据见 [disk_sizing_redacted.json](../results/personal_cloud_pr4_linux_20261118/disk_sizing_redacted.json)。

- **CPU/内存：足够。** 实测各容器合计 <1 GiB（research 评分后 254 MiB，其余均 <130 MiB）。
- **磁盘：前约 5 个月足够，长期取决于保留策略。** 固定占用约 17 GB（系统 5、Docker 0.5、镜像 2.8、升级时新旧镜像并存 2.8、服务器端构建临时 4、模型 0.4、日志 1.35、PostgreSQL 0.1）。采集缓存实测 04:28–08:22 增长 35.6 MB/h；`session_quotes` 近 24 小时 175,616 行、**代码中没有保留上限**，约 0.10 GB/天；原始响应 48 小时裁剪，约 0.35 GB 稳态。备份 gzip 实测压缩比 6.6，按保留 7 份计：第 30 天约 24 GB，第 90 天约 37 GB，第 180 天约 55 GB（接近 80% 警戒线）。
- **条件**：在本机或其他机器构建镜像后 `docker load`，或构建后清理构建缓存；备份压缩并把旧备份移出服务器；磁盘 70% 告警；第 150 天前为 `session_quotes` 增加保留策略（本轮未改，属于后续代码项）。

附带发现：本机 C 盘 97% 已用（剩余约 12 GB），Docker 构建缓存约 60 GB；本机采集库同样按上述速率增长，需要用户决定是否清理构建缓存（会影响其他项目的缓存，本轮未清理）。

## 8. 前瞻研究（只读）

首快照 `fa8cb76334c94f7560aab543c99cf9f9` 未改动：`ON_TIME_SCHEDULED`，计划截止 2026-11-18 05:15，信号可用 05:16:05.265738（上海）；15 个公司行；4 个 N1 非缺失：NVDA、AMZN、SK hynix（SKHYNIX）、Samsung Electronics（SAMSUNG_ELECTRONICS）；首窗 `PARTIAL_INITIAL_WINDOW`。标签队列 30 项：26 `WAIT_ENTRY`（最早入场 2026-11-18T14:30:00Z，最晚到期 2026-11-24T21:00:00Z）、4 `BLOCKED_SECURITY_MAPPING`；合格收益标签 0。重启没有错过截止，下一截止 2026-11-19 05:15。本轮未生成价格或标签，未改因子、未拟合、未读受限期标签。

## 9. 验收状态

| 范围 | 状态 |
|---|---|
| 本机 5 项业务恢复 | 已恢复，短时观察；无开机自启、无崩溃后守护 |
| Docker / Linux 引擎 | 通过（需手动启动 Docker Desktop） |
| Linux 代码适配 | 完成；真实凭据接口已实现但未用真实凭据运行 |
| 隔离容器（合成） | 42/42 检查符合预期 |
| 真实模型（合成输入） | 运行通过；非金融有效性 |
| 真实供应商（容器） | BLOCKED：moomoo Linux 授权缺失；金十云端许可缺失 |
| 真实收益标签 | 未运行：无到期 |
| 云端部署 | 未运行：未购买 |

计划—实际台账：[plan_execution.csv](../results/personal_cloud_pr4_linux_20261118/plan_execution.csv)。
