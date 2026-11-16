# Quant Portal v1

Personal Demo v1.5 adds a Chinese/English switch (saved per account), an independently foldable workbench, a relevance-screened technology and macro feed, an owner-only background Qwen analysis queue, a deduplicated rule-ranked Top 10, and a descriptive news-tone panel. On `http://127.0.0.1:8765/`, sign in, use **中文 / English** in the header, open a Top 10 row to reach its report, and expand the other sections as needed. The original supplier text remains expandable; a model result appears as a completed analysis only after a separate same-team content review. Reports without a reviewed translation show the original with a translation-pending label. Rank scores are product ordering, not market probabilities. News remains supplier reporting until independently checked. The local browser reads cache every five seconds and does not call Jin10, moomoo or the model on refresh. The [English v1.5 report](../../reports/2026-11-16_quant_portal_personal_v15.en.md), [中文报告](../../reports/2026-11-16_quant_portal_personal_v15.zh.md) and [plan–actual ledger](../../results/quant_portal_personal_v15_20261116/plan_execution.csv) distinguish completed software, real model attempts, content review and pending visual acceptance. Financial input v2.5 stays `REJECTED_CANDIDATE`.

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File apps/quant_portal/ops/analysis_personal.ps1 status
powershell.exe -NoProfile -ExecutionPolicy Bypass -File apps/quant_portal/ops/analysis_personal.ps1 stop
powershell.exe -NoProfile -ExecutionPolicy Bypass -File apps/quant_portal/ops/analysis_personal.ps1 start
```

The analysis worker uses a separate ignored private SQLite file and the pinned local Qwen model. Its stop command finishes the current bounded call before exiting. Portal and supplier collection have separate lifecycle controls below.

Personal Demo v1.4: after signing in at `http://127.0.0.1:8765/`, click **查看曲线** on a quote card. Choose the symbol, session and cached time range; click a point for source/receipt times, or click a news row to locate its supplier publication time. **回到最新** resumes the live local-cache view after zooming or reading. The chart uses saved supplier quotes and locally cached Jin10 reports, not complete trades or bars. Browser reads every five seconds add no provider requests. See the [v1.4 report](../../reports/2026-11-16_quant_portal_personal_timeline_v14.en.md) ([中文](../../reports/2026-11-16_quant_portal_personal_timeline_v14.zh.md), [plan–actual](../../results/quant_portal_personal_timeline_v14_20261116/plan_execution.csv)). The raw-response policy keeps originals for 48-hour chart rows and currently visible session cells; old originals pruned under v1.3 cannot be restored. The app is still loopback owner-only and the historical fact cards are unchanged.

Personal Demo v1.3 adds a per-symbol **盘前 / 常规 / 盘后 / 夜盘** selector for the same six existing subscriptions. Actual QUOTE push children supply separate extended-session source times when present; one six-symbol read-only REST batch every 60 seconds supplements the regular/base quote and backfill. The browser still reads only the private local cache every five seconds. Child time missing from REST is shown as unknown, and a New York calendar date is not assumed to be an official overnight trading date. See the [v1.3 report](../../reports/2026-11-16_quant_portal_personal_sessions_v13.en.md) ([中文](../../reports/2026-11-16_quant_portal_personal_sessions_v13.zh.md), [plan–actual](../../results/quant_portal_personal_sessions_v13_20261116/plan_execution.csv)).

Personal Demo v1.2 adds **重点事件** to the existing Jin10 news section. It uses explainable company/ETF and event-category phrases, with owner-editable interests, company/category/time filters, and conservative suspected-related-report expansion. The browser continues to read only the local owner cache every five seconds; this does not change Jin10 collection cadence or moomoo subscriptions. Rule ordering does not indicate market impact. See the [v1.2 report](../../reports/2026-11-16_quant_portal_personal_events_v12.en.md) and [plan–actual ledger](../../results/quant_portal_personal_feed_v12_20261116/plan_execution.csv).

Independent invitation-only application. The personal test runs on this computer at `127.0.0.1:8765`; it uses ignored SQLite databases and never queries moomoo, Jin10 or a model when a page is refreshed. Cached historical fact cards are labeled as development material. A separate local worker now receives six read-only moomoo quote streams and polls one latest Jin10 flash page every 60 seconds; the page reads only the private cache every five seconds. Public market display remains disabled.

For owner operation, see the [one-page Chinese personal guide](../../docs/quant_portal/personal_user_guide.zh.md). `owner@local.invalid` is a local account identifier; no email password recovery is provided. The optional `ops/reset_personal_password.ps1` command invokes Django's interactive password prompt without a password argument or ordinary-log value.

## Start, inspect and stop the personal test

From the repository root in PowerShell:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File apps/quant_portal/ops/start_personal.ps1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File apps/quant_portal/ops/status_personal.ps1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File apps/quant_portal/ops/stop_personal.ps1
```

The first start creates a private Python 3.11 environment with `requirements.lock`, which needs access to the package index unless the wheels are already cached. An existing compatible environment can be supplied with `start_personal.ps1 -PythonExe <path-to-python.exe>`. Startup applies migrations, verifies the pinned redacted SEC development summary, and generates one invitation. Open `http://127.0.0.1:8765/accounts/register/`. The registration email is `owner@local.invalid`; read the one-time code from the local ignored file `apps/quant_portal/var/personal_invite.txt`, then choose a password in the browser. Do not paste either value into chat or commit the file. If the reviewed source summary is absent or its hash differs, the bootstrap reports the gap rather than using private source text or inventing facts.

The local process, invite, database, secret and logs are under ignored `apps/quant_portal/var/`. The application and compose files never read the existing private market-observer database. Use a private backup and scratch restore probe:

The personal feed worker has its own start, status and stop commands. `start_personal.ps1` and `stop_personal.ps1` coordinate it with the portal. Repeated start does not create a second feed worker. Only the existing local owner can read `/personal-feed.json`; a different account gets HTTP 404. The worker uses an existing encrypted `quote:read` credential and the local `JIN10_MCP_TOKEN` without exposing them in the browser. Market closure, source timestamps, receipt timestamps and unknown delay or currency are shown separately. The account-specific moomoo quota and Jin10 vendor polling limit remain unconfirmed; `1440` is a local daily call cap, not a supplier allowance.

Personal feed v1.1 separates watchlist membership from quote subscription. The owner can subscribe or stop an already verified code on the dashboard; at the six-key local budget, the owner explicitly chooses which current subscription to replace. The background worker applies the change and the page waits for its acknowledgement. SNDK, SK hynix and SpaceX are separate news interests; a news interest does not create a price series. The dashboard keeps the last cached quote visible with its source time during market closure or a feed interruption. It polls only the local cache every five seconds. [v1.1 report](../../reports/2026-11-16_quant_portal_personal_feed_v11.en.md) and [plan–actual](../../results/quant_portal_personal_feed_v11_20261116/plan_execution.csv).

To record a manual market-open check during a real US regular session, run `python -m scripts.quant_personal_feed.open_market_acceptance --config configs/quant_portal_personal_feeds_v11_20261116.json --seconds 300`. It samples the private cache every five seconds (maximum 900 seconds) without changing subscriptions or calling providers. Its private receipt is written under the ignored app `var/` directory. A weekend or closed-market check is a precheck only.

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File apps/quant_portal/ops/feed_personal.ps1 start
powershell.exe -NoProfile -ExecutionPolicy Bypass -File apps/quant_portal/ops/feed_personal.ps1 status
powershell.exe -NoProfile -ExecutionPolicy Bypass -File apps/quant_portal/ops/feed_personal.ps1 stop
```

If the invite expires before registration, run `powershell.exe -NoProfile -ExecutionPolicy Bypass -File apps/quant_portal/ops/reissue_personal_invite.ps1`. It saves a new one-time code to a new ignored file under `var/` and prints only that file path. An account that has already registered simply logs in; it does not need another invite.

```powershell
python apps/quant_portal/ops/backup_personal.py
python apps/quant_portal/ops/restore_probe_personal.py apps/quant_portal/backups/<backup-name>.sqlite3
```

The scratch probe does not replace the live database. The complete [future Linux deployment runbook](../../docs/deployment/quant_portal/README.md) covers PostgreSQL, the independent worker, Caddy HTTPS, backup, restore and rollback. Prepared Docker files do not mean this app has been deployed to Alibaba Cloud. The 30/100/300-user load targets remain unmeasured.
