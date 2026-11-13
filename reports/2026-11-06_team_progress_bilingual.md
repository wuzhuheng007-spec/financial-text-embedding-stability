# Financial text embedding research progress 中英双语报告

As of 6 November 2026. Prepared for team sharing by Zhuheng Wu.

## 金融文本嵌入研究进度报告

团队共享版  2026年11月6日  汇报人 Zhuheng Wu

我们已经搭建并运行了金融文本处理、固定模型嵌入、稳定性比较和检索原型，也完成了两项限定范围的真实 CPU 嵌入试验。目前还没有证据证明文本嵌入能够提高收益预测或带来经济收益。正式 M5 仍是未接受的候选方案。

### 我们研究什么

项目围绕两个问题展开。第一，改变文本切块长度、边界或聚合方式后，公司之间的相似度和近邻关系会怎样变化。第二，在控制历史可用时间并采用公平的无文本基线后，文本特征能否提供额外的金融预测信息。当前实证成果主要回答第一个问题中的小样本工程部分。

| 工作 | 已经完成 | 结果边界 |
| --- | --- | --- |
| 数据与来源链 | 五家公司的年度 10-K Item 1 文本绑定申报身份和哈希；另有季度 MD&A 处理流程。 | 年度稳定性样本为5份独立文本，物理副本不增加样本量。 |
| 嵌入与词法对照 | 固定 MiniLM／ONNX；TF-IDF、BM25；等权和正文 token 加权聚合。 | 使用预训练编码器，没有训练或微调新模型。 |
| S2 与 S3 | 分别完成长度网格和内部切点位移网格；保存指标和向量哈希。 | 一个模型、五家公司；只支持固定输入的描述。 |
| 检索助手 | LlamaIndex 接入 BM25、dense、hybrid；166个来源片段。 | retrieval_only，answer=null；未运行生成或真实 MoE。 |
| M5 执行准备 | 候选、历史日历、日期覆盖审计及结构导入校验器已实现。 | 候选未冻结；结构通过不代表真实金融数据已准入。 |
| 分享与复核 | 已有公开 M0–M10 分享；本次整理后续代码、结果和中英报告。 | 只分享脱敏材料，完整原始数据和私有开发历史不在发布范围。 |

### 现在还差什么

不只是把数据导入。还需要取得可用且有使用依据的历史文件，完成证券连接和时间证据审计，并由研究者明确研究类型、队列、标签和评价规则。最新本地检查由执行者报告：登记位置中新增授权 M5 文件为0；本次没有收到新的机器回执来独立复核该检查。

## Financial text embedding research progress

Team report  6 November 2026  Prepared for sharing by Zhuheng Wu

We have implemented and exercised the text pipeline, pinned embedding workflow, stability comparisons and retrieval prototype. Two bounded CPU embedding probes have produced saved results. We have not established that text improves return forecasts or generates economic value. Formal M5 remains a proposed, unaccepted protocol.

### Research questions

The project asks how document splitting and aggregation affect firm similarities and peer groups, and whether text adds predictive information beyond fair price-only baselines without relying on future information. The current empirical evidence addresses a small engineering subset of the stability question.

| Workstream | Completed work | Scope |
| --- | --- | --- |
| Sources | Five annual 10-K Item 1 extracts have filing identities and text hashes; separate quarterly MD&A workflows exist. | Five independent annual documents, not a broad financial panel. |
| Representations | Pinned MiniLM/ONNX, TF-IDF, BM25, equal-chunk and body-token-weighted aggregation. | Pretrained inference, not new model training. |
| S2 and S3 | A complete length grid and a separate internal-boundary-shift grid, with saved metrics and vector hashes. | One model and five firms; descriptive evidence only. |
| Retrieval | LlamaIndex with BM25, dense and hybrid retrieval over 166 source-linked chunks. | Retrieval-only; answer=null; no generation or neural MoE run. |
| M5 preparation | Candidate execution rules, calendar, horizon audits and a structural importer. | No accepted protocol or admitted financial evaluation set. |
| Sharing | Earlier curated M0–M10 release, now extended with supplied later code and results. | No raw filings, vendor responses, weights, vectors or private history. |

The latest researcher-relayed local intake check reports zero newly registered authorized M5 files. That check had no new attached machine receipt and is therefore attributed to its executor. It does not prove that files or permissions do not exist elsewhere.

### Immediate need

Obtain actual source files and evidence of permitted use, settle the scientific design, and audit the historical joins and information clock. A successful file import alone would not make the project ready to claim financial validity.

## 当前技术路线与已运行试验

Current implementation and executed probes

### 已经实现的处理链

SEC 文本身份与哈希核验 → 固定 tokenizer → 无重叠切块 → 固定 MiniLM 的 ONNX CPU 编码 → 片段归一化 → 等权或正文 token 加权聚合 → 公司向量归一化 → 余弦矩阵、排序、Top-2 近邻和交互指标。每次受控运行先核验输入、配置、代码、模型文件和独立决定记录。

Implementation: verify source identity and bytes; tokenize and split without overlap; encode with pinned MiniLM/ONNX on CPU; normalize chunk vectors; aggregate equally or by actual body-token length; normalize firm vectors; calculate cosine, ranks, peers and declared interactions. Controlled runners check version-bound evidence before inference.

片段内部采用 attention-mask mean pooling，包含 mask 为有效位置的特殊 token；文档层正文长度权重不包含特殊 token。二者不能混淆。No whole-document truncation was used in these probes. Chunk means include attended special tokens; document-level body-token weights exclude them.

| Protocol | S2 length probe | S3 boundary shift |
| --- | --- | --- |
| Axis | 118, 123, 128, 133, 138 body tokens | 128-token base; shifts -10, -5, 0, +5, +10 |
| Aggregation | Equal chunks and body-token weighted | Equal chunks and body-token weighted |
| Completed jobs | 25 document-length jobs | 25 document-shift jobs |
| Saved chunk vectors | 1,083 | 1,080 |
| Saved company vectors | 50 | 50 |
| Reference | 128 tokens within each pooling | Zero shift within each pooling |
| Top-2 set changes | 4 firm/arm rows, all MSFT | 0 firm/arm rows |
| Outcome status | Saved metrics; local saved-vector verification reported PASS | Saved metrics; local saved-vector verification reported PASS |

### 如何理解这些结果

S2 的四行近邻集合变化出现在123等权、123加权、133等权、138等权。S3 的集合没有变化，但余弦值、部分名次和第二／第三名分差仍然变化。Top-2 不变不等于向量不变。两套参考的15个保存数组，按本地兼容性回读记录一致；附件审阅者没有取得向量本体来重放该比较。

These protocols change several things together. S2 changes boundaries, chunk counts and tails; S3 preserves chunk counts but changes chunk contents, first/tail lengths and body-token weights. Comparing their magnitudes cannot isolate a causal boundary effect. The ten firm pairs are dependent; no population, semantic-quality or financial conclusion follows.

## 完整非参考设置结果

All nonreference settings  Display rounded to six decimal places

平均绝对余弦变化与排序相关使用10个相互依赖的公司对；Top-2重合率先按每家公司计算，再对5家公司取均值。每个设置均与同一 pooling 的参考比较。Equal = equal_chunks；Weighted = body_token_weighted。

Mean absolute cosine change and Spearman use ten dependent pairs. Top-2 overlap is the mean of five firm-level set overlaps. Reference arms are included in the complete machine outputs and have no nonreference effect row below.

### S2 正文切块长度

| Tokens | Pooling | Mean |change| | Spearman | Top-2 |
| --- | --- | --- | --- | --- |
| 118 | Equal | 0.009057 | 0.939394 | 1.0 |
| 118 | Weighted | 0.007973 | 0.951515 | 1.0 |
| 123 | Equal | 0.012153 | 0.854545 | 0.9 |
| 123 | Weighted | 0.012047 | 0.878788 | 0.9 |
| 133 | Equal | 0.009280 | 0.866667 | 0.9 |
| 133 | Weighted | 0.006916 | 0.951515 | 1.0 |
| 138 | Equal | 0.009474 | 0.890909 | 0.9 |
| 138 | Weighted | 0.007581 | 0.975758 | 1.0 |

### S3 内部切点位移

| Shift | Pooling | Mean |change| | Spearman | Top-2 |
| --- | --- | --- | --- | --- |
| -10 | Equal | 0.004794 | 0.939394 | 1.0 |
| -10 | Weighted | 0.004800 | 0.951515 | 1.0 |
| -5 | Equal | 0.003905 | 0.987879 | 1.0 |
| -5 | Weighted | 0.004042 | 1.000000 | 1.0 |
| 5 | Equal | 0.005294 | 0.987879 | 1.0 |
| 5 | Weighted | 0.005074 | 0.963636 | 1.0 |
| 10 | Equal | 0.004714 | 0.987879 | 1.0 |
| 10 | Weighted | 0.004506 | 0.951515 | 1.0 |

原始精度及完整矩阵见 results/s2_round4/metrics.json 与 results/s3_round6/metrics.json。All declared arms and full-precision records remain available; no favorable setting was selected as a new default from these outcomes.

这些是文本表示的变化，不是收益率、预测误差或风险损失。These are representation changes, not returns, forecasting errors or trading losses.

## 正式金融研究仍需通过的门槛

Remaining gates before a formal M5 evaluation

M5 是候选设计，不是已执行实验。拟议比较为相同行上的 F0 价格特征、F1 价格加词法特征、F2 价格加 dense 特征，并采用时间划分和训练内拟合。候选提及五交易日期限及二十一日替代项；这些科学选择尚未获得本轮接受。

The proposed comparison uses matched rows for price-only F0, price plus lexical F1, and price plus dense F2, with chronological splits and training-only transforms. Proposed horizons and numerical rules are not accepted experimental results.

| Gate | Required contribution and evidence | Owner |
| --- | --- | --- |
| 数据与权限<br>Data and access | 实际取得的历史证券、连接、价格、公司行动／退出、文本文件及产品版本；具体使用和导出依据。<br>Actual files, product/version identity and permitted-use/export evidence. | Researcher with supervisor or data librarian |
| 研究口径<br>Scientific scope | 严格历史时点预测还是固定快照回溯；队列、期限、收益口径、退出处理、基线及评价规则。<br>Choose historical forecasting versus retrospective scope; freeze cohort, outcomes and comparisons. | Researcher and supervisors |
| 连接与有效期<br>Historical joins | 核查跨表CIK和证券完整性、有效期包含、跨CIK冲突、上市市场，以及 ambiguity_flag 的含义。<br>Resolve links, dates, market applicability and ambiguity semantics. | Data engineering with source evidence |
| 信息时钟<br>Information clock | 文本确切版本及公开证据、决策前价格可用性、模型时间证据；不把SEC接受时间当作精确公开时间。<br>Establish what was available before the decision, including the text and model vintage. | Researcher and source audit |
| 固定协议后执行<br>Execution after acceptance | 在数据门槛和版本限定决定通过后，才构造正式标签、拟合变换与预测器并评价。<br>Run financial work only after data and protocol gates pass. | Researcher and implementation |

现有 XNYS 日历包含1,588个常规交易日、63个工作日休市和14个提前收盘，覆盖2019-11-01至2026-02-28的规则区间。它是回溯日历，不证明每家公司当时适用的上市市场。五家公司的5／21日期限各有5个保存行端点，只有4家满足所需历史窗口；获准金融观测数仍为null。

The calendar and saved-row coverage are engineering checks, not an admitted financial sample. They do not establish information availability at the historical decision time.

## 证据索引与共享范围

Evidence index and publication boundaries

证据分层：机器指标可在附件中检查；原始向量的重算状态来自本地保存回执与报告；最新无新增数据的检查由执行者报告。本次汇总没有重新运行编码器、训练或回测。Evidence levels distinguish attached artifacts, locally reported verification and the newest user-relayed intake status.

| Evidence | Version bound location or identity |
| --- | --- |
| Earlier M0–M10 | Public share commit 2371e577243bbad91bdbc126f74c7e9845b434a9; docs/EVIDENCE_TRACE_M0_M7.md and docs/EVIDENCE_TRACE_M8_M10.md |
| S2 complete metrics | results/s2_round4/metrics.json<br>SHA-256 879b9753af91e8f1a1a9248aec4b81577a16c2ac902b33b8b63da3b148d2ac6d |
| S3 complete metrics | results/s3_round6/metrics.json<br>SHA-256 77e4bcdd7074a0206ccf798e4770106177b2409c57ecef02a7ef3d51b0b22f27 |
| M5 unchanged candidate | configs/m5_financial_protocol_round8.json<br>SHA-256 f1fa28949353adea6aa179854441b48658415ef577a006fe76bd01c779ddaa38 |
| Importer fixed source | scripts/validate_m5_import_round9.py<br>SHA-256 bae792fa4d9465af218a010ff6d7284b3293806757e639f84166cdd02147547c |
| Latest local source identity | Round-10 handoff records local commit 6d5922d4f5bff830a2d4b748091dbf41824c2ee2; this is not the new public-share commit. |

Importer review: the same synthetic input was rejected by the old code and passed by the fixed code. Eighteen supplied test functions and 288 independently constructed interval/permutation checks passed in the attachment review. This is software validation; it does not certify licensing, source truth or complete historical joins.

历史偏差仍保留：第三轮合成回归曾误触真实 Encoder，精确调用数未知；第四轮未推送的中间提交曾含私人文本，最终跟踪文件已移除。本次公开快照不引入该私有历史。Earlier deviations remain recorded without reproducing private text or rewriting the original history.

共享内容为代码、测试、候选、完整轻量指标、日历和脱敏反例；不包括原文、行情响应、权重、向量、私人决定或凭据。不新增许可证。The share is a curated snapshot of supplied files, not a complete mirror of the inaccessible Windows checkout; it cannot reproduce private-source runs from a clone alone.

官方依据已打开核对：SEC Webmaster FAQ（接受与网页可用存在不保证的延迟）；固定revision模型卡（没有给出全部训练语料的可核验统一截止时间）；Massive aggregate-bar FAQ（拆股复权不等于股息总收益）。具体链接与适用限制见同名 Markdown 报告。

## 口语化中英双语汇报稿

Plain-language briefing for the team

### 中文

我们目前主要完成了两部分工作。第一部分是把金融文本处理、切块、向量生成和结果追踪这条流程搭起来。第二部分是用五家公司的固定年报文本，实际跑了两组小实验：一组调整每块的长度，另一组保持块数不变，只移动内部切点。

结果是，调整长度时，微软在四个设置对照里的前两个近邻集合发生了变化；只移动边界时，这次所有公司的前两个近邻集合都没变，但相似度分数和部分排序还是会变。所以现在可以说，处理方式会影响这组固定输入的表示结果，还不能说模型普遍不稳定，更不能说已经证明会影响收益。

我们也做了一个能找回原文证据的检索助手，但目前没有生成答案，也没有运行真正的MoE。正式金融预测还没开始。接下来除了拿到历史数据，还要和导师确认研究口径，检查公司和证券的历史对应关系，以及每条信息在决策时是否已经可用。等这些条件都满足，再比较只用价格的模型和加入文本后的模型。

### English

We have mainly completed two pieces of work. First, we built the pipeline for processing financial text, splitting it into chunks, producing embeddings and tracing the outputs back to fixed inputs and settings. Second, we ran two small experiments on five fixed annual-report extracts. One changed chunk length; the other kept the number of chunks fixed and shifted the internal boundaries.

In the length experiment, Microsoft’s top-two peer set changed in four setting comparisons. In the boundary experiment, none of the top-two sets changed, although similarity scores and some rankings still moved. This shows sensitivity within this particular setup. It does not establish general instability or an effect on returns.

We also built a retrieval assistant that returns source evidence. It has not generated answers or run a genuine MoE model. Formal financial prediction has not started. The next step requires both actual historical data and agreed research rules: which firms and securities enter the sample, how they link over time, what outcome we predict, and which information was available before each decision. Once those conditions are met, we can fairly compare a price-only model with models that also use text. We are not claiming predictive gains or economic value yet.

## Primary source checks

- SEC, Webmaster Frequently Asked Questions: https://www.sec.gov/about/webmaster-frequently-asked-questions . Acceptance is not an exact first-public timestamp.
- Sentence Transformers, all-MiniLM-L6-v2 model card at the pinned revision: https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2/blob/1110a243fdf4706b3f48f1d95db1a4f5529b4d41/README.md . No complete verified global training-data cutoff is supplied.
- Massive, aggregate bar adjustment FAQ: https://massive.com/knowledge-base/article/is-massives-stock-data-adjusted-for-splits-or-dividends . Applicable aggregate bars are split-adjusted, not dividend total returns.

These sources were opened on 6 November 2026. They support the stated limitations, not the numerical outcomes of this project.
