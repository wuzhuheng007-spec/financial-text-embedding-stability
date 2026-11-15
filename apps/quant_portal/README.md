# Quant Portal v1

Independent invitation-only application. The personal test runs on this computer at `127.0.0.1:8765`; it uses an ignored SQLite database and never queries moomoo, Jin10 or a model when a page is refreshed. Cached historical fact cards are labeled as development material. A price feed is absent until separately authorized and implemented.

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

If the invite expires before registration, run `powershell.exe -NoProfile -ExecutionPolicy Bypass -File apps/quant_portal/ops/reissue_personal_invite.ps1`. It saves a new one-time code to a new ignored file under `var/` and prints only that file path. An account that has already registered simply logs in; it does not need another invite.

```powershell
python apps/quant_portal/ops/backup_personal.py
python apps/quant_portal/ops/restore_probe_personal.py apps/quant_portal/backups/<backup-name>.sqlite3
```

The scratch probe does not replace the live database. The complete [future Linux deployment runbook](../../docs/deployment/quant_portal/README.md) covers PostgreSQL, the independent worker, Caddy HTTPS, backup, restore and rollback. Prepared Docker files do not mean this app has been deployed to Alibaba Cloud. The 30/100/300-user load targets remain unmeasured.
