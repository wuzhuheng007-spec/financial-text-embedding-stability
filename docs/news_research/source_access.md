# 新闻来源接入 / News-source access

Checked / 核对日期：2026-11-14（Asia/Shanghai）。Scope / 范围：公开文档与公开预览；没有账户连接、批量下载或正式样本准入。Public documentation and previews only; no authenticated connection, bulk download or formal sample admission.

| 候选 / Candidate | 已读事实 / Inspected fact | 下一步 / Next step |
| --- | --- | --- |
| [HF financial-news-multisource](https://huggingface.co/datasets/Brianferrell787/financial-news-multisource/blob/main/README.md) | 卡片包含申请访问及不再分发正文的条件；日期字段可能经过归一化。Card includes access conditions and no redistribution of article text; dates may be normalized. | 确认访问，固定 revision/subset，检查原始时间与正文。Confirm access; pin revision/subset and inspect original timing/text. |
| [HF ML-Algo/FNSPID](https://huggingface.co/datasets/ML-Algo/FNSPID) | 公开预览的部分 Article 字段为空；不能推广为全库无正文。Some preview rows have null Article fields; this is not a corpus-wide result. | 核验具体分发版本、原始出处与小样本。Verify distribution identity, upstream source and a bounded sample. |
| [金十官方 MCP / Official Jin10 MCP](https://mcp.jin10.com/app/doc.html) | 官方说明 Token 接入及快讯/资讯工具。Official guide documents token-based access and flash/news tools. | 安全配置后核对实际历史深度、分页、字段和限额。After secure setup, inspect actual history, paging, fields and limits. |
| [WRDS RavenPack](https://wrds-www.wharton.upenn.edu/pages/about/data-vendors/ravenpack/) | 官方有产品介绍，不能据此认定账户正文权限。Product listing does not establish account entitlement to text. | 使用实际查询回执核对。Check actual query receipts and fields. |

## Hugging Face

公开可读内容先匿名检查。私有或 gated 数据需要的权限应通过安全登录/本机凭据配置完成；若需要 Token，按官方文档使用最小只读范围，不把值贴进聊天、日志或 GitHub。[官方 Token 指南](https://huggingface.co/docs/hub/security-tokens)。没有在本轮申请 gated 访问或接受条款。

Inspect public content anonymously first. Configure private/gated access through secure sign-in or local credentials. If a token is needed, use minimum read access following the [official guide](https://huggingface.co/docs/hub/security-tokens); keep its value out of chat, logs and GitHub. This update did not submit gated-access requests or accept terms.

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
