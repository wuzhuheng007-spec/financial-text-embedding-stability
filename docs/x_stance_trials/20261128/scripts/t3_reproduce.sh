#!/bin/bash
# Like-floor trial, step 3: reproduce the comparison on an INDEPENDENT copy. Nothing from the first run's working
# copy (c.sqlite3) is used. Inputs are only the frozen snapshot c_before.sqlite3, the candidate config cand.json and
# the GPU results results.jsonl, each checked against its recorded sha256 first. Same image, no network, production
# var mounted read-only (so the private watch list attaches as in steps 1-2). The first run's files are mounted
# read-only and never written. Output: repro_<utc>/compare.json and a field-by-field diff against the first run.
set -u
T=/srv/quant/x_offload/trial_like5_20261127; IMG=quant-personal-app:64d3154; VOL=quant-personal_personal_var
R=$T/repro_$(date -u +%Y%m%dT%H%M%SZ)
[ "$(docker image inspect $IMG --format '{{.Id}}')" = "sha256:68a978fbf9e5dafd5ff5aee639798ff8b50b568ad4cfc7fa8cbac2afe94d2efc" ] || { echo "ABORT: image id differs"; exit 1; }
( cd $T && sha256sum -c --quiet - ) <<'SUMS' || { echo "ABORT: an input changed"; exit 1; }
331008d7f25d487d883e288177604f11d857ff2883fcb2a4803d943c07419ee8  c_before.sqlite3
2894b70a50a82c4d91a0282cf556acd7d99ea51cf885eb44938bb35b447c7074  cand.json
f5c696c36e0fdb76af5c7ef578c23263df88461259af748afd136b9e5002e3c0  results.jsonl
256c90293f8a263954f9f7ce084a20da8c6087cfb6be6196456d0848d337d959  t2_compare.py
e2545d2115aa1245f08b9398ff41d083e0df666c77ff6766638ef3f262a40686  compare.json
SUMS
install -d -m 700 -o 10001 -g 10001 $R
cp $T/results.jsonl $T/results.jsonl.manifest.json $T/cand.json $R/ && chown 10001:10001 $R/* && chmod 600 $R/*
exec > >(tee $R/t3.log) 2>&1
date -u +start_utc=%FT%TZ; echo "repro dir $(basename $R)"
docker run --rm --network none --memory 1500m --cpus 2 --user 10001:10001 -v $VOL:/app/apps/quant_portal/var:ro -v $T:/orig:ro -v $R:/t -w /app --entrypoint sh $IMG -c '
set -e
python - <<PY
import sqlite3
for name in ("c_before.sqlite3", "c.sqlite3"):                  # two fresh copies of the frozen snapshot
    s = sqlite3.connect("file:/orig/c_before.sqlite3?mode=ro", uri=True); d = sqlite3.connect("/t/" + name); s.backup(d); d.close(); s.close()
PY
python -m scripts.x_stance_v1.cli --db /t/c.sqlite3 --config /t/cand.json build > /t/build1.json 2> /t/build1.err; echo "build1_exit=$?"
python -m scripts.x_stance_v1.cli --db /t/c.sqlite3 --config /t/cand.json import-results /t/results.jsonl > /t/import.json 2> /t/import.err; echo "import_exit=$?"
python -c "import json;d=json.load(open(\"/t/import.json\"));print({k:d.get(k) for k in (\"status\",\"lines\",\"labels_written\",\"already_present\",\"rejected\")})"
python -m scripts.x_stance_v1.cli --db /t/c.sqlite3 --config /t/cand.json build > /t/build2.json 2> /t/build2.err; echo "build2_exit=$?"
python /orig/t2_compare.py > /t/compare.json; echo "compare_exit=$?"
python - <<PY
import json
a, b = json.load(open("/orig/compare.json")), json.load(open("/t/compare.json"))
diffs = []
def walk(x, y, path):
    if isinstance(x, dict) and isinstance(y, dict):
        for k in sorted(set(x) | set(y)): walk(x.get(k), y.get(k), path + "." + str(k))
    elif isinstance(x, list) and isinstance(y, list) and len(x) == len(y):
        for i, (p, q) in enumerate(zip(x, y)): walk(p, q, path + "[" + str(i) + "]")
    elif x != y:
        diffs.append({"path": path, "first": x, "repro": y})
walk(a, b, "")
canon = lambda o: json.dumps(o, sort_keys=True, ensure_ascii=False)
print(json.dumps({"identical": canon(a) == canon(b), "differences": len(diffs), "first_differences": diffs[:20]}, ensure_ascii=False))
json.dump({"identical": canon(a) == canon(b), "differences": diffs}, open("/t/diff.json", "w"), ensure_ascii=False, indent=1)
PY
'
echo "docker_exit=$?"
rm -f $R/c.sqlite3 $R/c_before.sqlite3                          # the copies are not kept; the snapshot they came from is
sha256sum $R/compare.json $R/diff.json | sed "s#$R/##"; date -u +end_utc=%FT%TZ
