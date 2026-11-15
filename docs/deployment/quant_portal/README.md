# Quant Portal v1: local test and future Alibaba Cloud deployment

Status: **local personal test first**. The Docker configuration is prepared and syntax-checked, but this Windows host cannot start the Docker Linux engine because its current WSL virtualization environment is unavailable. No Alibaba Cloud server, domain, public release, provider feed, paid API, trading, or model training is part of this release.

The application lives in `apps/quant_portal/`; it does not import the private research database. The `web` service serves Django templates and static assets, `db` is PostgreSQL, `worker` is an independent process, and `migrate` is a one-shot database migration. Browser requests read local cached rows. Both moomoo and Jin10 public output are disabled by default. A missing feed must be displayed as unavailable rather than a synthetic quote.

## Personal Windows run

From the repository root, run:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File apps/quant_portal/ops/start_personal.ps1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File apps/quant_portal/ops/status_personal.ps1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File apps/quant_portal/ops/stop_personal.ps1
```

The local server binds only `127.0.0.1:8765`; its SQLite database, invite code, generated secret, PID and logs stay under ignored `apps/quant_portal/var/`. The first start installs pinned dependencies in an ignored virtual environment. Open `http://127.0.0.1:8765/accounts/register/` and enter the one-time invite from the private file named by the start output. The invite is never printed by the script. This is a personal development path, not a public server.

Create a point-in-time backup while the local app is running, then verify it on a scratch copy without replacing the live database:

```powershell
python apps/quant_portal/ops/backup_personal.py
python apps/quant_portal/ops/restore_probe_personal.py apps/quant_portal/backups/<backup-name>.sqlite3
```

The backup and its receipt remain ignored and private. Copy them to a separate protected location for actual disaster recovery; the scratch probe only checks readability and integrity.

## Future single-host Linux deployment

Initial sizing assumption: one 4-vCPU, 16-GiB Linux ECS host. This is a planning assumption, not a measured capacity claim. Select the region, image, disk class/capacity, bandwidth billing and backup policy before purchasing. The [cost register](cost_register.csv) leaves unknown amounts blank. Alibaba Cloud explains that ECS compute, storage, public bandwidth and snapshots can be billed separately [in its billing guide](https://www.alibabacloud.com/help/en/ecs/instance-types).

1. Create a Linux host with an SSH key and a non-root deployment account. Allow inbound TCP 80/443 for the invited users; restrict SSH to an administrator IP. Do not open PostgreSQL 5432, Django 8000, the background worker, Docker API or Caddy's admin interface. These port recommendations follow [Alibaba Cloud security-group guidance](https://help.aliyun.com/zh/ecs/user-guide/start-using-security-groups).
2. Install Docker Engine and the Compose plugin using the [official Docker Linux instructions](https://docs.docker.com/engine/install/). Clone the reviewed commit into a dedicated deployment directory. Keep `.env` outside Git and readable only by the deployment account. Ensure DNS A/AAAA records point to the selected server before starting HTTPS.
3. In `apps/quant_portal/`, run `python3 ops/init_env.py --domain portal.example.com` with the real domain. This creates private random database and Django secrets without printing them. Review `.env`; never copy development data or research credentials to the host. Give the release a unique `PORTAL_IMAGE` tag, for example `quant-portal-app:release-YYYYMMDD-1`.
4. Run `docker compose -f compose.yaml -f compose.prod.yaml config --quiet` to validate configuration without printing interpolated secrets. Then run `docker compose -f compose.yaml -f compose.prod.yaml up -d --build`. Inspect `docker compose -f compose.yaml -f compose.prod.yaml ps`; require healthy `db`, successful `migrate`, healthy `web` and running `worker` before inviting users. Run `docker compose -f compose.yaml -f compose.prod.yaml exec web python manage.py check --deploy` and resolve warnings before opening the service.
5. The production override publishes only Caddy 80/443. Caddy obtains and renews HTTPS certificates when DNS and the public ports work; see [Caddy automatic HTTPS](https://caddyserver.com/docs/automatic-https). The base web port is bound to host loopback only. The PostgreSQL and worker services have no published ports. Create the first administrator interactively with `docker compose -f compose.yaml -f compose.prod.yaml exec web python manage.py createsuperuser`; send invitations outside this application only after user access policy is agreed.

`compose.yaml` waits for PostgreSQL health and the one-shot migration before starting the web and worker, using [Compose dependency conditions](https://docs.docker.com/compose/how-tos/startup-order/). Runtime packages and three base images are pinned. On each upgrade, recheck the image digests and third-party advisories; pinning does not certify a package as safe.

## Backup, restore and rollback

Before each upgrade, use the deployment account to create a private directory outside Git with restrictive permissions, then make a logical PostgreSQL backup. The command below keeps the database password inside the container environment and writes only a private local file:

```sh
umask 077
mkdir -p backups
docker compose -f compose.yaml -f compose.prod.yaml exec -T db sh -c 'exec pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > backups/portal-before-upgrade.dump
sha256sum backups/portal-before-upgrade.dump > backups/portal-before-upgrade.dump.sha256
```

Copy the backup to protected off-host storage according to a retention policy. Test restoration to a **separate** PostgreSQL database or host, verify table counts and login before treating the backup as recoverable. Never run a restore over the live database as a routine check. The app does not accept user uploads in v1; if media storage is later enabled, back up `media_data` separately. PostgreSQL's [backup documentation](https://www.postgresql.org/docs/16/backup.html) distinguishes SQL dumps from filesystem-level backups.

Keep the previous immutable `PORTAL_IMAGE` tag and its configuration. For a code-only rollback with compatible schema, set `PORTAL_IMAGE` back to the previous tag in private `.env`, run `docker compose -f compose.yaml -f compose.prod.yaml up -d --no-build --no-deps web worker`, then check `/readyz/` and a login flow. A migration that changed schema incompatibly requires a planned outage and a verified restore into a separate database before cutover. An actual container rollback drill is still **NOT_RUN** on this host.

## Operations and acceptance gates

- The Compose `local` logging driver caps each service at three 10-MiB files; Docker documents [its log rotation behavior](https://docs.docker.com/engine/logging/drivers/local/). Monitor host disk usage and set alerts before the filesystem reaches 80%; this is an operator threshold, not an observed alarm.
- Monitor CPU, memory, disk, `/readyz/`, PostgreSQL connections and backup age. Keep security updates and Docker package versions under a scheduled maintenance plan. Current source rights must be checked before enabling any provider output or AI processing on a server.
- The frozen [release configuration](../../../configs/quant_portal_release_v1.json) sets local scenario targets for 30, 100 and 300 synthetic users. Request rate, duration and fixture size still need a finalized load protocol. No load result or hundreds-user capacity is claimed. Run future load tests only against local/test cached data, never a provider endpoint.
- Desktop and 390px checks of the local app must be recorded separately from cloud acceptance. The old private research Site stays private and separate. The financial-text 20,000-character input gap and formal research gates remain unresolved.

The next cloud actions are: choose a server and region; confirm data display rights; run container, backup/restore and rollback acceptance on a capable Linux host; finalize synthetic load protocol; then invite testers. No cloud resource has been purchased or deployed here.
