# Bounded official announcement corpus

The source scope is frozen in `configs/market_observer_official_v1.json`: NVDA
and MU SEC 8-K, 10-Q and 10-K filings within 2025-04-01 through 2026-07-31,
the earnings release exhibits linked from those filings, and dated FOMC
statements listed on the Federal Reserve meeting calendar. The request budget
is 80 new HTTP calls for this module, below the round-wide cap of 100. The
request ledger is written after each attempt and reruns reuse saved raw bytes.

Set `OFFICIAL_PRIOR_SOURCE_DIR` to the private directory containing the
previous SEC metadata and cached originals. Set `SEC_USER_AGENT` to an
identifying SEC-compliant user agent with a contact address. Neither value is
stored in public output. Then run:

```powershell
python -m scripts.market_observer_official_v1.build_corpus --dry-plan
python -m scripts.market_observer_official_v1.build_corpus
python -m scripts.market_observer_official_v1.validate_corpus
```

The complete raw files, extracted text, manifest, and per-request ledger stay
under the ignored `artifacts/market_observer_official_20261115/` directory.
The `results/market_observer_official_20261115/` directory contains only
source URLs, counts, statuses and hashes. A hashed SEC accession URL supports
the retrospective announcement-date proxy; it does not establish that the
current bytes were available on the historical date. SEC acceptance time,
filing date, official release time, web update time, and present retrieval
time remain separate. Standalone issuer IR archive completeness and historical
FOMC page versions are unknown.
