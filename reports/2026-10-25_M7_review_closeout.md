# M7 收尾：AAPL 10-Q 人工审阅交接与五家公司候选

**范围：**整理已保存的真实 SEC 证据与审阅工具，不重跑 M7 机制实验、不运行五家公司批量表示或正式金融实验。旧 `artifacts/m7/20261024T223714Z-tail-bm25-10q/` 保持只读。新材料位于 `artifacts/m7_review/20261024T235855Z-closeout/`，原 Git 基线为 `a66ec2bfd9af87526989c0c284ff6f3dc2dfe9e6`。这里的核查均为同一代理团队自检；研究者人工审阅仍为 **PENDING_HUMAN_REVIEW**。

## 计划—实际与验收边界

| 计划 | 实际材料 | 当前状态 |
|---|---|---|
| 核实旧运行与文件身份 | `baseline_audit.json` 对旧清单 172 个运行文件和 AAPL 原始申报、主 HTML、清理主文档、Item 2 文本及边界重新计算哈希 | 通过；旧证据未改 |
| 制作可审阅页面与空白决定表 | `review_page_v3/index.html`、`review_form.html`、`review_form.md`、六表索引与静态核查 | 材料生成；**浏览器实际渲染未确认，人工决定未收到** |
| 调查接收时间差异 | `acceptance_time/acceptance_time_evidence.json`、逐字段表与来源哈希 | 已证明保存的 API 字符串不同；原因与首次公开时间 **UNRESOLVED** |
| 同规则发现五家公司候选 | `candidates/run_v2/candidates.{json,csv}`、官方响应与审阅待办 | 五个候选发现；四家新公司的原始完整 submission、边界及人工审阅未做 |
| 整理研究记录、文档与测试 | [一页 M7 研究记录](2026-10-25_M7_research_note.md)、[五家公司试点方案](../docs/M7_10Q_FIVE_COMPANY_PILOT.md)、TASKS/ROADMAP 与测试/清单 | 仅限已有结果和候选准备；不冻结正式参数 |

## AAPL 人工检查的最小顺序

1. 在本机打开 `artifacts/m7_review/20261024T235855Z-closeout/review_page_v3/index.html`。先看 **开头对照**：前一段 Item 1 表格/页脚、被排除的目录短候选、真实 Item 2 标题和正文首段。
2. 看 **结尾对照**：MD&A 最后一段后是否立即进入 Part I Item 3；检查有无漏入市场风险正文或漏掉 MD&A 内容。
3. 展开 **MDA-1 至 MDA-6** 六张表，逐张对照安全呈现的源 HTML 行列与管道压平文本；特别检查日期、金额单位、负数、百分号、脚注及单元格粘连。六张表目前均为 `NEEDS_HUMAN_TABLE_REVIEW`，页面的程序提示不构成人工通过。
4. 在同目录 `review_form.html` 或可打印的 `review_form.md` 逐项填写开头、结尾、正文、六张表、异常与处理意见，并亲自填写审阅人、时间和 `ACCEPT/REVISE/REJECT`。未填写前文件仍为 **PENDING_HUMAN_REVIEW**；表单不会向网络提交。

原始完整 SEC 申报 SHA-256 `297258ba2ef1211b8cc6efdbf847ac127bd8cf39e15421ddde7e3aaf5200ffa4`；其中选中主 HTML 的**字节**区间是 `[1253,733856)`，HTML SHA-256 `d8929428b9228de965e64dc391c65301ce5a2299a9e442c76f72838af946b59c`。原始 HTML 中 Item 2/Item 3 标题锚点的位置另有记录。`[35413,51404)` 是保存的 `primary_text.txt` 经规范化后的 **Python Unicode 字符**位置，**不能**直接定位原始 HTML；对应提取章节 SHA-256 为 `de1f66f8a908d6cfadd9ca56bf32e07e521ff73c9c5254244bd6f15a14862e27`。页面及 `table_review_index.json` 分别标记 HTML、完整申报和清理文本的坐标及片段哈希。

本机浏览器工具拒绝 `file://` 协议；Codex 打开请求只返回 queued，故本轮只确认 HTML 静态结构与哈希，**没有宣称页面在浏览器中渲染成功**。研究者需自行在文件管理器或本机浏览器中打开上述 `index.html`；`browser_open_status.json` 保存尝试结果。

## 接收时间：已知的原始字段差异

| 来源快照 | 原样 `acceptanceDateTime` / 申报头字符串 | 时区标记与当前代码解释 | 获取时间与 SHA-256 |
|---|---|---|---|
| 旧 SEC submissions API | `2025-01-31T16:01:27.000Z` | 明确 UTC；当前 `_acceptance` 转为美东 `11:01:27-05:00` | 2026-10-19 00:33:45 UTC；`aad00f6aa75ba1405e0a34c788bdba9042281b2724fe1db3ff26f434b9a92e44` |
| 新 SEC submissions API | `2025-01-31T11:01:27.000Z` | 明确 UTC；当前代码转为美东 `06:01:27-05:00` | 2026-10-24 22:40:12 UTC；`5891cf875f690e38f26ca628f7091184c9d73ca34ca7646b9cecd5013aeac4ab` |
| 原始 10-Q 头 | `20250131060127` | 无时区标记；当前 `raw_header` 按 America/New_York 解释为 `06:01:27-05:00`，等于新版 API 同一时刻 | 原始文件 SHA-256 见上 |

同一 accession 的六个逐字段值中，只有 API 保存的 `acceptanceDateTime` 字符串变化；两版都带 `Z`，相差五小时。这是**保存响应本身的差异**，不是当前界面显示时区造成。现存两份代码的 `_acceptance` 函数正文相同，但仅凭当前文件不能还原当时服务器或解析器为何产生较旧值。详见 `acceptance_time/acceptance_time_review.md` 及带完整来源记录的 JSON。原因、精确首次公开时刻和历史可交易决策时点继续标记 **UNRESOLVED/UNVERIFIED**；不从字段差异推断修订。

## 五家公司后续候选（尚非接受的语料）

| 公司 | 最早 2025 年申报的原始 10-Q | 申报日 / 报告期末 | 财季与**代理起点** | 当前接入状态 |
|---|---|---|---|---|
| AAPL | `0000320193-25-000008` | 2025-01-31 / 2024-12-28 | FY2025 Q1；2024-09-29 → 2024-12-28 | 原始文件与程序边界已核；人工待审 |
| MSFT | `0000950170-25-010491` | 2025-01-29 / 2024-12-31 | FY2025 Q2；2024-10-01 → 2024-12-31 | SEC 元数据、主 HTML、MD&A 标题文字；完整原文/边界待核 |
| AMZN | `0001018724-25-000036` | 2025-05-02 / 2025-03-31 | FY2025 Q1；2025-01-01 → 2025-03-31 | 同上 |
| GOOGL | `0001652044-25-000043` | 2025-04-25 / 2025-03-31 | FY2025 Q1；2025-01-01 → 2025-03-31 | 同上；`goog-` 文件名需原始申报身份再核 |
| META | `0001326801-25-000054` | 2025-05-01 / 2025-03-31 | FY2025 Q1；2025-01-01 → 2025-03-31 | 同上 |

起点为前一份原始申报报告期末次日的**代理值**，已用各自主 HTML 的精确 XBRL 起止 duration context 佐证，但未由研究者接受为叙述覆盖起点。保存的 SEC submissions 快照中，上述报告期没有对应 `10-Q/A`；其他更正仍未排除。主文档的 MD&A 标题出现不等于正文边界通过。五家公司财季不一致，因此 2025 申报规则只是一套可复现的接入规则，并不构成同期样本。完整官方链接、来源哈希、主文档格式与各公司人工检查事项见[候选方案](../docs/M7_10Q_FIVE_COMPANY_PILOT.md)和本地 `candidates/CANDIDATES.md`。10-Q Part I Item 2 MD&A 不与现有 10-K Item 1 Business 混为同类章节。

## 验收与未完成事项

实际测试命令在分享目录运行，先将测试进程的 `PYTHONPATH` 指向该目录 `src`，再执行 `python -m pytest -q tests/test_m7_acceptance_time_review.py tests/test_m7_10q_candidates.py tests/test_m7_review_page.py -r s` 和 `python -m pytest -q -r s`。聚焦测试 **13 passed**；全套 **78 passed, 2 skipped**，跳过原因为本分享目录未存放固定模型文件。首次未指定 `PYTHONPATH` 的聚焦测试在收集时误导入旧 `quant` 可编辑安装，出现 3 个导入错误；这属于环境路径问题，已按正确源码目录重新运行并保留说明，不算作通过记录。日志为 `pytest_focused_final.log` 与 `pytest_full_final.log`。

本轮 `final_input_output_manifest.json` 再次核算旧 M7 运行清单、审阅包、候选响应、接收时间证据等 **286 个记录，失败 0**；它还保存所改源码、文档、关键输出的哈希。候选脚本在运行后只清理了一处行尾空格；实际运行的源码字节版本另存为 `candidates/source_at_run_v2.py`，SHA-256 与当时执行记录一致。`review_page_static_audit_v3.json` 是 **PASS_STATIC_ONLY**，`candidates/selfcheck.json` 是同团队自检。Git 暂存范围、凭据/原始 SEC/模型检查、实际本地提交另见新审阅目录的最终执行记录；本轮不推送。已执行的软件与同代理核查不能替代研究者对六张表和章节边界的决定。下一步仅需要 AAPL 审阅表的真实人工结论；若为 REVISE/REJECT，先在新运行中修正并复核，不覆盖旧证据。其余四家公司完整原始 10-Q 的接入与五公司批量表示仍为 TODO。
