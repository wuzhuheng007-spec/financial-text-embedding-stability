"""Back up owner stores and freeze the exact pre-score Chinese source inputs."""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
CFG = ROOT / "configs/news_sentiment_factor_v41_20261117.json"
PRIVATE = ROOT / "artifacts/news_sentiment_factor_v41_20261117"
PUBLIC = ROOT / "results/news_sentiment_factor_v41_20261117"
MODEL = PRIVATE / "model"
V4_DB = ROOT / "artifacts/news_sentiment_factor_v4_20261117/news_research.sqlite3"
FEED_DB = ROOT / "apps/quant_portal/var/personal_feeds.sqlite3"
DBS = {
    "portal": ROOT / "apps/quant_portal/var/dev.sqlite3",
    "personal_feed": FEED_DB,
    "personal_news": ROOT / "apps/quant_portal/var/personal_news_v18.sqlite3",
    "personal_analysis": ROOT / "apps/quant_portal/var/personal_analysis_v16.sqlite3",
    "english_v4_research": V4_DB,
}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def write(path: Path, obj: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((json.dumps(obj, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip())


def has_chinese(value: str) -> bool:
    return bool(re.search(r"[\u3400-\u9fff]", value))


def read_v4(cfg: dict) -> list[dict]:
    if not V4_DB.is_file():
        return []
    with sqlite3.connect(f"file:{V4_DB.as_posix()}?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        rows = db.execute("""SELECT provider,article_id,version,title,abstract,body,
            publisher,source_url,published_at_utc,first_received_at_utc,
            response_received_at_utc,text_sha256,raw_response_sha256,observation_mode
            FROM articles ORDER BY provider,article_id,version""").fetchall()
    selected = []
    for r in rows:
        source = (r["title"] or "") + "\n" + (r["abstract"] or "") + "\n" + (r["body"] or "")
        if not has_chinese(source):
            continue
        received = r["response_received_at_utc"] or r["first_received_at_utc"]
        selected.append({
            "provider": "moomoo_webapi", "article_id": r["article_id"], "version": r["version"],
            "title": r["title"] or "", "body": r["body"] or r["abstract"] or "",
            "body_source": "BODY" if r["body"] else "ABSTRACT" if r["abstract"] else "NONE",
            "publisher": r["publisher"], "source_url": r["source_url"],
            "published_at_utc": r["published_at_utc"], "first_received_at_utc": received,
            "received_at_utc": received, "received_basis": "V4_RESPONSE_READ_TIME" if r["response_received_at_utc"] else "V4_INGEST_FALLBACK",
            "source_text_hash": digest(source.encode("utf-8")),
            "raw_response_hash": r["raw_response_sha256"], "acquisition_batch": r["raw_response_sha256"],
            "cohort": "CACHE_REPLAY", "source_version_status": "CURRENT_VERSION_ONLY",
            "prior_v4_observation_mode": r["observation_mode"],
        })
    return selected


def read_feed(cfg: dict, existing: list[dict]) -> tuple[list[dict], list[dict]]:
    cutoff = cfg["freeze_cutoff_utc"]
    seen = {digest(normalize_text(x["title"] + "\n" + x["body"]).encode("utf-8")) for x in existing}
    selected, excluded = [], []
    with sqlite3.connect(f"file:{FEED_DB.as_posix()}?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        binding = db.execute("SELECT owner_id FROM owner_binding WHERE singleton=1").fetchone()
        if binding is None:
            raise ValueError("NO_OWNER_BINDING")
        rows = db.execute("""SELECT v.item_key,v.version,v.provider_id,v.title,v.content,v.source_url,
            v.published_raw,v.published_at_utc,v.received_at,v.content_sha256,v.raw_item_json,
            i.first_received_at FROM news_versions v JOIN news_items i ON i.item_key=v.item_key
            WHERE v.owner_id=? AND v.received_at<=? ORDER BY v.received_at,v.item_key,v.version""",
            (binding["owner_id"], cutoff)).fetchall()
    limit = cfg["sample_selection"]["jin10_max_new_versions"]
    for r in rows:
        if len(selected) >= limit:
            break
        text = (r["title"] or "") + "\n" + r["content"]
        identity = {"provider": "jin10", "article_id": r["item_key"], "version": r["version"]}
        if not has_chinese(text):
            excluded.append({**identity, "reason": "NO_CHINESE_SCRIPT"})
            continue
        cross = digest(normalize_text(text).encode("utf-8"))
        if cross in seen:
            excluded.append({**identity, "reason": "EXACT_NORMALIZED_TEXT_DUPLICATE"})
            continue
        seen.add(cross)
        selected.append({
            **identity, "title": r["title"] or "", "body": r["content"],
            "body_source": "BODY", "publisher": None, "source_url": r["source_url"],
            "published_at_utc": r["published_at_utc"],
            "published_raw": r["published_raw"],
            "first_received_at_utc": r["first_received_at"], "received_at_utc": r["received_at"],
            "received_basis": "PERSONAL_FEED_NEWS_VERSION_RECEIVED_AT",
            "source_text_hash": digest(text.encode("utf-8")),
            "raw_response_hash": digest(r["raw_item_json"].encode("utf-8")),
            "acquisition_batch": "BATCH_ID_NOT_STORED_IN_FEED_CACHE",
            "cohort": "CACHE_REPLAY", "source_version_status": "PERSONAL_CACHE_VERSION",
            "provider_id": r["provider_id"], "content_sha256_in_cache": r["content_sha256"],
        })
    return selected, excluded


def backup() -> list[dict]:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    directory = PRIVATE / "backups" / stamp
    directory.mkdir(parents=True, exist_ok=False)
    receipt = []
    for role, path in DBS.items():
        if not path.is_file():
            receipt.append({"role": role, "status": "ABSENT"})
            continue
        target = directory / f"{role}.sqlite3"
        with sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True) as src:
            with sqlite3.connect(target) as dst:
                src.backup(dst)
        with sqlite3.connect(f"file:{target.as_posix()}?mode=ro", uri=True) as check:
            integrity = check.execute("PRAGMA quick_check").fetchone()[0]
        receipt.append({"role": role, "status": "BACKED_UP", "bytes": target.stat().st_size,
                        "sha256": sha(target), "quick_check": integrity, "private_path": str(target)})
    return receipt


def freeze() -> dict:
    if (PRIVATE / "input_manifest_private.json").exists():
        raise FileExistsError("V41_INPUT_ALREADY_FROZEN")
    cfg = json.loads(CFG.read_text(encoding="utf-8"))
    if cfg["model"]["revision"] != "e91b1a3af10e1e8c9c03429d3cd7d5e9a1c8000d":
        raise ValueError("MODEL_REVISION_DRIFT")
    if cfg["freeze_cutoff_utc"] > now().replace("+00:00", "Z"):
        raise ValueError("FUTURE_FREEZE_CUTOFF")
    backups = backup()
    v4 = read_v4(cfg)
    feed, excluded = read_feed(cfg, v4)
    rows = v4 + feed
    files = {}
    for name in cfg["model"]["files"]:
        path = MODEL / name
        if not path.is_file():
            raise FileNotFoundError(path)
        files[name] = {"sha256": sha(path), "bytes": path.stat().st_size}
    actual_labels = json.loads((MODEL / "config.json").read_text(encoding="utf-8"))["id2label"]
    if actual_labels != cfg["model"]["id2label"]:
        raise ValueError("MODEL_CARD_CONFIG_LABEL_CONFLICT")
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    identity = [{"provider": r["provider"], "article_id": r["article_id"],
                 "version": r["version"], "source_text_hash": r["source_text_hash"]} for r in rows]
    private = {"schema": "news_sentiment_v41_input_manifest_private_1", "frozen_at_utc": now(),
               "frozen_at_asia_shanghai": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
               "head": head, "config_sha256": sha(CFG), "model_files": files,
               "backups": backups, "rows": rows, "excluded": excluded,
               "ordered_identity_sha256": digest(json.dumps(identity, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))}
    write(PRIVATE / "input_manifest_private.json", private)
    public = {"schema": "news_sentiment_v41_input_manifest_redacted_1",
              "frozen_at_utc": private["frozen_at_utc"], "frozen_at_asia_shanghai": private["frozen_at_asia_shanghai"],
              "head": head, "config_sha256": private["config_sha256"],
              "model_revision": cfg["model"]["revision"], "model_files": files,
              "backup_roles": [{k: b.get(k) for k in ("role", "status", "bytes", "quick_check")} for b in backups],
              "v4_replay_article_versions": len(v4), "jin10_replay_article_versions": len(feed),
              "excluded_before_cap": {key: sum(x["reason"] == key for x in excluded) for key in sorted({x["reason"] for x in excluded})},
              "ordered_identity_sha256": private["ordered_identity_sha256"],
              "private_manifest_sha256": sha(PRIVATE / "input_manifest_private.json"),
              "no_supplier_requests": True, "no_model_scores_before_freeze": True}
    write(PUBLIC / "input_manifest_redacted.json", public)
    return public


if __name__ == "__main__":
    print(json.dumps(freeze(), ensure_ascii=False))
