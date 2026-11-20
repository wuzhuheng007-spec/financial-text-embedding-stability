#!/bin/bash
# M1/M2 runbook sections 4-5: switch the research runtime and the web.
#   sudo bash -euo pipefail <RELDIR>/src/deploy/linux/host/release/m1m2_45_switch.sh </dev/null
# Steps:
#   - stop only the research timer, take the research lock, check that no job is running;
#   - install the host files, set the two image lines;
#   - start the new web with admission paused, register defaults.
# The research timer stays STOPPED; m1m2_60_resume.sh starts it and opens admission.
# Idempotent. Also used for a web-only image change: new manifest, then this script, then m1m2_60_resume.sh.
# Exit: 0 complete; 1 a check failed (after step 5: run m1m2_102_rollback_full.sh); 64 wrong invocation;
#       75 research lock not obtained (timer stopped, nothing switched; see the runbook).
[ -f "${BASH_SOURCE[0]:-}" ] || { echo "M1M2_STOP code=64 reason=E-1 guard: run as a file: sudo bash -euo pipefail <script> </dev/null" >&2; exit 64; }
M1M2_SCRIPT=m1m2_45_switch.sh; M1M2_STEPS=9
. "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/m1m2_env.sh"
S=$RELDIR/src

step "rollback point present"
[ -f "$B/private.env" ] && [ -f "$B/compose.yaml" ] && [ -d "$B/host" ] && [ -f "$B/quant-research-runner.sh" ] || m1m2_die 1 "rollback point $B missing or incomplete: run m1m2_30_prerelease_backup.sh; nothing changed"
ok "$B"

step "stop the research timer (only this timer)"
timer_set stop
ok "quant-research-runner.timer inactive"

step "take the research lock"
take_lock
ok "$LOCK"

step "drain: no research job running"
[ -z "$(docker ps -q --filter label=com.docker.compose.service=research-runner)" ] || m1m2_die 1 "a legacy research container is still running"
[ -z "$(docker ps -q --filter label=quant.research.job)" ] || m1m2_die 1 "a protected research job container is still running"
R=$(regsql "select count(*) from runs where status='RUNNING'")
[ "$R" = 0 ] || m1m2_die 1 "registry still has $R RUNNING runs"
ok "RUNNING=0, no job containers"

step "install host files"
install -m 0644 "$S/deploy/linux/compose.yaml" "$D/compose.yaml"
install -m 0644 "$S/deploy/linux/host/quant_research_backup.py" "$S/deploy/linux/host/backup_scope.json" "$D/host/"
install -m 0755 "$S/deploy/linux/host/quant-research-runner.sh" "$S/deploy/linux/host/quant-research-job.sh" "$S/deploy/linux/host/quant-restore-check.sh" "$SBIN/"
install -m 0644 "$S/deploy/linux/host/quant-research-runner.service" "$UNITS/"
systemctl daemon-reload
for p in "deploy/linux/compose.yaml:$D/compose.yaml" "deploy/linux/host/quant_research_backup.py:$D/host/quant_research_backup.py" \
         "deploy/linux/host/backup_scope.json:$D/host/backup_scope.json" "deploy/linux/host/quant-research-runner.sh:$SBIN/quant-research-runner.sh" \
         "deploy/linux/host/quant-research-job.sh:$SBIN/quant-research-job.sh" "deploy/linux/host/quant-restore-check.sh:$SBIN/quant-restore-check.sh" \
         "deploy/linux/host/quant-research-runner.service:$UNITS/quant-research-runner.service"; do
  [ "$(sha256sum < "${p#*:}" | cut -c1-64)" = "$(m1m2_src_sha "${p%%:*}")" ] || m1m2_die 1 "${p#*:} differs from the manifest after install"
done
ok "7 files equal the manifest; daemon-reload done"

step "image lines in the private env file"
env_set APP_IMAGE "$APP"; env_set NUMERIC_IMAGE "$NUM"
[ "$(env_get APP_IMAGE)" = "$APP" ] && [ "$(env_get NUMERIC_IMAGE)" = "$NUM" ] || m1m2_die 1 "env lines not set"
ok "$(env_show | tr '\n' ' ')"

step "pause admission (the new web starts paused)"
out=$(regcli admission pause --reason "M1/M2 release $RELEASE_ID in progress"); echo "$out"
[[ $out == *'"admission": "PAUSED"'* ]] || m1m2_die 1 "admission not paused"
ok

step "recreate the web service only"
$C up -d --no-build --no-deps web </dev/null
web_wait "$APP_ID"
ok

step "register defaults (idempotent)"
crun register-defaults
ok
complete
