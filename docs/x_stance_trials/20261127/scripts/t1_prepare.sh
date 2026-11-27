#!/bin/bash
# Like-floor trial, step 1 (offline, on a COPY): production var is mounted READ-ONLY; the copy, the candidate config
# and the work file live in a private trial dir. No network, no API request, no write to production.
set -u
T=/srv/quant/x_offload/trial_like5_20261127; IMG=quant-personal-app:64d3154; VOL=quant-personal_personal_var
[ -e $T ] && { echo "ABORT: $T exists"; exit 1; }
install -d -m 700 -o 10001 -g 10001 $T
exec > >(tee $T/t1.log) 2>&1
date -u +start_utc=%FT%TZ
docker run --rm --network none --memory 1500m --cpus 2 --user 10001:10001 -v $VOL:/app/apps/quant_portal/var:ro -v $T:/t --entrypoint sh $IMG -c '
set -e
python - <<PY
import sqlite3, json
src = sqlite3.connect("file:/app/apps/quant_portal/var/x_stance/x_stance.sqlite3?mode=ro", uri=True); dst = sqlite3.connect("/t/c.sqlite3"); src.backup(dst); dst.close()
from scripts.x_stance_v1 import config as C, sample
c = C.load(); before = sample.rules_version(c)
c["aggregation"]["account_rules"]["min_likes"] = 5
c["aggregation"]["account_rules"]["_trial_note"] = "offline like-floor trial 2026-11-27; never used in production"
json.dump(c, open("/t/cand.json", "w"), ensure_ascii=False, indent=1)
print("rules_version in force", before, "| candidate", sample.rules_version(C.load("/t/cand.json")))
PY
cp /t/c.sqlite3 /t/c_before.sqlite3
python -m scripts.x_stance_v1.cli --db /t/c.sqlite3 --config /t/cand.json build > /t/build1.json 2> /t/build1.err; echo "build1_exit=$?"; tail -c 600 /t/build1.err
python -m scripts.x_stance_v1.cli --db /t/c.sqlite3 --config /t/cand.json export-pending /t/work.jsonl > /t/export.json 2> /t/export.err; echo "export_exit=$?"; tail -c 600 /t/export.err; head -c 800 /t/export.json; echo
python - <<PY
import json, collections
m = json.load(open("/t/work.jsonl.manifest.json")); print({k: m[k] for k in m if k in ("items","by_queue","inference_identity","created_at_utc")})
c = collections.Counter()
for l in open("/t/work.jsonl", encoding="utf-8"):
    o = json.loads(l); c[(o.get("queue"), o.get("topic_id"), o.get("target_id"))] += 1
print(sorted(c.items(), key=str))
PY
'
echo "docker_exit=$?"; ls -la $T | awk '{print $1,$3,$5,$9}'; date -u +end_utc=%FT%TZ
