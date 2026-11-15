# Quant Portal 容器部署阻塞记录（v2.2 研究轮次）

**记录日期：**2026-11-16，Asia/Shanghai。本文只整理[上一轮只读 Docker/WSL 回执](../../results/quant_portal_personal_acceptance_20261116/docker_diagnosis_redacted.json)；本轮没有重新探测 Docker、改变 Windows 功能、重启电脑、改动个人数据库或启动容器。个人版仍是独立的本机服务，容器验收与本轮财务文本输入 v2.2 的工程比较分开。

## 已知、未知与未运行

| 项目 | 现有证据支持的状态 |
|---|---|
| Docker CLI 与目标上下文 | 上轮看到 CLI 29.6.2、`desktop-linux` 上下文。CLI 存在不代表 Linux 引擎可用。 |
| Linux 引擎 | 上轮有界 `docker version/info` 探测未收到服务端响应并被取消；`docker info --format {{.ServerVersion}}` 约 5 秒超时。 |
| WSL2 | 上轮 `wsl --status` 报不能运行，并指向虚拟化启用说明。 |
| 处理器报告标志 | `VMMonitorModeExtensions`、`VirtualizationFirmwareEnabled`、`SecondLevelAddressTranslationExtensions` 当时均为 `true`。这不能证明 Windows 可选功能、WSL2 运行态或 Docker 引擎已可用。 |
| Windows 可选功能 | `VirtualMachinePlatform` 与 `Microsoft-Windows-Subsystem-Linux` 的准确状态 **UNKNOWN**；上轮查询需要提升权限。不能据此认定具体缺失组件。 |
| 容器构建、PostgreSQL 迁移、应用健康检查 | **NOT_RUN**。云端部署也 **NOT_RUN**。 |

微软说明 WSL2 需要 Virtual Machine Platform 等 Windows 功能；Docker 的 Windows 文档列出 WSL2 后端的系统条件。它们提供排查依据，**没有判定这台电脑的根因**：[Microsoft WSL 手动安装与要求](https://learn.microsoft.com/en-us/windows/wsl/install-manual)、[Microsoft WSL 故障排查](https://learn.microsoft.com/en-us/windows/wsl/troubleshooting)、[Docker Desktop Windows 要求](https://docs.docker.com/desktop/setup/install/windows-install/)。

## 管理员应先完成的只读检查

在用户安排的维护窗口，由具有管理员权限的人打开 PowerShell，保存以下命令的实际输出及时间；不要在输出文件中放入账户凭据：

```powershell
Get-WindowsOptionalFeature -Online -FeatureName Microsoft-Windows-Subsystem-Linux
Get-WindowsOptionalFeature -Online -FeatureName VirtualMachinePlatform
wsl --version
wsl --status
wsl --list --verbose
```

若 WSL 版本命令不受当前系统支持，以 `wsl --status` 和 Windows 版本记录代替，不推断版本号。若功能被禁用、处于待重启状态或上述命令失败，记录原样错误，再按微软文档制定修复和重启时间；本文没有授权直接执行启用功能、改 BIOS/UEFI、删除 WSL 发行版或清空 Docker 数据。即使可选功能已启用，也应继续核对 WSL2 能否实际启动，不能只看功能标志。

## 条件恢复后的隔离验收顺序

1. 先确认 `wsl --status` 与 `wsl --list --verbose` 能返回可解释的状态，再确认 `docker context show` 指向预期 Linux 上下文、`docker info` 实际返回服务端信息。只读命令需设有界等待时间；失败时保存错误，不继续构建。
2. 准备仅本机持有的独立 `.env`、独立 Compose 项目名和数据卷；不得连接或复制个人 SQLite、邀请码、账号、私人行情或新闻正文。应用的[`compose.yaml`](../../apps/quant_portal/compose.yaml)把数据库置于内部网络，Web 端口默认仅映射 `127.0.0.1`；实际运行前仍须检查合并后的配置。
3. 在隔离项目中构建镜像，启动 PostgreSQL 并确认其健康检查；运行迁移，再启动 Web 与后台 worker，检查 `/readyz/` 和服务日志。每一步保存命令、退出码、时间与脱敏日志；失败即停在该门槛，不把后续阶段记为通过。
4. 用隔离数据验证页面和持久化，再停止**该隔离项目**并保留检查所需数据卷。禁止对现有 Docker 项目或个人库执行全局清理。容器验收通过也不等于云端部署、多人容量或金融研究有效性通过。

**本轮状态：**阻塞已定位到 Docker/WSL 运行条件未满足、准确系统功能状态未知；容器和云端验收保持 `NOT_RUN`。只有管理员只读检查和条件恢复完成后，才能安排上述隔离验收。
