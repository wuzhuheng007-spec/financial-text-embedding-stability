# PR #4 个人页面执行与迁移验收

执行时间：2026-11-18 05:28–05:51 Asia/Shanghai。仓库起点 `64f16d3c5acf44960903785ea2f4347ddd1f879b`，PR #4 原头 `9a244bf7640307c574d4ef3e3e90b2cf5fabcd37`。改动在独立 worktree `codex/pr4-execution-20261118` 上完成，随后应用到本机原有协作分支；旧研究结果和无关未跟踪文件未回退。PR 来源：[PR #4](https://github.com/wuzhuheng007-spec/financial-text-embedding-stability/pull/4)、[页面交接](../docs/quant_portal/personal_cloud_ui_20261118.zh.md)。

## 先备份并回读的首快照

05:17 只读回执在操作前备份，原件与副本 SHA-256 同为 `d9b3627e1651998aa0dae86b6a08618dbba3717632aafdbffde7177b19b51c76`。真实日度快照计划截止 2026-11-18 **05:15 上海时间**；实际 `generation_started_at` 为 05:15:05，完成为 05:15:05.264736，发布见证记录的 `signal_available_at` 为 **05:16:05.265738**。回执记为 `ON_TIME_SCHEDULED` / `PUBLISHED_AND_AVAILABLE`，不是在截止瞬间可用。该时点 1 批、15 公司行、4 个 N1 非缺失公司（AMZN、NVDA、Samsung Electronics、SK hynix）、11 个公司事件组成员行、10 个文章版本、30 个标签队列项（26 `WAIT_ENTRY`、4 `BLOCKED_SECURITY_MAPPING`）。首窗仍是 `PARTIAL_INITIAL_WINDOW`，金十分页衔接为 `POSSIBLE_GAP_NO_PAGE_OVERLAP`；不能推定新闻覆盖完整或形成合格收益标签。[脱敏回执](../results/personal_cloud_pr4_20261118/runtime_redacted.json)保存了同一私有来源的哈希、口径和时间。

门户、采集、分析、评分、研究队列、标签这 6 份本机 SQLite 在修改前在线备份并通过 `PRAGMA quick_check`；译文缓存修改前另作在线备份，也通过 `quick_check`。私人库、备份及原始正文没有入 Git。

## P0–P6 计划与实际

| 阶段 | 计划 | 实际 | 状态 |
|---|---|---|---|
| P0 | 读 PR、首快照、保护数据 | 完成上述回读及 7 份私人库备份；核对原账户、6 条自选和 14 张历史事实卡仍在本机 | 完成 |
| P1 | 隔离应用页面并做原账户中英桌面/390px检查 | 在 worktree 应用 PR，修正英文报价文案与窄屏控件；原账户实看总览、新闻、研究、设置、曲线及来源链接，中英文桌面与 390px 无整页横向溢出。刷新时保留新闻阅读位置。未译新闻仍显示待翻译 | 主要完成，全文双语未完成 |
| P2 | 恢复真实金十、保持 moomoo | 单采集进程重新启动后金十在 UTC 21:32:49、21:36:38、21:38:51、21:41:17、21:46:23 有真实业务成功，缓存 386→392 条；SQLite 锁在恢复期间仍反复出现，金十线程间歇退避，原始持锁者未定位。moomoo 推送持续到达。已知 HTTP 439 REST 端点停用并保持 `SECURITY_POLICY_BLOCKED` | 部分完成，长期稳定性未通过 |
| P3 | 对应新闻版本和评分，继续有据翻译/分析 | UTC 21:43:47 检查时，241 个已评分金十文章版本全部按原文哈希对应缓存；2 张旧已审阅卡和 1 条旧译文对应当前版本。另追加 1 条同团队校订机器译文，旧失败和驳回均保留；新模型生成 0 次。新闻主张未经独立核实，未放行新 AI 解读 | 部分完成 |
| P4 | Linux 适配与容器联调 | 研究日线 worker 的单实例锁加入 Linux `fcntl`，10 项定向检查通过；compose 配置语法通过。镜像上下文、PostgreSQL owner 解析、Windows DPAPI 凭据、独立采集/评分服务和私有迁移仍缺；Docker 引擎 8 秒有界检查超时，实际镜像构建/联调 **NOT_RUN** | 阻塞 |
| P5 | 云端采购与验收 | 未买服务器、未部署。仅核对阿里云国际站候选基础标价；账户站点、地域库存、含税总价、服务器采集许可均未确认 | 未执行 |
| P6 | 脱敏交接与 PR 同步 | 代码、配置、报告、定向检查和脱敏回执进入 PR 分支；最终远端 SHA 在交付时回读。私有原文、行情、数据库、译文全文及凭据均不上传 | 完成前复核 |

逐项机器台账见[CSV](../results/personal_cloud_pr4_20261118/plan_execution.csv)。软件检查为 20 项 Python 测试、10 项研究 worker 测试、2 个 JavaScript 语法检查和 diff 检查；这些只支持软件行为，不证明金十完整覆盖或新闻事实正确。[软件回执](../results/personal_cloud_pr4_20261118/software_checks.txt)。

## 当前页面与限制

本机地址为 <http://127.0.0.1:8765/>。原账户登录后可用四入口和行情卡；连接健康摘要把 moomoo 推送与金十中断分开，REST 439 不再被后续 SQLite 错误覆盖，也不会随页面刷新重试。新闻默认以金十报道为来源并保留原文入口；未审阅或旧驳回模型草稿继续不可当作可信解释。新增译文是 `CODEX_REVIEW`，数字顺序 9/9 符合原文，旧机器译文漏掉的 40 只已恢复；它没有完成独立来源核实。公开[脱敏译文回执](../results/personal_cloud_pr4_20261118/translation_review_redacted.json)不包含正文。

最后一次状态检查在 05:50 左右：门户 `/readyz/` 与 CSS 200；金十最近业务成功 UTC 21:46:23，但线程当时处于锁冲突退避、`worker_alive=0`；moomoo 推送最近收到 UTC 21:50:12，六只证券仍在缓存；定时 REST 停用。中文研究 worker 和日线 worker 的进程身份存活，后者下一条件采集窗口 UTC 2026-11-18 21:45；真实标签新增 0。**这是单时点观察，不是全天稳定性证明。**

## Linux 与采购闸门

当前[采购闸门说明](../docs/deployment/quant_portal/cloud_purchase_gate.zh.md)列出尚需完成的 Linux 栈和供应商权限。阿里云国际站[公开价格](https://www.alibabacloud.com/en/product/swas/pricing)给新加坡 Linux 2 vCPU/8 GB/160 GB 套餐 USD 35/月基础价；[官方创建说明](https://www.alibabacloud.com/help/en/simple-application-server/user-guide/create-a-server)明确地域套餐与实际价格以结账页为准。由于容器联调、供应商服务器使用授权和账户站点未核实，**不能给出真实总价或要求现在付款**。服务器未购买，云端验收 `NOT_RUN`。

下一项工程工作应先让本机 Docker 引擎可用，在隔离卷完成完整服务的 Linux 构建与端到端联调；同时追查金十 SQLite 持锁来源。不得把镜像语法通过、短时恢复或 HTTP 200 写成这两项通过。
