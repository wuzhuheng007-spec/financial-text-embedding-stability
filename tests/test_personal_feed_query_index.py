"""The hot last-written lookup must not sort quote history under a write lock."""
import tempfile
from pathlib import Path

from scripts.quant_personal_feed.store import PersonalFeedStore


def test_last_written_session_lookup_uses_ordered_index():
    with tempfile.TemporaryDirectory() as directory:
        store = PersonalFeedStore(Path(directory) / "feed.sqlite3", 1)
        try:
            plan = store.db.execute("""EXPLAIN QUERY PLAN SELECT price_text,source_at,
              parent_date_hint,source_time_field_path,date_basis,raw_sha256
              FROM session_quotes WHERE owner_id=? AND symbol=? AND session=?
              AND source_channel=? ORDER BY id DESC LIMIT 1""",
              (1, "NVDA", "OVERNIGHT", "WS_QUOTE")).fetchall()
            details = " ".join(row[3] for row in plan)
            assert "session_quotes_last_written" in details
            assert "TEMP B-TREE" not in details
        finally:
            store.close()
