#!/bin/bash
# Run at most one queued legacy research job and at most one numeric research job (one scheduler, one lock).
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
# Research job containers already running (normally none, since this lock is held): lets the worker confirm that
# a RUNNING row whose lease expired really has no live worker before marking it lost. Legacy jobs are identified by
# their container id, protected numeric jobs by their job token label (= the run's worker id).
LIVE=$( { docker ps --filter label=com.docker.compose.service=research-runner --format '{{.ID}}';
          docker ps --filter label=quant.research.job --format '{{.Label "quant.research.job"}}'; } | sort -u | paste -sd, -)
# 1) legacy engine in the app image (claims only legacy_mom20_v1 runs; also the lease-based recovery pass)
docker compose -f compose.yaml --env-file "$ENV" --profile jobs run --rm --no-deps -T \
  -e QUANT_CODE_VERSION="$REV" -e QUANT_IMAGE_ID="$IMG" -e QUANT_LIVE_WORKERS="$LIVE" research-runner || echo "legacy job exit $?"
# 2) numeric engines: protected job (separate control and compute containers, see quant-research-job.sh) under the
#    SAME lock. Skipped when NUMERIC_IMAGE is not configured in the private env file.
NUMERIC_IMAGE=$(sed -n 's/^NUMERIC_IMAGE=//p' "$ENV")
if [ -n "$NUMERIC_IMAGE" ]; then
  "$(dirname "$(readlink -f "$0")")/quant-research-job.sh" --image "$NUMERIC_IMAGE" \
    --var-volume "${QUANT_VAR_VOLUME:-quant-personal_personal_var}" --next --max-seconds 900 || echo "numeric job exit $?"
fi
