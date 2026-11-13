# M8：四份固定 10-Q 的接入与研究者审阅交接

本记录对应本地运行 `artifacts/m8/20261025T230645Z-four-10q/`，是同一代理执行的工程接入和自检，不是独立审计、研究者验收或正式金融实验。四份文件沿用 [冻结候选表](../artifacts/m7_review/20261024T235855Z-closeout/candidates/run_v2/candidates.json)，其 SHA-256 为 `7c4351b66306bcc6334edde1cf2ed40486e724196fb0370622962fc8dedf90cb`。没有因下载或提取方便而换公司、accession 或章节。

## 计划与实际

| 操作 | 已保存的实际证据 | 当前结论 |
| --- | --- | --- |
| 固定候选并下载完整原始申报 | 每家公司 `ingest/<ticker>/original_submission.txt`、旧与新 submissions 快照、官方 filing index、[获取回执](../artifacts/m8/20261025T230645Z-four-10q/receipt.json) | 四份原始 10-Q 均保存；获取回执仍保留初次发现版本差异时的 `PARTIAL_VERSION_REVIEW` |
| 核验身份、时间字符串和来源 | [保存来源自检](../artifacts/m8/20261025T230645Z-four-10q/source_self_check_v1.json)，逐家公司 `source_identity.json` 和 `source_receipts.json` | 四份保存来源字段自检均为 `PASS_SAVED_SOURCE_FIELDS`；这是同一代理的程序自检 |
| 处理旧独立 HTML 与新完整原文内嵌 HTML 的字节差异 | [差异诊断](../artifacts/m8/20261025T230645Z-four-10q/primary_version_diagnostic_v2.json)、[追加式裁定](../artifacts/m8/20261025T230645Z-four-10q/version_adjudication/summary.json) | 四份结构差异均可逐字节解释；后续候选提取以**新保存的完整原始申报**为来源，旧快照未覆盖 |
| 提取 Part I Item 2 MD&A 并核对位置、表格 | `extraction_v4/<ticker>/`、[v4b 自检](../artifacts/m8/20261025T230645Z-four-10q/extraction_self_check_v4b.json) | 四份程序提取、自检通过；共 44 张原始 HTML 表格有原文字节和清理正文字符映射；语义解释仍需研究者审阅 |
| 交接审阅 | [四家公司入口](../artifacts/m8/20261025T230645Z-four-10q/review_v7/index.html)、各公司页面、空白表单和版本回执 | 四家公司均为 `PENDING_HUMAN_REVIEW`；不能把程序边界通过写成人工接受 |

## 文件身份与哈希

下表的 `原文` 指完整 SEC 申报响应，`主文件` 指从该原文中保存的主 HTML，`章节` 指清理后主文档里的 Item 2 候选文本。全部为 SHA-256；官方 URL、获取时间、原始 SEC 字段、旧/新快照与 filing index 的其余哈希见各公司的 `source_identity.json`、`source_receipts.json` 和 [来源自检](../artifacts/m8/20261025T230645Z-four-10q/source_self_check_v1.json)。

| 公司；CIK；accession | 财年/财季；报告期末；申报日 | 原文 SHA-256 | 主文件 SHA-256 | Item 2 SHA-256；表格数 |
| --- | --- | --- | --- | --- |
| MSFT；`0000789019`；`0000950170-25-010491` | FY2025 Q2；2024-12-31；2025-01-29 | `e92280011b21c42f7db19ec041faa11bfa8b7dd8b3e1e2aaa74f13fc1fc4276a` | `6a4de55c96952d773bc44672f77eeee04ba4abb1c5dbaa285d3ba4781795b993` | `60653e4f3d91a82ed212b72c19ff618d769f5c379ac677603c57cefeb5404bf4`；10 |
| AMZN；`0001018724`；`0001018724-25-000036` | FY2025 Q1；2025-03-31；2025-05-02 | `834b76e2ff7363205aa7acc8e9219ded61259ecda3debd5b04c79be7388352b5` | `c41a62c1adc4a886085b495f30fbbeafcf85e3499ac607451966567331830172` | `43015b9006e416f31ff627e52d20bf522e50935c109d269d798641c48c420033`；6 |
| GOOGL；`0001652044`；`0001652044-25-000043` | FY2025 Q1；2025-03-31；2025-04-25 | `e39849eb31baac03f4d0f0ba254735f20ef039635b1812339aa640c0bfb17730` | `c65f5b83b4fe83b74c497d04ef64d2d06867a234309f45f3c67c29b414d24c48` | `5e75ca737c479302764d35550f581badd5461183479da2665806ce109a924883`；14 |
| META；`0001326801`；`0001326801-25-000054` | FY2025 Q1；2025-03-31；2025-05-01 | `c88bbd27e2752dcfc691c7b37a027bbac6260e373603292f172ff067625f2da9` | `b136c495760a92ae199b0cb45a88e261ba031847eb55270306a7fe78b9c8443d` | `5d7cf06e1b4ed5080d9377952b99794be59351a41bbef93f77284e1ae129f3b7`；14 |

财年/财季另由保存原文内的 `DocumentFiscalYearFocus` / `DocumentFiscalPeriodFocus` 复核。四份原始头的 accession、10-Q、CIK、报告期与申报日期和当前 submissions 字段相符。原始头接收时间字符串与 submissions 的 UTC 字符串可转换为同一时刻；**接收时间不是首次公开时间或可交易时点**。新旧已保存 submissions 快照中，对同一报告期均未列出 10-Q/A；搜索范围仅限保存的当前 recent 行及覆盖 2025 年的历史文件，不保证不存在其他更正。

## 版本差异与提取范围

旧候选独立主 HTML 和新完整原文中的内嵌主 HTML 哈希不同，因此初次获取未直接放行。四份逐字节裁定显示：新内嵌主 HTML 去掉 `<XBRL>\n` 前缀及 `</XBRL>` 后缀，旧独立 HTML 去掉一处 114 字节脚本元素后，剩余字节完全相同；每份 [逐公司裁定文件](../artifacts/m8/20261025T230645Z-four-10q/version_adjudication/MSFT.json) 保存相应哈希和位置。脚本来源及功能未核实、未执行；该结构证明不等于历史发布时间证明，也不等于研究者对 MD&A 内容的接受。

提取位置有明确单位：完整原始申报与 HTML 片段使用**字节**区间，清理后的主文档和 Item 2 使用 **Unicode 字符**区间。不能把清理正文的字符位置写成原始 HTML 字节位置。[v4b 自检](../artifacts/m8/20261025T230645Z-four-10q/extraction_self_check_v4b.json) 从保存文件重算四个 Item 2 候选区间、标题锚点及 44 张表的来源与压平文本哈希，结果 `PASS`。AMZN 另有 2 个边界附近的布局表候选，已与 6 张 Item 2 正文表分开记录。表格行列、期间和单位是否对读者可理解，仍由研究者判断。

## 期间和表格：保存正文支持的描述

| 公司 | 可定位的期间提示 | 人工审阅重点 |
| --- | --- | --- |
| MSFT | 同一 MD&A 存在 “Three Months Ended December 31” 和 “Six Months Ended December 31”；部分表同时呈现两种口径。 | 10 张表逐表核对季度与六个月列、单位；另有期末余额和未来履约期间，不压成一段覆盖期。 |
| AMZN | 现金流相关表有 “Three Months Ended March 31” 与 “Twelve Months Ended March 31”；另一表明示 “trailing twelve months”。 | 6 张正文表分别辨别季度、滚动十二个月、日期列及脚注。 |
| GOOGL | 多张表和正文有 “Three Months Ended March 31”；“year ended December 31, 2024” 一处是 2024 年 10-K 的交叉引用。 | 14 张表逐表核对；交叉引用不能误归类为该表年度覆盖期。 |
| META | 多张表和正文有 “Three Months Ended March 31”；起首的 “year ended December 31, 2024” 是 10-K 交叉引用。 | 14 张表逐表核对；相邻说明及指标定义的变更要保留。 |

上述提示来自 `extraction_v4/<ticker>/period_ledger.json`、`table_index.json` 和 `item2_candidate.txt`，是已保存正文中的短语及表格位置，并非整章时间范围。四份 `chapter_period` 均未定义；“前一报告期末次日”最多是工程代理，不能代替叙述、各张表或交叉引用的实际期间。

早期表格脚注提示主要识别 `<sup>`，会漏掉表后普通正文。保存文本显示 AMZN MDA-6，GOOGL MDA-1、5、10、14，以及 META MDA-2、3、5 后有编号注释或 `Note:`（例如 GOOGL 的 constant currency 注释、META 的 ARPP 定义说明）。[v4 表格索引](../artifacts/m8/20261025T230645Z-four-10q/extraction_v4/GOOGL/table_index.json) 将这类邻接提示标为 `NOTE_CUES_NEED_HUMAN_REVIEW`；即使没有 `<sup>`，也不能推断无脚注。MSFT 在相同邻接窗口未检出，不构成无脚注保证。表格标题候选、单位和注释完整性都保留待人审状态。

## 实际命令与尚待收尾

在本次会话工具输出中观察到的获取命令如下；总获取回执记录配置、候选哈希、源码哈希和输出清单，但**没有**保存命令行本身。初次退出码为 1，对应保留的 `PARTIAL_VERSION_REVIEW`；后续裁定与来源自检退出码为 0。

```powershell
$env:PYTHONPATH=(Resolve-Path -LiteralPath 'src').Path
python scripts/m8_ingest_four_10q.py --config configs/smoke.yaml --candidates artifacts/m7_review/20261024T235855Z-closeout/candidates/run_v2/candidates.json --output artifacts/m8/20261025T230645Z-four-10q
python scripts/m8_adjudicate_primary.py --run artifacts/m8/20261025T230645Z-four-10q --candidates artifacts/m7_review/20261024T235855Z-closeout/candidates/run_v2/candidates.json
python scripts/m8_source_selfcheck.py --run artifacts/m8/20261025T230645Z-four-10q
```

每家公司 `extraction_v4/<ticker>/extraction_receipt.json` 保存该公司实际 `m8_extract_10q.py --ingest ... --output ... --version-adjudication ...` 参数、输入和源码哈希。[v4b 自检回执](../artifacts/m8/20261025T230645Z-four-10q/extraction_self_check_v4b.json) 保存 `python scripts/m8_check_extraction.py --run-root artifacts/m8/20261025T230645Z-four-10q --output artifacts/m8/20261025T230645Z-four-10q/extraction_self_check_v4b.json` 的参数与 `PASS`。这些检查与被审实现同属本代理，不称独立审计。

- **浏览器核验：** 在仅绑定 `127.0.0.1` 的本机服务中，实际打开 [最终总入口](../artifacts/m8/20261025T230645Z-four-10q/review_v7/index.html)、四家公司证据页及空白表单。浏览器宽度 1072 CSS 像素，页面滚动宽度 1057；没有整页横向溢出。META MDA-5 原表、压平文本及表后编号注释实际显示。v4 总入口的窄列溢出及 v5 的附加裁定状态空值均保留为中间失败版本；v7 修复。用**全字段空白**的 META 软件测试记录实测下载并重新导入，版本哈希校验通过；记录仍是 `PENDING_HUMAN_REVIEW`、验收门槛 `NOT_MET`，没有填写人审身份、时间或决定。见 [浏览器自检](../artifacts/m8/20261025T230645Z-four-10q/browser_check/browser_validation_v1.json)。它不关闭旧 AAPL v4 表单的浏览器门槛。
- **测试：** [M8 聚焦日志](../artifacts/m8/20261025T230645Z-four-10q/tests/focused_final.log) 为 34 通过；[完整日志](../artifacts/m8/20261025T230645Z-four-10q/tests/full_final.log) 为 115 通过、2 跳过、0 失败。两项跳过均因固定模型文件不在本整理后的 checkout；本轮没有模型推理。
- **提交与分享范围检查：** 本地代码与报告可提交，但原始 SEC 申报、表格全文、缓存和审阅页面仅在忽略的本地 `artifacts/` 中；本轮不推送。暂存审查与最终 commit 见收尾记录。
- **研究者决定：四家均 `PENDING_HUMAN_REVIEW`。** 表单的姓名、时间、逐项决定保持空白；候选提取和打开页面都不算接受。

本轮没有启动五家公司批量表示推理、收益预测、回测或风险优化。现有接入与表格数不能证明文本金融增量价值，也不能排除未来信息污染。
