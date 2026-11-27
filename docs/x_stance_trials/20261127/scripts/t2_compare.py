"""Like-floor trial, comparison. Read-only on the two COPIES: c_before (as production built it, floor 20) and c
(floor 5, after the trial labels were imported and the same build ran). Prints counts only: no post text, no
handle, no author id (authors are compared inside this script and only counted)."""
import json, sqlite3, collections
B = sqlite3.connect("file:/t/c_before.sqlite3?mode=ro", uri=True); T = sqlite3.connect("file:/t/c.sqlite3?mode=ro", uri=True)
for d in (B, T): d.row_factory = sqlite3.Row
def groups(d, table, extra=""):
    q = f"SELECT window_label, payload_json FROM {table} WHERE window_kind='session3' AND lang_scope='all' AND topic_id='qqq' AND target_id='QQQ' {extra}"
    return {r["window_label"]: json.loads(r["payload_json"]) for r in d.execute(q)}
def authors(d, ids):
    if not ids: return set()
    return {r[0] for r in d.execute("SELECT DISTINCT author_id FROM x_posts WHERE post_id IN (%s)" % ",".join("?" * len(ids)), list(ids))}
def eq(p): return (p.get("shares_equal_weight") or {})
def cls(p): return p.get("classification") or {}
out = {}
for scope, table, extra in (("keyword", "x_daily_sentiment", ""), ("combined", "x_panel_sentiment", "AND scope='combined'")):
    gb, gt = groups(B, table, extra), groups(T, table, extra); rows = []
    for g in sorted(set(gb) | set(gt)):
        b, t = gb.get(g) or {}, gt.get(g) or {}
        ab, at = authors(B, b.get("vote_post_ids") or []), authors(T, t.get("vote_post_ids") or [])
        sb, st = eq(b), eq(t)
        rows.append({"group": g, "authors_before": len(ab), "authors_after": len(at), "new_authors": len(at - ab), "authors_lost": len(ab - at),
                     "valid_posts_before": b.get("posts_total"), "valid_posts_after": t.get("posts_total"),
                     "eq_before": {k: sb.get(k) for k in ("bullish", "bearish", "neutral")}, "eq_after": {k: st.get(k) for k in ("bullish", "bearish", "neutral")},
                     "eq_change_pp": {k: (None if sb.get(k) is None or st.get(k) is None else round(100 * (st[k] - sb[k]), 1)) for k in ("bullish", "bearish", "neutral")},
                     "excluded_before": b.get("excluded_or_unjudged"), "excluded_after": t.get("excluded_or_unjudged"),
                     "filtered_before": {k: cls(b).get(k) for k in ("filtered_low_likes", "filtered_likes_unknown", "filtered_target_not_named", "pending", "matched_posts")},
                     "filtered_after": {k: cls(t).get(k) for k in ("filtered_low_likes", "filtered_likes_unknown", "filtered_target_not_named", "pending", "matched_posts")},
                     "state_before": b.get("state"), "state_after": t.get("state"), "mode": t.get("mode") or b.get("mode")})
    out[scope] = rows
# the trial pairs themselves (QQQ): what the model said, by like band, and why the rest stayed out
sv_t = T.execute("SELECT topic_version, count(*) FROM x_post_topics WHERE topic_id='qqq' GROUP BY 1 ORDER BY 2 DESC").fetchall()
out["sample_versions_qqq"] = [(r[0][-40:], r[1]) for r in sv_t]
print(json.dumps(out, ensure_ascii=False))
