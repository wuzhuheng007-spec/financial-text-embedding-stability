# Quant Portal v1

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

To record a manual market-open check during a real US regular session, run `python -m scripts.quant_personal_feed.open_market_acceptance --config configs/quant_portal_personal_feeds_v11_20261116.json --seconds 120`. Its private receipt is written under the ignored app `var/` directory. The command observes cached source-time progression and does not change subscriptions.

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
