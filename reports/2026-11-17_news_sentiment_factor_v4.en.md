# News sentiment factor v4: bounded English media pilot

Run date: 2026-11-17 Asia/Shanghai. Baseline `563b8fc00e221ab7ade1fbe26e9fbdc276dc124f`; v3 artifacts remain unchanged. The [frozen protocol](../configs/news_sentiment_factor_v4_20261117.json), [actual receipt](../results/news_sentiment_factor_v4_20261117/actual_summary_redacted.json), [coverage receipt](../results/news_sentiment_factor_v4_20261117/coverage_redacted.json), and [online observation](../results/news_sentiment_factor_v4_20261117/online_observation_redacted.json) support the counts below. Private responses, article text, and account data remain local.

| Measure | Observed |
| --- | ---: |
| Provider requests / cap | 9 / 30, all moomoo WebAPI; 8 business successes, 1 required-keyword failure |
| Returned rows / distinct saved article versions | 80 / 30, from current keyword pages with duplicates |
| Saved versions with body text | 15 / 30; no saved title, abstract, or body qualified as English input |
| Articles in the three frozen 2025 windows | 0 |
| Version-at-the-time evidence / publication-or-update proxies | 0 / 30 |
| Verified independent event clusters / eligible company-days | 0 / 0; 30 article-ID groups are unresolved proxies |
| Real LM scores / FinBERT inference / qualified prospective scores | 0 / 0 / 0 |
| Return-model fits and later-label reads | 0 / 0 |

moomoo's current `news/search` endpoint returned items for NVDA, MU, and MSFT. `Accept-Language: en-US` produced the same NVDA payload hash. Its [official changelog](https://open.moomoo.com/changelog) records recent news-search changes and says news endpoints were temporarily removed from public documentation. We therefore did not guess historical date, pagination, or English-source parameters. The current download timestamp was not recast as historical availability, and a supplier publication-time field did not prove that the returned body version existed then.

All nine frozen symbol-by-2025-window cells are marked “historical query capability unverified, not queried,” rather than “no news.” The [window coverage table](../results/news_sentiment_factor_v4_20261117/historical_window_coverage_redacted.json) records the New York half-open boundaries and UTC conversions.

WRDS lists [RavenPack](https://wrds-www.wharton.upenn.edu/pages/about/data-vendors/ravenpack/), but a visible catalogue does not prove this account's entitlement or article-text access. No working programmatic credential was found and no RavenPack query ran. No Massive API key was configured; its [Stocks News](https://massive.com/docs/rest/stocks/news) and [Benzinga News](https://massive.com/docs/rest/partners/benzinga/news) products were documented but not called or purchased. See the [source-capability receipt](../results/news_sentiment_factor_v4_20261117/source_capability_redacted.json).

The first 240-second prospective observation had four local OAuth-decryption failures and sent no provider request. A second attempt reproduced the environment error once and stopped. The research venv lacked the Windows credential-unlocking module, so collection was moved to the already working local Python while the pinned model remains in the isolated research environment. A third 125-second observation made three successful requests and saw 30 duplicate rows, with no new version, English candidate, or prospective score. Earlier failure receipts are retained. This verifies polling and deduplication only.

Article versions, response hashes, supplier timestamps, local receipt times, and score times are separate in the private append-only store. An initial field named `first_received_at_utc` held database ingestion time; it was preserved while an added response-read-completion field was populated for 30/30 versions from the request ledger, as recorded in the [migration receipt](../results/news_sentiment_factor_v4_20261117/response_time_migration_redacted.json). The frozen English source-review sample was empty and semantic review was `NOT_RUN`. Synthetic checks for duplication, revision, late receipt, and owner-only cache access do not establish news quality.

N1 is the company-day average net tone across verified distinct event clusters; N2 is the distinct-cluster count. The future A/B/C/D price, count, tone, and count-plus-tone comparison has common samples, chronological partitions, a one-session primary outcome and five-session secondary outcome, with availability at actual score completion and overlapping labels purged. No factor row or fit exists yet; a cached quote is not an executable price. The [readiness receipt](../results/news_sentiment_factor_v4_20261117/factor_readiness_redacted.json) records `NOT_RUN` states.

Software checks comprised four SQLite/time-rule tests, two Django owner-isolation tests, and one explicitly synthetic LM/FinBERT plumbing input; final runs passed. Synthetic inference is excluded from real-news scoring. Earlier Windows SQLite handle and research-venv timezone failures and their fixes remain in the [revision receipt](../results/news_sentiment_factor_v4_20261117/implementation_revisions_redacted.json); the [timezone file receipt](../results/news_sentiment_factor_v4_20261117/timezone_data_redacted.json) records its version and hash.

The owner-only portal has a folded bilingual **Research news tone** panel that reads the local cache every five seconds and currently shows zero scores. Its code path makes no supplier or model request. With the existing owner login, the panel was visually inspected in Edge in Chinese and English at desktop width and 390 CSS pixels. It showed 30 versions, zero English candidates and scores, and an explicit empty state, with no panel overflow observed. This was browser responsive emulation, not a physical-phone test; see the [redacted visual receipt](../results/news_sentiment_factor_v4_20261117/visual_acceptance_redacted.json).

**Principal blocker:** an authorized English media-news source with the frozen historical-window query and article-version timing evidence has not been verified. The v3 issuer-disclosure scores and Chinese translations were not relabelled as media news. Financial v2.5 remains `REJECTED_CANDIDATE`; no Qwen, return fitting, or trading ran.
