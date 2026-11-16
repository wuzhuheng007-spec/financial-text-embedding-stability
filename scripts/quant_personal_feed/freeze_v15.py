"""Freeze private v1.5 development news before changing relevance rules.

The public receipt contains hashes and counts only. No news text is copied to it.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .event_rules import RULE_VERSION, classify

ROOT = Path(__file__).resolve().parents[2]


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def backup(source: Path, target: Path) -> str:
    if target.exists():
        raise FileExistsError(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True) as live:
        with sqlite3.connect(target) as frozen:
            live.backup(frozen)
            if frozen.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("Frozen SQLite integrity check failed")
    return digest(target)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--private-dir", type=Path, required=True)
    parser.add_argument("--public-receipt", type=Path, required=True)
    args = parser.parse_args()
    private = args.private_dir.resolve()
    if private.exists():
        raise FileExistsError(private)
    private.mkdir(parents=True)
    receipt = args.public_receipt.resolve()
    if receipt.exists():
        raise FileExistsError(receipt)
    feed_config = ROOT / "configs/quant_portal_personal_feeds_v11_20261116.json"
    settings = json.loads(feed_config.read_text(encoding="utf-8"))
    portal_path = ROOT / settings["portal_db"]
    cache_path = ROOT / settings["cache_db"]
    portal_snapshot = private / "portal_before_v15.sqlite3"
    cache_snapshot = private / "feed_before_v15.sqlite3"
    portal_hash = backup(portal_path, portal_snapshot)
    cache_hash = backup(cache_path, cache_snapshot)
    with sqlite3.connect(portal_snapshot) as db:
        row = db.execute("SELECT id FROM auth_user WHERE username=?", (settings["owner_username"],)).fetchone()
        if row is None:
            raise RuntimeError("Owner account missing from portal snapshot")
        owner_id = row[0]
        portal_counts = {table: db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in
                         ("auth_user", "core_watchlistitem", "core_userpreferences", "core_publicfactcard")}
    with sqlite3.connect(cache_snapshot) as db:
        db.row_factory = sqlite3.Row
        binding = db.execute("SELECT owner_id FROM owner_binding WHERE singleton=1").fetchone()
        if binding is None or binding[0] != owner_id:
            raise RuntimeError("Feed snapshot owner binding mismatch")
        rows = db.execute("""SELECT i.item_key,i.first_received_at,i.latest_received_at,
          v.version,v.title,v.content,v.published_at_utc,v.received_at,v.content_sha256
          FROM news_items i JOIN news_versions v ON v.item_key=i.item_key AND v.version=i.latest_version
          WHERE i.owner_id=? ORDER BY i.latest_received_at DESC,i.item_key LIMIT 500""", (owner_id,)).fetchall()
        total_news = db.execute("SELECT COUNT(*) FROM news_items WHERE owner_id=?", (owner_id,)).fetchone()[0]
    related, unrelated = [], []
    for row in rows:
        item = dict(row)
        labels = classify(item)
        record = {"item_key": item["item_key"], "version": item["version"],
                  "content_sha256": item["content_sha256"],
                  "first_received_at": item["first_received_at"],
                  "published_at_utc": item["published_at_utc"],
                  "old_rule_version": RULE_VERSION,
                  "old_entities": [x["id"] for x in labels["entities"]],
                  "old_categories": [x["id"] for x in labels["categories"]]}
        if labels["entities"] or any(x["id"] != "OTHER" for x in labels["categories"]):
            if len(related) < 20:
                related.append(record)
        elif len(unrelated) < 5:
            unrelated.append(record)
        if len(related) >= 20 and len(unrelated) >= 5:
            break
    manifest = {"schema": "personal_demo_v15_private_freeze", "selection":
                "latest_received_desc_item_key; first 20 old-rule related and first 5 old-rule unrelated",
                "related": related, "unrelated_counterexamples": unrelated}
    private_manifest = private / "sample_manifest_private.json"
    private_manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    now = datetime.now(timezone.utc)
    public = {"schema": "personal_demo_v15_freeze_receipt", "frozen_at_utc": now.isoformat().replace("+00:00", "Z"),
              "portal_snapshot_sha256": portal_hash, "feed_snapshot_sha256": cache_hash,
              "private_manifest_sha256": digest(private_manifest), "feed_config_sha256": digest(feed_config),
              "freeze_code_sha256": digest(Path(__file__)),
              "old_classifier_sha256": digest(ROOT / "scripts/quant_personal_feed/event_rules.py"),
              "portal_counts": portal_counts, "cached_news_total": total_news,
              "latest_news_scanned": len(rows), "related_frozen": len(related),
              "unrelated_counterexamples_frozen": len(unrelated),
              "private_files_not_for_publication": [p.name for p in (portal_snapshot, cache_snapshot, private_manifest)]}
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.write_text(json.dumps(public, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: public[k] for k in ("frozen_at_utc", "portal_counts", "cached_news_total",
                                             "related_frozen", "unrelated_counterexamples_frozen")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
