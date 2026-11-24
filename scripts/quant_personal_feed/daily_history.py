"""Read a year of regular-session moomoo daily closes from a local snapshot."""
import math
import os
import re
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


def read_daily_history(repo_root: Path, symbol: str, *, as_of=None) -> dict:
    if not re.fullmatch(r"[A-Z0-9.]{1,16}", symbol) or not symbol.strip("."):
        raise ValueError("Invalid US symbol")
    as_of = as_of or datetime.now(ZoneInfo("America/New_York")).date()
    try:
        start = as_of.replace(year=as_of.year - 1)
    except ValueError:
        start = as_of.replace(year=as_of.year - 1, day=28)
    configured = os.environ.get("MOOMOO_DISPLAY_HISTORY_DB")
    db_path = (Path(configured) if configured else repo_root /
               "apps/quant_portal/var/research/datasets/phase5_bundle/c5b5ec22/market_research.sqlite")
    result = {"symbol": symbol, "source": "moomoo", "interval": "1d",
              "price_type": "unadjusted_close", "from_date": start.isoformat(),
              "through_date": as_of.isoformat(), "bars": [], "state": "MISSING_SNAPSHOT"}
    if not db_path.is_file():
        return result
    with closing(sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True, timeout=2)) as db:
        rows = db.execute(
            """SELECT o.local_date, o.close FROM series s JOIN observations o USING(series_id)
               WHERE s.symbol = ? AND s.frequency = 'DAY' AND s.autype = 0
                 AND o.local_date BETWEEN ? AND ? AND o.research_eligible = 1
               ORDER BY o.local_date""",
            ("US." + symbol, result["from_date"], result["through_date"]),
        )
        result["bars"] = [{"date": date, "close": close}
                          for date, close in rows if close is not None and math.isfinite(close) and close > 0]
    result["state"] = "READY" if result["bars"] else "NO_DAILY_BARS"
    return result
