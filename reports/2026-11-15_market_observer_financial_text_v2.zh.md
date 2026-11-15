# 训练期财务文本输入 v2 与可追溯财报事实卡｜2026 年 11 月 15 日

执行日期为 **2026-11-15，Asia/Shanghai**。本轮只读取已保存、公告日期位于 **2025-04-01 至 2026-03-31** 的 NVIDIA、Micron SEC 财报附件及其同一申报编号下的主申报。范围见[冻结配置](../configs/market_observer_financial_text_v2.json)，实际阶段见 [P0–P7 台账](../results/market_observer_financial_text_v2_20261115/plan_execution.csv)。没有重新打开 2026 年四月至七月的正文或收益标签来设计规则，没有新增下载、模型拟合或收益比较。规则设计者在此前工作中已经看过四月至五月的开发结果，所以这次工程修订**不具备独立验证性质**。

## 来源与内容清单

保存的来源清单含 **8 份财报附件和 8 份关联主申报**，对应 8 个 SEC 事件组。运行前核对原始 HTML 与旧抽取文本的哈希；[预运行回执](../results/market_observer_financial_text_v2_20261115/preflight_retention_r4_redacted.json)记录配置、实际执行代码及辅助输入版本。公开[历史事实卡](../results/market_observer_financial_text_v2_20261115/fact_cards_v4_redacted.json)保留 SEC 原件链接、文档哈希和定位；逐条原文引文与原件留本机。[NVIDIA 2025 年 5 月财报附件](https://www.sec.gov/Archives/edgar/data/1045810/000104581025000115/q1fy26pr.htm)和[美光 2025 年 6 月财报附件](https://www.sec.gov/Archives/edgar/data/723125/000072312525000019/a2025q3ex991-pressrelease.htm)均为 SEC 托管的官方原件。

先按日期冻结 **72 项“文档 × 财务主题”**清单，覆盖九类所需信息。随后同一执行者回读原件，发现首版定位规则漏掉 **11 项**；旧清单保留，独立审阅层改为 **67 项已定位、4 项口径歧义、1 项按现行规则未找到**。“未找到”只说明保存原件中没有按规则定位，不能写成公司未披露。这份清单标记为 `CODEX_REFERENCE_DRAFT`，不是独立人工标准。

## v2 输入、事实卡与首次负面检查

初版 v2 从 16 份原件生成 **2,398 个带来源定位的块**：747 段、1,645 个表格行、6 个标题。它将原 HTML 的 DOM 路径与清理文本偏移分开，模型适配层按每事件 20,000 字符预算选入 **352 个完整块**，将 **2,046 个未选块**及理由显式保存；8 个事件都有遗漏。数字、脚注和未被选中的完整块留在私人原始层。[构建汇总](../results/market_observer_financial_text_v2_20261115/build_redacted.json)与[格式检查](../results/market_observer_financial_text_v2_20261115/format_validation_redacted.json)绑定这些数字。旧 128 词 TF-IDF 入口保留；新接口只做格式检查，状态为 `NOT_FITTED`。

确定性规则生成 **71 条候选事实**，另存的 `CODEX_REVIEW` 原件核对判定 **60 条有来源支持、11 条歧义**。8 张历史财报卡分别保留证券、原文指标和数值、财期、单位、可确认的 GAAP 口径、来源哈希与定位，以及未知项。**32 组**比较值绑定同一来源表中紧邻的上一财季；**8 组**指引上下界明确标为由来源表达式和候选 ID 计算。未保存合格的公布前共识，因此没有“超预期”标签。尚有 NVIDIA 将设备与无形资产合并披露的支出、部分自由现金流期间或单位、美光分部表金额量级等歧义。事实卡是开发材料，不能据此推断一般抽取准确率。

第一次真实训练期保留检查得到**负面结果**：以 **71 条已审候选事实**为分母，旧输入有 **34 条**完整证据，初版 v2 实际选入模型的文本只有 **18 条**。完整标准要求指标、数值、期间、单位、必要会计口径及适用的上季比较语境同时存在。初版 v2 没有让任何旧版不完整事实变成严格完整，60 条来源支持事实中仍有 **37 条**在模型输入中不完整。另以 **72 个主题片段**为分母，有 4 个片段从旧版“丢失”变成新版“完整保留”；这一较弱的片段口径不能替代事实口径。[首次比较回执](../results/market_observer_financial_text_v2_20261115/retention_redacted.json)将文档、事实与 **483 个字段评估槽**的分母分开保存。

缺口已定位到表头绑定：[美光 2025 年 6 月原件](https://www.sec.gov/Archives/edgar/data/723125/000072312525000019/a2025q3ex991-pressrelease.htm)的 `FQ3-25`、`FQ2-25` 财期和“in millions”单位在营收数值之前；初版 v2 选中了数值行，却没有识别 `FQ` 财期形式并一起选入表头。NVIDIA 的一张现金流表则在前四行之后才列出期间，超出了原扫描范围。第一次失败的代码与结果均保留。

## 独立版本 v2.1 修订与模型适配缺口

[v2.1 配置和变更记录](../configs/market_observer_financial_text_v21_CHANGE.md)在重新构建前冻结一项训练期修订：识别原文 `FQn-yy` 表头，扫描更靠后的现金流表头，并把原文表头及数值行复制到实际清理文本。仍使用同一批 **16 份文档、71 条已审候选、每事件 20,000 字符预算**。 [v2.1 构建回执](../results/market_observer_financial_text_v21_20261115/build_redacted.json)及[运行前哈希](../results/market_observer_financial_text_v21_20261115/preflight_build_redacted.json)保存来源和代码版本。修订针对第一次训练内容检查发现的问题，仍受此前验证结果已被设计者看过的影响。

[v2.1 内容比较](../results/market_observer_financial_text_v21_20261115/retention_redacted.json)继续使用 **71 条事实、483 个字段槽**。在**完整、未压入模型预算的私人解析块**中，严格完整事实由初版 v2 的 **18/71** 升至 **34/71**，与旧构建器 **34/71** 持平；主题片段由 **47/72** 升至 **67/72**。另份[来源定位审计](../results/market_observer_financial_text_v21_20261115/source_audit_redacted.json)将 **189 个选中表格行**和 **165 处复制表头**回查训练期原件，定位不匹配为零。这证明来源解析层保住了更多原文关系，**不等于实际模型输入变好或语义已获独立人工认证**。

相同字符预算下，v2.1 实际选入 **336 个块**，少于初版 v2 的 352 个；严格完整事实仍为 **18/71**。所选主题片段从 **43/72** 升至 **52/72**，上季比较期间字段从 **16/32** 升至 **32/32**，但 **60 条来源支持事实中仍有 35 条**在实际模型输入中不完整，旧版不完整事实没有一条因此变成严格完整。具体如美光 2025 年 6 月营收：单位和 `FQ` 表头已经与数值同块，但包含财期截止日的公告引言仍未被选入；完整解析层通过，实际模型输入仍只算部分保留。复制表头也使块变长，挤出其他数值行。两个版本各自的失败与修订结果均保留，不合并口径；本轮没有据此拟合收益模型。

## 页面与研究边界

现有私有[研究页面](https://quant-observer-research-nov15.soliddhole4.chatgpt.site/?tab=financial)已发布版本 3，展示带历史财期的事实卡。[验收回执](../results/market_observer_financial_text_v2_20261115/site_acceptance_redacted.json)记录本机桌面和真实 390px 视口检查：窄屏全页没有横向溢出，事实表可单独滚动；来源链接形式与原生部署版本已回读。远端登录态浏览器点击未运行。页面仍为仅所有者可见，dot 与实时行情没有随此次更新变成已连接。

这只是训练材料上的同一团队内容工程检查；旧验证结果曾影响设计，SEC 归档正文的历史首次可见时刻仍未独立证明。下一个**单一工程改动**应是更节省预算的适配器，把表头与财期引言连同数值放入模型输入，不反复复制长表头；仍须先用训练期材料审查。后续评价还需独立审阅真正未接触的新时期材料，冻结有时间依据的输入规则。已反复使用的四月至五月开发期以及早先暴露的六月至七月不能改称未接触测试集。正式 M5 准入、收益预测、交易和模型晋升均为 `NOT_RUN`。

最终[聚焦软件日志](../results/market_observer_financial_text_v2_20261115/focused_tests_final.log)及[回执](../results/market_observer_financial_text_v2_20261115/focused_tests_receipt_redacted.json)记录 **33 项合成检查通过**；先前分批检查与之重叠，不能累加。另份[发布前回执](../results/market_observer_financial_text_v2_20261115/publication_consistency_redacted.json)检查暂存清单、文档链接、数字／状态一致性和常见凭据／私人路径形式。原件回读、浏览器验收与两次训练内容比较分别统计，本轮模型拟合仍为 **0 次**。

[主内容提交](https://github.com/wuzhuheng007-spec/financial-text-embedding-stability/commit/8d8dc75d942222db76c10d75d080824467663696)已推送至既有协作分支，九份关键远端文件与本机字节回读一致；见[同步回执](../results/market_observer_financial_text_v2_20261115/sync_receipt_redacted.json)。另做本机脱敏交接文件夹和 ZIP，已通过 CRC 及 SHA-256 回读。本轮未合并默认分支，也未修改 Overleaf。
