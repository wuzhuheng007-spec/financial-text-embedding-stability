# 新闻情绪 v4.5：价格元数据、按需日 K 与首快照验收

执行日：2026-11-17（Asia/Shanghai）。起始 HEAD `237e697700d8fccc88b357b364f6942e1da7cf2a`，沿用 `codex/sync-research-progress-20261113`。以 `snapshot_acceptance_redacted.json` 的 **2026-11-17 18:08:31 上海时间**截面为以下数据计数依据，后续进程状态另见最终运行回执。没有更改中文模型、切分、公司关联、N1/N2、纽约 16:15 截止或研究起点；没有恢复可信 AI 解读队列。

## 先回答五个问题

1. **报价币种：NVDA、MU 的旧试点两日各关联了 USD 的外部证据链，未从 moomoo 日 K 原响应取得币种字段。** 两份 [SEC 申报封面：NVDA](https://www.sec.gov/Archives/edgar/data/1045810/000104581026000021/nvda-20260125.htm)、[MU](https://www.sec.gov/Archives/edgar/data/723125/000072312526000023/mu-20260903.htm)核对了普通股与 Nasdaq Global Select Market；本机 moomoo 官方证券信息将其映射为 `US.NVDA`、`US.MU`；[Nasdaq UTP 官方报价规范](https://www.nasdaqtrader.com/content/technicalsupport/specifications/utp/uqdfspecification.pdf)提供发行证券币种 `USD` 的编码说明；[moomoo 市场文档](https://open.moomoo.com/cli/market)要求按证券报价币种解释价格，并说明报价字段不提供币种。这是**跨来源推断**，不是 moomoo 直接返回的 USD，也不是财报或账户结算币种。初始记录原写 `valid_to=2026-12-31`，超出已核查资料；没有改写旧记录，而是追加修正使其**有效截止为 2026-11-17**。本轮实际回查的价格日为 11 月 13、16 日，共 8 个开／收字段与原始响应及哈希相符。未来日线必须以该日原件及证券名称一致性建立逐日关联；不能将初始记录自动外推。
2. **持续日 K 任务已实际注册运行，但尚未产生新日线。** 独立 worker 每 60 秒扫描已发布、N1 非缺失的 NVDA/MU 标签需求；按纽约实际收盘后 45 分钟、必要时 180 分钟安排，单日新增供应商请求上限 8 次，失败也计数。受控停止／启动及重复启动检查通过。供应商业务码 `-11` 限流和 `-12` 历史额度耗尽分别按日暂停、持续暂停，不再只依赖 HTTP 状态。当前没有合格队列，新增请求 **0**，所以历史日 K 的实际再次成功仍**待到点实测**。最早可能的目标交易日采集窗口为 **2026-11-18 21:45 UTC（上海 11 月 19 日 05:45）**，前提是首个快照按规则发布且相关 N1 非缺失；worker 本身继续每 60 秒扫描，不由网页刷新触发。供应商的[历史日 K 文档](https://open.moomoo.com/api/quote/basic-data/history-kline)支持本轮固定的日 K、常规时段、不复权口径；本机时间安排不保证供应商此时已完成最终数据。
3. **首次真实快照尚未生成。** 本轮截面未到 2026-11-17 21:15 UTC（上海 11 月 18 日 05:15）。真实批次 0、公司行 0、N1 非缺失行 0、观察事件组贡献 0、等待标签 0；研究 worker 的 `PENDING_MARKET_CUTOFF` 与原规则版本已回读。旧累计开发回放仍单列，没有改为前瞻快照。
4. **价格与标签：** NVDA、MU 各有 **2 条**旧试点、已完成且与原件相符的常规日 K，且相关开／收字段有币种证据链接。v4.5 自动新增日 K **0**；已核实官方开盘竞价价格各 **0**。这两日早于首个前瞻信号，不能拼成前瞻收益。当前合格真实价格—信号标签 **0**、到期标签 **0**、有效因子—收益配对 **0**。旧试点行动列表在 11 月 13—16 日未返回相应行动，仅是该供应商列表结果，不证明后来持有区间无拆股或分红；新持有区间行动证据 0。另版收益只定义未复权常规日 K 的价格收益，不是官方开盘竞价或含分红总收益。[moomoo 调整因子](https://open.moomoo.com/api/quote/basic-data/rehab)与[分红文档](https://open.moomoo.com/api/quote/corporate-actions/dividends)分别支撑列表字段口径。
5. **439 仍阻断定时 REST 快照。** `POST /api/v1.0/quote/stock-quote` 的状态为 `SECURITY_POLICY_BLOCKED`，最后业务成功为 08:27:20 UTC；本轮没有重试该端点。只读凭据状态显示 `quote:read`，但不能据此确定 439 的根因。[moomoo 官方错误表](https://open.moomoo.com/api/overview/getting-started)将 HTTP 439 / `-12009` 归为 WebAPI 安全策略阻断，建议核对 API 授权。用户如要恢复此 REST 端点，可在 moomoo 官方支持渠道核对该客户端／账户的**只读 stock-quote 端点授权和行情权限**；[询问草稿](2026-11-17_moomoo_rest_439_support_draft.zh.md)已备好，未发送。不需要在聊天中提供令牌、交易或持仓权限。WebSocket 推送与金十保持独立运行，既有成功时间见回执。

## 计划—实际

| 阶段 | 冻结计划 | 实际与限制 |
| --- | --- | --- |
| P0 | 核对现场、在线备份 | HEAD 与已知基线一致；门户、金十、moomoo 推送、REST 和研究 worker 分开回读；5 份私人 SQLite 在线备份 `integrity_check=ok`。旧结果保留。 |
| P1 | 两只证券的有来源币种证据 | 建立追加式元数据和 8 条字段链接；原价格币种仍是 `UNKNOWN`。首次有效期写得过宽，追加修正为 11 月 17 日；保留初始回执与错误记录。未来逐日重新绑定。 |
| P2 | 自动按需日线 | 实际独立进程已运行，受控重启和单实例回读通过；因尚无有效 N1 队列，新增供应商调用 0、真实新日线 0。旧 `import_pilot()` 未改。 |
| P3 | 公司行动与标签准入 | 两个旧试点行动列表回执保留；新持有区间回执 0、真实标签 0。检查未复权价格、币种、行动、发布见证、信号早于入场和到期时间；合成的幂等及失效追加通过，不能代替真实标签。 |
| P4 | 439 隔离 | 拒绝端点保持暂停；只读授权状态与官方错误解释已记录；原因仍未由供应商确认。历史日 K 以前成功，不将 REST 快照 439 自动扩展为全部行情权限失效。 |
| P5 | 首快照验收 | 首个截止还未到；真实快照和队列均为 0，自动入口保持运行，不提前制造记录。 |
| P6 | 定向验证与页面 | 10 项隔离合成检查、4 项 Django 检查、JS 语法和 Python 编译通过；8 个真实价格字段回查原件。登录后中文桌面、中文与英文 390px 截图实看，英文桌面 DOM 回读；390px `scrollWidth=375 < width=390`。界面显示推送与 REST 分开、日 K 条件采集和首快照待到点。 |

**实际失败与修正：** 初版软件检查曾因 Windows 临时 SQLite 句柄未关闭失败，补齐关闭后通过；新增页面计数测试初次运行缺测试表，修正测试初始化后通过；Django 命令最初缺隔离环境变量，两次报配置错误后使用测试专用变量通过。初版元数据的未来结束日期过宽，保留原条目并追加更正，未掩盖负面结果。冻结配置的 `metadata_policy.first_eligible_date_is_evidence_observation_date` 还混淆了价格日期与证据取得日期；[追加式协议更正](../results/news_sentiment_factor_v45_20261117/protocol_amendment_redacted.json)明确旧试点两日仅供回顾性核源，其 USD 证据 11 月 17 日才取得，不能视为此前前瞻可用。配置及旧回执未改写。最终 10 项合成检查和 4 项 Django 检查不是供应商持续可用或财务口径完全正确的证明。

**运行与下一次检查：** 在仓库根目录运行 `python -m scripts.news_sentiment_factor_v45.worker status` 查看独立日 K worker；`start` 幂等启动，`stop` 受控停止。门户 `& 'apps/quant_portal/ops/local.ps1' status`，采集 `& 'apps/quant_portal/ops/feed_personal.ps1' status`，中文研究 worker `& 'artifacts/news_sentiment_factor_v3_20261117/.venv/Scripts/python.exe' -m scripts.news_sentiment_factor_v42.worker status`。首次快照到点后应从真实数据、完成回执和发布见证回读，不能把心跳当成发布。私人原始行情、新闻、数据库、账户及凭据不进入 Git 或 ZIP。本轮新增模型拟合、IC、回测和交易调用均为 0；财务 v2.5 继续 `REJECTED_CANDIDATE`。

数据回执：`results/news_sentiment_factor_v45_20261117/` 中的 `preflight_redacted.json`、`metadata_redacted.json`、`metadata_scope_correction_redacted.json`、`snapshot_acceptance_redacted.json`、`price_qualification_redacted.json`、`label_gate_redacted.json`、`rest_439_redacted.json`、`software_checks_redacted.json`、`browser_acceptance_redacted.json` 与逐项 `plan_execution.csv`。每个关键数量可由相同截面与代码／配置哈希回查；旧私人原件保留本机。
