#!/bin/bash
# 2026-11-27 first due period after the feed release (pre-market from 09:00Z = Beijing 17:00). Read-only:
#   16:59 Beijing  baseline (health of the running feed + evidence over the hour before)
#   17:00-17:12    health of the running feed every 20 s (WAITING_FIRST_PUSH / OK / NO_FIRST_PUSH, reconnects)
#   17:11          review: evidence over [09:00Z, 09:11Z) from a read-only copy-free container on the live cache
# then a redacted receipt, committed to the feed branch and pushed. No restart, no server-side schedule, no writes.
set -u
D=/c/Users/ROG/Documents/ChatGPT/quant/local_only/feed_first_quote_20261127
W=/c/Users/ROG/Documents/ChatGPT/quant/worktrees/feed_health_20261126
U=/srv/quant/upload/feed_35a2471
cd $D; . ./srv.sh
exec >> $D/observe.log 2>&1
echo "== start $(date -u +%FT%TZ)"
until_utc() { while [ "$(date -u +%Y%m%d%H%M%S)" \< "$1" ]; do sleep 1; done; }
health() { ssh $O $S "sudo docker exec quant-personal-feed-1 python /app/deploy/linux/entrypoint.py health feed" 2>/dev/null; }
evidence() { ssh $O $S "V=\$(sudo docker volume inspect quant-personal_personal_var --format '{{.Mountpoint}}'); sudo docker run --rm --network none --read-only --tmpfs /tmp:size=50m -v \$V:/live:ro -v $U/first_quote_evidence.py:/app/scripts/quant_personal_feed/first_quote_evidence.py:ro -v $U:/r:ro --entrypoint python quant-personal-app:feed-35a2471 /r/evidence_run.py $1 $2" 2>/dev/null; }
pick() { python -c "
import json,sys
try:
    h=json.loads(sys.stdin.read()); m=h['providers']['moomoo']
    print(json.dumps({'t':sys.argv[1],'healthy':h.get('healthy'),'business':m.get('business'),'ok':m.get('ok'),'state':m.get('state'),'phase':m['market']['phase'],
      'grace_left_seconds':m.get('grace_left_seconds'),'down_seconds':m.get('down_seconds'),'heartbeat_age_seconds':m.get('heartbeat_age_seconds'),'grace':m.get('first_push_grace')}))
except Exception as e:
    print(json.dumps({'t':sys.argv[1],'business':'READ_ERROR','error_class':type(e).__name__}))" "$1"; }

until_utc 20261127085900
T=$(date -u +%FT%TZ); H=$(health | pick $T); E=$(evidence 2026-11-27T08:00:00Z 2026-11-27T08:59:00Z)
python -c "import json,sys; print(json.dumps({'taken_utc':sys.argv[1],'health':json.loads(sys.argv[2]),'evidence':json.loads(sys.argv[3])},ensure_ascii=False))" "$T" "$H" "${E:-{\}}" > $D/baseline_1659.json
echo "baseline $T $(echo "$H" | head -c 200)"

until_utc 20261127090000
: > $D/health.jsonl
reviewed=0
while [ "$(date -u +%Y%m%d%H%M%S)" \< "20261127091200" ]; do
  T=$(date -u +%FT%TZ); health | pick $T >> $D/health.jsonl
  if [ $reviewed = 0 ] && ! [ "$(date -u +%Y%m%d%H%M%S)" \< "20261127091100" ]; then
    evidence 2026-11-27T09:00:00Z 2026-11-27T09:11:00Z > $D/review_1711.json; reviewed=1; echo "review $(date -u +%T) $(wc -c < $D/review_1711.json) bytes"
  fi
  sleep 20
done
[ $reviewed = 1 ] || evidence 2026-11-27T09:00:00Z 2026-11-27T09:11:00Z > $D/review_1711.json
python $D/receipt.py $D || { echo RECEIPT_FAILED; exit 1; }

# publish only the redacted receipt and the health samples (no symbols, prices or address in either)
R=$W/docs/feed_acceptance/20261127
mkdir -p $R && cp $D/RECEIPT_redacted.json $D/health.jsonl $R/
if grep -qiE "[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+|price|bearer|token\"" $R/*; then echo "REDACTION_CHECK_FAILED: not pushed"; exit 1; fi
cd $W && git add docs/feed_acceptance/20261127 && git commit -q -m "Feed first due period 2026-11-27: redacted acceptance receipt

$(python -c "import json;r=json.load(open('$D/RECEIPT_redacted.json',encoding='utf-8'));print(r['result'])")

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>" && git push -q origin feed/health-session-20261126 && echo "pushed $(git rev-parse --short HEAD)"
echo "== end $(date -u +%FT%TZ)"
