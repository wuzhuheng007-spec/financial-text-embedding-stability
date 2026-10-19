# M5 preparation: corrected SEC filing–price chronology (19 October 2026)

**Historical original 10-K filing identity is verified for all five. Exact first public dissemination and point-in-time identity of the saved Item 1 text are unverified.** The 2026 download date is snapshot provenance, not evidence of 2025 unavailability. The previous v1 report is retained as superseded evidence.

| Firm / accession | Period; raw-header acceptance ET; filing / change date | Download UTC; exact first public instant | Revision/correction evidence | Conservative candidate ET open | 2025 price role / limit |
|---|---|---|---|---|---|
| AAPL / `0000320193-25-000079` | 2025-09-27; 2025-10-31T06:01:26-04:00; 2025-10-31 / 20251031 | 2026-10-19T00:34:25.699741+00:00; **unknown** | saved submissions: 1 same-period 10-K row(s), 0 10-K/A; same-date change header; PAC not excluded | 2025-11-03 (proposal only) | unassigned; exact public time and text vintage unverified |
| MSFT / `0000950170-25-100235` | 2025-06-30; 2025-07-30T16:11:40-04:00; 2025-07-30 / 20250730 | 2026-10-19T00:34:28.133868+00:00; **unknown** | saved submissions: 1 same-period 10-K row(s), 0 10-K/A; same-date change header; PAC not excluded | 2025-07-31 (proposal only) | unassigned; exact public time and text vintage unverified |
| AMZN / `0001018724-25-000004` | 2024-12-31; 2025-02-06T18:40:29-05:00; 2025-02-07 / 20250206 | 2026-10-19T00:34:30.770920+00:00; **unknown** | saved submissions: 1 same-period 10-K row(s), 0 10-K/A; same-date change header; PAC not excluded | 2025-02-10 (proposal only) | unassigned; exact public time and text vintage unverified |
| GOOGL / `0001652044-25-000014` | 2024-12-31; 2025-02-04T20:41:40-05:00; 2025-02-05 / 20250204 | 2026-10-19T00:34:32.178566+00:00; **unknown** | saved submissions: 1 same-period 10-K row(s), 0 10-K/A; same-date change header; PAC not excluded | 2025-02-06 (proposal only) | unassigned; exact public time and text vintage unverified |
| META / `0001326801-25-000017` | 2024-12-31; 2025-01-29T20:00:50-05:00; 2025-01-30 / 20250129 | 2026-10-19T00:34:33.633771+00:00; **unknown** | saved submissions: 1 same-period 10-K row(s), 0 10-K/A; same-date change header; PAC not excluded | 2025-01-31 (proposal only) | unassigned; exact public time and text vintage unverified |

## Interpretation and proposed time rules

The raw headers match M1 accession, Form 10-K, report period, filing date and acceptance fields. The JSON lists raw/source SHA-256 and all saved submissions-page hashes. The revision scan covers only those 2026 SEC snapshots and same-period rows: zero matching 10-K/A rows does **not** rule out same-day post-acceptance correction or another later change. `DATE AS OF CHANGE` equals the acceptance calendar date in these headers but is not a full PAC history. The saved submissions API acceptance strings differ from raw-header Eastern instants by 4–5 hours; both are retained. Exact first public timestamps are unavailable in these artifacts. After-5:30 p.m. ET submissions may be assigned the next business-day filing date and disseminated then. Thus the proposed conservative proxy is the first regular-session open **strictly after the official filing date**, assuming publication by filing-date end and unchanged decision-time Item 1 content; these assumptions require an approved evidence/revision policy.

Proposed sensitivity: compare this rule with one and two additional trading-session lags while holding text, universe and outcome fixed. An earlier same-day rule requires exact dissemination evidence. No rule is frozen.

M4's observed 2025 price window was 2025-01-02–2025-12-31, with 250 daily bars per security, as a retrospective **price-only** PCA engineering input. Its formal role is **UNASSIGNED**. For trailing features, use only bars before each decision and fit transforms on past data. For labels, use a predeclared post-decision horizon. For static comparison, full-window price geometry may be described without predictive or contemporaneous information claims. No blanket valid/invalid pairing follows before role selection. Bars are split-adjusted and omit dividends.

## Traceability and check command

- M1 corpus index SHA-256: `ce54c348458e2c90abee0435f72fe84ff3895e7695c2a2f4ed212eb61035a18c`.
- M4 manifest SHA-256: `c4815bcbeafea98e3810cb3f46849e6c1b544bd2e5beb3a4835a5fddb36ca755`; M4 config SHA-256: `a09317baf128cfb3dc446c140d4e36018f40904cc824018d93b4345dd0222535`.
- The companion JSON records raw filing and Item 1 hashes, SEC archive URLs, 2026 submissions-source receipts and scanned matching rows, raw/processed price hashes, and unsupported conclusions.
- Rebuild: `python scripts/build_m5_time_alignment.py --corpus artifacts/runs/20261019T003627Z-d3043c3e/corpus_index.json --m4-run artifacts/runs/20261019T004950Z-828af5af`.
- Source policy: [SEC Webmaster FAQ](https://www.sec.gov/about/webmaster-frequently-asked-questions), [SEC after-hours filing status](https://www.sec.gov/submit-filings/filer-support-resources/how-do-i-guides/determine-status-my-filing), [EDGAR correction/index policy](https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data), [Massive split/dividend FAQ](https://massive.com/knowledge-base/article/is-massives-stock-data-adjusted-for-splits-or-dividends).
