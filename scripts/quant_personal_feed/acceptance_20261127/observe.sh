#!/bin/bash
# 2026-11-27 first due period after the feed release (pre-market from 09:00Z = Beijing 17:00). Read-only:
#   16:59 Beijing  baseline: health + the confirmed subscription set (the fixed denominator), keyed with a local salt
#   17:00-17:12    health of the running feed every 20 s (WAITING_FIRST_PUSH / OK / NO_FIRST_PUSH, reconnects)
#   17:11          review over [09:00Z, 09:11Z) from a read-only container on the live cache, same salt
# then a redacted receipt, committed to the feed branch and pushed. No restart, no server-side schedule, no writes.
# Started late (machine asleep/offline; the task has StartWhenAvailable): the parts that were due are recorded as
# missed and the review becomes a read-only look-back at the cache (receipt: OBSERVATION_MISSED, live health missing).
# Exit: 0 receipt pushed | 2 pushed, OBSERVATION_MISSED | 3 pushed, FEED_NO_VALID_QUOTE | 4 RECEIPT_PUSH_FAILED
#       5 redaction check failed (not pushed) | 6 no review evidence / receipt not built.  Stage and class: run_status.json
# Retry only the publish step after exit 4:  observe.sh --push-only
set -u
D=/c/Users/ROG/Documents/ChatGPT/quant/local_only/feed_first_quote_20261127
W=/c/Users/ROG/Documents/ChatGPT/quant/worktrees/feed_health_20261126
U=/srv/quant/upload/feed_35a2471
cd $D; . ./srv.sh
exec >> $D/observe.log 2>&1
echo "== start $(date -u +%FT%TZ)"
now() { date -u +%Y%m%d%H%M%S; }
until_utc() { while [ "$(now)" \< "$1" ]; do sleep 1; done; }
status() {  # stage class rc note
  python -c "import json,sys,datetime;json.dump({'stage':sys.argv[1],'class':sys.argv[2] or None,'exit':int(sys.argv[3]),'note':sys.argv[4],
'at_utc':datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')},open(sys.argv[5],'w'))" "$1" "$2" "$3" "$4" $D/run_status.json
  echo "status stage=$1 class=${2:-none} exit=$3 $4"; }
[ -s $D/match_salt.hex ] || { python -c "import secrets;print(secrets.token_hex(16))" > $D/match_salt.hex; chmod 600 $D/match_salt.hex; }
health() { ssh -n $O -o ConnectTimeout=15 $S "sudo docker exec quant-personal-feed-1 python /app/deploy/linux/entrypoint.py health feed" 2>/dev/null; }
evidence() { ssh $O -o ConnectTimeout=15 $S "V=\$(sudo docker volume inspect quant-personal_personal_var --format '{{.Mountpoint}}'); sudo docker run -i --rm --network none --read-only --tmpfs /tmp:size=50m -v \$V:/live:ro -v $U/first_quote_evidence.py:/app/scripts/quant_personal_feed/first_quote_evidence.py:ro -v $U:/r:ro --entrypoint python quant-personal-app:feed-35a2471 /r/evidence_run.py $1 $2" < $D/match_salt.hex 2>/dev/null; }
valid_json() { python -c "import json,sys;json.load(open(sys.argv[1]))" "$1" 2>/dev/null; }
pick() { python -c "
import json,sys
try:
    h=json.loads(sys.stdin.read()); m=h['providers']['moomoo']
    print(json.dumps({'t':sys.argv[1],'healthy':h.get('healthy'),'business':m.get('business'),'ok':m.get('ok'),'state':m.get('state'),'phase':m['market']['phase'],
      'grace_left_seconds':m.get('grace_left_seconds'),'down_seconds':m.get('down_seconds'),'heartbeat_age_seconds':m.get('heartbeat_age_seconds'),'grace':m.get('first_push_grace')}))
except Exception as e:
    print(json.dumps({'t':sys.argv[1],'business':'READ_ERROR','error_class':type(e).__name__}))" "$1"; }

if [ "${1:-}" = --push-only ]; then   # after RECEIPT_PUSH_FAILED: publish the receipt already built, collect nothing
  [ -s $D/RECEIPT_redacted.json ] || { status RECEIPT "" 6 "--push-only but no local receipt"; exit 6; }
  echo "push-only retry"
else
# --- baseline (only if still before 17:00; a later read is not a 16:59 baseline)
until_utc 20261127085900
if [ "$(now)" \< "20261127090000" ]; then
  T=$(date -u +%FT%TZ); H=$(health | pick $T); E=$(evidence 2026-11-27T08:00:00Z 2026-11-27T08:59:00Z)
  python -c "import json,sys
try: e=json.loads(sys.argv[3])
except ValueError: e=None
print(json.dumps({'taken_utc':sys.argv[1],'health':json.loads(sys.argv[2]),'evidence':e},ensure_ascii=False))" "$T" "$H" "${E:-}" > $D/baseline_1659.json
  echo "baseline $T $(echo "$H" | head -c 200)"
else
  echo "BASELINE_MISSED started $(date -u +%T)"
fi

# --- live health and the on-time review (only while the live window is still open)
LATE=1
if [ "$(now)" \< "20261127091200" ]; then
  until_utc 20261127090000
  [ "$(now)" \< "20261127090100" ] && LATE=0 || echo "LIVE_HEALTH_LATE started $(date -u +%T)"
  [ -e $D/health.jsonl ] && cp $D/health.jsonl $D/health.prev.$(date -u +%H%M%S).jsonl
  : > $D/health.jsonl
  reviewed=0
  while [ "$(now)" \< "20261127091200" ]; do
    T=$(date -u +%FT%TZ); health | pick $T >> $D/health.jsonl
    if [ $reviewed = 0 ] && ! [ "$(now)" \< "20261127091100" ]; then
      evidence 2026-11-27T09:00:00Z 2026-11-27T09:11:00Z > $D/review_1711.json
      valid_json $D/review_1711.json && reviewed=1; echo "review $(date -u +%T) $(wc -c < $D/review_1711.json) bytes ok=$reviewed"
    fi
    sleep 20
  done
  [ $reviewed = 1 ] || { sleep 30; evidence 2026-11-27T09:00:00Z 2026-11-27T09:11:00Z > $D/review_1711.json; valid_json $D/review_1711.json && reviewed=1; }
  [ $reviewed = 1 ] && MODE= || MODE=--backfill
else
  echo "LIVE_WINDOW_MISSED started $(date -u +%T)"; MODE=--backfill
fi
if [ -n "$MODE" ]; then   # read-only look-back at the cache over the same window
  evidence 2026-11-27T09:00:00Z 2026-11-27T09:11:00Z > $D/review_backfill.json
  valid_json $D/review_backfill.json || { status REVIEW OBSERVATION_MISSED 6 "no review evidence (ssh or container failed); rerun observe.sh to look back"; exit 6; }
  echo "review_backfill $(date -u +%T) $(wc -c < $D/review_backfill.json) bytes"
fi

# --- receipt
python $D/receipt.py $D $MODE || { status RECEIPT "" 6 "receipt not built"; exit 6; }
fi
CLS=$(python -c "import json;print(' '.join(json.load(open('$D/RECEIPT_redacted.json',encoding='utf-8'))['failure_classes']))")

# --- publish only the redacted receipt and the health samples (no symbols, keys, prices or address in either)
R=$W/docs/feed_acceptance/20261127
mkdir -p $R && cp $D/RECEIPT_redacted.json $R/ && { [ -e $D/health.jsonl ] && cp $D/health.jsonl $R/ || true; }
KEYS=$(python -c "import json,glob
ks=set()
for f in glob.glob('$D/review_*.json')+['$D/baseline_1659.json']:
    try: d=json.load(open(f)); d=d.get('evidence') or d
    except Exception: continue
    ks|={v.get('key') for v in (d.get('per_subscription') or {}).values() if v.get('key')}|set(d.get('valid_quote_first_by_unconfirmed_key') or {})
print('|'.join(sorted(ks)) or 'NO_KEYS_TO_CHECK')")
if grep -qiE "[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+|price|bearer|token\"|$(cat $D/match_salt.hex)|$KEYS" $R/*; then
  status REDACTION "$CLS" 5 "redaction check failed: not pushed"; exit 5; fi
RES=$(python -c "import json;print(json.load(open('$D/RECEIPT_redacted.json',encoding='utf-8'))['result'])")
cd $W && git add docs/feed_acceptance/20261127 && { git diff --cached --quiet || git commit -q -m "Feed first due period 2026-11-27: redacted acceptance receipt

$RES
classes: ${CLS:-none}

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"; } || { status COMMIT "RECEIPT_PUSH_FAILED $CLS" 4 "commit failed; receipt kept at $D"; exit 4; }
git push -q origin feed/health-session-20261126 || { status PUSH "RECEIPT_PUSH_FAILED $CLS" 4 "push failed; receipt kept at $D and committed locally as $(git rev-parse --short HEAD)"; exit 4; }
echo "pushed $(git rev-parse --short HEAD)"
case " $CLS " in *" FEED_NO_VALID_QUOTE "*) RC=3;; *" OBSERVATION_MISSED "*) RC=2;; *) RC=0;; esac
status DONE "$CLS" $RC "pushed $(git rev-parse --short HEAD)"
echo "== end $(date -u +%FT%TZ)"
exit $RC
