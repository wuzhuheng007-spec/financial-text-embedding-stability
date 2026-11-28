"""Run inside the production web image with the var volume mounted read-only and no network:

  docker run --rm --network none --read-only --tmpfs /tmp:size=1500m --user 10001:10001 \
     -v quant-personal_personal_var:/v:ro -v <this file>:/x.py:ro --entrypoint python <image> /x.py > export.tar.gz

It copies the X store into tmpfs with the sqlite online backup, then writes a tar.gz to stdout holding QQQ-topic posts
with their topic rows and labels, the QQQ query runs, the queries, the run-post links and the hashes of the raw pages
(no page bodies, no provider cursors, no credentials). Nothing is written to the production volume.
"""
import hashlib, io, json, sqlite3, sys, tarfile, time

SRC = "/v/x_stance/x_stance.sqlite3"
src = sqlite3.connect(f"file:{SRC}?mode=ro", uri=True); db = sqlite3.connect("/tmp/c.sqlite3"); src.backup(db); src.close()
db.row_factory = sqlite3.Row
meta = {"exported_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "store": "production X stance store (online backup copy)"}


def rows(sql, args=()):
    return [dict(r) for r in db.execute(sql, args)]


qposts = rows("""select p.provider, p.post_id, p.author_id, p.created_at_utc, p.first_seen_at_utc, p.fetched_at_utc, p.ingest_mode,
                        p.source_url, p.text, p.lang, p.text_hash, p.edit_status, p.kind, p.parent_available, p.raw_batch,
                        p.engagement_snapshot_json
                 from x_posts p where exists (select 1 from x_post_topics t where t.provider=p.provider and t.post_id=p.post_id and t.topic_id='qqq')""")
topics = rows("select * from x_post_topics where topic_id='qqq'")
labels = rows("""select provider, post_id, topic_id, target_id, label, quality_status, is_relevant, evidence_text, reason_short, model_id,
                        model_revision, prompt_hash, inferred_at_utc, raw_output_hash, cache_key, inference_identity, identity_status,
                        label_written_at_utc from x_stance_labels where topic_id='qqq'""")
queries = rows("select * from x_queries")
runs = rows("""select run_id, provider, query_hash, topic_id, lang, window_start_utc, window_end_utc, parent_run_id, status, stop_reason,
                      (cursor is not null and cursor != '') as cursor_left, pages, requests, returned, in_window, out_of_window, duplicates,
                      new_posts, earliest_utc, latest_utc from x_query_runs""")
run_posts = rows("select run_id, provider, post_id from x_run_posts")
pages = rows("select provider, query_hash, window_start_utc, window_end_utc, response_sha256, fetched_at_utc, (cursor != '') as continuation from x_page_cache")
ledger = rows("""select request_id, job_type, pool, is_preflight, run_id, window_start_utc, window_end_utc, attempt, reserved_credits, settled_credits,
                        returned, unique_ids, status, cost_source, budget_day, created_at_utc from x_cost_ledger""")
plans = rows("select plan_id, job, plan_key, frozen_at_utc, sha256, plan_json from x_sample_plans")
watch = rows("select watch_id, stable_author_id, identity_status, enabled, watchlist_version from x_watch_authors")

buf = io.BytesIO()
with tarfile.open(fileobj=buf, mode="w:gz") as tf:
    hashes = {}
    for name, data in [("posts_qqq", qposts), ("topics_qqq", topics), ("labels_qqq", labels), ("queries", queries), ("runs", runs),
                       ("run_posts", run_posts), ("pages", pages), ("ledger", ledger), ("plans", plans), ("watch_authors", watch)]:
        b = "\n".join(json.dumps(r, ensure_ascii=False, sort_keys=True) for r in data).encode("utf-8") + b"\n"
        hashes[name] = {"rows": len(data), "sha256": hashlib.sha256(b).hexdigest()}
        ti = tarfile.TarInfo(f"{name}.jsonl"); ti.size = len(b); ti.mtime = 0; tf.addfile(ti, io.BytesIO(b))
    meta["files"] = hashes
    m = json.dumps(meta, indent=1).encode()
    ti = tarfile.TarInfo("export_meta.json"); ti.size = len(m); ti.mtime = 0; tf.addfile(ti, io.BytesIO(m))
sys.stdout.buffer.write(buf.getvalue())
