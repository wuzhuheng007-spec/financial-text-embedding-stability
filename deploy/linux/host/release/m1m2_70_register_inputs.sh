#!/bin/bash
# M1/M2 runbook section 7: register the private phase5 input (and, optionally, its reference ledgers).
#   sudo bash -euo pipefail <RELDIR>/src/deploy/linux/host/release/m1m2_70_register_inputs.sh INPUT_FILE [REFERENCE_DIR] </dev/null
# Mounts are minimal: no network, read-only root, research/ subpath writable, the source read-only.
# Repeating the script is idempotent (ALREADY_REGISTERED). A different file for the same version is refused
# with a non-zero exit and leaves the registry unchanged.
# Exit: 0 complete; 1 a check failed or the registration was refused; 64 wrong invocation.
[ -f "${BASH_SOURCE[0]:-}" ] || { echo "M1M2_STOP code=64 reason=E-1 guard: run as a file: sudo bash -euo pipefail <script> </dev/null" >&2; exit 64; }
M1M2_SCRIPT=m1m2_70_register_inputs.sh; M1M2_STEPS=3
F=${1:-}; REF=${2:-}
. "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/m1m2_env.sh"
reg() { docker run --rm --network none --read-only --tmpfs /tmp --user 10001:10001 --cap-drop ALL --security-opt no-new-privileges \
  --mount type=volume,src="$VOL",dst=/research,volume-subpath=research "$@" </dev/null; }

step "input file hash equals the manifest"
[ -f "$F" ] || m1m2_die 1 "usage: m1m2_70_register_inputs.sh INPUT_FILE [REFERENCE_DIR]"
[ "$(sha256sum < "$F" | cut -c1-64)" = "$INPUT_SHA" ] || m1m2_die 1 "input hash differs from the manifest; nothing registered"
chown 10001:10001 "$F"; chmod 0400 "$F"
ok "$INPUT_SHA"

step "register the snapshot phase5_bundle@$INPUT_VERSION"
out=$(reg --mount type=bind,src="$F",dst=/src/market_research.sqlite,readonly -e QUANT_RESEARCH_ROOT=/research "$NUM" register-bundle \
  --source /src/market_research.sqlite --dataset-id phase5_bundle --dataset-version "$INPUT_VERSION" \
  --title "phase5 输入快照（私有，$INPUT_VERSION）" --expected-sha256 "$INPUT_SHA"); echo "$out"
[[ $out == *'"state": "REGISTERED"'* || $out == *'"state": "ALREADY_REGISTERED"'* ]] || m1m2_die 1 "snapshot not registered"
ok

step "reference ledgers (optional)"
if [ -n "$REF" ]; then
  [ "$(find "$REF" -type f | wc -l)" = "$REF_FILES" ] || m1m2_die 1 "reference directory does not hold exactly $REF_FILES files"
  chown -R 10001:10001 "$REF"; find "$REF" -type d -exec chmod 0500 {} +; find "$REF" -type f -exec chmod 0400 {} +
  out=$(reg --mount type=bind,src="$REF",dst=/ref,readonly -e QUANT_RESEARCH_ROOT=/research "$NUM" register-reference --source /ref --input-sha "$INPUT_SHA")
  python3 -c 'import json,sys; d=json.loads(sys.argv[1]); print(d["state"], d["files"], d["index_sha256"][:16]); sys.exit(0 if d["state"] in ("REGISTERED","ALREADY_REGISTERED") else 1)' "$out" || m1m2_die 1 "reference ledgers not registered"
  ok "$REF_FILES files"
else
  ok "skipped (no reference directory given)"
fi
complete
