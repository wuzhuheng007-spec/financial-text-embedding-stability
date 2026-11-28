#!/bin/bash
# Read-only queue refresh: copies the production X store into a tmpfs inside a no-network container and reports
# active claims, oldest pending pair and the last import. Then reads the latest round receipt. Writes nothing.
IMG=$(sed -n 's/^APP_IMAGE=//p' /srv/quant/private.env)
docker run -i --rm --network none --read-only --tmpfs /tmp:size=900m --user 10001:10001 -v quant-personal_personal_var:/v:ro --entrypoint python $IMG - <<'PY'
import sqlite3, shutil, json, datetime
shutil.copy('/v/x_stance/x_stance.sqlite3', '/tmp/c.sqlite3')
db = sqlite3.connect('/tmp/c.sqlite3')
now = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
q = lambda s, *a: db.execute(s, a).fetchall()
out = {"checked_utc": now}
out["claims_active"] = q("SELECT work_sha256, COUNT(*), MIN(claimed_at_utc), MIN(expires_at_utc) FROM x_offload_claims WHERE released_at_utc IS NULL AND expires_at_utc>? GROUP BY work_sha256 ORDER BY 3", now)
out["claims_expired_unreleased"] = q("SELECT COUNT(*) FROM x_offload_claims WHERE released_at_utc IS NULL AND expires_at_utc<=?", now)[0][0]
out["claims_released_last"] = q("SELECT MAX(released_at_utc) FROM x_offload_claims")[0][0]
pend = q("""SELECT pt.topic_id, COUNT(*), MIN(p.created_at_utc), MIN(p.fetched_at_utc) FROM x_post_topics pt JOIN x_posts p USING(provider, post_id)
            WHERE pt.quality='PENDING' GROUP BY pt.topic_id""")
out["pending_pairs_any_version_by_topic"] = pend
out["last_label_written_utc"] = q("SELECT MAX(label_written_at_utc) FROM x_stance_labels")[0][0]
out["labels_last_24h"] = q("SELECT COUNT(*) FROM x_stance_labels WHERE label_written_at_utc>?", (datetime.datetime.now(datetime.timezone.utc)-datetime.timedelta(hours=24)).strftime('%Y-%m-%dT%H:%M:%SZ'))[0][0]
out["claims_active"] = [[w[:12]+"…", n, c, e] for w, n, c, e in out["claims_active"]]
print(json.dumps(out, ensure_ascii=False, indent=1))
PY
L=$(ls -d /srv/quant/x_offload/update_log/2* | tail -1); echo "latest round $(basename $L)"
python3 - "$L" <<'PY'
import json,sys,glob
d=sys.argv[1]+'/'; r=json.load(open(d+'receipt.json')); s=r['stages']
print(json.dumps({"ok": r['ok'], "collect": {k: s['collect'].get(k) for k in ('status','requests','new_posts')}, "import": s['import'], "export_items": s['export']['items'],
  "pending": r['pending'], "last_label": r['last_label_written_at_utc'], "last_success": r.get('last_successful_update_at_utc')}, ensure_ascii=False))
for f in sorted(glob.glob(d+'import_*.json')):
    j=json.load(open(f)); print(f.split('/')[-1], j['status'], j['labels_written'], j['rejected'])
PY
ls -1 /srv/quant/x_offload/inbox | wc -l | sed 's/^/inbox files /'
for m in /srv/quant/x_offload/outbox/*.manifest.json; do python3 -c "import json,sys;d=json.load(open(sys.argv[1]));print(d['file'],d['items'],(d.get('claim') or {}).get('expires_at_utc'))" $m; done | tail -14
