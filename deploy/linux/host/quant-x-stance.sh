#!/bin/bash
# X stance study jobs (one-off containers; the quote collector is NOT started and is not touched).
# Usage: quant-x-stance.sh status|usage|plan|migrate|preflight|daily|classify [N]|smoke-model|build|pipeline|vocab ...
#   preflight    bounded live check: at most 200 returned posts / USD 0.03 in total, charged to the normal pools
#   daily        collection only (recent sample + historical queue); refuses until a current preflight receipt passes
#   classify N   local CPU model on up to N pending items; starts the worker, stops it afterwards
#   build        derived tables (shares, snapshots, views, episodes, QQQ pairing)
#   pipeline     daily -> classify -> build, each step on its own; what the timer runs
# Collection and build take the data lock (shared with the backup) only for their own short step. The CPU model
# step does NOT hold that lock, so a long inference run cannot block backups, quotes or news.
# Every provider request goes through the ledger in the persistent volume. The key comes from
# /srv/quant/secrets/x_stance.env and is never printed.
set -euo pipefail
CMD=${1:-status}; shift || true
cd /srv/quant/app/deploy/linux
ENV=/srv/quant/private.env
KEYFILE=/srv/quant/secrets/x_stance.env
if [ -z "${TWITTERAPI_IO_API_KEY:-}" ] && [ -r "$KEYFILE" ]; then set -a; . "$KEYFILE"; set +a; fi
export TWITTERAPI_IO_API_KEY=${TWITTERAPI_IO_API_KEY:-}
APP_IMAGE=$(sed -n 's/^APP_IMAGE=//p' "$ENV")
VOL=quant-personal_personal_var

with_lock() { ( flock -w 900 9 || { echo "data lock busy for 15 min; skipped" >&2; exit 75; }; "$@" ) 9>/run/lock/quant-data.lock; }
net_job()  { docker compose -f compose.yaml --env-file "$ENV" run --rm --no-deps -T -e TWITTERAPI_IO_API_KEY --entrypoint python feed -m scripts.x_stance_v1.cli "$@"; }
# no provider network and no key: the store volume and, for the model steps, the internal worker network only
local_job() { docker run --rm --network "${X_NET:-none}" --memory 1g --cpus 1 -v "$VOL":/app/apps/quant_portal/var -e X_STANCE_LLM_URL=http://quant-x-llm:8080 \
                --entrypoint python "$APP_IMAGE" -m scripts.x_stance_v1.cli "$@"; }
model_step() { /usr/local/sbin/quant-x-llm.sh start >&2 || return 4; local rc=0; X_NET=quant-x-llm local_job "$@" || rc=$?; /usr/local/sbin/quant-x-llm.sh stop >&2; return $rc; }

case "$CMD" in
  status|usage|plan|migrate) with_lock local_job "$CMD" ;;
  vocab)                     with_lock local_job vocab "$@" ;;
  build)                     with_lock local_job build ;;
  preflight|daily)           with_lock net_job "$CMD" ;;
  classify)                  model_step classify --limit "${1:-50}" ;;
  smoke-model)               model_step smoke-model ;;
  pipeline)
    rc=0; with_lock net_job daily || rc=$?
    if [ "$rc" -ne 0 ]; then echo "collection step ended with $rc; later steps skipped" >&2; exit "$rc"; fi
    model_step classify --limit "${X_CLASSIFY_LIMIT:-400}" || echo "classify step ended with $?; items stay PENDING" >&2
    with_lock local_job build ;;
  *) echo "unknown command" >&2; exit 2 ;;
esac
