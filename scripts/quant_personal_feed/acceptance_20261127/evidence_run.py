"""Runs first_quote_evidence.collect on the live cache mounted read-only at /live. The match-key salt is read from stdin
(first line, hex), so it is never on a command line or in a file on the server; without it the output is unkeyed."""
import json
import sys
sys.path.insert(0, "/app")
from pathlib import Path
from scripts.quant_personal_feed import first_quote_evidence as fq

line = sys.stdin.readline().strip() if not sys.stdin.isatty() else ""
salt = bytes.fromhex(line) if line else None
o = fq.collect("/live/personal_feeds.sqlite3", fq.utc(sys.argv[1]), fq.utc(sys.argv[2]), Path("/app"), key_salt=salt)
o["collected_at_utc"] = fq.iso(fq.datetime.now(fq.UTC))
print(json.dumps(o, ensure_ascii=False, default=str))
