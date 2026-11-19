#!/bin/bash
# Operator variables for the M1/M2 release / rollback runbook, defined AND verified in the root shell that uses them.
# Source it at the top of every root block (a new `sudo bash` does not inherit the admin shell's variables):
#   . /srv/quant/upload/m1m2c_<SH>/src/deploy/linux/host/m1m2_release_env.sh <SH>
# <SH> = RELEASE_MANIFEST.runtime.short (the commit the sealed images were built from).
# It returns non-zero - and the caller, run with `bash -euo pipefail`, stops - when anything does not match:
# not root, bad SH, deploy dir / env file / volume / upload dir missing, or a loaded image whose id differs from the
# sealed manifest. It prints only paths and image names, never the env file's content.
# M1M2_ROOT / M1M2_VOL / M1M2_LOCK override the production defaults for the isolated smoke test only.
m1m2_release_env() {
  local want got kind img
  [ "$(id -u)" = 0 ] || { echo "m1m2_release_env: run as root (sudo bash ...)" >&2; return 1; }
  [[ "${1:-}" =~ ^[0-9a-f]{7}$ ]] || { echo "usage: . m1m2_release_env.sh <7-hex short sha from RELEASE_MANIFEST.runtime.short>" >&2; return 1; }
  ROOTQ=${M1M2_ROOT:-/srv/quant}
  SH=$1
  D=$ROOTQ/app/deploy/linux
  ENV=$ROOTQ/private.env
  VOL=${M1M2_VOL:-quant-personal_personal_var}
  APP=quant-personal-app:$SH
  NUM=quant-research-numeric:$SH
  U=$ROOTQ/upload/m1m2c_$SH
  B=$ROOTQ/backup/host-before-$SH
  LOCK=${M1M2_LOCK:-/run/lock/quant-research.lock}
  C="docker compose -f $D/compose.yaml --env-file $ENV"
  [ -f "$D/compose.yaml" ] || { echo "m1m2_release_env: $D/compose.yaml missing" >&2; return 1; }
  [ -f "$ENV" ] || { echo "m1m2_release_env: env file $ENV missing" >&2; return 1; }
  docker volume inspect "$VOL" >/dev/null 2>&1 || { echo "m1m2_release_env: volume $VOL missing" >&2; return 1; }
  [ -f "$U/RELEASE_MANIFEST.json" ] && [ -d "$U/src/deploy/linux/host" ] || { echo "m1m2_release_env: upload dir $U incomplete (manifest or extracted src missing)" >&2; return 1; }
  for kind in numeric app; do
    img=$NUM; [ "$kind" = app ] && img=$APP
    want=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["runtime"]["images"][sys.argv[2]]["local_image_id"])' "$U/RELEASE_MANIFEST.json" "$kind") \
      || { echo "m1m2_release_env: manifest has no $kind image id" >&2; return 1; }
    got=$(docker image inspect "$img" --format '{{.Id}}' 2>/dev/null) || got="NOT_LOADED"
    [ "$got" = "$want" ] || { echo "m1m2_release_env: $img id $got != sealed manifest $want" >&2; return 1; }
  done
  export ROOTQ SH D ENV VOL APP NUM U B LOCK C
  echo "release env verified: SH=$SH APP=$APP NUM=$NUM VOL=$VOL D=$D U=$U B=$B LOCK=$LOCK"
}
m1m2_release_env "$@"
