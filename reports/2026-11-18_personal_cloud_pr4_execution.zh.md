# PR #4 个人页面执行与迁移验收

执行起于 2026-11-18 05:28 Asia/Shanghai；最后运行回读的精确时间见追加回执。仓库起点 `64f16d3c5acf44960903785ea2f4347ddd1f879b`，PR #4 原头 `9a244bf7640307c574d4ef3e3e90b2cf5fabcd37`。改动在独立 worktree `codex/pr4-execution-20261118` 上完成，随后应用到本机原有协作分支；旧研究结果和无关未跟踪文件未回退。PR 来源：[PR #4](https://github.com/wuzhuheng007-spec/financial-text-embedding-stability/pull/4)、[页面交接](../docs/quant_portal/personal_cloud_ui_20261118.zh.md)。

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
| P6 | 脱敏交接与 PR 同步 | 代码、配置、报告、定向检查和脱敏回执进入 PR 分支；远端 SHA 与六个关键文件 blob 已回读一致。私有原文、行情、数据库、译文全文及凭据均不上传 | 完成 |

逐项机器台账见[CSV](../results/personal_cloud_pr4_20261118/plan_execution.csv)。软件检查为 20 项 Python 测试、10 项研究 worker 测试、2 个 JavaScript 语法检查和 diff 检查；这些只支持软件行为，不证明金十完整覆盖或新闻事实正确。[软件回执](../results/personal_cloud_pr4_20261118/software_checks.txt)。

## 06:00 后续运行核对

为缩短 SQLite 写锁占用，先将重复报价包跳过有序历史清理（`57abc4e`）；真实运行仍记录锁错误，因此该修订单独未解决金十问题。随后将原始推送留存扫描移出长写事务、把删除限制为每批 400 条（`71709e2`），只重启采集进程。第二次切换后金十在 UTC 21:58:28 与 22:00:43 有真实业务成功，缓存增加到 393 条；UTC 21:58:55 仍出现一次 SQLite 锁。原始持锁者仍未确定，短时成功不能写成持续稳定。修订后另跑 22 项定向 Python 测试，结果全过；两轮测试有重叠，不相加为独立检查数。[追加脱敏状态](../results/personal_cloud_pr4_20261118/runtime_postfix_redacted.json)与旧回执并存。

Docker 引擎第二次 8 秒检查仍超时；`wsl --status` 报 WSL2 无法启动。CPU 固件虚拟化与 SLAT 均为 `True`，但查询 `VirtualMachinePlatform` 和 WSL Windows 可选功能返回“请求的操作需要提升”，所以不能断言具体哪项功能未启用。[微软故障排查](https://learn.microsoft.com/en-us/windows/wsl/troubleshooting)给出管理员检查路径；本轮未修改系统设置或重启电脑。Linux 容器联调仍为 `NOT_RUN`。

第三次切换合并了完全重复推送的连接活跃时间和计数写入，但 UTC 22:05:05 前后仍观测到一次锁，因而没有把它记为修复完成。随后在修改前备份的 135351 条时段报价上定位到写事务内的无合适索引查询：NVDA 与 TQQQ 夜盘各 100 次“最后写入”查询分别耗时 8.265 秒、4.422 秒，并需临时排序。新增按 owner、证券、时段、渠道及写入 ID 排列的索引后，同一备份副本查询分别为 0.015 秒和小于 0.001 秒。升级前再次在线备份并通过 `quick_check`，采集进程单独重启，实库索引及查询计划已回读。该慢查询是**有证据的锁竞争来源**，但尚需真实轮询观察才能确认最终稳定性。[诊断回执](../results/personal_cloud_pr4_20261118/sqlite_lock_diagnosis_redacted.json)不含报价或正文。最新一轮 30 项软件检查通过，与早先检查有重叠。

新增索引后金十在 UTC 22:07:17、22:08:18、22:09 和 22:10:18 至少四轮连续业务成功，期间未再记录 SQLite 锁；moomoo 推送继续到达并于 UTC 22:10:22 保存新报价。观察只有数分钟，分页衔接缺口仍保留。中文研究评分 worker 原进程在 UTC 21:56:57 停止，日志末尾为运行库的窗口关闭中止信息，触发者未查明。我曾误用系统 Python 启动，因缺少 `torch` 留下一次 `ModuleNotFoundError` 失败回执；随后改用项目既有隔离环境，UTC 22:10:11 再次完成真实评分。旧可信 AI 解读队列继续暂停，未提升任何旧驳回输出。

## 当前页面与限制

本机地址为 <http://127.0.0.1:8765/>。原账户登录后可用四入口和行情卡；连接健康摘要把 moomoo 推送与金十中断分开，REST 439 不再被后续 SQLite 错误覆盖，也不会随页面刷新重试。新闻默认以金十报道为来源并保留原文入口；未审阅或旧驳回模型草稿继续不可当作可信解释。新增译文是 `CODEX_REVIEW`，数字顺序 9/9 符合原文，旧机器译文漏掉的 40 只已恢复；它没有完成独立来源核实。公开[脱敏译文回执](../results/personal_cloud_pr4_20261118/translation_review_redacted.json)不包含正文。

05:50 的早先状态检查：门户 `/readyz/` 与 CSS 200；金十最近业务成功 UTC 21:46:23，但线程当时处于锁冲突退避、`worker_alive=0`；moomoo 推送最近收到 UTC 21:50:12，六只证券仍在缓存；定时 REST 停用。后续短时观察见上节和追加状态回执。中文研究 worker 和日线 worker 的进程身份存活，后者下一条件采集窗口 UTC 2026-11-18 21:45；真实标签新增 0。**这些是有限时点观察，不是全天稳定性证明。**

## Linux 与采购闸门

当前[采购闸门说明](../docs/deployment/quant_portal/cloud_purchase_gate.zh.md)列出尚需完成的 Linux 栈和供应商权限。阿里云国际站[公开价格](https://www.alibabacloud.com/en/product/swas/pricing)给新加坡 Linux 2 vCPU/8 GB/160 GB 套餐 USD 35/月基础价；[官方创建说明](https://www.alibabacloud.com/help/en/simple-application-server/user-guide/create-a-server)明确地域套餐与实际价格以结账页为准。由于容器联调、供应商服务器使用授权和账户站点未核实，**不能给出真实总价或要求现在付款**。服务器未购买，云端验收 `NOT_RUN`。

下一项工程工作应先让本机 Docker 引擎可用，在隔离卷完成完整服务的 Linux 构建与端到端联调；同时追查金十 SQLite 持锁来源。不得把镜像语法通过、短时恢复或 HTTP 200 写成这两项通过。
