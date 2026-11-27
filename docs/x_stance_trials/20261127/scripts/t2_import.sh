#!/bin/bash
# Like-floor trial, step 2 (offline, on the COPY): import the local-GPU results into c.sqlite3 only, rebuild the copy
# with the trial config, then compare with c_before. Production var is mounted READ-ONLY (as in step 1, so the private watch list attaches the same way).
set -u
T=/srv/quant/x_offload/trial_like5_20261127; IMG=quant-personal-app:64d3154; VOL=quant-personal_personal_var
exec > >(tee $T/t2.log) 2>&1
date -u +start_utc=%FT%TZ
docker run --rm --network none --memory 1500m --cpus 2 --user 10001:10001 -v $VOL:/app/apps/quant_portal/var:ro -v $T:/t -w /app --entrypoint sh $IMG -c '
python -m scripts.x_stance_v1.cli --db /t/c.sqlite3 --config /t/cand.json import-results /t/results.jsonl > /t/import.json 2> /t/import.err; echo "import_exit=$?"; head -c 1200 /t/import.json; echo; tail -c 400 /t/import.err
python -m scripts.x_stance_v1.cli --db /t/c.sqlite3 --config /t/cand.json build > /t/build2.json 2> /t/build2.err; echo "build2_exit=$?"; tail -c 400 /t/build2.err
python /t/t2_compare.py > /t/compare.json; echo "compare_exit=$? bytes=$(wc -c < /t/compare.json)"'
date -u +end_utc=%FT%TZ
