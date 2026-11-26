import sys, json
sys.path.insert(0, "/app")
from pathlib import Path
from scripts.quant_personal_feed import first_quote_evidence as fq
o = fq.collect("/live/personal_feeds.sqlite3", fq.utc(sys.argv[1]), fq.utc(sys.argv[2]), Path("/app"))
o["collected_at_utc"] = fq.iso(fq.datetime.now(fq.UTC))
print(json.dumps(o, ensure_ascii=False, default=str))
