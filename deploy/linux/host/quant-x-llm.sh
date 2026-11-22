#!/bin/bash
# Local stance model worker for the X study: llama.cpp server on CPU, one slot, memory-limited, on an internal
# docker network with no route to the internet. Usage: quant-x-llm.sh start|stop|status
# The image is pinned by digest and the model file is checked against its SHA-256 before every start.
# It is NOT started by any timer on its own; quant-x-stance.sh starts it for a classify step and stops it after.
set -euo pipefail
CMD=${1:-status}
NAME=quant-x-llm
NET=quant-x-llm
IMAGE=ghcr.io/ggml-org/llama.cpp@sha256:c2cf6c72d913b6c89a5f0929f2a866e6161e09e7dc187e0f3b0f21e923e26db3
DIR=/srv/quant/models/x_stance
FILE=qwen2.5-1.5b-instruct-q4_k_m.gguf
SHA=6a1a2eb6d15622bf3c96857206351ba97e1af16c30d7a74ee38970e434e9407e
MEM=${X_LLM_MEMORY:-3g}
APP_IMAGE=$(sed -n 's/^APP_IMAGE=//p' /srv/quant/private.env)
case "$CMD" in
  start)
    [ -s "$DIR/$FILE" ] || { echo "model file missing: $DIR/$FILE" >&2; exit 3; }
    echo "$SHA  $DIR/$FILE" | sha256sum -c --status || { echo "model file does not match the pinned SHA-256" >&2; exit 3; }
    docker network inspect "$NET" >/dev/null 2>&1 || docker network create --internal "$NET" >/dev/null
    if [ "$(docker inspect -f '{{.State.Running}}' "$NAME" 2>/dev/null || true)" = true ]; then echo "already running"; exit 0; fi
    docker rm -f "$NAME" >/dev/null 2>&1 || true
    docker run -d --name "$NAME" --network "$NET" --memory "$MEM" --memory-swap "$MEM" --cpus 2 --pids-limit 128 --read-only --tmpfs /tmp:size=64m \
      --cap-drop ALL --security-opt no-new-privileges --restart no -v "$DIR":/models:ro "$IMAGE" \
      -m "/models/$FILE" -c 2048 -t 2 -np 1 --threads-http 2 --host 0.0.0.0 --port 8080 >/dev/null
    for i in $(seq 1 60); do
      if docker run --rm --network "$NET" --entrypoint python "$APP_IMAGE" -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://$NAME:8080/health', timeout=3).status == 200 else 1)" >/dev/null 2>&1; then echo "ready after about $((i*3))s"; exit 0; fi
      [ "$(docker inspect -f '{{.State.Running}}' "$NAME" 2>/dev/null || true)" = true ] || { echo "worker exited: $(docker inspect -f '{{.State.ExitCode}} oom={{.State.OOMKilled}}' "$NAME")" >&2; exit 4; }
      sleep 2
    done
    echo "worker did not become ready" >&2; exit 4 ;;
  stop)
    docker rm -f "$NAME" >/dev/null 2>&1 || true; echo "stopped" ;;
  status)
    if [ "$(docker inspect -f '{{.State.Running}}' "$NAME" 2>/dev/null || true)" = true ]; then
      docker stats --no-stream --format 'running cpu={{.CPUPerc}} mem={{.MemUsage}}' "$NAME"
    else
      echo "not running$(docker inspect -f ' (last exit={{.State.ExitCode}} oom={{.State.OOMKilled}})' "$NAME" 2>/dev/null || true)"
    fi ;;
  *) echo "usage: quant-x-llm.sh start|stop|status" >&2; exit 2 ;;
esac
