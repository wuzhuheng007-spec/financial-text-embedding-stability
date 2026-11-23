#!/bin/bash
# X stance study jobs (one-off containers; the quote collector is NOT started and is not touched).
# Usage: quant-x-stance.sh status|usage|plan|migrate|queue|preflight|daily|classify [N] [auto|recent|history] [SECONDS]|
#                          watch show|import FILE|enable ID|disable ID|confirm-handle ID HANDLE|verify-candidate ID|
#                          classify-recent|classify-history|smoke-model|build|pipeline|vocab ...|export-pending FILE|import-results FILE
#   preflight    bounded live check: at most 200 returned posts / USD 0.03 in total, charged to the normal pools
#   daily        collection only (watched authors, then keyword sample, then historical queue); refuses until a current
#                preflight receipt passes. Watched authors and keywords SHARE the recent pool (600 + 2,400 of 3,000).
#   classify N [QUEUE] [SECONDS]  local CPU model on up to N pending items within a time budget; starts the worker, stops it afterwards
#   classify-recent    the recent queue only (newest sample day first)
#   classify-history   the historical queue only; does nothing while the recent queue still has work
#   queue        backlog of both queues, age of the oldest pending item, completion per sample day
#   build        derived tables (shares, snapshots, views, episodes, QQQ pairing)
#   pipeline     daily -> build -> classify RECENT -> build; what the daily timer runs. The historical queue is
#                NOT part of it: it has its own unit (quant-x-stance-history) and only uses what recent leaves.
#   export-pending / import-results   hashed work file for the local GPU worker, and the checked import of its answers.
#                The file holds post text: it lives in /srv/quant/x_offload (root only) and travels over SSH only.
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

OFF=/srv/quant/x_offload
# the job container runs as uid 10001: the private directory belongs to that uid and to nobody else
offload_job() { install -d -m 700 -o 10001 -g 10001 "$OFF"; docker run --rm --network none --memory 1g --cpus 1 -v "$VOL":/app/apps/quant_portal/var -v "$OFF":/offload                 --entrypoint python "$APP_IMAGE" -m scripts.x_stance_v1.cli "$@"; }
RECENT_LIMIT=${X_CLASSIFY_LIMIT:-2000}; RECENT_SECONDS=${X_RECENT_SECONDS:-9000}
HISTORY_LIMIT=${X_HISTORY_LIMIT:-2000}; HISTORY_SECONDS=${X_HISTORY_SECONDS:-10800}

case "$CMD" in
  status|usage|plan|migrate|queue) with_lock local_job "$CMD" ;;
  watch)                     # the watched-author list. import reads a file from /srv/quant/x_offload (root only); verify-candidate is the only form that can reach the provider
    case "${1:-show}" in
      import)           f=$(basename "${2:?file name}"); with_lock offload_job watch import "/offload/$f" ;;
      verify-candidate) with_lock net_job watch verify-candidate "${2:?watch id}" ;;
      *)                with_lock local_job watch "$@" ;;
    esac ;;
  export-pending)            f=$(basename "${1:?file name}"); shift; with_lock offload_job export-pending "/offload/$f" "$@" ;;
  import-results)            f=$(basename "${1:?file name}"); with_lock offload_job import-results "/offload/$f" ;;
  classify-recent)           model_step classify --queue recent --limit "${1:-$RECENT_LIMIT}" --max-seconds "${2:-$RECENT_SECONDS}" ;;
  classify-history)          model_step classify --queue history --limit "${1:-$HISTORY_LIMIT}" --max-seconds "${2:-$HISTORY_SECONDS}" ;;
  vocab)                     with_lock local_job vocab "$@" ;;
  build)                     with_lock local_job build ;;
  preflight|daily)           with_lock net_job "$CMD" ;;
  classify)                  model_step classify --limit "${1:-50}" --queue "${2:-auto}" --max-seconds "${3:-3600}" ;;
  smoke-model)               model_step smoke-model ;;
  pipeline)
    rc=0; with_lock net_job daily || rc=$?
    if [ "$rc" -ne 0 ]; then echo "collection step ended with $rc; later steps skipped" >&2; exit "$rc"; fi
    with_lock local_job build || echo "first build step ended with $?" >&2          # memberships and the day's rows exist before any inference
    model_step classify --queue recent --limit "$RECENT_LIMIT" --max-seconds "$RECENT_SECONDS" || echo "recent classify step ended with $?; items stay PENDING" >&2
    with_lock local_job build ;;
  *) echo "unknown command" >&2; exit 2 ;;
esac
