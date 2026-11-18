#!/bin/bash
# Refresh SEC EDGAR filing metadata for the owner's watchlist (one-off container,
# feed service network and volumes, collector NOT started). Run by quant-sec-refresh.timer.
set -euo pipefail
cd /srv/quant/app/deploy/linux
ENV=/srv/quant/private.env
APP_IMAGE=$(sed -n 's/^APP_IMAGE=//p' "$ENV")
REV=$(docker image inspect "$APP_IMAGE" --format '{{index .Config.Labels "org.opencontainers.image.revision"}}')
# SEC_USER_AGENT (with a contact address) comes from /srv/quant/secrets/sec.env via the unit.
: "${SEC_USER_AGENT:?SEC_USER_AGENT missing}"
# Same data lock as quant-backup: an import never overlaps a backup. Exit 75 = lock busy.
exec 9>/run/lock/quant-data.lock
flock -w 900 9 || { echo "data lock busy for 15 min; refresh skipped" >&2; exit 75; }
exec docker compose -f compose.yaml --env-file "$ENV" run --rm --no-deps -T \
  -e SEC_USER_AGENT -e QUANT_CODE_VERSION="$REV" --entrypoint python feed \
  -m scripts.quant_cloud_official.sec_filings refresh --config configs/quant_cloud_official_sources_v1.json
