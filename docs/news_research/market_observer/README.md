# Read-only market observer v1 / 只读市场观察 v1

The newer [historical development report](../../../reports/2026-11-15_market_observer_historical_v1.en.md) ([中文报告](../../../reports/2026-11-15_market_observer_historical_v1.zh.md)) is a separate 2025-04 to 2026-07 retrieval and retrospective A/B price-direction exercise. It does not import historical bars or news into the live SQLite observer, and it does not establish point-in-time news access or an independent test. See its [plan–actual ledger](../../../results/market_observer_historical_20261115/plan_execution.csv).

Executed 2026-11-15, Asia/Shanghai. This is an engineering observer with a private SQLite ledger and a local HTML page. It does not place orders or access holdings, balances or order history. The historical M5 research protocol and financial experiment status are separate.

## Latest watchlist v2 update

The [watchlist v2 configuration](../../../configs/market_observer_watchlist_v2_20261115.json) places TQQQ/SOXX/SPY/QQQ first, followed by the magnificent seven, memory, SpaceX and related issuers. It distinguishes 20 display entities, 19 requested US quote securities, a non-subscribed GOOG class alias, and SK hynix's separate Korean ordinary security. Nine new official moomoo basicinfo rows were checked and imported as **current-only** identities; see the [mapping receipt](../../../results/watchlist_live_20261115/security_mapping_redacted.json) and [source register](source_access.md). Provider identity, historical validity, quote entitlement and push freshness remain separate.

An earlier WebAPI token grant included `quote:write` and `trade:read` and was rejected and discarded; the [earlier authorization receipt](../../../results/watchlist_live_20261115/authorization_failure_redacted.json) remains. The later grant actually contained only `quote:read` plus a redacted account selector and was encrypted locally with DPAPI. One quota GET returned HTTP 404, so capacity is UNKNOWN. After repairing an idless `ret_code` control-frame mismatch, two bounded one-key `US.TQQQ` WebSocket runs authenticated and received subscribe acknowledgments during a closed US market. The final [live-probe receipt](../../../results/watchlist_live_20261115/live_probe_redacted.json) records one distinct old-timestamp quote, UNKNOWN delay, US market-state code 11 (`AFTER_HOURS_END`), a confirmed unsubscribe and zero active subscriptions after stop. **Market-open updates and `LIVE_VERIFIED` remain pending.** The runnable [watchlist-v2 NO_FEED config](../../../configs/market_observer_v1.watchlist_v2.example.json) separately passed start/status/stop. The new [English](../../../reports/2026-11-15_market_observer_watchlist_live.en.md) and [Chinese](../../../reports/2026-11-15_market_observer_watchlist_live.zh.md) reports, [plan–actual](../../../results/watchlist_live_20261115/plan_execution.csv), [saved-data desktop/390px receipt](../../../results/watchlist_live_20261115/visual_acceptance_redacted.json), [initial post-stop page receipt](../../../results/watchlist_live_20261115/visual_dashboard_probe_v3_redacted.json) and [final page receipt](../../../results/watchlist_live_20261115/visual_dashboard_poststop_v4_redacted.json) give the actual result. The final page labels code 11 as the *last* provider market signal and says the signal time is unavailable; it does not claim a connected-session screenshot. No process remains running.

## Current connection boundary

Codex has a moomoo MCP configuration and a `quote:read` OAuth login. The current desktop session was launched before the new connection and does not dynamically expose moomoo tools. A fresh native Codex CLI session discovered read-only moomoo tool bindings; exact account invocation is recorded in the [connection receipt](../../../results/market_observer_v1_20261115/moomoo_connection_receipt.json) and [capability matrix](../../../results/market_observer_v1_20261115/moomoo_capability_matrix.json). MCP tool exposure, authenticated quote returns, and standalone WebSocket push are different gates. The [source register](source_access.md) gives the official documents and rights limits.

The ordinary public service configuration defaults to `NO_FEED`. A standalone `quote:read` credential now exists locally, and a one-key bounded WebSocket connection was tested. Account-wide quota, permission for the full watchlist, storage conditions and open-market freshness remain unresolved before continuous `LIVE_PUSH` can be enabled. Jin10's prior MCP probe saved a private, partial flash window; its polling quota and retention terms remain unconfirmed. No infinite Jin10 poll is enabled. The no-feed service can start, show its heartbeat and stored data, and stop cleanly; it does not collect current market data.

## Local commands

Run from the repository root with Python 3.11. The example configuration contains no secrets and places the database and page under ignored `artifacts/`.

```powershell
python -m scripts.market_observer_v1.cli seed --config configs/market_observer_v1.example.json
python -m scripts.market_observer_v1.cli replay-jin10 --config configs/market_observer_v1.example.json --source-db artifacts/jin10_20261115/jin10_v2.sqlite --limit 40
python -m scripts.market_observer_v1.cli start --config configs/market_observer_v1.example.json
python -m scripts.market_observer_v1.cli status --config configs/market_observer_v1.example.json
python -m scripts.market_observer_v1.cli stop --config configs/market_observer_v1.example.json
python -m scripts.market_observer_v1.cli render --config configs/market_observer_v1.example.json
```

Open `artifacts/market_observer_v1/dashboard_latest.html` locally after `render` or while the service runs. The local page may contain provider text; do not upload it. A [public blank template](../../../results/market_observer_v1_20261115/dashboard_public_template.html) shows the layout without provider content. At the v1 baseline, twelve US ticker candidates were linked to twelve official basicinfo resolutions and one Friday quote remained a quarantined probe snapshot. [Import receipt](../../../results/market_observer_v1_20261115/moomoo_post_probe_import_redacted.json). The v2 watchlist adds the separately verified `US.SKHY` ADR and `KR.000660` ordinary security, while Samsung Electronics remains in the news/earnings issuer pool only. The later WebSocket probe added one old-timestamp quote but no eligible complete one-minute bar.

`start` keeps the no-feed process running until `stop`, a disk/event budget, or an explicit runtime cap. It does not start at Windows login. The private SQLite separates sessions, instruments, quotes/bars, news versions, claims/clusters, immutable predictions and outcomes, review items, model versions and collection gaps. Saved Jin10 replay is explicitly historical; it cannot create a contemporaneous prediction.

## Time and evaluation rule

Future real observations require an authorized push session, source-published and local-received times, resolved instrument identity, a documented US regular session, and an input snapshot persisted before its forecast window. The fixed comparator is five-minute pre-cutoff price momentum. The first complete one-minute bar beginning at or after durable prediction persistence supplies the entry close; the close of the 30th following complete bar supplies the outcome. The same window in QQQ is a descriptive difference, never a causal alpha claim. Bars crossing the close, missing bars, delayed data and adjustment mismatch retain distinct statuses. Corrections append new records; old decisions and outcomes remain. No live prediction or outcome was produced in this stage.

The learning queue separates extraction correctness from subsequent price movement. Only reviewed, rights-eligible, time-available records can enter a versioned training manifest; repeatedly used shortfacts passages stay development data. No automatic retraining or model promotion is implemented.

## Evidence

- [Plan–actual ledger](../../../results/market_observer_v1_20261115/plan_execution.csv)
- [Redacted saved-data and lifecycle receipt](../../../results/market_observer_v1_20261115/local_acceptance_redacted.json)
- [Desktop/390px visual receipt](../../../results/market_observer_v1_20261115/visual_acceptance_redacted.json)
- [English progress report](../../../reports/2026-11-15_market_observer_v1.en.md) · [中文进度](../../../reports/2026-11-15_market_observer_v1.zh.md)
