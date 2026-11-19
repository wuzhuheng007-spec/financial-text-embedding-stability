#!/bin/bash
# Run at most one queued research job in a one-off, network-less container (profile "jobs").
# Started by quant-research-runner.timer; the web page only queues runs. Exit 0 also when idle.
set -euo pipefail
cd /srv/quant/app/deploy/linux
ENV=/srv/quant/private.env
APP_IMAGE=$(sed -n 's/^APP_IMAGE=//p' "$ENV")
REV=$(docker image inspect "$APP_IMAGE" --format '{{index .Config.Labels "org.opencontainers.image.revision"}}')
# Own lock: one research job at a time; does not wait on the backup/SEC data lock.
exec 9>/run/lock/quant-research.lock
flock -n 9 || { echo "a research job is already running"; exit 0; }
exec docker compose -f compose.yaml --env-file "$ENV" --profile jobs run --rm --no-deps -T \
  -e QUANT_CODE_VERSION="$REV" research-runner
