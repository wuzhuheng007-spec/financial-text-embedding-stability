#!/bin/bash
# Helpers for the ISOLATED M1/M2 acceptance loop (test volume m1m2_loop_var, test web container on 127.0.0.1:8798).
# Never touches production volumes, services or images. Source it: . deploy/linux/host/m1m2_loop_lib.sh
export MSYS_NO_PATHCONV=1
LOOP_VOL=${LOOP_VOL:-m1m2_loop_var}
LOOP_WEB=${LOOP_WEB:-http://127.0.0.1:8798}
LOOP_LOG=${LOOP_LOG:?set LOOP_LOG}
log() { echo "$(date -u +%FT%T.%3NZ) $*" | tee -a "$LOOP_LOG"; }
# numeric job container: no network, read-only root fs, read-only input mount, measured limits
numeric() {  # numeric NAME [extra docker args...] -- [worker args...]
  local name=$1; shift; local dargs=(); while [ "$1" != "--" ]; do dargs+=("$1"); shift; done; shift
  docker run --rm --name "m1m2-loop-$name" --label m1m2-loop=1 --network none --read-only --tmpfs /tmp:size=256m \
    --cpus "${NUM_CPUS:-1}" --memory "${NUM_MEM:-2g}" --pids-limit "${NUM_PIDS:-128}" --security-opt no-new-privileges --cap-drop ALL \
    -v "$LOOP_VOL:/app/apps/quant_portal/var" -v "$LOOP_VOL:/inputs:ro" -e QUANT_RESEARCH_INPUT_ROOT=/inputs/research \
    -e QUANT_CODE_VERSION="$REV" -e QUANT_IMAGE_ID="$NUM_ID" -e QUANT_LIVE_WORKERS="${LIVE:-}" "${dargs[@]}" "$NUM_IMG" "$@"
}
legacy() {  # legacy NAME -- [worker args...]   (app image, default engines = legacy only)
  local name=$1; shift; shift
  docker run --rm --name "m1m2-loop-$name" --label m1m2-loop=1 --network none --read-only --tmpfs /tmp:size=64m --cpus 1 --memory 1g --pids-limit 128 \
    --security-opt no-new-privileges --cap-drop ALL --entrypoint python -v "$LOOP_VOL:/app/apps/quant_portal/var" \
    -e QUANT_CODE_VERSION="$REV" -e QUANT_IMAGE_ID="$APP_ID" "$APP_IMG" -m scripts.quant_research_v1.worker "$@"
}
api() {  # api METHOD PATH [form data]
  local m=$1 p=$2 d=${3:-}
  curl -s -c "$LOOP_JAR" -b "$LOOP_JAR" -H "Cookie: $SESSION" -o /dev/null "$LOOP_WEB/" >/dev/null
  local tok; tok=$(grep -E "csrftoken" "$LOOP_JAR" | awk '{print $7}' | tail -1)  # HttpOnly cookies are stored as #HttpOnly_ lines
  if [ "$m" = POST ]; then curl -s -H "Cookie: $SESSION; csrftoken=$tok" -H "X-CSRFToken: $tok" -H "Referer: $LOOP_WEB/" -X POST --data "$d" "$LOOP_WEB$p"
  else curl -s -H "Cookie: $SESSION" "$LOOP_WEB$p"; fi
}
