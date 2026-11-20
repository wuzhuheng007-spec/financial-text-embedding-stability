#!/bin/bash
# M1/M2 runbook sections 9 and 10.2: full rollback to the pre-release web and host files. Use it both when a
# release fails before m1m2_60_resume.sh and as the level-2 rollback after a release.
#   sudo bash -euo pipefail <RELDIR>/src/deploy/linux/host/release/m1m2_102_rollback_full.sh </dev/null
# Before switching, queued v2 runs are cancelled: the pre-release worker has no engine dispatch, claims the
# oldest QUEUED run and fails phase5 runs. v2 tables, artifacts, references and datasets are kept, so a later
# re-release (m1m2_45_switch.sh + m1m2_60_resume.sh) finds them.
# Exit: 0 complete; 1 a check failed; 64 wrong invocation; 75 research lock not obtained (timer stopped,
# admission paused, nothing switched).
[ -f "${BASH_SOURCE[0]:-}" ] || { echo "M1M2_STOP code=64 reason=E-1 guard: run as a file: sudo bash -euo pipefail <script> </dev/null" >&2; exit 64; }
M1M2_SCRIPT=m1m2_102_rollback_full.sh; M1M2_STEPS=10
. "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/m1m2_env.sh"

step "rollback point complete; pre-release web image loaded"
for f in private.env compose.yaml host quant-research-runner.sh quant-restore-check.sh quant-research-runner.service; do
  [ -e "$B/$f" ] || m1m2_die 1 "rollback point $B lacks $f; nothing changed"
done
m1m2_image_is "$OLD_APP" "$OLD_APP_ID" || m1m2_die 1 "$OLD_APP not loaded with $OLD_APP_ID; nothing changed"
ok "$B; $OLD_APP"

step "pause admission"
out=$(regcli admission pause --reason "full rollback to $OLD_APP"); echo "$out"
[[ $out == *'"admission": "PAUSED"'* ]] || m1m2_die 1 "admission not paused"
ok

step "stop the research timer"
timer_set stop
ok

step "take the research lock (a running job finishes first)"
take_lock
ok

step "cancel queued v2 runs (the pre-release worker cannot run them)"
out=$(regcli cancel-queued --engine phase5_o2o_v1@1 --reason "rollback to $OLD_APP (no engine dispatch)"); echo "$out"
[ "$(regsql "select count(*) from runs where status='QUEUED' and engine_id='phase5_o2o_v1'")" = 0 ] || m1m2_die 1 "queued v2 runs remain"
ok

step "image lines: APP_IMAGE=$OLD_APP, no NUMERIC_IMAGE"
env_set APP_IMAGE "$OLD_APP"; env_del NUMERIC_IMAGE
[ "$(env_get APP_IMAGE)" = "$OLD_APP" ] && [ -z "$(env_get NUMERIC_IMAGE)" ] || m1m2_die 1 "env lines not set"
ok "$(env_show | tr '\n' ' ')"

step "restore host files from the rollback point"
cp -a "$B/compose.yaml" "$D/compose.yaml"; cp -a "$B/host/." "$D/host/"
install -m 0755 "$B/quant-research-runner.sh" "$B/quant-restore-check.sh" "$SBIN/"; rm -f "$SBIN/quant-research-job.sh"
install -m 0644 "$B/quant-research-runner.service" "$UNITS/"; systemctl daemon-reload
same_file "$B/compose.yaml" "$D/compose.yaml" && same_file "$B/quant-research-runner.sh" "$SBIN/quant-research-runner.sh" \
  && same_file "$B/quant-restore-check.sh" "$SBIN/quant-restore-check.sh" && same_file "$B/quant-research-runner.service" "$UNITS/quant-research-runner.service" \
  && [ ! -e "$SBIN/quant-research-job.sh" ] || m1m2_die 1 "restored files differ from the rollback point"
ok "compose, host dir, runner, restore-check and unit equal $B; job launcher removed"

step "recreate the web service on $OLD_APP"
$C up -d --no-build --no-deps web </dev/null
web_wait "$OLD_APP_ID"
ok

step "remove the admission flag (the pre-release web ignores it; a re-release starts clean)"
out=$(regcli admission open); echo "$out"
[[ $out == *'"admission": "OPEN"'* ]] || m1m2_die 1 "flag not removed"
ok

step "release the lock and start the research timer"
drop_lock; timer_set start
ok
complete
