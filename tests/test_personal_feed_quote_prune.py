from pathlib import Path
from tempfile import TemporaryDirectory

from scripts.quant_personal_feed.store import PersonalFeedStore


def test_duplicate_quote_does_not_change_history_and_new_source_time_is_kept():
    with TemporaryDirectory() as folder:
        store = PersonalFeedStore(Path(folder) / "feed.sqlite3", owner_id=1)
        try:
            def quote(source_time: str, received_at: str) -> dict:
                return {
                    "instrument_id": "US.TQQQ",
                    "provider_time": source_time,
                    "received_at": received_at,
                    "payload": {"price": "50.00"},
                    "delay_status": "UNKNOWN",
                }

            first = quote("2026-11-17T20:00:00Z", "2026-11-17T20:00:01Z")
            assert store.put_quote(1, first, symbol="TQQQ", display_name="TQQQ",
                                   market_state="UNKNOWN", limit=1)
            duplicate = quote(first["provider_time"], "2026-11-17T20:00:02Z")
            assert not store.put_quote(1, duplicate, symbol="TQQQ", display_name="TQQQ",
                                       market_state="UNKNOWN", limit=1)
            assert store.db.execute("SELECT COUNT(*) FROM quotes").fetchone()[0] == 1
            later = quote("2026-11-17T20:01:00Z", "2026-11-17T20:01:01Z")
            assert store.put_quote(1, later, symbol="TQQQ", display_name="TQQQ",
                                   market_state="UNKNOWN", limit=1)
            assert store.db.execute("SELECT provider_time FROM quotes").fetchone()[0] == later["provider_time"]
        finally:
            store.close()
