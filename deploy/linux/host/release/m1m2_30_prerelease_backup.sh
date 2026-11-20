#!/bin/bash
# M1/M2 runbook section 3: pre-release backup. Run it once per release, before m1m2_45_switch.sh.
#   sudo bash -euo pipefail <RELDIR>/src/deploy/linux/host/release/m1m2_30_prerelease_backup.sh </dev/null
# Copies the files a level-2 rollback restores, records the running web image, then runs the daily backup with
# its independent restore check (quant-backup.service) and requires RESTORE_CHECK ok.
# It refuses an existing rollback point: re-running the old section-3 block would have overwritten
# host-before-<SH> with post-release files.
# Exit: 0 complete; 1 a check failed; 64 wrong invocation.
[ -f "${BASH_SOURCE[0]:-}" ] || { echo "M1M2_STOP code=64 reason=E-1 guard: run as a file: sudo bash -euo pipefail <script> </dev/null" >&2; exit 64; }
M1M2_SCRIPT=m1m2_30_prerelease_backup.sh; M1M2_STEPS=4
. "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/m1m2_env.sh"

step "rollback point does not exist yet; running web is the manifest's pre-release image"
[ ! -e "$B" ] || m1m2_die 1 "$B already exists: a rollback point is never overwritten; nothing changed"
W=$(web_ctr); [ -n "$W" ] || m1m2_die 1 "no running web container"
[ "$(docker inspect -f '{{.Image}}' "$W")" = "$OLD_APP_ID" ] || m1m2_die 1 "running web is not the manifest's pre-release image $OLD_APP ($OLD_APP_ID)"
ok "$B free; web runs $OLD_APP"

step "copy host state into the rollback point"
install -d -m 0700 "$B"
cp -a "$ENV" "$D/compose.yaml" "$D/host" "$SBIN/quant-research-runner.sh" "$SBIN/quant-restore-check.sh" "$SBIN/quant-backup.sh" \
  "$UNITS/quant-research-runner.service" "$UNITS/quant-research-runner.timer" "$UNITS/quant-backup.service" "$UNITS/quant-backup.timer" "$B/"
docker inspect "$W" --format '{{.Config.Image}} {{.Image}}' > "$B/web_image_before.txt"
docker run --rm --network none --read-only --user 10001:10001 -v "$VOL:/v:ro" --entrypoint sha256sum "$NUM" /v/research/registry.sqlite3 </dev/null > "$B/registry_sha_before.txt"
( cd "$B" && find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS )
ok "$(wc -l < "$B/SHA256SUMS") files; web $(cat "$B/web_image_before.txt")"

step "daily backup with independent restore check (quant-backup.service)"
systemctl start quant-backup.service
[ "$(systemctl show quant-backup.service -p Result --value)" = success ] || m1m2_die 1 "quant-backup.service result is not success"
ok

step "newest daily RESTORE_CHECK.json is ok"
N=$(ls -1d "$ROOTQ"/backup/daily/*/ | sort | tail -1)
python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); sys.exit(0 if d.get("ok") is True else 1)' "${N}RESTORE_CHECK.json" || m1m2_die 1 "${N}RESTORE_CHECK.json not ok"
ok "$N"
complete
