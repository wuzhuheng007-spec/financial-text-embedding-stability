# 阿里云个人版采购闸门（2026-11-18 第二轮更新）

**当前结论：代码与容器层面已具备部署条件；真实供应商联调和云端验收仍未执行，暂不下单需由用户先完成账户侧确认。** 服务器未购买、未部署。

## 已经解决（本机实测）

| 项目 | 状态 | 证据 |
|---|---|---|
| Docker / Linux 引擎 | 用户重启后 WSL2 可用；需手动启动 Docker Desktop，引擎 29.6.2，OSType=linux | [docker_checks](../../../results/personal_cloud_pr4_linux_20261118/docker_checks_redacted.json) |
| 镜像构建范围 | 根目录白名单上下文，含 scripts/configs/必要脱敏回执；无私人数据 | [运行手册](../../../deploy/linux/README.zh.md) |
| 账户 owner ID | Django ORM 读取 PostgreSQL，保留 owner_binding 校验 | `scripts/quant_personal_feed/owner.py` |
| 凭据 | Linux 独立授权 + AES-GCM 服务端存储；不迁移 DPAPI | `ServerFileTokenStore` |
| 服务入口 | feed / research（唯一快照调度者）/ daily-price / web 各自前台运行，业务健康检查 | `deploy/linux/entrypoint.py` |
| 隔离联调 | 构建、迁移、HTTPS 登录、所有者隔离、合成缓存、真实模型对合成输入评分、重启持久化、备份恢复、桌面与 390px | [container_acceptance](../../../results/personal_cloud_pr4_linux_20261118/container_acceptance_redacted.json) |

## 仍然阻塞（按类型）

- **凭据缺失**：Linux 上没有 moomoo `quote:read` 授权；金十令牌未在容器中使用。真实供应商联调 `BLOCKED`。
- **供应商权限（账户特定）**：moomoo 与金十条款是否允许云服务器采集、缓存、AI 加工和个人远程展示，以及服务器所在地域访问，需用户向供应商确认。HTTP 439 端点继续暂停。
- **服务器购买**：站点（中国站/国际站）、地域、库存、含税总价和自动续费只在用户账户的结账页可见。
- **真实验收**：云端真实采集推进、下一次自然截止自动发布、服务器重启恢复，均 `NOT_RUN`。

## 购买时需选择的配置

单人试点：Linux（Ubuntu 22.04/24.04 或 Debian 12）、2 vCPU / 8 GB / ≥80 GB SSD、1 台、1 个月、**关闭或核对自动续费**。测试中评分容器在评分后约 254 MiB，其余容器各 <130 MiB；8 GB 留有余量，4 GB 也可能够用但未压测。阿里云国际站公开基础价 Singapore 2 vCPU/8 GB/160 GB 为 USD 35/月（2026-11-18 第一轮查询），**只作参考，不是订单总价**；按实名身份，中国居民身份证用户应走中国站。

剩余输入：域名及 DNS、私人 .env、两个 secret 文件、模型卷、SSH 访问。迁移、单写入者切换与回滚见[运行手册](../../../deploy/linux/README.zh.md)。凭据、验证码和付款资料不经聊天或 Git 传递。
