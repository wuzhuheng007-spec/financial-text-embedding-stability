# Private read-only market observer v1

All runtime files remain under ignored `artifacts/`. The committed
`configs/market_observer_v1.example.json` is a credential-free template.
It defaults to `NO_FEED` because a standalone authorized push path and
continuous Jin10 request quota have not been verified. Starting the service
with this template checks process control, storage and dashboard updates;
it does **not** collect live quotes or call a provider. `LIVE_PUSH` is gated
until an actual read-only adapter and permission receipt are available.

From the repository root:

```text
python -m scripts.market_observer_v1.cli seed --config configs/market_observer_v1.example.json
python -m scripts.market_observer_v1.cli replay-jin10 --config configs/market_observer_v1.example.json --source-db artifacts/jin10_20261115/jin10_v2.sqlite --limit 100
python -m scripts.market_observer_v1.cli import-moomoo-probe --config configs/market_observer_v1.example.json --projection <private-projection.json>
python -m scripts.market_observer_v1.cli start --config configs/market_observer_v1.example.json
python -m scripts.market_observer_v1.cli status --config configs/market_observer_v1.example.json
python -m scripts.market_observer_v1.cli stop --config configs/market_observer_v1.example.json
python -m scripts.market_observer_v1.cli render --config configs/market_observer_v1.example.json
```

`seed` registers the 12 requested ticker strings as
`PENDING_OFFICIAL_LOOKUP`, with null provider code and market. Quotes require
a separately verified instrument record. `import-moomoo-probe` checks the
private raw MCP response hash, links exact official basicinfo codes in new
rows and retains the original candidate strings. A quote without an exact
local receipt, delay field or market-state response goes only to
`probe_observations`, with a bounded receipt interval. It does not qualify as
a complete minute bar or a live price outcome. `replay-jin10` reads only the
already saved private Jin10 database; it makes zero network requests and
preserves the recorded partial source coverage. Replaying again adds no news
versions, fetch records or duplicate coverage gaps. News identities and
potential collisions are recorded separately from event clusters.

The private HTML page may include provider title or a short content preview.
Keep the page, SQLite database, service log and raw provider responses in
`artifacts/`; only the empty dashboard template and redacted receipts may be
shared. Source publication, local receipt and durable persistence timestamps
are separate. Neither old quotes nor a local receipt prove realtime status or
the original public availability time.

`Store.insert_prediction` requires a frozen, hashed input manifest and an
evaluation protocol hash, price basis and calendar version. Predictions,
outcomes, quote versions, news versions and source claims are append-only.
Corrections use new rows. The evaluator accepts only complete regular-session
minute bars after the durable prediction time and records pending or gap
statuses when the window cannot be evaluated.
