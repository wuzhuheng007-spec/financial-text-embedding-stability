# Dry run of the page pilot on a copy of the production store (no network, no write to the volume).
import json, shutil, sqlite3
from datetime import datetime, timezone, timedelta
from scripts.x_stance_v1 import config as C, incremental, page_pilot, store, vocab
from scripts.x_stance_v1.ledger import Ledger
shutil.copy('/v/x_stance/x_stance.sqlite3', '/tmp/c.sqlite3')
db = store.connect('/tmp/c.sqlite3')
now = datetime.now(timezone.utc)
cfg = C.load()
print("shipped state", page_pilot.active(cfg, now))
from scripts.x_stance_v1 import cli
ap = cfg["incremental"]["page_pilot"]
ap.update(approved="DRY RUN ONLY", active_from_utc=store.iso(now - timedelta(hours=1)), active_until_utc=store.iso(now + timedelta(days=3)))
led = Ledger(db, cfg)
us = incremental.units(db, cfg, now)
kw = sum(1 for u in us if u["job_type"] == "recent"); wl = sum(1 for u in us if u["job_type"] == "watchlist")
print("regular queries this round: keyword", kw, "watched", wl)
h = page_pilot.headroom(led, cfg, now, keyword_queries=kw, watched_queries=wl)
print(json.dumps(h, ensure_ascii=False))
# how many pilot pages would fit right now, and at each later round of today if regular use stayed as now
room = led.pool["recent"][1] - h["used"]["recent_pool"]["credits"] - h["kept_for_regular"]["keyword"]["credits"] - h["kept_for_regular"]["watched_authors"]["credits"]
print("pilot pages that fit now (guard only):", max(0, room // led.check_credits))
# windows of the last 4 Beijing days the pilot would have continued
rows = db.execute("""SELECT date(datetime(r.started_at_utc,'+8 hours')), r.lang, COUNT(*) FROM x_query_runs r JOIN x_queries q ON q.query_hash=r.query_hash
   WHERE q.vocab_version LIKE 'incremental:%' AND r.topic_id='qqq' AND r.status='PLANNED_SAMPLE' AND r.cursor<>'' AND r.started_at_utc>='2026-11-24T16:00:00Z' GROUP BY 1,2""").fetchall()
print("qqq windows with a cursor left, by Beijing day:", [tuple(r) for r in rows])
p = incremental.preview(db, cfg, now, led)
print("preview requests_sent", p["requests_sent"], "pilot state", p["page_pilot"]["state"], "ok", p["page_pilot"]["headroom_now"]["ok"])
print("ledger rows before/after on the copy:", sqlite3.connect('/tmp/c.sqlite3').execute("SELECT count(*) FROM x_cost_ledger WHERE job_type='pilot_pages'").fetchone()[0])
