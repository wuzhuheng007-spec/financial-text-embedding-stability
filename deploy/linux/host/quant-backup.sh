#!/bin/bash
# Daily backup of the quant-personal deployment (runs as root from quant-backup.timer).
#  - PostgreSQL (accounts, settings, watchlist, fact cards): pg_dump custom format
#  - every *.sqlite3 in the personal_var / private_artifacts volumes: SQLite online
#    backup API run as uid 10001 inside the app image (no root-owned -wal/-shm files)
#  - SHA-256 manifest and row counts; then an independent restore check
#  - retention: directories older than KEEP_DAYS are removed, but the newest MIN_KEEP always stay
set -euo pipefail
umask 077
ROOT=/srv/quant/backup/daily
KEEP_DAYS=${KEEP_DAYS:-14}
MIN_KEEP=${MIN_KEEP:-3}
APP_IMAGE=$(sed -n 's/^APP_IMAGE=//p' /srv/quant/private.env)
HOST_TOOLS=/srv/quant/app/deploy/linux/host
# One data lock shared with quant-sec-refresh: a backup never overlaps an import.
exec 9>/run/lock/quant-data.lock
flock -w 1800 9 || { echo "data lock busy for 30 min; backup skipped" >&2; exit 75; }
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
DIR="$ROOT/$STAMP.partial"
cd /srv/quant/app/deploy/linux
C=(docker compose -f compose.yaml --env-file /srv/quant/private.env)
mkdir -p "$DIR"

# 1. PostgreSQL
"${C[@]}" exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc --no-owner --no-privileges' > "$DIR/postgres.dump"
"${C[@]}" exec -T db sh -c 'pg_restore --list' < "$DIR/postgres.dump" > "$DIR/postgres.toc"
# SQL on stdin: no nested shell quoting.
"${C[@]}" exec -T db sh -c 'psql -X -q -U "$POSTGRES_USER" -d "$POSTGRES_DB" -AtF, -v ON_ERROR_STOP=1' > "$DIR/postgres.rowcounts.live.csv" <<'SQL'
select table_name, (xpath('/row/c/text()', query_to_xml(format('select count(*) as c from public.%I', table_name), false, true, '')))[1]::text
from information_schema.tables where table_schema = 'public' and table_type = 'BASE TABLE' order by 1;
SQL

# 2. SQLite files in the private volumes: online backup, single-file copies, manifest
mkdir -p "$DIR/sqlite"; chown 10001:10001 "$DIR/sqlite"
for vol in personal_var private_artifacts; do
  docker volume inspect "quant-personal_$vol" >/dev/null 2>&1 || continue
  mkdir -p "$DIR/sqlite/$vol"; chown 10001:10001 "$DIR/sqlite/$vol"
  docker run --rm --network none --user 10001:10001 --entrypoint python     -v "quant-personal_$vol:/src" -v "$DIR/sqlite/$vol:/out" -v "$HOST_TOOLS/quant_sqlite_backup.py:/x/quant_sqlite_backup.py:ro"     "$APP_IMAGE" /x/quant_sqlite_backup.py backup /src /out </dev/null | sed "s/^/$vol: /" >> "$DIR/sqlite.log"
done
chown -R root:root "$DIR/sqlite"; chmod -R go-rwx "$DIR/sqlite"

# 3. Manifest, then publish atomically
( cd "$DIR" && find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS )
mv "$DIR" "$ROOT/$STAMP"
echo "backup $ROOT/$STAMP size=$(du -sh "$ROOT/$STAMP" | cut -f1)"

# 4. Independent restore check (separate throwaway PostgreSQL, no network)
/usr/local/sbin/quant-restore-check.sh "$ROOT/$STAMP"

# 5. Retention
mapfile -t ALL < <(find "$ROOT" -mindepth 1 -maxdepth 1 -type d -name '20*Z' | sort)
KEEP_FROM=$(( ${#ALL[@]} - MIN_KEEP ))
for i in "${!ALL[@]}"; do
  d=${ALL[$i]}
  if [ "$i" -lt "$KEEP_FROM" ] && [ -n "$(find "$d" -maxdepth 0 -mtime +"$KEEP_DAYS")" ]; then
    rm -rf -- "$d"; echo "retention: removed $d"
  fi
done
find "$ROOT" -mindepth 1 -maxdepth 1 -type d -name '*.partial' -mtime +1 -exec rm -rf -- {} +
