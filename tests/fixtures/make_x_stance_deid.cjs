// Builds tests/fixtures/x_stance_view_deid.json: one de-identified read model for the page tests and the preview
// screenshots. Everything in it is made up and deterministic: counts, shares and closes are synthetic, there is no post
// text, no author name and no account id. Only the shape follows the live read model (2026-11-26) and the dates follow
// the NYSE calendar from 2026-08-24 (Labor Day and Thanksgiving closed), in groups of three sessions.
//   node tests/fixtures/make_x_stance_deid.cjs
const fs = require('fs'), path = require('path');
const here = __dirname, EMPTY = JSON.parse(fs.readFileSync(path.join(here, 'x_stance_view_empty.json'), 'utf8'));
const closed = new Set(['2026-09-07', '2026-11-26']);
const days = [];
for (let t = Date.UTC(2026, 7, 24); days.length < 66; t += 864e5) { const d = new Date(t), iso = d.toISOString().slice(0, 10); if (d.getUTCDay() % 6 && !closed.has(iso)) days.push(iso); }
const groups = []; for (let i = 0; i + 3 <= days.length; i += 3) groups.push(days.slice(i, i + 3));
let seed = 7; const rnd = () => (seed = (seed * 16807) % 2147483647) / 2147483647;
const split = (n, b) => { const bull = Math.round(n * b), bear = Math.round((n - bull) * 0.8); return {bullish: bull, bearish: bear, neutral: n - bull - bear}; };
const shares = c => { const n = c.bullish + c.bearish + c.neutral; return n ? {bullish: c.bullish / n, bearish: c.bearish / n, neutral: c.neutral / n} : {bullish: null, bearish: null, neutral: null}; };
function row(g, i, {authors, posts, pending = 0, status = 'COMPLETE', empty = null, bias = 0.6}) {
  const a = split(authors, bias), p = split(posts, bias + 0.02), done = posts + Math.round(posts * 0.2), matched = done + pending;
  return {window_kind: 'session3', window_label: g[2], group_sessions: g, group_size: 3, topic_id: 'qqq', target_id: 'QQQ', lang_scope: 'all', mode: 'BATCH_AFTER_CLOSE',
    window_start_utc: `${g[0]}T00:00:00Z`, window_end_utc: `${g[2]}T21:00:00Z`, signal_committed_at_utc: `${g[2]}T23:30:00Z`, snapshot_id: `synthetic-snapshot-${i}`,
    accounts: a, accounts_total: authors, posts: p, posts_total: posts, shares_equal_weight: shares(a), shares_by_post: shares(p), shares: shares(a),
    excluded_or_unjudged: posts ? {IRRELEVANT: Math.round(posts * 0.1), EVIDENCE_NOT_IN_TEXT: Math.round(posts * 0.08), JSON_INVALID: posts > 50 ? 1 : 0} : {},
    classification: {matched_posts: matched, classified_or_prefiltered: done, pending, share_done: matched ? done / matched : null, filtered_low_likes: posts * 4, filtered_target_not_named: Math.round(posts / 9), sample_version: 'synthetic-sample-version'},
    collection_status: status, low_sample: authors > 0 && authors < 30, low_sample_threshold: 30, state: 'UNKNOWN', state_reason: pending ? 'CLASSIFICATION_INCOMPLETE' : null, empty_reason: empty,
    activation: {model_id: 'synthetic-model', model_revision: 'synthetic-revision-0000', prompt_hash: 'synthetic-prompt-hash', identity: 'synthetic-identity', sample_version: 'synthetic-sample-version'}};
}
const d = JSON.parse(JSON.stringify(EMPTY));
d.has_data = true;
d.daily = groups.map((g, i) => {
  const last = i === groups.length - 1;
  if (i === 9) return row(g, i, {authors: 0, posts: 0, empty: 'NO_SAMPLE'});                                    // a group with nothing collected
  if (i === 15) return row(g, i, {authors: 0, posts: 0, pending: 40, empty: 'PENDING'});                       // a group waiting for the model
  const authors = i === 18 ? 22 : 60 + Math.round(rnd() * 50);
  return row(g, i, {authors, posts: Math.round(authors * 1.25), pending: last ? 90 : i > 16 ? 20 : 0, status: i > 16 ? 'PARTIAL' : 'COMPLETE', bias: 0.5 + rnd() * 0.3});
});
const sel = '2026-11-23T01:10:22Z';
d.panels.watchlist = {sample_version: 'synthetic-sample-version+watchlist', available: true, narratives: [], daily: groups.map((g, i) => row(g, i, i === groups.length - 1 ? {authors: 1, posts: 1, status: 'PARTIAL', bias: 1} : {authors: 0, posts: 0, empty: 'NO_SAMPLE'}))};
d.panels.combined = {sample_version: 'synthetic-sample-version+combined', available: true, narratives: [], daily: d.daily.map(r => ({...JSON.parse(JSON.stringify(r)), accounts_total: r.accounts_total ? r.accounts_total + 1 : 0}))};
d.watchlist.summary = {...(d.watchlist.summary || {}), imported: true, logical_authors: 25, selected_at_utc: sel};
if (d.account_rules && d.account_rules.named_accounts) d.account_rules.named_accounts = d.account_rules.named_accounts.map((n, i) => ({...n, label: `synthetic-author-${i + 1}`, handle: null, identified: false}));   // no real names
const research = {}, frozenLast = '2026-11-17';
days.forEach((day, i) => { if (day <= frozenLast && i !== 20) research[day] = Math.round((600 + i * 1.7 + Math.sin(i / 3) * 9) * 100) / 100; });   // one session without a close
['2026-11-18', '2026-11-19', '2026-11-20', '2026-11-23'].forEach((day, i) => { research[day] = 640 + i * 2.5; });
d.closes = {qqq: research};
d.closes_meta = {frozen_input_last_date: frozenLast, display_fill: {state: 'READY', source: 'moomoo daily bars, unadjusted (portal display history)', basis: 'UNADJUSTED', currency: 'USD', after: frozenLast, dates_added: 4, through: '2026-11-23'},
  research: {basis: 'ADJUSTED', currency: 'USD', series_ids: ['US.QQQ|DAY|a1'], source: 'frozen research price input (anchor close of each h=1 pair)'}};
d.updates = {state: 'FRESH', ok: true, stage_exits: {build: 0, collect: 0, export: 0}, age_hours: 0.5, interval_hours: 2, round_started_at_utc: '2026-11-26T02:20:00Z', round_finished_at_utc: '2026-11-26T02:28:00Z',
  last_successful_update_at_utc: '2026-11-26T02:28:00Z', last_successful_collection_at_utc: '2026-11-26T02:24:00Z', last_label_written_at_utc: '2026-11-26T02:25:00Z', last_summary_committed_at_utc: '2026-11-26T02:26:00Z',
  collection: {status: 'RAN', requests: 32, returned: 120, new_posts: 100}, credit_hold: null, pending: {new_posts_queue: 90, history_backlog: 1500}};
d.collection = {...d.collection, posts: 9000, state: 'HAS_GAPS', last_post_created_at_utc: '2026-11-25T20:19:00Z', last_fetch_at_utc: '2026-11-26T02:22:00Z'};
d.conditional = [1, 3, 5, 10, 20, 30].flatMap(h => ['bullish', 'bearish', 'neutral'].map(s_ => ({topic_id: 'qqq', target_id: 'QQQ', kind: 'desc_return', h, state: s_, n_dates: 0, state_days_without_return: 0,
  mean: null, median: null, up_share: null, unconditional: {n_dates: 0, mean: null}, mean_ci95: null, status: 'INCONCLUSIVE', status_reason: 'TOO_FEW_STATE_DAYS'})));
fs.writeFileSync(path.join(here, 'x_stance_view_deid.json'), JSON.stringify(d, null, 1) + '\n');
console.log(`groups ${groups.length}, first ${groups[0][0]}, last ${groups[groups.length - 1][2]}`);
