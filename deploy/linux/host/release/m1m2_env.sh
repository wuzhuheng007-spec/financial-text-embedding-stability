#!/bin/bash
# M1/M2 operator library. SOURCED by the numbered scripts in this directory; never run it on its own.
#
# Every script is run as a FILE, with stdin from /dev/null:
#     sudo bash -euo pipefail <RELDIR>/src/deploy/linux/host/release/<script>.sh [args] </dev/null
#
# Finding E-1 (production, 2026-11-20 08:58 Beijing). The earlier runbook fed each root block to bash on stdin
# (`sudo bash -euo pipefail <<'BLOCK'`). The first `docker compose run` inherited that stdin and read the rest
# of the block as its own input. bash then reached EOF and exited 0, so the remaining steps never ran.
# Defences:
#   - every script refuses to run unless bash reads it from a file (exit 64);
#   - stdin is redirected to /dev/null for the whole script;
#   - each container command also gets an explicit </dev/null;
#   - each step prints "STEP k/N ...: OK", and the last line is "M1M2_COMPLETE ..."; a script that stops early
#     prints "M1M2_INCOMPLETE ..." instead.
#
# What it defines and verifies (any mismatch stops the caller, which runs under -e):
#   RELDIR             the release directory: RELDIR/RELEASE_MANIFEST.json, and RELDIR/src = git archive of
#                      delivery.commit. Every file in delivery.source_files must match its hash, including this
#                      library and the calling script.
#   APP NUM            runtime images from the manifest; both must be loaded with exactly the recorded ids
#                      (APP_ID, NUM_ID).
#   OLD_APP OLD_APP_ID the web image that ran before M1/M2 (level-2 rollback target), from the manifest.
#   B                  the pre-release backup (rollback point), from the manifest.
#   D ENV VOL LOCK C JOURNAL   deployment paths and the compose command.
# Test-only overrides for the isolated smoke host: M1M2_VOL and M1M2_LOCK_WAIT. Never set them in production.
# Output shows paths, image names and ids only, never the content of the private env file.

[ -f "${BASH_SOURCE[1]:-}" ] || { echo "M1M2_STOP code=64 reason=E-1 guard: run the script as a file: sudo bash -euo pipefail <script> </dev/null" >&2; exit 64; }
exec </dev/null
[[ $- == *e* && $- == *u* ]] && [ -o pipefail ] || { echo "M1M2_STOP code=64 reason=run with: sudo bash -euo pipefail <script> </dev/null" >&2; exit 64; }
[ "$(id -u)" = 0 ] || { echo "M1M2_STOP code=64 reason=run as root (sudo)" >&2; exit 64; }
: "${M1M2_SCRIPT:?}" "${M1M2_STEPS:?}"

ROOTQ=/srv/quant
D=$ROOTQ/app/deploy/linux
ENV=$ROOTQ/private.env
VOL=${M1M2_VOL:-quant-personal_personal_var}
LOCK=/run/lock/quant-research.lock
SBIN=/usr/local/sbin
UNITS=/etc/systemd/system
JOURNAL=$ROOTQ/backup/m1m2_ops_journal.log
C="docker compose -f $D/compose.yaml --env-file $ENV"
RELDIR=$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[1]}")")/../../../../.." && pwd)
M1M2_STEP=0
M1M2_DONE=0
M1M2_STEP_NAME=start

m1m2_journal() { [ -d "$ROOTQ/backup" ] && echo "$(date -u +%FT%TZ) $M1M2_SCRIPT release=${RELEASE_ID:-?} $*" >> "$JOURNAL" || true; }
m1m2_die() { echo "M1M2_STOP script=$M1M2_SCRIPT step=$M1M2_STEP code=$1 reason=$2" >&2; m1m2_journal "STOP step=$M1M2_STEP code=$1 $2"; exit "$1"; }
m1m2_on_exit() {
  local rc=$?
  if [ "$M1M2_DONE" != 1 ]; then
    echo "M1M2_INCOMPLETE script=$M1M2_SCRIPT exit=$rc last_step=$M1M2_STEP/$M1M2_STEPS ($M1M2_STEP_NAME)" >&2
    m1m2_journal "INCOMPLETE exit=$rc last_step=$M1M2_STEP"
  fi
}
trap m1m2_on_exit EXIT
step() { M1M2_STEP=$((M1M2_STEP + 1)); M1M2_STEP_NAME=$1; echo "== STEP $M1M2_STEP/$M1M2_STEPS $1 (start $(date -u +%FT%TZ))"; }
ok() { echo "== STEP $M1M2_STEP/$M1M2_STEPS $M1M2_STEP_NAME: OK${1:+ - $1}"; m1m2_journal "STEP $M1M2_STEP OK $M1M2_STEP_NAME"; }
complete() {
  [ "$M1M2_STEP" = "$M1M2_STEPS" ] || m1m2_die 70 "only $M1M2_STEP of $M1M2_STEPS steps ran"
  M1M2_DONE=1; m1m2_journal "COMPLETE"
  echo "M1M2_COMPLETE script=$M1M2_SCRIPT release=$RELEASE_ID steps=$M1M2_STEPS at=$(date -u +%FT%TZ)"
}

# --- release directory and manifest: every listed file must match, this library and the caller included
[ -f "$RELDIR/RELEASE_MANIFEST.json" ] || m1m2_die 1 "no RELEASE_MANIFEST.json in $RELDIR"
M1M2_MF=$(python3 - "$RELDIR" <<'PY'
import hashlib, json, pathlib, shlex, sys
rel = pathlib.Path(sys.argv[1]); m = json.loads((rel / "RELEASE_MANIFEST.json").read_text(encoding="utf-8"))
bad = [f for f, h in m["delivery"]["source_files"].items()
       if not (rel / "src" / f).is_file() or hashlib.sha256((rel / "src" / f).read_bytes()).hexdigest() != h]
im, pre = m["runtime"]["images"], m["production"]
out = {"RELEASE_ID": m["release_id"], "M1M2_BAD_FILES": " ".join(bad),
       "APP": im["app"]["tag"], "APP_ID": im["app"]["local_image_id"], "NUM": im["numeric"]["tag"], "NUM_ID": im["numeric"]["local_image_id"],
       "OLD_APP": pre["pre_release_app_image"]["tag"], "OLD_APP_ID": pre["pre_release_app_image"]["local_image_id"],
       "B": pre["pre_release_backup"], "INPUT_SHA": m["inputs"]["phase5_bundle"]["sha256"],
       "INPUT_VERSION": m["inputs"]["phase5_bundle"]["dataset_version"], "REF_FILES": str(m["inputs"]["phase5_bundle"]["reference_files"])}
print("\n".join(f"{k}={shlex.quote(v)}" for k, v in out.items()))
PY
) || m1m2_die 1 "manifest unreadable or incomplete"
eval "$M1M2_MF"
[ -z "$M1M2_BAD_FILES" ] || m1m2_die 1 "release files differ from the manifest: $M1M2_BAD_FILES"
m1m2_src_sha() { python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["delivery"]["source_files"][sys.argv[2]])' "$RELDIR/RELEASE_MANIFEST.json" "$1"; }
for f in "${BASH_SOURCE[0]}" "${BASH_SOURCE[1]}"; do
  rel=deploy/linux/host/release/$(basename "$f")
  [ "$(sha256sum < "$f" | cut -c1-64)" = "$(m1m2_src_sha "$rel")" ] || m1m2_die 1 "$rel differs from the manifest"
done

# --- deployment and images
[ -f "$D/compose.yaml" ] || m1m2_die 1 "$D/compose.yaml missing"
[ -f "$ENV" ] || m1m2_die 1 "env file $ENV missing"
docker volume inspect "$VOL" >/dev/null 2>&1 </dev/null || m1m2_die 1 "volume $VOL missing"
m1m2_image_is() { [ "$(docker image inspect "$1" --format '{{.Id}}' 2>/dev/null </dev/null || echo NOT_LOADED)" = "$2" ]; }
m1m2_image_is "$APP" "$APP_ID" || m1m2_die 1 "$APP not loaded with the manifest id $APP_ID"
m1m2_image_is "$NUM" "$NUM_ID" || m1m2_die 1 "$NUM not loaded with the manifest id $NUM_ID"
export ROOTQ D ENV VOL LOCK C RELDIR RELEASE_ID APP APP_ID NUM NUM_ID OLD_APP OLD_APP_ID B
m1m2_journal "START sha256=$(sha256sum < "${BASH_SOURCE[1]}" | cut -c1-16)"
echo "M1M2_START script=$M1M2_SCRIPT release=$RELEASE_ID app=$APP num=$NUM vol=$VOL rollback_point=$B"

# --- helpers (all container commands read /dev/null)
take_lock() { exec 9>"$LOCK"; flock -w "${M1M2_LOCK_WAIT:-1500}" 9 || m1m2_die 75 "research lock not obtained within ${M1M2_LOCK_WAIT:-1500} s; later steps did not run"; }
drop_lock() { flock -u 9; exec 9>&-; }
crun() { $C --profile jobs run --rm --no-deps -T research-runner "$@" </dev/null; }
regcli() { docker run --rm --network none --read-only --tmpfs /tmp --user 10001:10001 --cap-drop ALL --security-opt no-new-privileges \
  --mount type=volume,src="$VOL",dst=/research,volume-subpath=research -e QUANT_RESEARCH_ROOT=/research "$NUM" "$@" </dev/null; }
regsql() { docker run --rm --network none --user 10001:10001 -v "$VOL:/v" --entrypoint python "$NUM" -c \
  "import sqlite3,sys; c=sqlite3.connect('file:/v/research/registry.sqlite3?mode=ro', uri=True); print(c.execute(sys.argv[1], sys.argv[2:]).fetchone()[0])" "$@" </dev/null; }
env_set() { if grep -q "^$1=" "$ENV"; then sed -i "s|^$1=.*|$1=$2|" "$ENV"; else echo "$1=$2" >> "$ENV"; fi; }
env_del() { sed -i "/^$1=/d" "$ENV"; }
env_get() { sed -n "s/^$1=//p" "$ENV"; }
env_show() { grep -E '^(APP_IMAGE|NUMERIC_IMAGE)=' "$ENV" || echo "(no NUMERIC_IMAGE line)"; }
web_ctr() { $C ps -q web </dev/null; }
web_wait() {  # web_wait IMAGE_ID: web container healthy AND running exactly that image
  local s=none id="" i
  for i in $(seq 1 60); do id=$(web_ctr); [ -n "$id" ] && s=$(docker inspect -f '{{.State.Health.Status}}' "$id" </dev/null); [ "$s" = healthy ] && break; sleep 5; done
  [ "$s" = healthy ] || m1m2_die 1 "web not healthy ($s)"
  [ "$(docker inspect -f '{{.Image}}' "$id" </dev/null)" = "$1" ] || m1m2_die 1 "web runs $(docker inspect -f '{{.Image}}' "$id"), expected $1"
  echo "web healthy after ~$((i * 5)) s on $1"
}
timer_set() {  # timer_set start|stop
  systemctl "$1" quant-research-runner.timer
  local want=active; [ "$1" = stop ] && want=inactive
  [ "$(systemctl is-active quant-research-runner.timer || true)" = "$want" ] || m1m2_die 1 "quant-research-runner.timer is not $want"
}
same_file() { [ "$(sha256sum < "$1" | cut -c1-64)" = "$(sha256sum < "$2" | cut -c1-64)" ]; }
