#!/bin/bash
# X stance study, metered jobs (one-off container on the feed service's network and volumes; the quote collector
# is NOT started and is not touched). Usage: quant-x-stance.sh status|usage|plan|preflight|daily|build
#   preflight  bounded live check: at most 200 returned posts / USD 0.03 in total, charged to the normal pools
#   daily      recent sample + historical queue + derived tables; refuses until a preflight has passed
# Every request goes through the ledger in the persistent volume (5,000 posts and USD 0.75 per Shanghai day,
# USD 22.50 per rolling 30 days). The key comes from /srv/quant/secrets/x_stance.env and is never printed.
set -euo pipefail
CMD=${1:-status}
case "$CMD" in status|usage|plan|preflight|daily|build) ;; *) echo "unknown command" >&2; exit 2;; esac
cd /srv/quant/app/deploy/linux
ENV=/srv/quant/private.env
KEYFILE=/srv/quant/secrets/x_stance.env
if [ -z "${TWITTERAPI_IO_API_KEY:-}" ] && [ -r "$KEYFILE" ]; then set -a; . "$KEYFILE"; set +a; fi
export TWITTERAPI_IO_API_KEY=${TWITTERAPI_IO_API_KEY:-}
# Same data lock as quant-backup: a write never overlaps a backup. Exit 75 = lock busy.
exec 9>/run/lock/quant-data.lock
flock -w 900 9 || { echo "data lock busy for 15 min; skipped" >&2; exit 75; }
exec docker compose -f compose.yaml --env-file "$ENV" run --rm --no-deps -T \
  -e TWITTERAPI_IO_API_KEY --entrypoint python feed -m scripts.x_stance_v1.cli "$CMD"
