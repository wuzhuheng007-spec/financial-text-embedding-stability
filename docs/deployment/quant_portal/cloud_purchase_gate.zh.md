# 阿里云个人版采购闸门（2026-11-18）

**当前结论：暂不下单。** PR #4 页面已在 Windows 本机运行；Linux 容器没有完成构建和联调，供应商账户对云端采集与私人展示的授权条件也没有核实。阿里云服务器尚未购买、尚未部署。

本机实测 `docker info` 两次在 8 秒内无响应，`wsl --status` 报 WSL2 无法运行。处理器的固件虚拟化与 SLAT 均返回 `True`；读取 Windows `VirtualMachinePlatform`、`Microsoft-Windows-Subsystem-Linux` 可选功能状态时系统要求提升权限，因此具体缺项仍未核实。需要用户或管理员在**提升权限的 PowerShell** 只读运行 `Get-WindowsOptionalFeature -Online -FeatureName VirtualMachinePlatform` 和 `Get-WindowsOptionalFeature -Online -FeatureName Microsoft-Windows-Subsystem-Linux`，并核对 `bcdedit /enum` 中的 `hypervisorlaunchtype`。若需启用功能或重启，应由用户在保留当前服务及备份后安排；本轮没有自动修改系统。见[微软 WSL 故障排查](https://learn.microsoft.com/en-us/windows/wsl/troubleshooting)。

## 条件性配置与价格

按单人试点列一项候选：阿里云国际站轻量应用服务器，Singapore，Linux，2 vCPU、8 GB RAM、160 GB SSD、20 Mbit/s 峰值带宽、5 TB 月流量，**公开基础标价 USD 35/月**。该标价来自[官方定价页](https://www.alibabacloud.com/en/product/swas/pricing)，不是订单总价；税费、备份、域名、流量超额、所在账户站点及当前地域库存尚未核实。新加坡超额流量公开标价 USD 0.081/GB。选择新加坡只是候选，不表示已验证从上海访问的延迟或上游接口的地域许可；[官方地域说明](https://www.alibabacloud.com/help/en/simple-application-server/product-overview/regions-and-network-connectivity)指出地域购买后不可更改，跨境网络可能有延迟和丢包。实际可买套餐及总价以控制台结账页为准。

阿里云中国站与国际站的商品、币种和身份要求不可混同。国际站[身份核验说明](https://www.alibabacloud.com/help/en/account/account-verification-overview)还指出：仅有中国居民身份证的个人应使用中国站注册购买；中国内地资源购买须完成相应身份核验。用户账户所属站点和可用付款方式尚未检查。

## 下单前必须完成

1. Linux 镜像从仓库根目录构建并包含必要的 `scripts`、`configs` 与只读脱敏映射；私人原件、数据库、模型权重和密钥只通过受控迁移及持久化卷进入服务器。
2. 采集 owner ID 在 PostgreSQL 下可靠解析；moomoo OAuth 在 Linux 上通过供应商允许的独立只读授权和服务端密钥存储实现，不复制 Windows DPAPI 密文；金十采集、moomoo 推送、中文评分、日度快照和日线任务均有独立前台入口及单实例控制。
3. Docker 引擎可用，在隔离卷完成镜像构建、PostgreSQL 迁移、登录、采集缓存、研究 worker、备份恢复与重启联调。本轮只有 compose 配置语法检查通过，实际容器测试未运行。
4. 核实 moomoo 与金十条款及当前账户是否允许该地域的服务器采集、缓存、AI 加工与个人远程展示；HTTP 439 定时 REST 端点保持暂停。不能把本机只读许可自动延伸为云端或公共再分发许可。
5. 建立单写入者迁移切换：先停本机相应采集，再启云端，回读来源时间、哈希、账户、自选、14 张历史事实卡和旧研究快照；回滚时反向操作，不倒填前瞻时间。

完成上述联调后，用户在[官方创建页面](https://www.alibabacloud.com/help/en/simple-application-server/user-guide/create-a-server)对应控制台选择站点、地域、Linux 镜像、套餐、数量 1、时长 1 个月，核对实际含税总价和默认自动续费设置，再自行完成身份、付款及新服务器只读数据授权。凭据、验证码和付款资料不在聊天或 Git 中传递。
