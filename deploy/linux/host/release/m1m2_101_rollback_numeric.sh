#!/bin/bash
# M1/M2 runbook section 10.1: level-1 rollback. Withdraws numeric jobs only; web and the legacy worker stay
# on this release.
#   sudo bash -euo pipefail <RELDIR>/src/deploy/linux/host/release/m1m2_101_rollback_numeric.sh </dev/null
# Queued phase5 runs stay QUEUED (not lost). The legacy worker never claims them; the page shows
# ENGINE_NOT_BUILT.
# Roll forward again: m1m2_45_switch.sh, then m1m2_60_resume.sh (the first tick re-registers the capability).
# Exit: 0 complete; 1 a check failed; 64 wrong invocation; 75 research lock not obtained (timer stopped,
# admission paused, nothing withdrawn).
[ -f "${BASH_SOURCE[0]:-}" ] || { echo "M1M2_STOP code=64 reason=E-1 guard: run as a file: sudo bash -euo pipefail <script> </dev/null" >&2; exit 64; }
M1M2_SCRIPT=m1m2_101_rollback_numeric.sh; M1M2_STEPS=7
. "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/m1m2_env.sh"

step "pause admission"
out=$(regcli admission pause --reason "level-1 rollback: numeric jobs withdrawn"); echo "$out"
[[ $out == *'"admission": "PAUSED"'* ]] || m1m2_die 1 "admission not paused"
ok

step "stop the research timer"
timer_set stop
ok

step "take the research lock (a running job finishes first)"
take_lock
ok

step "withdraw the numeric capability"
out=$(regcli withdraw-capability --engine phase5_o2o_v1@1); echo "$out"
[ "$(regsql "select count(*) from worker_capabilities where engine_id='phase5_o2o_v1'")" = 0 ] || m1m2_die 1 "capability rows remain"
ok

step "remove NUMERIC_IMAGE from the private env file"
env_del NUMERIC_IMAGE
[ -z "$(env_get NUMERIC_IMAGE)" ] || m1m2_die 1 "NUMERIC_IMAGE still set"
ok "$(env_show | tr '\n' ' ')"

step "release the lock and start the research timer"
drop_lock; timer_set start
ok

step "open admission"
out=$(regcli admission open); echo "$out"
[[ $out == *'"admission": "OPEN"'* ]] || m1m2_die 1 "admission not open"
ok
complete
