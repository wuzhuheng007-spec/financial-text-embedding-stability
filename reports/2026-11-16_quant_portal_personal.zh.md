# Quant Portal 本机个人测试版与多人部署准备

**执行日期及时区：**2026-11-16，Asia/Shanghai。P0 核对的本地与远端基线均为 `2802244db3f30d8fce38a43ef57281e1b11735ad`。你随后明确“多人只是计划，先完成个人能用的测试版”，因此本轮以本机个人版为交付重点；阿里云与多人验收保留为后续计划。[冻结配置](../configs/quant_portal_release_v1.json)和[P0–P7 计划—实际台账](../results/quant_portal_20261116/plan_execution.csv)记录了范围调整。旧实验、私人数据库、原私有站点和无关未跟踪文件均保留。

## 本机实际可用部分

独立的[Django 应用](../apps/quant_portal/README.md)提供本机启动、状态、停止、邀请码注册、登录、退出、个人自选股、时区设置和带来源链接的历史财报事实卡。服务留在 `http://127.0.0.1:8765/`，仅绑定本机；最终就绪与 CSS HTTP 检查均为 200。首次注册需从被 Git 忽略的 `apps/quant_portal/var/personal_invite.txt` 私人文件读取一次性码，自己在页面设置密码。邀请码未被使用，值未进入 Git、报告或普通日志。[运维回执](../results/quant_portal_20261116/ops_receipt_redacted.json)保留了启动和停止脚本先前的失败、修复及最终状态。

首位个人账户注册后会得到 **6 个可编辑的证券代码候选**，并不据此宣称实时行情或完整历史身份。应用本机库导入了 [v4 脱敏研究摘要](../results/market_observer_financial_text_v2_20261115/fact_cards_v4_redacted.json)中 **11 条**有定位的 NVDA/MU 历史事实，输入哈希见运维回执。我重新打开两份 SEC 官方原件——[NVIDIA Q4 FY26](https://www.sec.gov/Archives/edgar/data/1045810/000104581026000019/q4fy26pr.htm)和[美光 FQ2-26](https://www.sec.gov/Archives/edgar/data/723125/000072312526000004/a2026q2ex991-pressrelease.htm)——按指标、数值、财期和口径做了同一执行团队的逐项回查；[11 条来源回执](../results/quant_portal_20261116/fact_source_web_check.json)保留证据定位。页面分别显示币种、GAAP/调整口径及未知值。这些仍是 `CODEX_REVIEW_DEVELOPMENT` 开发摘要，不是独立人工金标准、最新财报或模型输出。

页面刷新不向供应商或模型发起请求；**没有假价格**。moomoo 与金十对多人展示默认关闭。[权限清单](../docs/quant_portal/source_rights_register.md)分别登记个人使用、服务器采集、缓存、受邀用户展示、AI 加工和再分发。金十[用户协议](https://flash.jin10.com/agreement)与[版权声明](https://www.jin10.com/about/index.html)限制未经授权的传播；moomoo [条款](https://www.moomoo.com/terms)对行情再分发有限制。本轮直接打开 moomoo 条款页遇到限流，所以没有据此推定任何新增授权。登录墙与免费提供不等于取得展示许可。

## 验证、修正与边界

最终 [Django 检查](../results/quant_portal_20261116/django_check.log)、[迁移一致性检查](../results/quant_portal_20261116/migration_check.log)和[17 项合成测试](../results/quant_portal_20261116/focused_tests_final.log)通过，测试使用隔离数据库。同 17 项还在关闭个人模式的配置下重放，**不是 34 项独立测试**。第一次以真实个人模式重放时有一项测试假设默认模式关闭而失败；[修正回执](../results/quant_portal_20261116/validation_corrections.json)保留错误与改法。覆盖邀请码、账户隔离、退出与会话失效、登录限流、CSRF、来源展示门槛、关闭公开管理后台和空闲后台任务。最终本机 SQLite 备份在独立副本恢复，**19 张表**完整性为 `ok`，未改动运行库；首次 Windows 文件句柄清理失败及修复也留在运维回执。

前端检查了原私有 Site 的实际源码布局，但新模板和 CSS 为本轮编写。浏览器检查曾发现注册页 CSS 404，以及 Windows 子进程继续占端口导致旧代码仍在服务；两者已修复，静态文件 HTTP 200。你按 Escape 中断电脑操作后，本轮停止了浏览器动作，**桌面完整流程与 390px 视觉验收为 NOT_RUN**。因此个人版已能启动和首次注册，但不能把视觉检查写成通过。

PostgreSQL、独立后台进程、迁移、Compose、Caddy HTTPS 与锁定依赖已准备；基础和生产 Compose 语法、生产静态资源清单的本机预检通过。本机 WSL 虚拟化条件阻断 Docker Linux 引擎，容器构建/运行、PostgreSQL 联调、30/100/300 人压测、容器回滚及阿里云部署均 **NOT_RUN**。[未来部署手册](../docs/deployment/quant_portal/README.md)与[成本清单](../docs/deployment/quant_portal/cost_register.csv)将未知金额写为 `UNKNOWN`。[Apache-2.0 提案](../docs/quant_portal/open_source_review.md)尚未成为仓库许可证；[第三方依赖清单](../docs/quant_portal/third_party_inventory.md)和[只输出位置的扫描](../results/quant_portal_20261116/release_scan_redacted.json)只是发布审查材料。

## 研究线与下一步

本轮模型拟合、四月至七月标签读取、交易与正式 M5 评估均为 **0/NOT_RUN**。既有**每事件 20,000 字符模型输入的证据遗漏**仍未解决，见[前一轮报告](2026-11-15_market_observer_financial_text_v2.zh.md)。现在最直接的一步是你用本机私人一次性码完成注册，在桌面与 390px 页面做一次真实使用检查。将来多人或云端运行还需容器环境、压测和回滚演练、代码归属/许可证核对，以及供应商对受邀用户展示的明确授权。
