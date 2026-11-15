# Quant Portal: personal test first

The current deliverable is a **loopback-only personal test** in [apps/quant_portal](../../apps/quant_portal). It provides a private account, editable watchlist, personal settings and source-linked historical NVDA/MU SEC fact summaries. Prices and Jin10/moomoo news are **not connected** in this app. No fabricated quote, prediction or market result is shown. The original private research Site and research scripts remain separate.

Read in this order:

1. [Personal start, status, stop and first registration](../../apps/quant_portal/README.md).
2. [2026-11-16 English progress](../../reports/2026-11-16_quant_portal_personal.en.md) / [中文进度](../../reports/2026-11-16_quant_portal_personal.zh.md) and [plan–actual ledger](../../results/quant_portal_20261116/plan_execution.csv).
3. [Source rights register](source_rights_register.md), [open-source proposal](open_source_review.md) and [third-party inventory](third_party_inventory.md).
4. [Future Alibaba Cloud operations and cost](../deployment/quant_portal/README.md) only when a multiuser release is selected and its data rights are cleared.

The local fact-card input is the already redacted, SHA-pinned [v4 research summary](../../results/market_observer_financial_text_v2_20261115/fact_cards_v4_redacted.json), and [two official SEC exhibits](../../results/quant_portal_20261116/fact_source_web_check.json) were revisited for the selected values. They are same-team `CODEX_REVIEW_DEVELOPMENT` material with historical fiscal periods, not independent human gold or current earnings. The financial-text 20,000-character model-input gap, exposed validation periods, formal M5 admission and financial evaluation remain unchanged.

Invitation-only multiuser operation, PostgreSQL containers, public DNS/HTTPS, provider feeds and load capacity are a **later plan**. No Alibaba Cloud resource was purchased or deployed in this round. The private Site has not been made public.
