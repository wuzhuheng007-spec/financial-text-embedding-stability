// X stance panel: renders the real empty read model and a made-up filled one in a minimal DOM, in Chinese and in
// English. Checks: no sample rows without data, the missing inputs are named, backfill is labelled apart from live,
// at most five views, post text and keywords are marked as data, links only to x.com, and no Chinese is left in
// English mode. The filled payload is synthetic: it exercises the renderer, it is not a market result.
const test = require('node:test'), assert = require('node:assert'), fs = require('fs'), path = require('path'), vm = require('vm');
const core = path.join(__dirname, '../apps/quant_portal/static/core');
const src = fs.readFileSync(path.join(core, 'x_stance_v1.js'), 'utf8');
const i18n = require(path.join(core, 'i18n_v15.js'));
const EMPTY = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures/x_stance_view_empty.json'), 'utf8'));
const han = /[㐀-鿿]/;

class El {
  constructor(tag) { this.tagName = tag; this.children = []; this.attrs = {}; this.dataset = {}; this.style = {}; this.className = ''; this.hidden = false; this._text = ''; this.listeners = {};
    this.classList = {add: c => { this.className += ' ' + c; }}; }
  set textContent(v) { this.children = []; this._text = String(v); }
  get textContent() { return this._text + this.children.map(c => c.textContent).join(''); }
  append(...n) { this.children.push(...n); }
  replaceChildren(...n) { this.children = n; this._text = ''; }
  setAttribute(k, v) { this.attrs[k] = String(v); if (k.startsWith('data-')) this.dataset[k.slice(5)] = String(v); }
  addEventListener(k, f) { this.listeners[k] = f; }
  all(pred, out = []) { for (const c of this.children) if (c instanceof El) { if (pred(c)) out.push(c); c.all(pred, out); } return out; }
  querySelectorAll(sel) { const m = /^\[data-(\w+)(?:="([^"]+)")?\]$/.exec(sel); return this.all(e => m[1] in e.dataset && (m[2] == null || e.dataset[m[1]] === m[2])); }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
}
class Text { constructor(t) { this.textContent = String(t); } }

function mount(payload) {
  const root = new El('section'); root.dataset.url = '/x-stance.json';
  const state = new El('span');
  root.dataset.watchUrl = '/x-stance/watch/';
  for (const t of ['overview', 'settings', 'simple', 'authors', 'daily', 'views', 'history']) {
    const b = new El('button'); b.dataset.xtab = t; root.append(b);
    const p = new El('div'); p.dataset.xpanel = t; p.hidden = t !== 'settings'; root.append(p);
  }
  const document = {getElementById: id => id === 'x-stance' ? root : id === 'x-stance-state' ? state : null, createElement: t => new El(t), querySelector: () => ({value: 'csrf-token-of-the-page'}),
    createElementNS: (_, t) => new El(t), createTextNode: t => new Text(t)};
  const done = new Promise(res => {
    const fetch = async (url, opt) => { if (opt && opt.method === 'POST') { (payload.__posts = payload.__posts || []).push({url, body: JSON.parse(opt.body), headers: opt.headers}); return {ok: true, json: async () => ({})}; }
      return {ok: true, json: async () => { setTimeout(res, 0); return payload; }}; };
    vm.runInNewContext(src, {document, fetch, Node: El, Set, Math, Object, String, console});
  });
  return done.then(() => ({root, state, panel: n => root.querySelector(`[data-xpanel="${n}"]`)}));
}
// every piece of text a reader would see, except nodes marked translate="no" (data, shown as stored)
function texts(el, out = []) {
  if (el instanceof Text) { out.push(el.textContent); return out; }
  if (el.attrs.translate === 'no') return out;
  if (el._text) out.push(el._text);
  for (const k of ['aria-label']) if (el.attrs[k]) out.push(el.attrs[k]);
  el.children.forEach(c => texts(c, out));
  return out;
}
const rowsOf = p => p.all(e => e.tagName === 'tr').length;

function filled() {
  const d = JSON.parse(JSON.stringify(EMPTY));
  d.has_data = true; d.collection.state = 'HAS_GAPS'; d.collection.posts = 120; d.data_kinds = {live: 0, retrospective: 120};
  const day = (label, sh, state, reason) => ({window_kind: 'session', window_label: label, topic_id: 'qqq', target_id: 'QQQ', lang_scope: 'all', mode: 'RETROSPECTIVE_VISIBLE_NOW',
    shares: sh, accounts_total: sh.bullish == null ? 0 : 40, posts_total: 55, unjudged_total: 3, state, state_reason: reason,
    classification: {sample_accounts: 60, share_done: reason === 'CLASSIFICATION_INCOMPLETE' ? 0 : 1, pending: 0, matched_posts: 80}});
  d.daily = [day('2025-06-02', {bullish: 0.5, bearish: 0.25, neutral: 0.25}, 'bullish', null), day('2025-06-03', {bullish: null, bearish: null, neutral: null}, 'UNKNOWN', 'COLLECTION_PARTIAL'),
    day('2025-06-04', {bullish: 0.2, bearish: 0.2, neutral: 0.6}, 'UNKNOWN', 'LOW_SAMPLE'), day('2025-06-05', {bullish: null, bearish: null, neutral: null}, 'UNKNOWN', 'CLASSIFICATION_INCOMPLETE')];
  d.returns = [{topic_id: 'qqq', session_date: '2025-06-02', h: 1, anchor_close: 100}, {topic_id: 'qqq', session_date: '2025-06-04', h: 1, anchor_close: 103}];
  d.narratives = Array.from({length: 7}, (_, i) => ({topic_id: 'qqq', target_id: 'QQQ', window_label: '2025-06-02', rank: i + 1, title: `降息会抬高估值 sample view ${i}`,
    supporting_accounts: 9 - i, share_of_sample_accounts: 0.1, stance_composition: {bullish: 5, bearish: 2, neutral: 2 - 0},
    evidence: [{post_id: `90${i}`, url: `https://x.com/u/status/90${i}`, stance: 'bullish'}, {post_id: `91${i}`, url: 'javascript:alert(1)', stance: 'bearish'}]}));
  d.episodes = [{topic_id: 'qqq', state: 'bullish', start_session: '2025-06-02', end_session: '2025-06-02', sessions: 1, calendar_days: 1, left_censored: true, right_censored: true}];
  d.conditional = [1, 3, 5, 10, 20, 30].flatMap(h => ['bullish', 'bearish', 'neutral'].map(s => ({topic_id: 'qqq', kind: 'desc_return', h, state: s, n_dates: 1, mean: null, median: null,
    up_share: null, unconditional: {mean: null}, mean_ci95: null, status: 'INCONCLUSIVE'})));
  return d;
}

test('with no store every data panel names the missing inputs and shows no rows', async () => {
  const ui = await mount(EMPTY);
  assert.strictEqual(ui.state.textContent, '尚无数据');
  for (const p of ['daily', 'views', 'history']) {
    const t = texts(ui.panel(p)).join('|');
    assert.strictEqual(rowsOf(ui.panel(p)), 0, p);
    for (const need of ['采集端自检结果', '留出期起点']) assert.ok(t.includes(need), `${p} lacks ${need}`);
    assert.ok(!/\d+(\.\d+)?%/.test(t), `${p} shows a percentage without data`);
  }
  const s = texts(ui.panel('settings')).join('|');
  for (const need of ['尚未采集', '未知，待复检', '已固定版本', '未经人工验证', '编辑关键词', '停用', '采集端还没有上报检查结果', '采集端凭据可用', '尚未上报', '未配置（仅统计行业观点）', '计划抽样，不是全量', '0 / 5000', '0 / 0.75', '0 / 3000', '0 / 2000', '0 / 22.5', '0 / 200', '未运行', '不是供应商账单']) assert.ok(s.includes(need), need);
  assert.strictEqual(rowsOf(ui.panel('settings')), (1 + 4) + (1 + 4) + (1 + 2));     // header + the four configured topics; R3: header + four follower tiers; header + two named accounts
});

test('keywords and quoted post text are marked as data, not interface text', async () => {
  const ui = await mount(filled());
  const marked = ui.root.all(e => e.attrs.translate === 'no').map(e => e.textContent);
  assert.ok(marked.some(t => t.includes('存储芯片')) && marked.some(t => t.includes('降息会抬高估值')));
});

test('filled panels: backfill label, null shares stay blank, five views at most, safe links', async () => {
  const ui = await mount(filled());
  const daily = texts(ui.panel('daily')).join('|');
  assert.ok(daily.includes('历史回填（现在可见的样本）') && !daily.includes('实时留档'));
  assert.ok(daily.includes('样本账号过少') && daily.includes('本次采集样本中的比例') && daily.includes('交易日窗口总数 4；有有效标签并画入图表 2；没有有效标签：1；待分类：1'));
  const dayRows = ui.panel('daily').all(e => e.tagName === 'tr').filter(r => /2025-06-0\d/.test(r.textContent)).map(r => r.all(e => e.tagName === 'td').map(c => c.textContent));
  assert.deepStrictEqual(dayRows.map(c => c[0]), ['2025-06-04', '2025-06-02']);                 // only dates with valid labels are listed and drawn: no blank row, no 0 %, no neutral for an empty day
  assert.deepStrictEqual([dayRows[1][4], dayRows[1][3]], ['55', '40']);                          // valid posts and authors, each in its own column
  assert.ok(dayRows[1][2].includes('加权 50.0% / 25.0% / 25.0%'));                                 // the weighted shares are a small line in the shares cell
  assert.strictEqual(ui.panel('daily').all(e => (e.className || '').includes('xs-bar-empty')).length, 0);
  assert.strictEqual(ui.panel('daily').all(e => e.tagName === 'button' && e.textContent === '查看材料').length, 2);
  assert.strictEqual(ui.panel('views').all(e => (e.className || '') === 'xs-view').length, 5);
  const links = ui.panel('views').all(e => e.tagName === 'a');
  assert.ok(links.length === 5 && links.every(a => a.href.startsWith('https://x.com/') && a.rel === 'noopener noreferrer'));
  const hist = texts(ui.panel('history')).join('|');
  assert.ok(hist.includes('样本不足，无结论') && hist.includes('不是可成交收益') && !hist.includes('已估计'));
  assert.strictEqual(ui.panel('history').all(e => e.tagName === 'table')[1].all(e => e.tagName === 'tr').length, 1 + 18);   // all six horizons x three states
});

test('nothing visible is left in Chinese in English mode', async () => {
  for (const payload of [EMPTY, filled()]) {
    const ui = await mount(payload);
    const left = [...new Set(texts(ui.root).concat([ui.state.textContent]).map(t => t.trim()).filter(Boolean))].filter(t => han.test(i18n.translate(t)));
    assert.deepStrictEqual(left, []);
  }
});

// End to end: a read model the real pipeline built from a SYNTHETIC store (tests/test_x_stance_ui_payload.py writes it
// and sets X_STANCE_UI_PAYLOAD). The checks hold for any pipeline-built payload: nothing invented, nothing leaked, every
// drawn number traceable to its row. A report of the checks is written to X_STANCE_UI_REPORT when set.
test('a pipeline-built synthetic store renders every state without inventing or leaking anything', {skip: !process.env.X_STANCE_UI_PAYLOAD && 'needs X_STANCE_UI_PAYLOAD (set by tests/test_x_stance_ui_payload.py)'}, async () => {
  const d = JSON.parse(fs.readFileSync(process.env.X_STANCE_UI_PAYLOAD, 'utf8'));
  const ui = await mount(d), ov = ui.panel('overview');
  const report = {payload_rows: d.daily.length, checks: {}};
  const ok = (name, cond) => { report.checks[name] = !!cond; assert.ok(cond, name); };
  const marked = el => el.all(() => true).filter(e => e.attrs.translate === 'no').map(e => e.textContent.trim());
  ok('state follows has_data', ui.state.textContent === (d.has_data ? '已有数据' : '尚无数据'));
  ok('two panel cards and one receipt', ov.all(e => e.className === 'card xs-head').length === 2 && ov.all(e => e.className === 'card xs-receipt').length === 1);
  const q = d.daily.filter(r => r.topic_id === 'qqq' && r.lang_scope === 'all'), kind = q.some(r => r.window_kind === 'session3') ? 'session3' : 'session';
  const rows = q.filter(r => r.window_kind === kind);
  ok('every QQQ group is on the chart axis', ov.all(e => e.tagName === 'g' && (e.attrs.class || '').startsWith('xs-c-slot')).length === rows.length);
  ok('drawn shares sum to one per row', rows.filter(r => (r.shares_equal_weight || {}).bullish != null).every(r => Math.abs(r.shares_equal_weight.bullish + r.shares_equal_weight.bearish + r.shares_equal_weight.neutral - 1) < 1e-9));
  const kw = ov.all(e => e.className === 'card xs-head')[0], withEq = rows.filter(r => (r.shares_equal_weight || {}).bullish != null).pop();
  if (withEq) ok('the keyword card shows the latest group with values and its own author count', marked(kw).some(v => v.includes(String(withEq.accounts_total))));
  ok('no share is shown for a card without equal-weight shares', ov.all(e => e.className === 'card xs-head').every(c => c.all(e => (e.className || '').includes('xs-stat-v')).length === 0 || c.all(e => e.tagName === 'p' && e.className === 'xs-main-low').length === 0));
  ok('links go to x.com only', ui.root.all(e => e.tagName === 'a').every(a => !a.href || a.href.startsWith('https://x.com/') || a.href.startsWith('https://twitter.com/')));
  ok('nothing visible left in Chinese in English mode', [...new Set(texts(ui.root).concat([ui.state.textContent]).map(t => t.trim()).filter(Boolean))].filter(t => han.test(i18n.translate(t))).length === 0);
  if (process.env.X_STANCE_UI_REPORT) fs.writeFileSync(process.env.X_STANCE_UI_REPORT, JSON.stringify(report, null, 1));
});

// R3: account rules, both queues and weighted shares. The payload is synthetic.
test('account rules, the two queues and weighted next to equal-weight shares are shown as stored', async () => {
  const d = JSON.parse(JSON.stringify(EMPTY));
  let ui = await mount(d);
  let s = texts(ui.panel('settings')).join('|');
  for (const need of ['账号权重与点赞过滤', '点赞下限（低于此数的帖子不计入样本）', '不计入样本', '粉丝数下限', '未填写，暂按粉丝数分档', '不是历史数值', '分类队列', '还没有待分类的帖子。',
    '点赞不足，未计入样本', '点赞数未知，未计入样本']) assert.ok(s.includes(need), need);
  const marked = ui.root.all(e => e.attrs.translate === 'no').map(e => e.textContent);
  assert.ok(marked.includes('1,000,000') && marked.includes('口罩哥') && marked.some(t => t.startsWith('account-rules-v2-20261125-')));     // numbers and names are data
  d.classification = {matched_pairs: 40, classified_or_prefiltered: 30, pending: 10, share_done: 0.75, filtered_low_likes: 3210, filtered_likes_unknown: 4};
  d.queues = {recent: {pairs: 40, pending: 10, sample_days: 2, sample_days_complete: 1, oldest_pending_age_hours: 12.5, by_sample_day: {'2026-11-19': {pairs: 20, done: 20, share_done: 1}, '2026-11-18': {pairs: 20, done: 10, share_done: 0.5}}},
    history: {pairs: 900, pending: 900, sample_days: 90, sample_days_complete: 0, oldest_pending_age_hours: 30, by_sample_day: {}},
    rates_last_7_days: {posts_received_per_day: 714.3, model_inferences_per_day: 4.3}};
  d.has_data = true;
  d.daily = [{window_kind: 'session', window_label: '2026-11-19', topic_id: 'qqq', target_id: 'QQQ', lang_scope: 'all', mode: 'BATCH_AFTER_CLOSE', shares: {bullish: 0.5, bearish: 0.2, neutral: 0.3},
    shares_equal_weight: {bullish: 0.25, bearish: 0.5, neutral: 0.25}, accounts_total: 40, unjudged_total: 2, state: 'bullish', state_reason: null,
    classification: {sample_accounts: 44, share_done: 1, filtered_low_likes: 3210}}];
  ui = await mount(d);
  s = texts(ui.panel('settings')).join('|');
  for (const need of ['10 / 40', '1 / 2', '12.5', '900 / 900', '714.3', '4.3', '3210', '20 / 20', '10 / 20', '50.0%', '近期队列先处理']) assert.ok(s.includes(need), need);
  const cells = ui.panel('daily').all(e => e.tagName === 'tr').find(r => r.textContent.includes('2026-11-19')).all(e => e.tagName === 'td').map(c => c.textContent);
  assert.ok(cells[2].startsWith('25.0% / 50.0% / 25.0%') && cells[2].includes('加权 50.0% / 20.0% / 30.0%') && cells[6].includes('点赞不足 3210'));   // one account one vote (bull / bear / neutral) first, the weighted shares under it, and what the like floor left out
  assert.ok(texts(ui.panel('daily')).join('|').includes('按账号权重计算'));
  const left = [...new Set(texts(ui.root).map(t => t.trim()).filter(Boolean))].filter(t => han.test(i18n.translate(t)));
  assert.deepStrictEqual(left, []);                                                           // nothing of the new cards is left in Chinese in English mode
  d.account_rules = {enabled: false, version: null};
  ui = await mount(d);
  assert.ok(texts(ui.panel('settings')).join('|').includes('未启用，每个账号按一票计算。') && !texts(ui.panel('daily')).join('|').includes('按账号权重计算'));
});

// Watched authors. The payload is synthetic: invented handles, invented posts.
function watchPayload() {
  const d = JSON.parse(JSON.stringify(EMPTY));
  const author = (id, name, handle, over) => ({watch_id: id, display_name: name, handle, profile_url: handle ? `https://x.com/${handle}` : null, aliases: [], source_lists: ['总榜'], scheduling_priority: 2,
    enabled: true, identity_status: 'SCREENSHOT_HANDLE_PENDING_PROVIDER_ID', availability: 'WAITING_FOR_FIRST_COLLECTION', last_success_at_utc: null, last_post_created_at_utc: null, last_checked_ny_date: null,
    handle_candidates: [], handle_history: [], id_bound: false, ready_for_queries: true, handle_text_in_image: null, stored_posts: 0, weight_override: null, ...over});
  d.watchlist = {summary: {imported: true, logical_authors: 4, with_handle: 3, blocked_ambiguous: 1, ready_for_queries: 3, stable_ids_bound: 1, collected_at_least_once: 1, enabled: 4,
      selected_at_utc: '2026-11-23T00:10:00Z', suballocation: {}}, plan: null, reading: [],
    authors: [author('watch-01', '作者甲', 'Synth_One', {aliases: ['口罩哥'], scheduling_priority: 1, identity_status: 'RESOLVED_FROM_PROVIDER', availability: 'COLLECTED', id_bound: true, stored_posts: 3,
        last_post_created_at_utc: '2026-11-22T15:00:00Z'}),
      author('watch-02', '作者乙', 'Synth_Two', {availability: 'NO_VISIBLE_POSTS_IN_WINDOW'}), author('watch-03', '作者丙', 'Synth_Three', {enabled: false}),
      author('watch-90', '作者丁', null, {identity_status: 'AMBIGUOUS_HANDLE_BLOCKED', ready_for_queries: false, handle_text_in_image: 'AmbigCandidate', handle_candidates: [{handle: 'AmbigCandidat'}]})]};
  return d;
}

test('watched authors: list, ambiguous author, switches and the three panels with no data', async () => {
  const d = watchPayload();
  const ui = await mount(d);
  const t = texts(ui.panel('authors')).join('|');
  for (const need of ['重点作者名单', '不会在 X 上关注任何人', '事后选定的作者面板', '额度分配（近期额度内的划分，不是新增额度）', '0 / 600', '0 / 2400', '0 / 2000', '还没有冻结的采集计划；以上只是计划分配，不是进度。',
    '已由采集结果绑定稳定编号', '用户名来自截图，稳定编号待首次采集绑定', '用户名有歧义，暂不查询', '已采集', '该日没有可见帖子', '等待凭据', '待身份核对', '图片上的写法', '候选写法', '监测中', '已停用',
    '调度优先级只决定查询的先后，不是统计权重', '还没有采集到重点作者的帖子：正在等待凭据。']) assert.ok(t.includes(need), need);
  assert.ok(!t.includes('已关注') && !/\d+(\.\d+)?%/.test(t));                                 // nothing claims a follow, and no share is shown for authors without data
  const links = ui.panel('authors').all(e => e.tagName === 'a');
  assert.deepStrictEqual(links.map(a => a.href), ['https://x.com/Synth_One', 'https://x.com/Synth_Two', 'https://x.com/Synth_Three']);     // the ambiguous author has no link to open
  const marked = ui.panel('authors').all(e => e.attrs.translate === 'no').map(e => e.textContent.trim());
  for (const need of ['作者甲', '@Synth_One', 'AmbigCandidate', 'AmbigCandidat', '总榜']) assert.ok(marked.includes(need), need);        // names, handles and list names are data
  assert.ok(ui.panel('authors').all(e => (e.className || '').includes('tag-dim') && e.textContent === '口罩哥').length === 1);             // the alias is shown with its author
  const off = ui.panel('authors').all(e => e.tagName === 'button' && e.textContent === '已停用')[0];
  off.listeners.click();
  await new Promise(r => setTimeout(r, 5));
  assert.deepStrictEqual(d.__posts.map(x => [x.url, x.body]), [['/x-stance/watch/', {watch_id: 'watch-03', enabled: true}]]);
  // panel switch on the daily tab: no list data yet -> the missing inputs, never rows
  const btn = name => ui.panel('daily').all(e => e.tagName === 'button' && e.textContent === name)[0];
  assert.ok(btn('关键词样本') && !btn('重点作者') && btn('合并去重') && btn('关键词样本').attrs['aria-pressed'] === 'true');     // 2026-11-27: the watched-author panel is no longer a scope; its list stays in the authors section
  const left = [...new Set(texts(ui.root).map(x => x.trim()).filter(Boolean))].filter(x => han.test(i18n.translate(x)));
  assert.deepStrictEqual(left, []);
});

test('watched authors: raw posts with the reason each is or is not counted, and separate panel rows', async () => {
  const d = watchPayload();
  d.settings.acquisition_gate = {allowed: true, reasons: [], key_env_name: 'TWITTERAPI_IO_API_KEY'};
  d.watchlist.plan = {ny_date: '2026-11-22', frozen_at_utc: '2026-11-23T05:30:00Z', authors: 3, not_asked: ['watch-90'], max_pages_per_author: 3, posts_budget: 600, first_round_max_posts: 60};
  const post = (id, text, likes, analysis) => ({watch_id: 'watch-01', post_id: id, created_at_utc: '2026-11-22T15:00:00Z', first_seen_at_utc: '2026-11-23T05:31:00Z', kind: 'original', lang: 'zh', text,
    url: `https://x.com/Synth_One/status/${id}`, likes_at_fetch: likes, sources: ['watch:watch-01'], analysis});
  const A = (state, reason, stance) => [{topic_id: reason === 'NO_TARGET_KEYWORD_IN_TEXT' ? null : 'qqq', target_id: reason === 'NO_TARGET_KEYWORD_IN_TEXT' ? null : 'QQQ', state, reason, stance}];
  d.watchlist.reading = [post('11', '纳指100这里我继续加仓', 240, A('IN_ANALYSIS', null, 'bullish')), post('12', '随口一说 纳指100', 3, A('NOT_IN_ANALYSIS', 'BELOW_LIKE_FLOOR', null)),
    post('13', 'BTC 今天很强', 900, A('NOT_IN_ANALYSIS', 'NO_TARGET_KEYWORD_IN_TEXT', null)), post('14', '纳指100 怎么看', 80, A('WAITING_FOR_CLASSIFICATION', null, null)),
    post('15', '纳指100 今天吃了火锅', 50, A('NOT_IN_ANALYSIS', 'IRRELEVANT', null)), post('16', '第六条不显示', 50, A('WAITING_FOR_CLASSIFICATION', null, null))];
  const dayRow = (acc, sh) => ({window_kind: 'session', window_label: '2026-11-20', topic_id: 'qqq', target_id: 'QQQ', lang_scope: 'all', mode: 'BATCH_AFTER_CLOSE', shares: sh, shares_equal_weight: sh,
    accounts_total: acc, unjudged_total: 0, state: acc ? 'bullish' : 'UNKNOWN', state_reason: acc ? null : 'NO_VALID_ACCOUNTS', classification: {sample_accounts: acc, share_done: 1, filtered_low_likes: 1}});
  d.has_data = true;
  d.daily = [dayRow(40, {bullish: 0.5, bearish: 0.25, neutral: 0.25})];
  d.panels.watchlist = {sample_version: 'vocab-x@rules-y+watchlist:list-1', available: true, classification: {matched_pairs: 4}, narratives: [], daily: [dayRow(3, {bullish: 1, bearish: 0, neutral: 0})]};
  d.panels.combined = {sample_version: 'vocab-x@rules-y+combined:list-1', available: true, classification: {matched_pairs: 44}, narratives: [], daily: [dayRow(42, {bullish: 0.6, bearish: 0.2, neutral: 0.2})]};
  const ui = await mount(d);
  const t = texts(ui.panel('authors')).join('|');
  for (const need of ['已计入：看多', '未计入：点赞低于下限', '未计入：正文没有提到任何标的关键词', '待分类', '未计入：模型判为无关', '有已计入的观点', '未计入、待分类和无关都不算中性', '等待首次采集']) assert.ok(t.includes(need), need);
  assert.ok(!t.includes('等待凭据') && !t.includes('未计入：中性'));
  const shown = ui.panel('authors').all(e => (e.className || '') === 'xs-view-title').map(e => e.textContent);
  assert.deepStrictEqual(shown, ['纳指100这里我继续加仓', '随口一说 纳指100', 'BTC 今天很强', '纳指100 怎么看', '纳指100 今天吃了火锅']);      // five at most; the low-like post and the BTC post are still readable
  assert.ok(ui.panel('authors').all(e => e.tagName === 'a').every(a => a.href.startsWith('https://x.com/')));
  const cells = () => ui.panel('daily').all(e => e.tagName === 'tr').find(r => r.textContent.includes('2026-11-20')).all(e => e.tagName === 'td').map(c => c.textContent);
  const click = name => ui.panel('daily').all(e => e.tagName === 'button' && e.textContent === name)[0].listeners.click();
  assert.deepStrictEqual([cells()[2].slice(0, 21), cells()[3]], ['50.0% / 25.0% / 25.0%', '40']);
  assert.ok(!ui.panel('daily').all(e => e.tagName === 'button' && e.textContent === '重点作者').length);                // no watched-author scope any more
  click('合并去重');
  assert.deepStrictEqual([cells()[2].slice(0, 21), cells()[3]], ['60.0% / 20.0% / 20.0%', '42']);                    // each panel shows its own denominator
  d.watchlist.reading = d.watchlist.reading.filter(r => r.post_id !== '11');
  const ui2 = await mount(d);
  assert.ok(texts(ui2.panel('authors')).join('|').includes('无达标样本') && !texts(ui2.panel('authors')).join('|').includes('有已计入的观点'));    // "no qualifying sample", never "neutral"
  const left = [...new Set(texts(ui2.root).concat(texts(ui.root)).map(x => x.trim()).filter(Boolean))].filter(x => han.test(i18n.translate(x)));
  assert.deepStrictEqual(left, []);
});

// Overview on the de-identified fixture (tests/fixtures/x_stance_view_deid.json, made by make_x_stance_deid.cjs; all
// numbers synthetic, no post text): the update receipt, one card per panel that says which sample, which period, how many
// authors and what the evidence is, and one chart that keeps every group, can be narrowed, read by keyboard or tap,
// and says why a price is missing. The same fixture drives the preview screenshots.
const DEID = () => JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures/x_stance_view_deid.json'), 'utf8'));
const dataOf = x => x.all(() => true).filter(e => e.attrs.translate === 'no').map(e => e.textContent.trim());
const allText = x => texts(x).join('|') + '|' + dataOf(x).join('|');
const leftChinese = ui => [...new Set(texts(ui.root).concat([ui.state.textContent]).map(t => t.trim()).filter(Boolean))].filter(t => han.test(i18n.translate(t)));
const cards = ov => ov.all(e => e.className === 'card xs-head');
const slots = ov => ov.all(e => e.tagName === 'g' && (e.attrs.class || '').startsWith('xs-c-slot'));
const picked = ov => texts(ov.all(e => e.className === 'xs-pick')[0]).join('|') + '|' + dataOf(ov.all(e => e.className === 'xs-pick')[0]).join('|');

test('overview cards: sample, period, authors and evidence, with stage tags next to the numbers', async () => {
  const ui = await mount(DEID()), ov = ui.panel('overview'), [kw, cb] = cards(ov);
  assert.strictEqual(cards(ov).length, 2);                                                                                                    // keyword and combined; the watched-author card is gone
  const k = allText(kw);
  for (const need of ['关键词检索采到', '最新一组（三个交易日）', '2026-11-20 – 2026-11-24', '分类未完成，占比还会变', '按作者（每人一票）', '按有效帖子（每帖一次）', '已处理判断', '模型待分类',
    '覆盖不全', '分类未完成', '行情不完整', '模型未人工验证', '距每组 500 条还差', '详情：票数、进度、模型与时间', 'synthetic-model', '看空较之前', '组均值']) assert.ok(k.includes(need), `keyword card lacks ${need}`);
  const r = DEID().daily.at(-1), c = r.classification;
  assert.ok(dataOf(kw).includes(`${c.classified_or_prefiltered} / ${c.matched_posts}`) && dataOf(kw).includes(String(c.pending)));          // processed judgements, numerator and denominator, and the true model queue
  assert.ok(dataOf(kw).some(v => v.startsWith(`${(r.shares_equal_weight.bullish * 100).toFixed(1)}% (${r.accounts.bullish})`)));            // by author: share and real votes
  assert.ok(dataOf(kw).some(v => v.startsWith(`${(r.shares_by_post.bullish * 100).toFixed(1)}% (${r.posts.bullish})`)));                  // by valid post, listed apart
  assert.ok(!allText(ov).includes('名单选定于'));                                                                                             // nothing of the watched-author card is left
  assert.ok(!allText(ov).includes('读数提醒'));                                                                                                // the fixture's classifier was not reviewed: no review note
  assert.ok(allText(cb).includes('跨来源去重'));
  assert.ok(kw.all(e => e.tagName === 'details').every(e => !e.open));                                                                        // the identity detail starts closed
  assert.deepStrictEqual(leftChinese(ui), []);
});

test('overview chart: every group on the axis, a range to narrow it, keyboard and tap (not hover), and why a price is missing', async () => {
  const d = DEID(), ui = await mount(d), ov = ui.panel('overview');
  assert.strictEqual(slots(ov).length, d.daily.length);                                                                                       // the full group index
  assert.ok(dataOf(ov).includes('2026-08-24 – 2026-11-24'));                                                                              // first and last session shown
  const svg = ov.all(e => e.tagName === 'svg')[0], marks = svg.all(e => e.tagName === 'text').map(e => e.textContent);
  assert.ok(marks.includes('∅') && marks.includes('…') && marks.some(t => t.endsWith('*')));                                                // empty and pending groups keep their place
  // the latest group is selected first; its price is missing because it is after the price data
  assert.ok(picked(ov).includes('2026-11-20 – 2026-11-24') && picked(ov).includes('缺价') && picked(ov).includes('(AFTER_PRICE_INPUT_END)'));
  const s = slots(ov);
  s.at(-1).listeners.keydown({key: 'ArrowLeft', preventDefault() {}});
  assert.ok(picked(ov).includes('2026-11-17 – 2026-11-19') && picked(ov).includes('USD') && picked(ov).includes('未复权展示收盘价'));          // a display close says so
  slots(ov)[0].listeners.keydown({key: 'Home', preventDefault() {}});
  assert.ok(picked(ov).includes('2026-08-24 – 2026-08-26') && picked(ov).includes('复权研究收盘价'));
  slots(ov)[6].listeners.click();
  assert.ok(picked(ov).includes('(NO_CLOSE_FOR_SESSION)'));                                                                                    // a session in range without a close
  assert.strictEqual(slots(ov).filter(g => g.attrs.tabindex === '0').length, 1);                                                             // one tab stop, arrows move inside
  assert.ok(!('pointerenter' in slots(ov)[9].listeners) && !('mouseenter' in slots(ov)[9].listeners));                                    // hovering never moves the selection
  slots(ov)[9].listeners.click();
  assert.ok(picked(ov).includes('这一组没有采到符合条件的帖子'));
  // the line breaks at the missing close and where the research series gives way to the display series
  assert.deepStrictEqual(svg.all(e => e.tagName === 'polyline').map(e => e.attrs.class), ['xs-c-price', 'xs-c-price']);                     // two research stretches; the one display close is a lone hollow point
  assert.strictEqual(svg.all(e => e.tagName === 'circle' && e.attrs.class === 'xs-c-dot xs-c-dot-disp').length, 1);
  const t = allText(ov);
  for (const need of ['实线和实心点：复权研究收盘价（USD）', '虚线和空心点：未复权展示收盘价（USD，只画图）', '空心点：', '休市日不在轴上', '选中一组']) assert.ok(t.includes(need), need);
  ov.all(e => e.tagName === 'button' && e.textContent === '最近 10 组')[0].listeners.click();
  const ov2 = ui.panel('overview');
  assert.strictEqual(slots(ov2).length, 10);
  assert.ok(dataOf(ov2).includes('10') && dataOf(ov2).includes(String(d.daily.length)));                                                     // shown and total
  assert.ok(!ov2.all(e => e.tagName === 'button' && e.textContent === '重点作者').length);                                                  // no watched-author scope on the chart
  assert.deepStrictEqual(leftChinese(ui), []);
});

test('history: an inconclusive return cell names the machine reason and the days behind it', async () => {
  const ui = await mount(DEID()), h_ = allText(ui.panel('history'));
  assert.ok(h_.includes('样本不足，无结论') && h_.includes('这个状态的交易日太少') && h_.includes('(TOO_FEW_STATE_DAYS)') && h_.includes('有收益的状态日'));
  assert.deepStrictEqual(leftChinese(ui), []);
});

test('overview states: empty store, failed read and a slow read never show shares', async () => {
  let ui = await mount(EMPTY);
  assert.ok(!/\d+(\.\d+)?%/.test(allText(ui.panel('overview'))) && allText(ui.panel('overview')).includes('采集端自检结果'));                   // no data: the missing inputs, no shares
  ui = await mount({state: 'READ_ERROR'});
  assert.ok(ui.state.textContent === '读取失败' && texts(ui.panel('overview')).join('|').includes('读取失败'));
  // slow: until the read model arrives, the overview has nothing that looks like a result
  const root = new El('section'); root.dataset.url = '/x-stance.json'; const state = new El('span'), ov = new El('div'); ov.dataset.xpanel = 'overview'; root.append(ov);
  for (const t of ['settings', 'simple', 'authors', 'daily', 'views', 'history']) { const p = new El('div'); p.dataset.xpanel = t; root.append(p); }
  let release; const gate = new Promise(r => { release = r; });
  const document = {getElementById: id => id === 'x-stance' ? root : id === 'x-stance-state' ? state : null, createElement: t => new El(t), querySelector: () => null, createElementNS: (_, t) => new El(t), createTextNode: t => new Text(t)};
  vm.runInNewContext(src, {document, fetch: async () => { await gate; return {ok: true, json: async () => DEID()}; }, Node: El, Set, Math, Object, String, console});
  await new Promise(r => setTimeout(r, 5));
  assert.strictEqual(ov.children.length, 0);
  release(); await new Promise(r => setTimeout(r, 5));
  assert.strictEqual(cards(ov).length, 2);
});

test('overview receipt: a FRESH receipt of a failed or partial round, a provider pause and a stale receipt are told apart', async () => {
  const cases = [[{ok: false, stage_exits: {build: 1}}, ['本轮失败'], ['本轮成功', '部分成功']],
    [{ok: false, stage_exits: {collect: 0, build: 1}}, ['本轮部分成功：汇总没有完成'], ['本轮成功']],
    [{ok: true, stage_exits: {collect: 2, build: 0}}, ['本轮部分成功：有步骤出错'], ['本轮成功']],
    [{ok: true, stage_exits: {build: 0}, collection: {status: 'PAUSED_PROVIDER_CREDITS'}, credit_hold: {reason: 'PROVIDER_CREDITS_EXHAUSTED'}}, ['本轮成功', '供应商暂停'], []],
    [{state: 'STALE', ok: true, stage_exits: {build: 0}}, ['数据过期'], []]];
  for (const [over, want, never] of cases) {
    const d = DEID(); d.updates = {...d.updates, ...over};
    const ui = await mount(d), rt = texts(ui.panel('overview').all(e => e.className === 'card xs-receipt')[0]).join('|');
    for (const w of want) assert.ok(rt.includes(w), `${JSON.stringify(over)} lacks ${w}`);
    for (const n of never) assert.ok(!rt.includes(n), `${JSON.stringify(over)} shows ${n}`);
    assert.deepStrictEqual(leftChinese(ui), []);
  }
  const ui = await mount(DEID()), rec = ui.panel('overview').all(e => e.className === 'card xs-receipt')[0];
  assert.ok(dataOf(rec).includes('2026-11-26 10:28') && rec.all(() => true).some(e => e.dataset.utc === '2026-11-26T02:28:00Z'));            // Beijing time shown, UTC kept
});

test('watched authors: a failed classification is named as such, never as neutral or "could not be judged"', async () => {
  const d = watchPayload();
  d.watchlist.reading = [{watch_id: 'watch-01', post_id: '21', created_at_utc: '2026-11-22T15:00:00Z', first_seen_at_utc: '2026-11-23T05:31:00Z', kind: 'original', lang: 'zh', text: 'synthetic post',
    url: 'https://x.com/Synth_One/status/21', likes_at_fetch: 40, sources: [], analysis: [{topic_id: 'qqq', target_id: 'QQQ', state: 'NOT_IN_ANALYSIS', reason: 'EVIDENCE_NOT_IN_TEXT', stance: null}]}];
  const ui = await mount(d), t = texts(ui.panel('authors')).join('|');
  assert.ok(t.includes('未计入：模型给出的依据在原文里找不到（分类失败）') && !t.includes('未计入：无法判定'));
  const left = [...new Set(texts(ui.root).map(x => x.trim()).filter(Boolean))].filter(x => han.test(i18n.translate(x)));
  assert.deepStrictEqual(left, []);
});

test('reading note: only for the reviewed classifier and prompt, on the cards own basis, and only next to real shares', async () => {
  const reviewed = d => Object.assign(d, {classifier: Object.assign({}, d.classifier, {model_id: 'Qwen/Qwen2.5-3B-Instruct', prompt_version: 'x-stance-simple-v1-20261123'})});
  let ui = await mount(reviewed(DEID())), t = allText(ui.panel('overview'));
  assert.ok(t.includes('读数提醒'));
  for (const need of ['在看多、看空、中性三类里', '本模型约 82% 看多，复核约 42%', '本模型约 2/3 判成了看多', '复核仍约 75% 看多', '不是人工']) assert.ok(t.includes(need), `note lacks ${need}`);
  for (const wrong of ['约 64%', '约 30%', '看多或看空']) assert.ok(!t.includes(wrong), `note still says ${wrong}`);   // the old note mixed denominators and blurred where the neutral posts went
  assert.deepStrictEqual(leftChinese(ui), []);
  for (const other of [{prompt_version: 'x-stance-simple-v2'}, {model_id: 'Qwen/Qwen2.5-7B-Instruct'}]) {
    const d = reviewed(DEID()); Object.assign(d.classifier, other);
    ui = await mount(d); assert.ok(!allText(ui.panel('overview')).includes('读数提醒'), JSON.stringify(other));
  }
  const empty = reviewed(DEID()); empty.has_data = false;
  ui = await mount(empty); assert.ok(!allText(ui.panel('overview')).includes('读数提醒'));
});

test('the translation catalog has no key defined twice (a later copy silently wins)', () => {
  const src = fs.readFileSync(path.join(core, 'i18n_v15.js'), 'utf8'), seen = new Map(), dups = [];
  src.split('\n').forEach((line, i) => {
    const m = line.match(/^\s*"((?:\\.|[^"\\])*)"\s*:/); if (!m) return;
    if (seen.has(m[1])) dups.push(`${m[1]} (lines ${seen.get(m[1])} and ${i + 1})`); else seen.set(m[1], i + 1);
  });
  assert.deepStrictEqual(dups, []);
});
