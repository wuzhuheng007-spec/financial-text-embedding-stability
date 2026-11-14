# 新闻来源接入 / News-source access

Checked / 核对日期：2026-11-14（Asia/Shanghai）。Scope / 范围：公开文档、原作者固定版本，以及一次匿名有界 CSV 小样本读取；没有账户连接、全库下载或正式金融样本准入。Public documentation, pinned original-author releases and one anonymous bounded CSV sample; no account connection, full-corpus download or formal financial admission.

| 候选 / Candidate | 已读事实 / Inspected fact | 下一步 / Next step |
| --- | --- | --- |
| [HF financial-news-multisource](https://huggingface.co/datasets/Brianferrell787/financial-news-multisource/blob/main/README.md) | 卡片包含申请访问及不再分发正文的条件；日期字段可能经过归一化。Card includes access conditions and no redistribution of article text; dates may be normalized. | 确认访问，固定 revision/subset，检查原始时间与正文。Confirm access; pin revision/subset and inspect original timing/text. |
| [原作者 FNSPID / Original-author FNSPID](https://huggingface.co/datasets/Zihan1004/FNSPID/tree/bf9189c41527198897d1af3e17b1a0095279fc45) | 已对 `Stock_news/nasdaq_exteral_data.csv` 匿名分段读取并保存本机样本；下文列出来源、许可差异与限额。An anonymous bounded sample of that exact file is retained locally; provenance, license conflict and limits follow. | 检查公司事件字段；核实历史身份及原始发布时间后才能讨论金融准入。Check company-event fields; verify historical identity and original timing before financial admission. |
| [HF ML-Algo/FNSPID](https://huggingface.co/datasets/ML-Algo/FNSPID) | 公开预览的部分 Article 字段为空；不能推广为全库无正文。Some preview rows have null Article fields; this is not a corpus-wide result. | 核验具体分发版本、原始出处与小样本。Verify distribution identity, upstream source and a bounded sample. |
| [金十官方 MCP / Official Jin10 MCP](https://mcp.jin10.com/app/doc.html) | 官方说明 Token 接入及快讯/资讯工具。Official guide documents token-based access and flash/news tools. | 安全配置后核对实际历史深度、分页、字段和限额。After secure setup, inspect actual history, paging, fields and limits. |
| [WRDS RavenPack](https://wrds-www.wharton.upenn.edu/pages/about/data-vendors/ravenpack/) | 官方有产品介绍，不能据此认定账户正文权限。Product listing does not establish account entitlement to text. | 使用实际查询回执核对。Check actual query receipts and fields. |

## Hugging Face

公开可读内容先匿名检查。私有或 gated 数据需要的权限应通过安全登录/本机凭据配置完成；若需要 Token，按官方文档使用最小只读范围，不把值贴进聊天、日志或 GitHub。[官方 Token 指南](https://huggingface.co/docs/hub/security-tokens)。没有在本轮申请 gated 访问或接受条款。

Inspect public content anonymously first. Configure private/gated access through secure sign-in or local credentials. If a token is needed, use minimum read access following the [official guide](https://huggingface.co/docs/hub/security-tokens); keep its value out of chat, logs and GitHub. This update did not submit gated-access requests or accept terms.

### Original-author FNSPID bounded sample / 原作者 FNSPID 有界样本

The original [GitHub README at commit `4054842`](https://github.com/Zdong104/FNSPID_Financial_News_Dataset/blob/4054842ec476953b30ee874d4b7e8eea786a21fa/README.md) points to the [Hugging Face file at revision `bf9189c`](https://huggingface.co/datasets/Zihan1004/FNSPID/blob/bf9189c41527198897d1af3e17b1a0095279fc45/Stock_news/nasdaq_exteral_data.csv). Preserve the file's spelling: `Stock_news/nasdaq_exteral_data.csv`. The [pinned GitHub LICENSE](https://github.com/Zdong104/FNSPID_Financial_News_Dataset/blob/4054842ec476953b30ee874d4b7e8eea786a21fa/LICENSE) and [pinned HF README](https://huggingface.co/datasets/Zihan1004/FNSPID/blob/bf9189c41527198897d1af3e17b1a0095279fc45/README.md) state CC BY-NC-4.0; the HF README says commercial use requires separate permission. A dated note near the top of the GitHub README says commercial and research rights were released, while later text on that page restricts commercial use. This conflict remains unresolved; it does not establish commercial permission or third-party article-text redistribution rights. The [Creative Commons explanation](https://creativecommons.org/licenses/by-nc/4.0/) applies to material actually covered by that license and does not resolve other rights.

本轮只按原项目的非商业研究声明，在未使用账户或 Token 的情况下读取本机小样本；正文不上传 GitHub 或交接包。A `Range` response returned HTTP 206 for the pinned file. The run scanned 100 complete logical CSV records and saved 100 nonempty, nonsentinel `Article` values, then stopped at the saved-body limit; the client consumed 737,280 bytes including a preflight probe. The requested byte ranges were capped at 50 MiB. See the [source and sample receipt](../../results/news_n1_20261114/source_sample_receipt.json) and [aggregate profile](../../results/news_n1_20261114/sample_profile.json). These counts describe only this first-record sample, not the full dataset. The original 23.2 GB file's provider-displayed hash was not independently recomputed.

The observed header separates `Article` from four named summary fields and contains no sentiment-label column. The saved text is usable for a bounded text-engineering field check; completeness against publisher originals is unknown. Stored `Date` strings mostly fall at UTC midnight, which does not verify an intraday publication instant. `Stock_symbol` is a candidate association only. Historical company–security identity, timestamp validity and a full financial protocol remain open.

## Jin10 / 金十

接入地址采用官方文档中的 `https://mcp.jin10.com/mcp`。具体启用步骤以平台当前说明和安全凭据配置为准。已有用户账号截图不等于 Token 已配置。工具可用性不等于已有完整历史数据库，也不等于已获正文再发布授权。

Use the official endpoint `https://mcp.jin10.com/mcp` and the platform's current setup instructions with secure credentials. An account screenshot does not configure a token. Tool availability establishes neither a complete historical database nor permission to republish article text.

## 统一的小样本准入 / Common bounded-sample admission

1. 记录来源 ID、版本/取得时间、可用范围和权限证据。Record source identity, version/retrieval time, available scope and access evidence.
2. 取固定小样本，保留文章 ID、标题/正文、原语言、来源 URL 和原始时间字段。Take a fixed small sample retaining IDs, text, original language, URLs and raw time fields.
3. 核对是否为转载、修订、翻译、事后市场总结或仅有标题；这些情况分别编码。Distinguish syndication, revisions, translations, market recaps and headline-only records.
4. 日期只有天级、转换规则未知或正文缺失时如实标注，不猜造精确时刻或正文。Mark day-level precision, unknown conversions or missing text; do not invent them.
5. 用明确 entity_id 进行抽取，证券身份未核验时 financial_eligible=false。Extract against entity IDs; keep financial_eligible=false until historical security identity is supported.
6. 原始文本放在允许的存储位置；GitHub 保留 schema、来源清单、可分享结果及运行回执。Store original text only where permitted; publish schemas, source registers, shareable results and receipts on GitHub.

来源接入状态和金融实验状态分别维护；不能把账户连接成功或下载成功写成金融有效性。Track source access separately from financial experiments; successful authentication or download does not establish financial usefulness.
