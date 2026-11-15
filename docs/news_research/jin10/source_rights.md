# Jin10 MCP source and use boundary / 金十 MCP 来源与使用边界

Checked 2026-11-15 (Asia/Shanghai). This register records the pages consulted for the bounded development intake. The pages can change; this is not a contract or a claim of broader rights.

| Official page | What it supports | What remains unconfirmed |
| --- | --- | --- |
| [MCP integration guide](https://mcp.jin10.com/app/doc.html) | Official endpoint, Bearer-token setup, and demo shapes for quote, flash and news tools. The guide names `list_flash` and gives a sample `id`/`next_offset`; those are **examples**, not an observed live tool schema. | The page does not list `list_calendar`, a call quota, collection frequency, retention period, or redistribution permission. A [bounded authenticated probe](../../../results/news_jin10_20261115/probe_redacted_run2.json) later observed `list_calendar` and actual flash fields; that observation is not a provider policy or a guarantee of future availability. |
| [Jin10 user agreement](https://flash.jin10.com/agreement) | Clauses 2.4 and 2.6 describe restrictions on unauthorized copying/distribution and a personal, noncommercial use right. | Whether a particular MCP subscription grants additional storage, internal display, or redistribution rights. |
| [Jin10 copyright statement](https://www.jin10.com/about/index.html) | It restricts third-party quoting, copying, reposting and use without authorization, and calls for source attribution within any authorization. | Permission to publish raw flash/news text, raw API responses, or a mirrored feed. |

## Handling rule for this project

Use a short private, bounded connection test only. Keep provider response bodies and the SQLite database in ignored local `artifacts/` paths. Public GitHub files may contain code, schema descriptions, non-content aggregate receipts, hashes and links to official source pages; they must not include a Token, raw response, article/flash body, private configuration or account information. A local display is a development view, not permission for public republication.

Authentication establishes access, **not** storage, mirroring or redistribution rights. Until the applicable MCP terms and quota are confirmed, do not start a scheduled poll or the prepared 24-hour trial. A first local receipt time does not establish historical first-public availability, and a post-release consensus value is not a qualified pre-release snapshot.
