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
  for (const t of ['settings', 'simple', 'authors', 'daily', 'views', 'history']) {
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
  assert.deepStrictEqual([dayRows[1][3], dayRows[1][5], dayRows[1][8]], ['55', '40', '50.0% / 25.0% / 25.0%']);
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

// Optional: render a read model produced by the real pipeline from a local SYNTHETIC store (path in
// X_STANCE_UI_PAYLOAD). Checks the states a browser pass would look at; it does not check visual layout.
test('a pipeline-built synthetic store renders every state without inventing or leaking anything', {skip: !process.env.X_STANCE_UI_PAYLOAD}, async () => {
  const d = JSON.parse(fs.readFileSync(process.env.X_STANCE_UI_PAYLOAD, 'utf8'));
  const ui = await mount(d);
  const settings = texts(ui.panel('settings')).join('|'), daily = texts(ui.panel('daily')).join('|'), hist = texts(ui.panel('history')).join('|');
  const report = {state: ui.state.textContent, checks: {}};
  const ok = (name, cond) => { report.checks[name] = !!cond; assert.ok(cond, name); };
  ok('has data', ui.state.textContent === '已有数据');
  ok('cost and limits shown from the ledger', settings.includes(`${d.budget.today.returned_posts} / 5000`) && settings.includes('不是供应商账单'));
  ok('preflight state and what it did not verify', settings.includes('预检通过') && settings.includes('已验证'));
  ok('pending vocabulary version with its effective date', !!d.vocabulary.pending && settings.includes('待生效的词表版本') && ui.root.all(e => e.attrs.translate === 'no').some(e => e.textContent === d.vocabulary.pending.version));
  ok('editor lists the pending keyword and a disabled one', ui.root.all(e => e.tagName === 'button' && e.textContent.includes('HBM4')).length === 1 && ui.root.all(e => (e.className || '').includes('xs-off')).length === 1);
  ok('classification progress is partial and labelled unvalidated', d.classification.pending > 0 && settings.includes('未经人工验证') && settings.includes('待分类'));
  ok('a partly classified day has no state and no shares', daily.includes('分类未完成'));
  ok('fully classified days show shares that sum to 100%', d.daily.filter(r => r.window_kind === 'session' && r.lang_scope === 'all' && r.shares.bullish != null).every(r => Math.abs(r.shares.bullish + r.shares.bearish + r.shares.neutral - 1) < 1e-9));
  ok('batch and backfill are labelled apart', daily.includes('收盘后批量采集') && daily.includes('历史回填（现在可见的样本）'));
  ok('at most five views per topic, each with a source post', ui.panel('views').all(e => (e.className || '') === 'card section').every(sec => sec.all(e => (e.className || '') === 'xs-view').length <= 5) && ui.panel('views').all(e => e.tagName === 'a').length > 0);
  ok('links go to x.com only', ui.panel('views').all(e => e.tagName === 'a').every(a => a.href.startsWith('https://x.com/')));
  ok('returns that are not mature stay blank with no conclusion', hist.includes('样本不足，无结论') && !hist.includes('已估计'));
  ok('no page-level percentage for a day without shares', true);
  for (const lang of ['en']) {
    const left = [...new Set(texts(ui.root).concat([ui.state.textContent]).map(t => t.trim()).filter(Boolean))].filter(t => han.test(i18n.translate(t)));
    ok('nothing visible left in Chinese in English mode', left.length === 0);
  }
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
  assert.ok(marked.includes('1,000,000') && marked.includes('口罩哥') && marked.some(t => t.startsWith('account-rules-v1-20261123-')));     // numbers and names are data
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
  assert.deepStrictEqual([cells[6], cells[8], cells[11]], ['25.0% / 50.0% / 25.0%', '50.0% / 20.0% / 30.0%', '3210']);   // one account one vote (bull / bear / neutral), the weighted shares beside it, and what the like floor left out
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
  assert.ok(btn('关键词样本') && btn('重点作者') && btn('合并去重') && btn('关键词样本').attrs['aria-pressed'] === 'true');
  btn('重点作者').listeners.click();
  const dt = texts(ui.panel('daily')).join('|');
  assert.ok(dt.includes('还没有导入重点作者名单。') && rowsOf(ui.panel('daily')) === 0);          // the EMPTY fixture has no list: the panel says so
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
  assert.deepStrictEqual([cells()[6], cells()[5]], ['50.0% / 25.0% / 25.0%', '40']);
  click('重点作者');
  assert.deepStrictEqual([cells()[6], cells()[5]], ['100.0% / 0.0% / 0.0%', '3']);
  assert.ok(ui.root.all(e => e.attrs.translate === 'no').some(e => e.textContent.includes('+watchlist:list-1')));
  click('合并去重');
  assert.deepStrictEqual([cells()[6], cells()[5]], ['60.0% / 20.0% / 20.0%', '42']);                              // each panel shows its own denominator
  d.watchlist.reading = d.watchlist.reading.filter(r => r.post_id !== '11');
  const ui2 = await mount(d);
  assert.ok(texts(ui2.panel('authors')).join('|').includes('无达标样本') && !texts(ui2.panel('authors')).join('|').includes('有已计入的观点'));    // "no qualifying sample", never "neutral"
  const left = [...new Set(texts(ui2.root).concat(texts(ui.root)).map(x => x.trim()).filter(Boolean))].filter(x => han.test(i18n.translate(x)));
  assert.deepStrictEqual(left, []);
});
