#!/bin/bash
# M1/M2 runbook section 6: resume scheduling and open admission (after m1m2_45_switch.sh).
#   sudo bash -euo pipefail <RELDIR>/src/deploy/linux/host/release/m1m2_60_resume.sh </dev/null
# Steps:
#   - start the research timer and wait for the first runner tick it triggers to FINISH successfully;
#   - check that the numeric image registered its capability;
#   - open admission.
# A tick started by the timer is scheduler evidence; the operator never starts the service by hand here.
# Exit: 0 complete; 1 a check failed (admission stays paused); 64 wrong invocation.
[ -f "${BASH_SOURCE[0]:-}" ] || { echo "M1M2_STOP code=64 reason=E-1 guard: run as a file: sudo bash -euo pipefail <script> </dev/null" >&2; exit 64; }
M1M2_SCRIPT=m1m2_60_resume.sh; M1M2_STEPS=5
. "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/m1m2_env.sh"

step "switched state present (image lines, healthy web)"
[ "$(env_get APP_IMAGE)" = "$APP" ] && [ "$(env_get NUMERIC_IMAGE)" = "$NUM" ] || m1m2_die 1 "image lines are not this release's ($(env_show | tr '\n' ' ')): run m1m2_45_switch.sh first"
web_wait "$APP_ID"
ok

step "start the research timer"
I0=$(systemctl show quant-research-runner.service -p InvocationID --value)
timer_set start
ok "quant-research-runner.timer active"

step "first runner tick triggered by the timer finishes successfully"
I=""; st=""
for i in $(seq 1 120); do
  I=$(systemctl show quant-research-runner.service -p InvocationID --value); st=$(systemctl show quant-research-runner.service -p ActiveState --value)
  [ -n "$I" ] && [ "$I" != "$I0" ] && [ "$st" != activating ] && [ "$st" != active ] && break
  sleep 2
done
[ -n "$I" ] && [ "$I" != "$I0" ] || m1m2_die 1 "no runner tick within 240 s after the timer started"
[ "$(systemctl show quant-research-runner.service -p Result --value)" = success ] || m1m2_die 1 "runner tick $I did not succeed"
journalctl _SYSTEMD_INVOCATION_ID="$I" --no-pager -o short-iso 2>/dev/null | grep -v -E "Container .* (Creating|Created)" | tail -4 || true
ok "tick $I success"

step "numeric capability registered for this image"
n=$(regsql "select count(*) from worker_capabilities where image_id=? and engine_id='phase5_o2o_v1'" "$NUM_ID")
[ "$n" -ge 1 ] || m1m2_die 1 "no phase5_o2o_v1 capability row for $NUM_ID"
ok "$n row(s) for $NUM_ID"

step "open admission"
out=$(regcli admission open); echo "$out"
[[ $out == *'"admission": "OPEN"'* ]] || m1m2_die 1 "admission not open"
ok
complete
