"""Hand pending classification work to a worker that is not on the server (the owner's local GPU) and take its
answers back.

  export_pending  -> FILE (JSON lines, one pending post/topic/target each) + FILE.manifest.json with the file's
                     SHA-256, the count and the complete inference identity the answers must be produced under
  the worker      -> RESULTS (JSON lines: the same ids + raw model output + real start/completion times)
                     + RESULTS.manifest.json (hash of the results file, the identity it actually ran with)
  import_results  -> every line is checked before anything is written: the results file matches its hash, the
                     worker's identity is exactly the active one, the post still exists with the same text, the
                     pair is a member of a live sample version, and the key recomputed here equals the worker's.
                     The raw output then goes through the same strict validator as a server inference.

Importing the same file twice writes nothing the second time. A line that fails a check is counted and skipped;
it is never repaired and never becomes a label. The work file contains post text: it is private data, travels over
the existing SSH channel only and is never committed. Standard library only.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from . import stance, store

FORMAT = "x-stance-offload-v1"


class OffloadError(ValueError):
    pass


def _line(obj: dict) -> str:
    """One JSON line. Real posts contain U+2028 / U+2029 / U+0085, which json leaves as they are and which
    str.splitlines() treats as line ends: on 2026-11-23 the first real work file could not be read back line by
    line. They are written escaped, and readers split on the newline character only."""
    s = json.dumps(obj, ensure_ascii=False, sort_keys=True)
    for ch in (chr(0x2028), chr(0x2029), chr(0x85)):
        s = s.replace(ch, "\\u%04x" % ord(ch))
    return s + chr(10)


def _lines(p: Path) -> list[str]:
    with open(p, encoding="utf-8", newline="") as f:
        return [x for x in f.read().split(chr(10)) if x.strip()]


def _sha_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _manifest_path(p: Path) -> Path:
    return p.with_name(p.name + ".manifest.json")


def export_pending(db, cfg: dict, out: Path, *, versions=None, queue: str = "auto", limit: int | None = None, now: str | None = None, claim_hours: float | None = None) -> dict:
    """Pending pairs in exactly the order the server worker would take them (recent first).

    With `claim_hours` every exported pair is claimed for that long: the server worker and any other export leave
    it alone until the answers are imported (which releases the claim) or the claim runs out. A pair another
    export still holds is not exported again."""
    ident = stance.identity_hash(cfg)
    c, n, by_queue = cfg["classifier"], 0, {q: 0 for q in stance.QUEUES}
    t0 = now or store.utcnow()
    held, skipped_claimed, taken = store.active_claims(db, t0), 0, []
    members = {}
    for v in stance._versions(cfg, versions):
        for r in db.execute("SELECT provider, post_id, topic_id, target_id FROM x_post_topics WHERE topic_version=? AND quality='PENDING'", (v,)):
            members.setdefault(tuple(r), []).append(v)
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        for r in stance.work_items(db, cfg, versions):
            if limit is not None and n >= limit:
                break
            if (queue in stance.QUEUES and r["queue"] != queue) or r["pre"] != "PENDING":
                continue
            key = stance.cache_key(r, r["target_id"], r["topic_id"], cfg)
            if stance._has_label(db, r, key) or db.execute("SELECT 1 FROM x_inference_cache WHERE cache_key=?", (key,)).fetchone():
                continue
            pair = (r["provider"], r["post_id"], r["topic_id"], r["target_id"])
            if pair in held:
                skipped_claimed += 1
                continue
            taken.append(pair)
            prompt = stance.PROMPTS[c["prompt_version"]].format(topic_id=r["topic_id"], target_id=r["target_id"], post_id=r["post_id"], kind=r["kind"],
                                                                parent_available=bool(r["parent_available"]), text=r["author_text"])
            f.write(_line({"provider": r["provider"], "post_id": r["post_id"], "topic_id": r["topic_id"], "target_id": r["target_id"], "queue": r["queue"],
                                "sample_day": r["sample_day"], "text_hash": r["text_hash"], "kind": r["kind"], "parent_available": r["parent_available"],
                                "member_of": members.get((r["provider"], r["post_id"], r["topic_id"], r["target_id"]), []), "cache_key": key, "prompt": prompt}))
            n += 1; by_queue[r["queue"]] += 1
    man = {"format": FORMAT, "kind": "PENDING_WORK", "created_at_utc": now or store.utcnow(), "file": out.name, "sha256": _sha_file(out), "items": n, "by_queue": by_queue,
           "inference_identity": ident, "identity": stance.inference_identity(cfg), "contains": "post text (private); do not commit or publish"}
    man["skipped_claimed_by_another_export"] = skipped_claimed
    man["content_sha256_per_line"] = "sha256 of each line's text is its text_hash; the whole file is `sha256`"
    if claim_hours:
        from datetime import timedelta
        exp = store.iso(store.parse(t0) + timedelta(hours=float(claim_hours)))
        db.execute(store.CLAIMS_SCHEMA)
        db.executemany("INSERT OR REPLACE INTO x_offload_claims VALUES(?,?,?,?,?,?,?,?,NULL)", [(*p, ident, man["sha256"], t0, exp) for p in taken])
        db.commit()
        man["claim"] = {"pairs": len(taken), "claimed_at_utc": t0, "expires_at_utc": exp}
    _manifest_path(out).write_text(json.dumps(man, indent=1, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    return {k: man[k] for k in ("format", "items", "by_queue", "sha256", "inference_identity", "skipped_claimed_by_another_export")} | {"file": str(out), "manifest": str(_manifest_path(out)), "claim": man.get("claim")}


def import_results(db, cfg: dict, results: Path, *, versions=None, now: str | None = None) -> dict:
    man_path = _manifest_path(results)
    if not man_path.exists():
        raise OffloadError("RESULTS_MANIFEST_MISSING")
    man = json.loads(man_path.read_text(encoding="utf-8"))
    if man.get("format") != FORMAT or man.get("kind") != "RESULTS":
        raise OffloadError("RESULTS_MANIFEST_FORMAT")
    if man.get("sha256") != _sha_file(results):
        raise OffloadError("RESULTS_FILE_HASH_MISMATCH")
    ident = stance.identity_hash(cfg)
    if man.get("inference_identity") != ident or man.get("identity") != stance.inference_identity(cfg):
        raise OffloadError("INFERENCE_IDENTITY_IS_NOT_THE_ACTIVE_ONE")          # answers of another model/prompt/parameter set are not this configuration's
    vs = stance._versions(cfg, versions)
    c, ph = cfg["classifier"], stance.prompt_hash(cfg["classifier"]["prompt_version"])
    out = {"status": "IMPORTED", "lines": 0, "labels_written": 0, "already_present": 0, "rejected": {}, "by_quality": {}, "inference_identity": ident,
           "worker": man.get("worker"), "results_sha256": man["sha256"]}

    def reject(why):
        out["rejected"][why] = out["rejected"].get(why, 0) + 1

    for line in _lines(results):
        if not line.strip():
            continue
        out["lines"] += 1
        try:
            o = json.loads(line)
            ids = (o["provider"], str(o["post_id"]), o["topic_id"], o["target_id"])
            raw, started, completed = o["raw_output"], o["inference_started_at_utc"], o["inference_completed_at_utc"]
            store.parse(started); store.parse(completed)
        except (ValueError, KeyError, TypeError):
            reject("LINE_NOT_READABLE"); continue
        post = db.execute("SELECT * FROM x_posts WHERE provider=? AND post_id=?", ids[:2]).fetchone()
        if post is None:
            reject("POST_NOT_IN_STORE"); continue
        post = dict(post)
        if o.get("text_hash") != post["text_hash"]:
            reject("TEXT_CHANGED_OR_WRONG_POST"); continue
        if not db.execute(f"SELECT 1 FROM x_post_topics WHERE provider=? AND post_id=? AND topic_id=? AND target_id=? AND quality='PENDING' AND topic_version IN ({','.join('?' * len(vs))})", ids + tuple(vs)).fetchone():
            reject("NOT_A_MEMBER_OF_A_LIVE_SAMPLE_VERSION"); continue
        key = stance.cache_key(post, ids[3], ids[2], cfg)
        if o.get("cache_key") != key:
            reject("INFERENCE_KEY_MISMATCH"); continue
        if not isinstance(raw, str) or completed < started or completed < post["fetched_at_utc"]:
            reject("OUTPUT_OR_TIMES_NOT_PLAUSIBLE"); continue
        if db.execute("SELECT 1 FROM x_stance_labels WHERE provider=? AND post_id=? AND topic_id=? AND target_id=? AND cache_key=?", ids + (key,)).fetchone():
            out["already_present"] += 1; continue
        lab = stance.validate_output(raw, post=post, topic_id=ids[2], target_id=ids[3])
        raw_hash, written = store.sha(raw), max(now or store.utcnow(), completed)
        db.execute("INSERT OR IGNORE INTO x_inference_cache(cache_key, raw_output, raw_output_hash, created_at_utc, inference_identity, started_at_utc, completed_at_utc) VALUES(?,?,?,?,?,?,?)",
                   (key, raw, raw_hash, completed, ident, started, completed))
        store.put_label(db, {"provider": ids[0], "post_id": ids[1], "topic_id": ids[2], "target_id": ids[3], "label": lab["label"], "quality_status": lab["quality_status"],
                             "is_relevant": None if lab["is_relevant"] is None else int(lab["is_relevant"]), "evidence_text": lab["evidence_text"], "reason_short": lab["reason_short"],
                             "model_id": c["model_id"], "model_revision": c["immutable_revision"], "prompt_hash": ph, "inferred_at_utc": completed, "raw_output_hash": raw_hash,
                             "cache_key": key, "inference_identity": ident, "identity_status": "RECORDED", "inference_started_at_utc": started,
                             "inference_completed_at_utc": completed, "label_written_at_utc": written})
        out["labels_written"] += 1
        out["by_quality"][lab["quality_status"]] = out["by_quality"].get(lab["quality_status"], 0) + 1
    db.execute("INSERT OR IGNORE INTO x_inference_identities VALUES(?,?,?)", (ident, now or store.utcnow(), json.dumps({"declared": stance.inference_identity(cfg), "runtime_reported": man.get("worker")}, sort_keys=True, ensure_ascii=False)))
    try:                                                        # the worker is done with this work file: its claims end, whatever each line became
        out["claims_released"] = db.execute("UPDATE x_offload_claims SET released_at_utc=? WHERE work_sha256=? AND released_at_utc IS NULL", (now or store.utcnow(), man.get("work_sha256"))).rowcount
    except Exception:  # noqa: BLE001 - a store that never claimed anything
        out["claims_released"] = 0
    db.commit()
    return out


def write_results(work: Path, results: Path, classifier, cfg: dict, *, clock=None, worker: dict | None = None, limit: int | None = None) -> dict:
    """The worker side. Reads the work file (after checking it against its manifest), calls the classifier once per
    line and writes the answers with the real start and completion time of each call."""
    man = json.loads(_manifest_path(work).read_text(encoding="utf-8"))
    if man.get("sha256") != _sha_file(work):
        raise OffloadError("WORK_FILE_HASH_MISMATCH")
    if man.get("inference_identity") != stance.identity_hash(cfg) or man.get("identity") != stance.inference_identity(cfg):
        raise OffloadError("WORKER_CONFIG_IS_NOT_THE_REQUESTED_IDENTITY")
    tick = clock or store.utcnow
    seen = stance.check_runtime(cfg, classifier)
    n = 0
    with open(results, "w", encoding="utf-8", newline="\n") as f:
        for line in _lines(work):
            if limit is not None and n >= limit:
                break
            o = json.loads(line)
            started = tick()
            try:
                raw = classifier(o["prompt"], {"post_id": o["post_id"], "topic_id": o["topic_id"], "target_id": o["target_id"]}) if getattr(classifier, "wants_ids", False) else classifier(o["prompt"])
            except stance.ClassifierUnavailable:
                break                                           # the rest simply stays pending on the server
            f.write(_line({k: o[k] for k in ("provider", "post_id", "topic_id", "target_id", "text_hash", "cache_key")} | {"raw_output": raw if isinstance(raw, str) else "",
                               "inference_started_at_utc": started, "inference_completed_at_utc": tick()}))
            n += 1
    out = {"format": FORMAT, "kind": "RESULTS", "created_at_utc": tick(), "file": results.name, "sha256": _sha_file(results), "items": n, "work_sha256": man["sha256"],
           "inference_identity": man["inference_identity"], "identity": man["identity"], "worker": {**(worker or {}), "runtime_reported": seen}}
    _manifest_path(results).write_text(json.dumps(out, indent=1, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    return out
