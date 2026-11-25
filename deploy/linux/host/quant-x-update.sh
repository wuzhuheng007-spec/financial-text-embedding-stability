#!/bin/bash
# One update round of the X stance study, run by quant-x-update.timer every two hours on this server.
#
#   1 collect   intraday increment (every query from where it last succeeded). Sends nothing while the provider
#               refuses for lack of credit (HTTP 402) or the gate is closed; the round goes on either way.
#   1b backfill the frozen focus plans named in X_FOCUS_PLANS (default below), each through the same ledger: units
#               already read are skipped at no cost, a used-up pool waits for the next budget day, a plan that
#               reached its limit asks nothing. Same hold and gate as the increment.
#   2 import    every results file the owner's GPU worker left in /srv/quant/x_offload/inbox (checked, idempotent)
#   3 build     derived tables: shares, snapshots, main views
#   4 export    pending pairs that no worker has claimed yet -> /srv/quant/x_offload/outbox (new posts first)
#   5 receipt   stage timings + the store's own freshness facts, stored for the page
#
# The classification itself does not run here: the version in force is answered by the owner's GPU only.
# The collection step holds the key; no other step has network access. Nothing is retried in a loop.
set -uo pipefail
cd /srv/quant/app/deploy/linux
ENV=/srv/quant/private.env; KEYFILE=/srv/quant/secrets/x_stance.env; VOL=quant-personal_personal_var; OFF=/srv/quant/x_offload
APP_IMAGE=$(sed -n 's/^APP_IMAGE=//p' "$ENV")
LOGDIR=$OFF/update_log; install -d -m 700 -o 10001 -g 10001 "$OFF" "$OFF/inbox" "$OFF/outbox" "$OFF/inbox_done"; install -d -m 700 "$LOGDIR"
TS=$(date -u +%Y%m%dT%H%M%SZ); R=$LOGDIR/$TS; mkdir -p "$R"
exec 9>/run/lock/quant-x-update.lock; flock -n 9 || { echo "another update round is still running; skipped"; exit 75; }
with_lock() { ( flock -w 900 8 || { echo "data lock busy for 15 min; skipped" >&2; exit 75; }; "$@" ) 8>/run/lock/quant-data.lock; }
local_job()   { docker run --rm --network none --memory 1500m --cpus 2 -v "$VOL":/app/apps/quant_portal/var --entrypoint python "$APP_IMAGE" -m scripts.x_stance_v1.cli "$@"; }
offload_job() { docker run --rm --network none --memory 1g --cpus 1 -v "$VOL":/app/apps/quant_portal/var -v "$OFF":/offload --entrypoint python "$APP_IMAGE" -m scripts.x_stance_v1.cli "$@"; }
net_job()     { docker compose -f compose.yaml --env-file "$ENV" run --rm --no-deps -T -e TWITTERAPI_IO_API_KEY --entrypoint python feed -m scripts.x_stance_v1.cli "$@"; }
now() { date +%s.%N; }
secs() { python3 -c "print(round($2-$1,1))"; }
STARTED=$(date -u +%FT%TZ); T0=$(now)

# 1 collect
t=$(now); rc=0
( if [ -z "${TWITTERAPI_IO_API_KEY:-}" ] && [ -r "$KEYFILE" ]; then set -a; . "$KEYFILE"; set +a; fi
  export TWITTERAPI_IO_API_KEY=${TWITTERAPI_IO_API_KEY:-}
  with_lock net_job incremental run ) > "$R/collect.json" 2> "$R/collect.err" || rc=$?
COLLECT_S=$(secs $t $(now)); COLLECT_RC=$rc

# 1b backfill plans
t=$(now); FOCUS_PLANS=${X_FOCUS_PLANS-qqq-20261126-depth}
for fp in $FOCUS_PLANS; do
  ( if [ -z "${TWITTERAPI_IO_API_KEY:-}" ] && [ -r "$KEYFILE" ]; then set -a; . "$KEYFILE"; set +a; fi
    export TWITTERAPI_IO_API_KEY=${TWITTERAPI_IO_API_KEY:-}
    with_lock net_job focus run "$fp" ) > "$R/focus_$fp.json" 2> "$R/focus_$fp.err" || true
done
FOCUS_JSON=$(python3 - "$R" $FOCUS_PLANS <<'EOF2'
import json, sys
out = {}
for fp in sys.argv[2:]:
    try:
        d = json.load(open(f"{sys.argv[1]}/focus_{fp}.json"))
    except Exception:
        out[fp] = {"status": "NO_OUTPUT"}; continue
    p = d.get("progress") or {}
    out[fp] = {"status": d.get("status"), "stopped": d.get("stopped"), "requests": d.get("requests", d.get("requests_sent")), "units_asked": d.get("units_asked"),
               "pools_exhausted": [x.get("pool") for x in d.get("pools_exhausted") or []], "plan_new_posts": p.get("new_posts"), "plan_returned": p.get("returned")}
print(json.dumps(out))
EOF2
)
FOCUS_S=$(secs $t $(now))

# 2 import
t=$(now); IMPORTED=0; IMPORT_FAILED=0; LABELS=0
for m in "$OFF"/inbox/*.jsonl.manifest.json; do
  [ -e "$m" ] || continue
  f=${m%.manifest.json}; n=$(basename "$f")
  [ -e "$f" ] || continue
  if with_lock offload_job import-results "/offload/inbox/$n" > "$R/import_$n.json" 2> "$R/import_$n.err"; then
    IMPORTED=$((IMPORTED+1)); LABELS=$((LABELS + $(python3 -c "import json,sys; print(json.load(open(sys.argv[1])).get('labels_written',0))" "$R/import_$n.json" 2>/dev/null || echo 0)))
    mv "$f" "$m" "$OFF/inbox_done/"
  else
    IMPORT_FAILED=$((IMPORT_FAILED+1))
  fi
done
IMPORT_S=$(secs $t $(now))

# 3 build
t=$(now); rc=0
with_lock local_job build > "$R/build.json" 2> "$R/build.err" || rc=$?
BUILD_S=$(secs $t $(now)); BUILD_RC=$rc

# 4 export what no worker has claimed yet
t=$(now); rc=0; W=work_$TS.jsonl
with_lock offload_job export-pending "/offload/outbox/$W" > "$R/export.json" 2> "$R/export.err" || rc=$?
EXPORTED=$(python3 -c "import json,sys; print(json.load(open(sys.argv[1])).get('items',0))" "$R/export.json" 2>/dev/null || echo 0)
[ "$EXPORTED" = 0 ] && rm -f "$OFF/outbox/$W" "$OFF/outbox/$W.manifest.json"
EXPORT_S=$(secs $t $(now)); EXPORT_RC=$rc

# 5 receipt
STAGES=$(python3 - "$R/collect.json" <<EOF
import json, sys
try:
    c = json.load(open(sys.argv[1]))
except Exception:
    c = {"status": "NO_OUTPUT"}
col = {k: c.get(k) for k in ("status", "requests", "units_asked", "returned", "new_posts", "duplicates", "stopped", "transient_errors")}
col["requests"] = col["requests"] if col["requests"] is not None else c.get("requests_sent")
col.update(exit=$COLLECT_RC, seconds=$COLLECT_S, gate_reasons=(c.get("gate") or {}).get("reasons"))
print(json.dumps({"started_at_utc": "$STARTED", "collect": col, "backfill": {"plans": json.loads('$FOCUS_JSON' or '{}'), "seconds": $FOCUS_S}, "import": {"files": $IMPORTED, "failed": $IMPORT_FAILED, "labels_written": $LABELS, "seconds": $IMPORT_S},
                  "build": {"exit": $BUILD_RC, "seconds": $BUILD_S}, "export": {"exit": $EXPORT_RC, "items": $EXPORTED, "seconds": $EXPORT_S}, "round_seconds": round($(now) - $T0, 1)}))
EOF
)
with_lock local_job update-receipt "$STAGES" > "$R/receipt.json" 2> "$R/receipt.err" || true
python3 - "$R/receipt.json" <<'EOF'
import json, sys
try:
    r = json.load(open(sys.argv[1]))
except Exception as ex:
    print("receipt not readable:", type(ex).__name__); sys.exit(0)
s = r["stages"]
print("round", r["round_started_at_utc"], "->", r["round_finished_at_utc"], "ok" if r["ok"] else "NOT OK", "| collect", s["collect"]["status"], "requests", s["collect"]["requests"], "new", s["collect"]["new_posts"], s["collect"]["seconds"], "s",
      "| import files", s["import"]["files"], "labels", s["import"]["labels_written"], s["import"]["seconds"], "s | build", s["build"]["exit"], s["build"]["seconds"], "s | export", s["export"]["items"], s["export"]["seconds"], "s | total", s["round_seconds"], "s")
print("pending: new posts", r["pending"]["new_posts_queue"], "history backlog", r["pending"]["history_backlog"], "| hold", (r["credit_hold"] or {}).get("reason"), "| last successful collection", r["last_successful_collection_at_utc"], "| last successful update", r["last_successful_update_at_utc"])
EOF
ls -1t "$LOGDIR" | tail -n +200 | while read d; do rm -rf "$LOGDIR/$d"; done      # keeps the newest rounds' logs (about two weeks)
exit 0
