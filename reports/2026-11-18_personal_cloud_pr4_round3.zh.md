# PR #4 第三轮：部署前修正、磁盘治理与授权链路验证

执行 2026-11-18 08:40–09:40（Asia/Shanghai）。起点提交 `d710addf970f850530b9d859e413263c1f8764ac`（远端一致，无未提交修改）。未购买服务器，未在云端部署，未启动第二套真实供应商采集，未改动前瞻快照、标签、因子或研究结果，未运行收益拟合、IC 或交易。

## 结果类别

| 类别 | 结果 |
|---|---|
| 已修正（代码/文档） | 验收脚本口径与退出码；监督进程；moomoo 授权回调路径；moomoo-only 无需金十密钥；行情保留/归档工具；v42 Windows 启动窗口；容量结论 |
| 合成流程真实通过 | 第三轮矩阵 r3 + r3b 中所有必需检查 PASS（被取代的 3 行见更正文件） |
| 模拟提供方通过 | OAuth 回调路径 5 个情形（不是 moomoo 真实授权） |
| 真实模型 + 合成输入 | LOAD-1：199 条积压约 54 s 清空 |
| BLOCKED | S8：Linux 无 moomoo 授权，真实供应商联调未执行 |
| NOT_RUN | 宿主机重启；SSH 跳转一段；云端部署；真实收益标签 |

矩阵：[r3](../results/personal_cloud_pr4_linux_20261118/verification_matrix_r3.csv)、[r3b](../results/personal_cloud_pr4_linux_20261118/verification_matrix_r3b.csv)，每行分别记录 `command_status`、`assertion`（PASS/FAIL/BLOCKED/NOT_RUN）、`required`、命令、退出码、耗时、配置、输入类型和日志。更正见 [verification_corrections_r3.json](../results/personal_cloud_pr4_linux_20261118/verification_corrections_r3.json)。

## P0 运行核实（进程身份 + 业务字段）

首次核实发现 **v42 已停止约 75 分钟**，而第二轮矩阵 P3 的状态命令退出码为 0——这正是“退出码 0 ≠ 业务正常”。日志为 `forrtl: error (200): program aborting due to window-CLOSE event`；第二次停止（00:53:58 UTC）无日志。用同参数的假子进程探测：v42 `start()` 的 `DETACHED_PROCESS` 让工作进程拥有**可见的 Windows Terminal 控制台窗口**（可见窗口 1→2），`CREATE_NO_WINDOW` 不会。机制已确认，关窗者未查明。仓库已改为 `CREATE_NO_WINDOW`；生产用同一 `start()`、仅在调用时替换标志重启（生产代码未改），工作进程 33580 的进程树无可见窗口。

最终核实（01:26 UTC）：门户 readyz 200 且 PID 匹配；金十 38 s 前成功（本轮失败累计 22→24）；moomoo 推送持续，近 10 分钟 1,805 行；v42 PID 匹配、16 s 前完成任务、积压 0；v45 PID 匹配、心跳 47 s；REST 439 仍 `SECURITY_POLICY_BLOCKED`；旧 AI 队列 `ANALYSIS_PAUSED_FOR_V17`；第二个真实采集进程 0。快照 `fa8cb76334c94f7560aab543c99cf9f9`、30 个标签项、所有者摘要均未变。见 [runtime_r3_redacted.json](../results/personal_cloud_pr4_linux_20261118/runtime_r3_redacted.json)。

## P1 验收脚本

`verify_matrix.py` 重写：命令成功与业务断言分开；BLOCKED/NOT_RUN 单列且不计为通过；任何必需检查 FAIL 时整体退出 1（首轮 r3 因 HOLD-ERROR 实际退出 1）；每行即时保存；`try/finally` 总是停止测试栈；PRE 组重建测试账户、合成数据，核对模型卷哈希，缺失状态可重新生成。B1/B7 记录参数、耗时、退出码与日志；S2 断言 8 个生产标志；S8 记为 BLOCKED。恢复计时从发信号前到健康确认：research 子进程 8.8 s，web PID 1 7.3 s；第二轮“3.2 s”保留原回执并追加更正。首轮 r3 中 REC-1（脚本自杀）、HOLD-ERROR（环境合并错误）、LOAD-1（**假通过**）已由 r3b 取代。

## P2 容量与保留

单位统一为 MiB/GiB（2^20/2^30）。在线备份副本的 dbstat：原始响应表 184.5 MiB + 主键索引 17.4 MiB；`session_quotes` 65.5 MiB + 三个索引 53.9 MiB；空闲页 0。实时文件主库 324.5 MiB、WAL 5.5 MiB、SHM 32 KiB。35.6 MB/h 与 0.10 GB/天不一致的原因：前者是盘后活跃 4 小时且原始响应尚未到 48 h 稳态；后者的 24 h 窗口包含 11-17 10:00–20:59 UTC 采集中断。没有一个完整不间断交易日的观测，所以改为情景区间：启用 14–30 天保留时约 16–30 GiB；不启用时第 180 天中/高情景约 56/98 GiB。见 [disk_sizing_r3_redacted.json](../results/personal_cloud_pr4_linux_20261118/disk_sizing_r3_redacted.json)。第二轮“约 5 个月够用”“CPU/内存足够”已撤回。

保留工具 `scripts/quant_personal_feed/retention.py`（默认 dry-run）在**隔离副本**上验证：模拟 48 h 原始响应裁剪后空出 201.9 MiB 可复用页、文件仍 326.4 MiB；按 2026-11-28、保留 8 天计选出 225,015 行（保护 24 个最新单元格行与 18 个仍有原始证据的行）；归档 12.1 MiB，文件哈希、重读摘要、恢复摘要均通过后才删除；113 批，单批写锁最长 43 ms；并发写入 731 次 0 错误、最长等待 175 ms；只对副本 VACUUM 后 4.3 MiB。研究代码（v41–v45）不读取 `session_quotes`，工具运行前自动核对。生产历史未删除，未执行生产 VACUUM。

LOAD-1（并行评分 + 备份）：200 条合成中文文本、真实模型；积压峰值 199，约 54 s 清空；研究容器 CPU 峰值 178%、均值 114%，内存峰值 277 MiB；326 MiB 副本单步在线备份 1.2 s（太短，未采到备份容器资源）。测试在 32 逻辑 CPU 的 Docker Desktop 上做一次，不能推断 4 vCPU 的容量。另：对运行中库做分步在线备份实测 186 s（写入使其反复重启），已改为建议单步备份。

## P3 授权回调

旧命令 `run --rm feed moomoo-auth ...` 没有端口映射，回调又绑在容器回环，**确实无法使用**。现在：`moomoo-auth` 服务（profile `auth`）发布 `127.0.0.1:8790:8790`，容器内绑定 0.0.0.0，`redirect_uri` 固定 `http://127.0.0.1:8790/callback`，提供方返回 `error=` 时明确拒绝。模拟提供方（容器内，断言 URL 已指向模拟后才发请求）通过：正常授权落盘为服务器加密存储；错误 state、提供方拒绝、redirect_uri 不匹配均不落盘；容器仅绑回环时主机无法到达；每次退出后端口释放、无残留容器。SSH 一跳未执行；moomoo 是否接受该回调形式未验证，若不接受改用其文档的 API Key 方式。金十令牌已从基础 Compose 移到 `compose.jin10.yaml`，moomoo-only 部署不需要 `JIN10_MCP_TOKEN_PATH`（CFG-1/CFG-2）。

## P4 无人值守恢复

`supervisor.py` 作为 feed/research/daily-price 的 PID 1：可恢复故障按 60–900 s 退避重启、每小时最多 6 次；授权失败、439、额度、显式暂停只 HOLD 不重启。实测：子进程被杀后 8.8 s 恢复（容器不重启）；无授权 moomoo 进入 RUNNING_HOLD，60 s 内 0 次重启、健康检查非零、Docker 重启计数 0；PostgreSQL 停 20 s 期间 readyz 失败而 Docker 仍显示 healthy（30 s 间隔），恢复后无重启；SQLite 队列被独占锁 25 s 后积压清空。`docker kill` 后容器保持 exited——仅为 Docker Desktop / Engine 29.6.2 上的本次观察。宿主机重启 `NOT_RUN`。可恢复的“线程停止但进程活着”只在单元测试中用假子进程验证（重启上限后 HOLD）；真实栈中只验证了授权失败型。

本机 Docker 引擎本轮两次异常：一次 `_ping` 500（数秒恢复）；一次在 Windows 绑定挂载上对 342 MB SQLite 做单个大 DELETE 时 WSL 引擎停止（重启 11 s）。之后实验改在 Docker 卷内、分批删除、限制内存。

## P5 C 盘与采购准备

Docker 虚拟磁盘 `docker_data.vhdx` 在 C 盘 90.6 GB；构建缓存约 60 GB，最大的是另一项目约 3 个月前的层；本项目今天产生且未共享的缓存约 4.3 GB。未删除任何东西，未重建大型镜像（用 devmount 测试）。可选：按 ID 删除本项目缓存（约 4.3 GB）、把 Docker 磁盘迁到 D 盘（429 GB 可用）、其他项目缓存由其负责人决定。见 [docker_disk_inventory_redacted.json](../results/personal_cloud_pr4_linux_20261118/docker_disk_inventory_redacted.json) 与 [最小云端方案](../docs/deployment/quant_portal/minimal_cloud_plan_20261118.zh.md)。最小方案为“网站 + 已授权 moomoo 行情”；云端没有获许可的新闻源时，新闻、情绪与日度快照留在本机，不同步本机金十缓存。

## 仍需用户操作

1. 决定 C 盘处理方式（上面三项之一）。
2. 在 moomoo 账户中确认适用条款主体、行情权限，以及服务器使用是否允许；服务器存在后按运行手册完成授权。
3. 在结账页确认站点、地域、套餐、域名与最终含税价格、续费方式。
4. 如需云端新闻，取得金十书面许可。
5. 选择本机与云端的行情单写入者切换时间窗口。

计划—实际：[plan_execution_r3.csv](../results/personal_cloud_pr4_linux_20261118/plan_execution_r3.csv)。
