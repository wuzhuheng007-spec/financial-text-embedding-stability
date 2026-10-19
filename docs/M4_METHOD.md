# M4 engineering method and acceptance boundary

## Inputs and scope

`configs/smoke.yaml` fixes the ordered five-company SEC demonstration pool and `as_of=2025-12-31`. `configs/m4_demo.yaml` now fixes an **engineering demo** price window of 2025-01-01 through 2025-12-31 inclusive. The one-calendar-year start was not previously specified in the repository; it is an explicit M4 engineering choice, not an approved formal research horizon. Formal horizons, decision time, availability lag, splits and evaluation remain unresolved in `docs/PROTOCOL.md`.

The M1 corpus is revalidated from SEC raw bytes before any TF-IDF fitting or market work. The TF-IDF fit corpus is exactly the five validated Item 1 Business files. It lowercases, uses `[a-z][a-z0-9]+`, raw term counts, smoothed `idf=log((1+n)/(1+df))+1`, and row L2 normalization. The output records the CIK, accession, file hashes, document order, vocabulary and fit boundary. This is an in-corpus descriptive control, not an out-of-sample predictive baseline.

## Massive source and authorization

Only a real `MASSIVE_API_KEY` in the process environment enables HTTP requests. The key is placed in the `Authorization: Bearer` header; it is never written to a URL, log or manifest. No key or endpoint response means account entitlement is **unverified**. Published plan tables describe possible access and cannot establish this account's subscription. An authorization error is kept as a raw response/receipt and blocks subsequent fixed-pool requests.

For each fixed ticker, the date-filtered Massive `/v3/reference/tickers` response is preserved and matched to the SEC CIK. Mismatched or ambiguous ticker/CIK identity excludes that ticker. All `/v2/aggs/ticker/.../range/1/day/...` pages are preserved as raw bytes with source URL, retrieval time, HTTP status, SHA-256 and provider `request_id`; `next_url` is followed with a host check and page cap. Every supplied OHLCV and optional `vw`/`n` field is retained, together with the exact `c` field used for returns. The data version is the set of raw response hashes, request IDs and retrieval timestamps; Massive does not expose a frozen historical revision snapshot through these requests.

Massive's `adjusted=true` on aggregate bars means **split adjustment only**. The demo uses `c_t/c_(t-1)-1`, so it is a split-adjusted price return, **not** a dividend-inclusive total return. No missing bar or return is filled with zero or a different vendor. A union of observed dates defines the full T×N grid in fixed ticker order; a return exists only when both that date and the immediately preceding union date have valid closes. `common_masks.json` records observed cells and complete dates. Securities with failed identity or market access are excluded explicitly; PCA is withheld unless all five fixed-pool securities have valid bars.

PCA takes only complete rows from the configured past window. It centers security columns, does not scale them, checks finite data and effective matrix rank, and refuses K above that rank. The SVD yields N×K stock loadings and T×K date scores for the complete-date subset. The metadata records ticker/date order, window, centering vector, scale vector, requested/used K, singular values and effective rank. The factor signs are arbitrary; this is descriptive dimensionality reduction, not a trading result.

## Point-in-time limits and acceptance

Massive's ticker-overview documentation notes that its historical `date` parameter may expose filing-derived information by period-of-report date even when the filing was submitted later. The M4 reference match only checks security identity; it does not certify a point-in-time tradable information set. The M1 SEC ticker map and acceptance-date caveats in `DATA_CONTRACT.md` also remain. The demo's `as_of` date is not a market-decision timestamp.

The M4 real-price and PCA tasks are accepted only after an actual authorized endpoint run, raw response/hash audit, complete-company/date masks, and finite-shape checks. Synthetic tests exercise failures and orientation but do not count as downloaded prices or account entitlement. No result here establishes incremental value of text relative to price.

## Verified vendor documentation

- [Massive REST authentication](https://massive.com/docs/rest): Bearer-header API key form.
- [Massive all-tickers reference](https://massive.com/docs/rest/stocks/tickers/all-tickers): date filter, CIK and `next_url`.
- [Massive daily custom bars](https://massive.com/docs/rest/stocks/aggregates/custom-bars): fields, `adjusted`, empty intervals and pagination.
- [Massive split/dividend adjustment FAQ](https://massive.com/knowledge-base/article/is-massives-stock-data-adjusted-for-splits-or-dividends): dividend adjustment is not supplied by aggregate bars.
- [Massive ticker overview](https://massive.com/docs/rest/stocks/tickers/ticker-overview): historical `date` semantics and filing period caveat.
