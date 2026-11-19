#!/bin/bash
# Independent restore check for one backup directory.
# Restores postgres.dump into a throwaway PostgreSQL container (same pinned image, no network,
# tmpfs data), compares every table's row count with the counts taken at backup time, checks
# the owner account, verifies SHA-256 sums and re-runs quick_check on SQLite copies.
# Writes RESTORE_CHECK.json into the backup directory; exits non-zero on any failure.
set -euo pipefail
B=${1:?backup directory}
PG_IMAGE=postgres:16-bookworm@sha256:a49f7438ba03589f9b1ca062f2da82e899b48ea1dc50d2449fa09a70be83ebca
NAME=quant-restore-check-$$
cleanup() { docker rm -f "$NAME" >/dev/null 2>&1 || true; }
trap cleanup EXIT

( cd "$B" && sha256sum --quiet -c SHA256SUMS )
docker run -d --name "$NAME" --network none --tmpfs /var/lib/postgresql/data \
  -e POSTGRES_PASSWORD=restorecheck -e POSTGRES_DB=restore_check "$PG_IMAGE" >/dev/null
for i in $(seq 1 60); do docker exec "$NAME" pg_isready -U postgres -d restore_check >/dev/null 2>&1 && break; sleep 1; done
docker exec -i "$NAME" pg_restore -U postgres -d restore_check --no-owner --no-privileges --exit-on-error < "$B/postgres.dump"
docker exec "$NAME" psql -U postgres -d restore_check -AtF, -c "
  select table_name, (xpath('/row/c/text()', query_to_xml(format('select count(*) as c from public.%I', table_name), false, true, '')))[1]::text
  from information_schema.tables where table_schema = 'public' and table_type = 'BASE TABLE' order by 1" > "$B/postgres.rowcounts.restored.csv"
OWNER=$(docker exec "$NAME" psql -U postgres -d restore_check -Atc "select id||':'||username||':'||is_active from auth_user where username='owner@local.invalid'")

# SQLite: restore every backed-up file into an isolated scratch directory and verify it with
# the app image's own reader (no network, uid 10001); the backup directory itself is read-only.
SQLITE_OK=true
APP_IMAGE=$(sed -n 's/^APP_IMAGE=//p' /srv/quant/private.env)
: > "$B/sqlite_restore_check.jsonl"
for man in $(find "$B/sqlite" -name MANIFEST.json 2>/dev/null | sort); do
  vdir=$(dirname "$man"); vol=$(basename "$vdir")
  # The backup stays root-only (0600); verification works on a scratch copy owned by the app uid.
  scratch=$(mktemp -d /srv/quant/backup/restore-scratch.XXXX)
  cp -a "$vdir" "$scratch/b"; mkdir "$scratch/r"; chown -R 10001:10001 "$scratch"
  if docker run --rm --network none --user 10001:10001 --entrypoint python        -v "$scratch/b:/b:ro" -v "$scratch/r:/r" -v /srv/quant/app/deploy/linux/host/quant_sqlite_backup.py:/x/q.py:ro        "$APP_IMAGE" /x/q.py verify /b /r </dev/null > "$scratch.out"; then :; else SQLITE_OK=false; fi
  python3 -c 'import json,sys; print(json.dumps({"volume": sys.argv[1], **json.load(open(sys.argv[2]))}))' "$vol" "$scratch.out" >> "$B/sqlite_restore_check.jsonl" || SQLITE_OK=false
  rm -rf "$scratch" "$scratch.out"
done

# Research: restore registry + files from the backup copy only (production volume NOT mounted),
# verify hashes and table digests, and re-run completed runs under new run ids in isolation.
RESEARCH_OK=true
if [ -d "$B/research_backup/research" ]; then
  scratch=$(mktemp -d /srv/quant/backup/restore-scratch.XXXX)
  cp -a "$B/research_backup" "$scratch/b"; mkdir "$scratch/r"; chown -R 10001:10001 "$scratch"
  REV=$(docker image inspect "$APP_IMAGE" --format '{{index .Config.Labels "org.opencontainers.image.revision"}}')
  IMG=$(docker image inspect "$APP_IMAGE" --format '{{.Id}}')
  if docker run --rm --network none --user 10001:10001 --entrypoint python -e QUANT_RESEARCH_LEASE_SECONDS=60 -e QUANT_CODE_VERSION="$REV" -e QUANT_IMAGE_ID="$IMG"        -v "$scratch/b:/b:ro" -v "$scratch/r:/r" -v /srv/quant/app/deploy/linux/host/quant_research_backup.py:/x/r.py:ro        "$APP_IMAGE" /x/r.py verify /b /r </dev/null > "$B/research_restore_check.json"; then :; else RESEARCH_OK=false; fi
  rm -rf "$scratch"
fi

python3 - "$B" "$OWNER" "$SQLITE_OK" "$RESEARCH_OK" <<'PY'
import csv, json, sys, pathlib, datetime
b, owner, sqlite_ok, research_ok = pathlib.Path(sys.argv[1]), sys.argv[2], sys.argv[3] == "true", sys.argv[4] == "true"
live = dict(csv.reader(open(b / "postgres.rowcounts.live.csv")))
rest = dict(csv.reader(open(b / "postgres.rowcounts.restored.csv")))
diff = {t: [live.get(t), rest.get(t)] for t in sorted(set(live) | set(rest)) if live.get(t) != rest.get(t)}
# django_session may change between the dump snapshot and the count query; report but do not fail on it
hard = {t: v for t, v in diff.items() if t != "django_session"}
result = {"checked_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
          "backup": b.name, "tables": len(rest), "rows_restored": sum(int(v) for v in rest.values()),
          "count_differences": diff, "owner_row": owner, "sqlite_quick_check_ok": sqlite_ok,
          "key_tables": {t: rest.get(t) for t in ("auth_user", "core_userpreferences", "core_watchlistitem", "core_publicfactcard", "django_migrations")}}
sq = b / "sqlite_restore_check.jsonl"
result["sqlite"] = [json.loads(l) for l in sq.read_text().splitlines() if l.strip()] if sq.exists() else []
rr = b / "research_restore_check.json"
result["research"] = json.loads(rr.read_text()) if rr.exists() and rr.read_text().strip() else None
result["research_ok"] = research_ok
result["ok"] = not hard and owner.startswith("1:owner@local.invalid:t") and sqlite_ok and research_ok and len(rest) > 0
(b / "RESTORE_CHECK.json").write_text(json.dumps(result, indent=1))
print(json.dumps(result))
sys.exit(0 if result["ok"] else 1)
PY
