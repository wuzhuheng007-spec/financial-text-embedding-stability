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
IMG=$(docker image inspect "$APP_IMAGE" --format '{{.Id}}')
# Research job containers already running (normally none, since this lock is held): lets the worker
# confirm that a RUNNING row whose lease expired really has no live worker before marking it lost.
# (docker ANDs repeated label filters, so each job service is listed separately.)
LIVE=$( { docker ps --filter label=com.docker.compose.service=research-runner --format '{{.ID}}';
          docker ps --filter label=com.docker.compose.service=research-numeric-runner --format '{{.ID}}'; } | paste -sd, -)
# 1) legacy engine in the app image (claims only legacy_mom20_v1 runs)
docker compose -f compose.yaml --env-file "$ENV" --profile jobs run --rm --no-deps -T \
  -e QUANT_CODE_VERSION="$REV" -e QUANT_IMAGE_ID="$IMG" -e QUANT_LIVE_WORKERS="$LIVE" research-runner || echo "legacy job exit $?"
# 2) numeric engines in the separate numeric image, under the SAME lock (one scheduler). It claims only the
#    engines whose capability self-test passes in that image. Skipped when NUMERIC_IMAGE is not configured.
NUMERIC_IMAGE=$(sed -n 's/^NUMERIC_IMAGE=//p' "$ENV")
if [ -n "$NUMERIC_IMAGE" ]; then
  NREV=$(docker image inspect "$NUMERIC_IMAGE" --format '{{index .Config.Labels "org.opencontainers.image.revision"}}')
  NIMG=$(docker image inspect "$NUMERIC_IMAGE" --format '{{.Id}}')
  docker compose -f compose.yaml --env-file "$ENV" --profile jobs run --rm --no-deps -T \
    -e QUANT_CODE_VERSION="$NREV" -e QUANT_IMAGE_ID="$NIMG" -e QUANT_LIVE_WORKERS="$LIVE" research-numeric-runner || echo "numeric job exit $?"
fi
