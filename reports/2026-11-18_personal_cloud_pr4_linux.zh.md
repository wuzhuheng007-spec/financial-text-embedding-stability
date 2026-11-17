# PR #4 第二轮：重启恢复、Linux 容器适配与隔离联调

执行时间 2026-11-18 06:41–07:40（Asia/Shanghai）。电脑 06:21:54 重启。PR 头起点 `61b5f44`（远端未变）。生产运行目录 `financial-text-embedding-stability-sync-20261113` 本轮**未改代码**；改动在 worktree 分支完成并推送到 PR 来源分支。

## 重启恢复（本机）

重启后 5 个服务全部停止；采集库仍显示 `worker_alive=1`、研究状态文件仍写 RUNNING，均为旧状态。先用 SQLite 在线备份接口备份 8 个私人库（quick/integrity 全部 ok），再逐个启动门户、采集、v42（隔离 torch 环境）、v45；AI 解读队列保持 `PAUSED_FOR_V17`。23:23 UTC 回读：门户 readyz/CSS 200；金十最近业务成功 23:23:01，失败累计仍为 22，缓存 402→419；moomoo 推送持续；REST 快照仍 `SECURITY_POLICY_BLOCKED` 未重试；v42 积压清零；重启后无新 SQLite 锁。重启期间（22:21–22:43 UTC）的金十缺口没有补抓，分页仍 `POSSIBLE_GAP_NO_PAGE_OVERLAP`。账户/自选/设置/14 张事实卡摘要前后一致；门户启动按既有逻辑新增 1 条邀请记录。所有者浏览器视觉验收 `NOT_RUN`（未使用所有者密码），以回滚事务的测试客户端验证登录后页面 200、第二账户 404。

## Docker 与 Linux

`wsl --status` 现已正常（重启解决了上一轮的 WSL2 故障），但 Docker 引擎不会自动启动；启动 Docker Desktop 后 18 秒就绪，5 项检查退出码全 0，`OSType=linux`、Engine 29.6.2、内核 6.18 WSL2。

## 代码变更

- `deploy/linux/`：根目录白名单构建（上下文 2.17 MB）、app/research 两个目标、前台入口与业务健康检查、Compose（db 不开端口、backend internal、research 无网络、仅 feed/daily-price 出站）、Caddy、隔离测试叠加层、合成验收脚本、运行手册。
- `scripts/quant_personal_feed/owner.py`：PostgreSQL 下经 Django ORM 解析 owner；Windows 保持原 SQLite 查询。
- `moomoo_webapi_oauth.py`：`ServerFileTokenStore`（AES-256-GCM + 独立密钥文件，拒收 DPAPI 密文与非 quote:read 范围）、平台选择工厂、无浏览器服务器授权（SSH 转发回环回调）；采集、日线和 REST 模块改用工厂，Windows 行为不变。
- `settings.py`：仅 `/healthz/`、`/readyz/` 豁免 HTTPS 跳转，使容器内健康探针可用。
- 新增 Linux 专用采集配置及隔离测试配置（两个供应商关闭）、`tests/test_server_token_store.py`。

## 隔离验收（项目 quant-pr4-test / quant-pr4-restore，端口 18443/18444）

通过：镜像构建与依赖；PostgreSQL 迁移与健康；生产标志在容器内实际生效；经 Caddy 本地 CA 的 HTTPS 登录（非公网可信证书）；所有者隔离；静态资源；合成缓存写入、去重、修订、同价报价不重复；第二个采集实例被锁拒绝；真实模型对 4 条合成中文版本完成 9 个分段评分（模型 `e91b1a3…`，非金融有效性检验）；v43 合成窗口/快照/见证/标签检查 13/13；down/up 后数据持久且不重复评分；PG 与 SQLite 备份恢复到另一套卷后可登录读取；桌面与 390px 无横向溢出。过程中发现并修复 5 个只在 Linux 运行时出现的缺陷。新测试队列缺少冻结的关联规则表，计划快照被拒绝生成（失败即停）。

未运行/阻塞：真实供应商连通（无 Linux 授权）、真实收益标签（无到期）、云端部署。

## 前瞻研究

首快照 `fa8cb763` 未改：截止 05:15，可用 05:16:05.265738（上海），15 公司行、4 个 N1（NVDA、AMZN、SK hynix、Samsung），30 个标签队列项（26 `WAIT_ENTRY`、4 `BLOCKED_SECURITY_MAPPING`），最早入场 2026-11-18 14:30 UTC，当前无到期标签。重启未错过截止；下一截止 2026-11-19 05:15。未生成价格或标签，未改因子、未拟合、未读受限期标签。

## 进度

```
本机服务恢复      ██████████ 100%（短时观察）
Docker/Linux 引擎 ██████████ 100%
Linux 代码适配    █████████░  90%（真实授权待用户）
隔离容器验收      █████████░  90%（真实供应商未联调）
云端部署          ░░░░░░░░░░   0%（未购买）
```

台账：[plan_execution.csv](../results/personal_cloud_pr4_linux_20261118/plan_execution.csv)；采购闸门：[cloud_purchase_gate.zh.md](../docs/deployment/quant_portal/cloud_purchase_gate.zh.md)。
