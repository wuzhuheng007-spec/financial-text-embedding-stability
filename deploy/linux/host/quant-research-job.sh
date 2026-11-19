#!/bin/bash
# Protected numeric research job (closeout 2026-11-20, finding F1). ONE definition of the container flags, used
# unchanged by the timer runner (quant-research-runner.sh), the restore check (quant-restore-check.sh) and the
# isolated acceptance loop. CONTROL and COMPUTE are separate containers of the same numeric image:
#   control  registry access: ONLY the research/ subpath of the var volume (read-write); scheduler logic only.
#   compute  the frozen numeric program: ONLY the registered input snapshot directory (read-only subpath) and
#            research/runs/<id>/work (read-write subpath). No registry, no other run, no other var data,
#            no network, read-only root fs, no capabilities, measured CPU/memory/pids limits.
# The web page only queues runs. The CALLER holds /run/lock/quant-research.lock (one scheduler).
#
# usage: quant-research-job.sh --image IMAGE (--var-volume VOLUME | --var-dir DIR) (--next | --run RUN_ID)
#                              [--max-seconds 900] [--probe-isolation]
#   --var-volume  a docker volume holding research/ (production: quant-personal_personal_var)
#   --var-dir     a host directory holding research/ (restore check scratch copy)
#   --probe-isolation  before the computation, run the isolation probe in a container with EXACTLY the compute
#                      flags (drills/acceptance; it attempts writes on the read-only input and records the result)
# exit: 0 idle / accepted / cancelled, 1 failed, 2 target not claimable, 143 terminated, 64 usage, 70 docker error
set -uo pipefail
IMAGE= VOL= VDIR= MODE= RUN= MAXS=900 PROBE=0
while [ $# -gt 0 ]; do
  case "$1" in
    --image) IMAGE=$2; shift 2;; --var-volume) VOL=$2; shift 2;; --var-dir) VDIR=$2; shift 2;;
    --next) MODE=next; shift;; --run) MODE=run; RUN=$2; shift 2;; --max-seconds) MAXS=$2; shift 2;;
    --probe-isolation) PROBE=1; shift;;
    *) echo "unknown argument: $1" >&2; exit 64;;
  esac
done
if [ -z "$IMAGE" ] || [ -z "$MODE" ] || { [ -z "$VOL" ] && [ -z "$VDIR" ]; } || ! [[ "$MAXS" =~ ^[0-9]+$ ]]; then
  echo "usage: $0 --image IMAGE (--var-volume VOL | --var-dir DIR) (--next | --run RUN_ID) [--max-seconds N] [--probe-isolation]" >&2; exit 64
fi
if [ "$MODE" = run ] && ! [[ "$RUN" =~ ^run-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{6}$ ]]; then echo "bad run id" >&2; exit 64; fi
log() { echo "$(date -u +%FT%TZ) quant-research-job: $*"; }

REV=$(docker image inspect "$IMAGE" --format '{{index .Config.Labels "org.opencontainers.image.revision"}}') || exit 70
IMG=$(docker image inspect "$IMAGE" --format '{{.Id}}') || exit 70
TOKEN="qj$(od -An -N5 -tx1 /dev/urandom | tr -d ' \n')"   # 12 chars: worker id of the run AND label of its containers
CPUS=${QUANT_JOB_CPUS:-1.0}; MEM=${QUANT_JOB_MEM:-1g}; PIDS=${QUANT_JOB_PIDS:-192}
ISOLATION="SEPARATE_COMPUTE_CONTAINER_V1: input read-only subpath; writable work subpath runs/<id>/work only; no registry; no network"
HARD=(--network none --read-only --security-opt no-new-privileges --cap-drop ALL --user 10001:10001)
CNAME="quant-research-compute-$TOKEN"; SNAME="quant-research-supervise-$TOKEN"

mount_of() {  # mount_of SUBPATH TARGET [ro] -> global array M
  if [ -n "$VOL" ]; then M=(--mount "type=volume,src=$VOL,dst=$2,volume-subpath=$1${3:+,readonly}")
  else M=(--mount "type=bind,src=$VDIR/$1,dst=$2${3:+,readonly}"); fi
}
control() {  # control NAME [docker-run options...] -- worker args...
  local name=$1; shift; local opts=(); while [ "$1" != "--" ]; do opts+=("$1"); shift; done; shift
  mount_of research /research
  docker run --name "quant-research-control-$name-$TOKEN" --label "quant.research.job=$TOKEN" --label quant.research.role=control \
    "${HARD[@]}" --tmpfs /tmp:size=64m --cpus 0.5 --memory 768m --pids-limit 64 "${M[@]}" "${opts[@]}" \
    -e QUANT_RESEARCH_ROOT=/research -e QUANT_CODE_VERSION="$REV" -e QUANT_IMAGE_ID="$IMG" -e QUANT_JOB_ISOLATION="$ISOLATION" \
    "$IMAGE" "$@"
}
compute_args() {  # the ONLY definition of the compute container's mounts and limits
  mount_of "$IN_SUB" /input ro; local mi=("${M[@]}")
  mount_of "$WORK_SUB" /work; local mw=("${M[@]}")
  C=(--label "quant.research.job=$TOKEN" "${HARD[@]}" --tmpfs /tmp:size=256m --cpus "$CPUS" --memory "$MEM" --pids-limit "$PIDS"
     "${mi[@]}" "${mw[@]}" -e QUANT_CODE_VERSION="$REV" -e QUANT_IMAGE_ID="$IMG" --entrypoint python)
}

# 1) CONTROL: claim (records this image's verified capability; claims only v2 engines it supports)
J=$(control claim --rm -- job-claim --worker-id "$TOKEN" ${RUN:+--run "$RUN"} --max-seconds "$MAXS"); CRC=$?
log "claim exit $CRC: $J"
RID=$(sed -n 's/.*"run_id": "\(run-[0-9]\{8\}T[0-9]\{6\}Z-[0-9a-f]\{6\}\)".*/\1/p' <<<"$J")
if [ -z "$RID" ]; then [ "$MODE" = run ] && exit 2; exit 0; fi
if grep -q '"abort"' <<<"$J"; then exit 1; fi
IN_SUB=$(sed -n 's/.*"input_subpath": "\(research\/datasets\/[A-Za-z0-9._@-]*\/[A-Za-z0-9._@-]*\)".*/\1/p' <<<"$J")
SHA=$(sed -n 's/.*"content_sha256": "\([0-9a-f]\{64\}\)".*/\1/p' <<<"$J")
WORK_SUB="research/runs/$RID/work"
if [ -z "$IN_SUB" ] || [ -z "$SHA" ]; then
  log "claim output not understood; failing the run"; control fin --rm -- job-finalize "$RID" --compute-exit 255 --stop-reason supervisor_lost; exit 1
fi
compute_args

# 2) optional isolation probe with the identical compute flags (writes work/isolation_probe.json)
if [ "$PROBE" = 1 ]; then
  docker run --rm --name "quant-research-probe-$TOKEN" --label quant.research.role=probe "${C[@]}" "$IMAGE" \
    -m scripts.quant_research_v1.isolation_probe --input /input --work /work; log "isolation probe exit $?"
fi

# 3) COMPUTE (detached) + lease keeper (detached), watched here
TERMINATED=0; trap 'TERMINATED=1' TERM INT
docker run -d --name "$CNAME" --label quant.research.role=compute "${C[@]}" "$IMAGE" \
  -m scripts.quant_research_v1.engines.phase5_o2o compute --input /input --work /work --sha "$SHA" --max-seconds $((MAXS + 30)) >/dev/null \
  || { log "compute container did not start"; control fin --rm -- job-finalize "$RID" --compute-exit 255 --stop-reason supervisor_lost; exit 70; }
control supervise -d -- job-supervise "$RID" --worker-id "$TOKEN" --max-seconds "$MAXS" >/dev/null
SNAME="quant-research-control-supervise-$TOKEN"
log "run $RID: compute $CNAME, lease keeper $SNAME (token $TOKEN)"
REASON=none
while :; do
  if [ "$TERMINATED" = 1 ]; then REASON=terminated; break; fi
  [ "$(docker inspect -f '{{.State.Running}}' "$CNAME" 2>/dev/null)" = true ] || break
  if [ "$(docker inspect -f '{{.State.Running}}' "$SNAME" 2>/dev/null)" != true ]; then
    case "$(docker inspect -f '{{.State.ExitCode}}' "$SNAME" 2>/dev/null)" in
      10) REASON=cancel;; 11) REASON=timeout;; 12) REASON=terminated;; *) REASON=supervisor_lost;;
    esac; break
  fi
  sleep 2 & wait $!
done
trap - TERM INT
[ "$REASON" != none ] && docker stop -t 20 "$CNAME" >/dev/null 2>&1
docker wait "$CNAME" >/dev/null 2>&1
CEXIT=$(docker inspect -f '{{.State.ExitCode}}' "$CNAME" 2>/dev/null || echo 255)
OOM=$(docker inspect -f '{{.State.OOMKilled}}' "$CNAME" 2>/dev/null || echo unknown)
docker stop -t 5 "$SNAME" >/dev/null 2>&1
docker rm -f "$CNAME" "$SNAME" >/dev/null 2>&1
log "compute exit $CEXIT (oom_killed=$OOM), stop reason $REASON"

# 4) CONTROL: finalize (independent verification + acceptance + terminal state)
control fin --rm -- job-finalize "$RID" --compute-exit "$CEXIT" --stop-reason "$REASON"; FRC=$?
log "finalize exit $FRC"
exit $FRC
