# Read-only market observer v1 / 只读市场观察 v1

Executed 2026-11-15, Asia/Shanghai. This is an engineering observer with a private SQLite ledger and a local HTML page. It does not place orders or access holdings, balances or order history. The historical M5 research protocol and financial experiment status are separate.

## Current connection boundary

Codex has a moomoo MCP configuration and a `quote:read` OAuth login. The current desktop session was launched before the new connection and does not dynamically expose moomoo tools. A fresh native Codex CLI session discovered read-only moomoo tool bindings; exact account invocation is recorded in the [connection receipt](../../../results/market_observer_v1_20261115/moomoo_connection_receipt.json) and [capability matrix](../../../results/market_observer_v1_20261115/moomoo_capability_matrix.json). MCP tool exposure, authenticated quote returns, and standalone WebSocket push are different gates. The [source register](source_access.md) gives the official documents and rights limits.

The independently running service defaults to `NO_FEED`. A verified standalone moomoo push credential, account market entitlement and quota, symbol resolution, and storage conditions are required before enabling `LIVE_PUSH`. Jin10's prior MCP probe saved a private, partial flash window; its polling quota and retention terms remain unconfirmed. No infinite Jin10 poll is enabled. The local service can still start, show its heartbeat and stored data, and stop cleanly. This is **not live market collection**.

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

Open `artifacts/market_observer_v1/dashboard_latest.html` locally after `render` or while the service runs. The local page may contain provider text; do not upload it. A [public blank template](../../../results/market_observer_v1_20261115/dashboard_public_template.html) shows the layout without provider content. The twelve original US ticker candidate rows were preserved and linked to twelve official basicinfo resolutions in a separate table; one Friday quote is a quarantined probe snapshot, with zero eligible one-minute bars. [Import receipt](../../../results/market_observer_v1_20261115/moomoo_post_probe_import_redacted.json). Samsung Electronics and SK hynix remain in the news/earnings issuer pool only.

`start` keeps the no-feed process running until `stop`, a disk/event budget, or an explicit runtime cap. It does not start at Windows login. The private SQLite separates sessions, instruments, quotes/bars, news versions, claims/clusters, immutable predictions and outcomes, review items, model versions and collection gaps. Saved Jin10 replay is explicitly historical; it cannot create a contemporaneous prediction.

## Time and evaluation rule

Future real observations require an authorized push session, source-published and local-received times, resolved instrument identity, a documented US regular session, and an input snapshot persisted before its forecast window. The fixed comparator is five-minute pre-cutoff price momentum. The first complete one-minute bar beginning at or after durable prediction persistence supplies the entry close; the close of the 30th following complete bar supplies the outcome. The same window in QQQ is a descriptive difference, never a causal alpha claim. Bars crossing the close, missing bars, delayed data and adjustment mismatch retain distinct statuses. Corrections append new records; old decisions and outcomes remain. No live prediction or outcome was produced in this stage.

The learning queue separates extraction correctness from subsequent price movement. Only reviewed, rights-eligible, time-available records can enter a versioned training manifest; repeatedly used shortfacts passages stay development data. No automatic retraining or model promotion is implemented.

## Evidence

- [Plan–actual ledger](../../../results/market_observer_v1_20261115/plan_execution.csv)
- [Redacted saved-data and lifecycle receipt](../../../results/market_observer_v1_20261115/local_acceptance_redacted.json)
- [Desktop/390px visual receipt](../../../results/market_observer_v1_20261115/visual_acceptance_redacted.json)
- [English progress report](../../../reports/2026-11-15_market_observer_v1.en.md) · [中文进度](../../../reports/2026-11-15_market_observer_v1.zh.md)
