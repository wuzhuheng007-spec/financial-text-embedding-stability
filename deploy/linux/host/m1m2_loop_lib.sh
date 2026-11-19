#!/bin/bash
# Helpers for the ISOLATED M1/M2 acceptance loop (test volume, test web container on 127.0.0.1:8798).
# Never touches production volumes, services or images. Source it: . deploy/linux/host/m1m2_loop_lib.sh
# Closeout 2026-11-20 (F1): numeric jobs go ONLY through the candidate launcher quant-research-job.sh (separate
# control and compute containers, input read-only subpath, work subpath only). The earlier numeric() helper,
# which mounted the volume read-write plus a read-only alias of the input, is removed.
LOOP_VOL=${LOOP_VOL:?set LOOP_VOL (test volume)}
LOOP_WEB=${LOOP_WEB:-http://127.0.0.1:8798}
LOOP_LOG=${LOOP_LOG:?set LOOP_LOG}
HOSTDIR=$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")
log() { echo "$(date -u +%FT%T.%3NZ) $*" | tee -a "$LOOP_LOG"; }
case "$LOOP_VOL" in quant-personal_*) echo "refusing a production volume name" >&2; return 1;; esac
job() {  # job [launcher args...]   e.g. job --run RUN_ID ; job --next --max-seconds 30
  "$HOSTDIR/quant-research-job.sh" --image "${NUM_IMG:?}" --var-volume "$LOOP_VOL" "$@"
}
legacy() {  # legacy NAME -- [worker args...]   (app image, default engines = legacy only; same as research-runner)
  local name=$1; shift; shift
  docker run --rm --name "m1m2c-loop-$name" --label m1m2c-loop=1 --network none --read-only --tmpfs /tmp:size=64m --cpus 1 --memory 1g --pids-limit 128     --security-opt no-new-privileges --cap-drop ALL --entrypoint python -v "$LOOP_VOL:/app/apps/quant_portal/var"     -e QUANT_CODE_VERSION="${REV:?}" -e QUANT_IMAGE_ID="${APP_ID:?}" -e QUANT_LIVE_WORKERS="${LIVE:-}" "${APP_IMG:?}" -m scripts.quant_research_v1.worker "$@"
}
api() {  # api METHOD PATH [form data]   (owner session cookie in $SESSION, CSRF from the cookie jar $LOOP_JAR)
  local m=$1 p=$2 d=${3:-}
  curl -s -c "$LOOP_JAR" -b "$LOOP_JAR" -H "Cookie: $SESSION" -o /dev/null "$LOOP_WEB/" >/dev/null
  local tok; tok=$(grep -E "csrftoken" "$LOOP_JAR" | awk '{print $7}' | tail -1)  # HttpOnly cookies are stored as #HttpOnly_ lines
  if [ "$m" = POST ]; then curl -s -H "Cookie: $SESSION; csrftoken=$tok" -H "X-CSRFToken: $tok" -H "Referer: $LOOP_WEB/" -X POST --data "$d" "$LOOP_WEB$p"
  else curl -s -H "Cookie: $SESSION" "$LOOP_WEB$p"; fi
}
