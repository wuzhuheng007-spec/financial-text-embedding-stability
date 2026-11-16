# Personal loopback feeds: moomoo quotes and Jin10 flashes

**Execution date:** 16 November 2026, Asia/Shanghai. **Scope:** the existing owner's `http://127.0.0.1:8765/` portal. Financial input v2.5 is paused and remains `REJECTED_CANDIDATE`.

## What ran

The [redacted runtime receipt](../results/quant_portal_personal_feed_20261116/runtime_redacted.json) records one read-only moomoo WebSocket worker with acknowledged subscriptions for TQQQ, QQQ, SOXX, SPY, NVDA and MU. All six have source-timed quotes in an ignored, owner-bound SQLite cache. The observed US market state was closed; five latest quote source times were near 13 November 22:00 UTC and SPY's was 13 November 21:15 UTC. Receipt times are separate. This does not establish market-open update speed or realtime entitlement. Currency and delay remain unknown where the push did not establish them. The account-specific subscription quota remains unknown.

The same worker made a fresh Jin10 MCP `initialize`, `initialized` and `tools/list` handshake, then used the negotiated `list_flash` latest-page schema. It cached actual flash items and successfully completed multiple 60-second polling cycles; repeated rows were deduplicated. At the [owner-page readback](../results/quant_portal_personal_feed_20261116/portal_readback_redacted.json), 33 cached items and all six quote symbols were returned. That count describes the readback instant, not total vendor coverage. The worker uses a **local** 1,440-business-call UTC-day ceiling; the supplier's actual account limit is unconfirmed. HTTP 401/403 stops that provider, and 429 backs off. Jin10 items remain “reported, pending cross-check.”

The page reads `/personal-feed.json` every five seconds. Page refresh does not call a supplier. The owner endpoint returned HTTP 200 with private cache headers; anonymous access redirected to login, and a synthetic second account received HTTP 404. The dashboard rendered HTTP 200 with the personal quote/news roots and without the old public-display approval notice. These are code and server readbacks, **not visual browser acceptance**. The computer-use tool stopped before desktop and 390px inspection because it could not verify the browser URL; those checks are `NOT_RUN`. The owner has not been observed using the new UI.

The prechange private backup and current personal database matched row-for-row across account, watchlist, preferences and historical fact-card tables: 1, 6, 1 and 14 rows respectively. The backup and private cache remain local. A duplicate start was rejected; a graceful stop unsubscribed and retained the cached data, after which both portal and worker were restarted. No transaction, position or asset API was used.

## Verification and limits

The [test receipt](../results/quant_portal_personal_feed_20261116/test_summary_redacted.json) distinguishes three synthetic cache tests, a 25-test Django portal suite, Python compilation, JavaScript syntax, actual provider retrieval and private-cache readback. A closed-market packet repetition exposed a cache issue: full push payloads varied while the displayed last price and source time stayed unchanged. Dedup now keys the displayed price at a fixed source time; old private rows were retained. No claim of financial correctness follows from these software checks.

The [P0–P7 ledger](../results/quant_portal_personal_feed_20261116/plan_execution.csv) records each planned and actual step. Outstanding checks are new-layout desktop/390px visual inspection, one market-open push observation, currency/delay evidence and actual supplier quota terms. Source behavior was checked against the [moomoo WebSocket authentication](https://open.moomoo.com/api/quote/push/auth), [subscription](https://open.moomoo.com/api/quote/push/subscribe) and [push format](https://open.moomoo.com/api/quote/push/data-format) references and the [Jin10 MCP guide](https://mcp.jin10.com/app/doc.html); actual response shape and permissions came from the local session, not examples.

## Operation

From the repository root, run `powershell.exe -NoProfile -ExecutionPolicy Bypass -File apps/quant_portal/ops/start_personal.ps1`, `status_personal.ps1` or `stop_personal.ps1` at the same path. The [owner guide](../docs/quant_portal/personal_user_guide.zh.md) has the full commands. Start is idempotent; stop ends both portal and collector. Public redistribution, cloud deployment, model inference, formal financial labels and return evaluation remain off.
