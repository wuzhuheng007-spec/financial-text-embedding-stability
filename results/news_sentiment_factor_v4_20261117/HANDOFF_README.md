# News sentiment factor v4 handoff

Read in this order: the Chinese or English report in `reports/`, the frozen configuration in `configs/`, `actual_summary_redacted.json`, `source_capability_redacted.json`, `coverage_redacted.json`, `online_observation_redacted.json`, `factor_readiness_redacted.json`, then the code and test files under `scripts/`, `tests/`, and `apps/`.

The run used 9 of 30 permitted supplier requests. moomoo returned 80 rows representing 30 distinct current article versions. None qualified as an English title or belonged to the three frozen 2025 historical windows. WRDS RavenPack entitlement and Massive/Benzinga credentials were not established. Real LM and FinBERT media-news scores, prospective scores, and return-model fits were all **0**. A synthetic model smoke test is separate from these counts. The existing quote and Jin10 collectors continued running; the bounded v4 observation ended.

The private article text, raw responses, SQLite databases, credentials, model weights, and user account data stay on the originating computer. This handoff contains code, frozen configuration, redacted counts, tests, and reports only. The owner-only portal panel at `http://127.0.0.1:8765/` displays the present zero-score state. Financial input v2.5 remains `REJECTED_CANDIDATE`.

Next prerequisite: verify one licensed English media-news source with historical query, title or text access, and version-time evidence. Do not substitute issuer filings or translated Chinese reports for English media news.
