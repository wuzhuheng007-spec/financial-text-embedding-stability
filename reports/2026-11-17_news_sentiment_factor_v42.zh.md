# 新闻情绪 v4.2：恢复、公司关联与公司快照

执行日期：2026-11-17（Asia/Shanghai）。基线及起始 HEAD 均为 `60e761631034fcf7bfefa4ec539bb93d9c972331`，分支 `codex/sync-research-progress-20261113`。本轮沿用 v4.1 中文模型 revision `e91b1a3af10e1e8c9c03429d3cd7d5e9a1c8000d`、原分句和 `P(positive)-P(negative)`，没有读取收益标签、拟合收益模型或更改旧实验。

## 计划与实际

| 阶段 | 计划 | 实际 |
| --- | --- | --- |
| P0 | 备份并核对三个计数截面 | SQLite 在线备份门户、采集、评分库共 3 份，`integrity_check=ok`。v4.1 的 15:15 截面是 1 条、15:20 截面是 2 条；本轮 16:03:10 截面为累计 7 个新收到且完成评分的文章版本、31 个片段。三个数字分别绑定各自时间和 run_id；旧回执未改写。 |
| P1 | 重启不漏停机新闻 | 固定研究起点 07:11:45 UTC，持久化队列与 `(received_at,item_key,version)` 游标；每次启动做一次起点审计，并扫描最后时间戳并列项。先对照已提交评分，才考虑一次有限恢复。16:03 截面 7 个队列任务均 `DONE`，其中 2 个记录为延迟恢复；评分可用时间保留实际完成时间。 |
| P2 | 单实例、状态与故障演练 | Windows 原子字节锁、PID+进程创建时间及心跳共同识别活进程。隔离库的 8 项故障演练最终 8/8；开发中前两次演练失败及修复原因保留，不能把最终通过写成首次通过。旧 v4.1 意外退出的原因仍**未查明**。 |
| P3 | 有来源的公司关联 | 先冻结 40 个片段的同团队审阅清单。加入有官方依据的“博通 / Broadcom / AVGO”别名，原文相同片段复用旧评分，只生成新关联版本。修订后的集合比较从 32/40 到 38/40，新增正确链接 6，样本中新增错误链接 0，仍漏 3 个链接、4 个多公司片段保持未决。没有把宏观消息分给所有公司。 |
| P4 | 修订与事件去重 | 截止时点先选可用且已评分的最新文章版本，再进行公司关联和来源内分组。首个开发回放暴露旧版本残留问题，原快照保留并标为 `REJECTED_DIAGNOSTIC`；随后另版规则建立两份回放。跨来源转载关系尚未核实，仍分别计数。 |
| P5 | 不可改写公司快照 | 建立显式 `as_of`、append-only 表和更新／删除拒绝触发器；保存输入清单哈希、规则版本、实际生成时间、N1/N2 与缺口。真实 NY 16:15 截止尚未到：**真实前瞻公司快照 0**。保存 3 份 `DEVELOPMENT_REPLAY`（含一份被驳回的诊断版），不能回填为历史前瞻信号。 |
| P6 | 可执行覆盖与未来标签 | 研究范围限定为已配置的金十本机最新页采集及现有 moomoo 缓存。金十分页衔接仍为 `POSSIBLE_GAP_NO_PAGE_OVERLAP`；观察到的 N2 与完整数量分列，完整数量为缺失而非零。1 日／5 日未来标签队列已建；本轮因前瞻快照未到点，实际标签任务 0。 |
| P7 | 真实运行和交接 | 07:31:37–07:47:07 UTC 做了 32 次采样，跨度 930.233 秒，最大采样间隔 30.018 秒：31 次运行、1 次主动停止，随后恢复。登录后的中英桌面实看通过；另用 Edge 响应式工具的 **390×700 CSS px** 视口逐项检查中英研究区，内容换行可读，未目视发现整页横向溢出。 |

上述当期累计数字来自 `results/news_sentiment_factor_v42_20261117/final_counts_redacted.json`，工作区的真实运行数据仍会继续增长。15:15 的 1 条来自 v4.1 `prospective_score_observation_redacted.json`（run `v41_observe_20261117T071145Z`），15:20 的 2 条来自 v4.1 `final_runtime_readback_local.json`，均是当时截面，不能改称 v4.2 新增。

## 恢复与故障证据

第一次受控启动前，金十于 07:31:07 UTC 已写入一条版本；v4.2 于 07:31:27 UTC 启动，07:31:34 UTC 完成实际评分。补算记录为 `RECOVERY_DELAYED`，未把新闻发布时间或收到时间写作评分完成时间。最终受控重启时又发现一个停机期间收到的版本；截至 16:03:10（上海）有 2 个完成的延迟恢复任务。持久队列保存 `PENDING/STARTED/RECOVERY_PENDING/DONE/QUARANTINED` 等状态和逐次尝试；每日 500 次上限合计旧 v4.1 与本版的实际尝试，重启不能重置。该截面当日尝试 7 次、积压 0。自动恢复上限为每版本首次尝试加 1 次恢复；异常退出后的 `STARTED` 先查完整评分提交，已有结果只补状态。

隔离演练覆盖：停止后新增再启动、并列时间戳且排序键较小、`STARTED` 中断、结果已提交但队列状态未更新、预算暂停时重复启动、双进程抢锁、短时 SQLite 占用和相同版本哈希冲突。前两轮演练因锁字节读取和测试库连接关闭问题失败，第三轮 8/8；这些是合成故障，不替代长时间生产稳定性。另在真实 worker 运行时再次执行 `start`，收到 `WORKER_ALREADY_RUNNING`，原 worker 保持运行；Windows 虚拟环境启动器与真正的 Python worker 为父子进程，不计作两个 worker。真实观察的主动停止出现在第 15 次采样，随后状态为 `RUNNING`。日志记录异常类别、栈位置、阶段和退出码；本轮没有证据解释 v4.1 更早的意外退出。

## 公司关联和快照边界

别名依据：[Broadcom 投资者 FAQ](https://investors.broadcom.com/company-information/faqs)明确公司名、`AVGO` 及 Nasdaq 市场；[Broadcom 官方中文文件](https://docs.broadcom.com/doc/supplier-environmental-and-social-responsibility-code-of-conduct-chinese-simplified)使用“博通 (Broadcom)”。匹配只作用于当前片段，短代码有词边界。40 条审阅仍为 `CODEX_REVIEW`，旧问题和未参与修改规则的开发片段都已经被本团队接触，不能称独立准确率。新规则仍漏掉紧贴的 `GOOGL`/`META` 等边界及繁体别名案例；不在本轮按同一 40 条继续调规则。

回放快照按 `received_at<=as_of` 且 `scored_at<=as_of` 过滤。序列化结果检查 9/9 通过：禁止未来 `as_of`、更新／删除、旧修订重复计数，实际成员均在截止前收到且评分；3 份回放共存储 219 条**跨版本累计成员行**，不能当 219 个独立事件。`N1` 是直接公司关联的事件组文本净语气均值；`N2` 是指定来源范围内观察到的去重组数。来源连续性未证实时，不产生“完整新闻数”。同一新闻的文本语气既不证明报道真实，也不是上涨概率。首个回放的错误选择已单独版本化修订，旧快照继续可查但不被晋升。

快照时间使用 `America/New_York`，沿用 16:15 截止，真实下次截止为 2026-11-17 21:15 UTC（上海次日 05:15）。2026 年休市与半日列表核对了 [NYSE 官方交易日历](https://www.nyse.com/trade/hours-calendars)及 [Nasdaq 日历](https://www.nasdaq.com/market-activity/stock-market-holiday-schedule)。半日仍按本研究冻结的 16:15 截止，**不是交易所半日官方收盘时刻**。本轮未在该截止后运行，不能声称形成真实前瞻公司快照或未来收益标签。

## 限制与运行方式

金十分页连续性未证明，跨来源转载尚未可靠归并，公司未决片段不分配情绪；旧 v4.1 意外退出原因未查明。英文档案受单独权限限制，未被中文研究覆盖。财务输入 v2.5 仍为 `REJECTED_CANDIDATE`。本轮模型没有改变，收益拟合、IC、盈利分析均为 0。

最后一处未来标签队列代码修订提交后，研究 worker 于 08:05:45 UTC 正常停止，并于 08:05:51 UTC 以新 run `v42_20261117T080551230383Z` 启动。08:06:09 UTC 的单时点回读显示 worker 存活、积压 0，门户 HTTP 200，金十及 moomoo 采集均存活；见 `final_runtime_redacted.json`。这不是全天稳定性验收。

在仓库根目录运行：

```powershell
& 'artifacts/news_sentiment_factor_v3_20261117/.venv/Scripts/python.exe' -m scripts.news_sentiment_factor_v42.worker start
& 'artifacts/news_sentiment_factor_v3_20261117/.venv/Scripts/python.exe' -m scripts.news_sentiment_factor_v42.worker status
& 'artifacts/news_sentiment_factor_v3_20261117/.venv/Scripts/python.exe' -m scripts.news_sentiment_factor_v42.worker stop
```

所有者页面为 `http://127.0.0.1:8765/`，研究情绪折叠区只读本机缓存。`tzdata==2026.6` 已装入隔离研究环境，版本来源见 [PyPI](https://pypi.org/project/tzdata/2026.6/)。页面和进程状态是回执时点的观察，不能推断全天可用。私人原文、评分、快照成员、备份和日志仅保留本机；公开交接仅含脱敏汇总。
