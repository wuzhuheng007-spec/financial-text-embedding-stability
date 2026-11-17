"""Isolated container acceptance helper. TEST VOLUMES ONLY.

Every row written here is marked SYNTHETIC. It is mounted into containers of a
separate Compose project for acceptance and is not part of the image.

  create-users          create the test owner and a second test account
  seed-feed             write marked synthetic news/quotes; check dedup/versioning
  inspect               counts only (no text) from feed, queue and score stores
  sqlite-backup SRC DST online backup + integrity/quick check + table counts
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path("/app")
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
CONFIG = ROOT / os.environ.get("QUANT_FEED_CONFIG", "configs/quant_portal_personal_feeds_linux_test_v1.json")
MARK = "【合成测试 SYNTHETIC】"


def _django():
    sys.path.insert(0, str(ROOT / "apps/quant_portal"))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "portal.settings")
    import django
    django.setup()


def create_users() -> dict:
    _django()
    from django.contrib.auth import get_user_model
    model = get_user_model()
    out = {}
    for name, env in (("owner@local.invalid", "TEST_OWNER_PASSWORD"), ("other@local.invalid", "TEST_OTHER_PASSWORD")):
        user, created = model.objects.get_or_create(username=name, defaults={"email": name})
        if created:
            user.set_password(os.environ[env])
            user.save()
        out[name] = {"id": user.pk, "created": created}
    return out


def seed_feed() -> dict:
    from scripts.quant_personal_feed.owner import resolve_owner_id
    from scripts.quant_personal_feed.store import PersonalFeedStore
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    owner = resolve_owner_id(config)
    store = PersonalFeedStore(ROOT / config["cache_db"], owner)
    now = datetime.now(timezone.utc)
    limits = dict(max_items=config["retention"]["news_items"], max_versions=config["retention"]["news_versions_per_item"])
    items = [
        {"id": "synthetic-001", "title": MARK + "英伟达公司订单增长", "content": MARK + "英伟达公司宣布季度订单增长，利润改善。", "time": now.strftime("%Y-%m-%d %H:%M:%S")},
        {"id": "synthetic-002", "title": MARK + "美光公司亏损扩大", "content": MARK + "美光公司表示亏损扩大，需求减少。", "time": now.strftime("%Y-%m-%d %H:%M:%S")},
        {"id": "synthetic-003", "title": MARK + "宏观数据", "content": MARK + "某合成宏观指标与预期一致。", "time": now.strftime("%Y-%m-%d %H:%M:%S")},
    ]
    results = {}
    for offset, item in enumerate(items):
        stamp = (now + timedelta(milliseconds=offset)).isoformat().replace("+00:00", "Z")
        results[item["id"]] = store.put_news(owner, item, received_at=stamp, **limits)
    store.db.commit()
    duplicate = store.put_news(owner, items[0], received_at=now.isoformat().replace("+00:00", "Z"), **limits)
    changed = dict(items[1], content=items[1]["content"] + "（合成修订版）")
    revision = store.put_news(owner, changed, received_at=(now + timedelta(seconds=1)).isoformat().replace("+00:00", "Z"), **limits)
    quote = {"instrument_id": "SYNTHETIC.TEST", "provider_time": now.isoformat(), "received_at": now.isoformat(),
             "payload": {"price": "100.00"}}
    first_quote = store.put_quote(owner, quote, symbol="SYNTH", display_name="SYNTHETIC TEST", market_state="SYNTHETIC", limit=10)
    repeat_quote = store.put_quote(owner, dict(quote, received_at=(now + timedelta(seconds=1)).isoformat()),
                                   symbol="SYNTH", display_name="SYNTHETIC TEST", market_state="SYNTHETIC", limit=10)
    store.db.commit()
    counts = {t: store.db.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in ("news_items", "news_versions", "quotes")}
    binding = store.db.execute("SELECT owner_id FROM owner_binding").fetchone()[0]
    store.close()
    return {"owner_id": owner, "owner_binding": binding, "first_insert": results, "duplicate": duplicate,
            "changed_content": revision, "quote_first": first_quote, "quote_repeat_same_price": repeat_quote,
            "counts": counts, "kind": "SYNTHETIC_TEST_VOLUME"}


def _count(db: Path, sql: str):
    if not db.is_file():
        return None
    with closing(sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True, timeout=5)) as con:
        return con.execute(sql).fetchall()


def inspect() -> dict:
    feed = ROOT / "apps/quant_portal/var/personal_feeds.sqlite3"
    queue = ROOT / "artifacts/news_sentiment_factor_v42_20261117/queue.sqlite3"
    score = ROOT / "artifacts/news_sentiment_factor_v41_20261117/chinese_tone.sqlite3"
    return {
        "feed_news_versions": _count(feed, "SELECT count(*) FROM news_versions"),
        "feed_journal_mode": _count(feed, "PRAGMA journal_mode"),
        "feed_indexes": _count(feed, "SELECT count(*) FROM sqlite_master WHERE type='index'"),
        "queue_jobs_by_state": _count(queue, "SELECT state,count(*) FROM jobs GROUP BY state"),
        "queue_worker_runs": _count(queue, "SELECT count(*) FROM worker_runs"),
        "score_rows_by_status": _count(score, "SELECT status,model_revision,count(*) FROM scores GROUP BY status,model_revision"),
        "score_articles_by_cohort": _count(score, "SELECT cohort,count(*) FROM articles GROUP BY cohort"),
        "daily_snapshots": _count(queue, "SELECT count(*) FROM sqlite_master WHERE name='daily_snapshots'"),
    }


def sqlite_backup(src: str, dst: str) -> dict:
    with closing(sqlite3.connect(src, timeout=30)) as s, closing(sqlite3.connect(dst)) as d:
        s.backup(d)
    with closing(sqlite3.connect(f"file:{dst}?mode=ro", uri=True)) as c:
        tables = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        return {"quick_check": c.execute("PRAGMA quick_check").fetchone()[0],
                "integrity_check": c.execute("PRAGMA integrity_check").fetchone()[0],
                "sha256": hashlib.sha256(Path(dst).read_bytes()).hexdigest(),
                "table_counts": {t: c.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0] for t in tables}}


if __name__ == "__main__":
    command = sys.argv[1]
    if command == "create-users":
        result = create_users()
    elif command == "seed-feed":
        result = seed_feed()
    elif command == "inspect":
        result = inspect()
    elif command == "sqlite-backup":
        result = sqlite_backup(sys.argv[2], sys.argv[3])
    else:
        raise SystemExit("unknown command")
    print(json.dumps(result, ensure_ascii=False, default=str))
