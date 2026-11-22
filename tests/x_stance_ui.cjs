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
  for (const t of ['settings', 'daily', 'views', 'history']) {
    const b = new El('button'); b.dataset.xtab = t; root.append(b);
    const p = new El('div'); p.dataset.xpanel = t; p.hidden = t !== 'settings'; root.append(p);
  }
  const document = {getElementById: id => id === 'x-stance' ? root : id === 'x-stance-state' ? state : null, createElement: t => new El(t),
    createElementNS: (_, t) => new El(t), createTextNode: t => new Text(t)};
  const done = new Promise(res => {
    const fetch = async () => ({ok: true, json: async () => { setTimeout(res, 0); return payload; }});
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
    for (const need of ['供应商密钥', '实采预检', '分类模型的固定版本', '行情输入版本', '留出期起点']) assert.ok(t.includes(need), `${p} lacks ${need}`);
    assert.ok(!/\d+(\.\d+)?%/.test(t), `${p} shows a percentage without data`);
  }
  const s = texts(ui.panel('settings')).join('|');
  for (const need of ['尚未采集', '未开启', '未固定版本，不运行', '缺少供应商密钥', '尚未通过实采预检', '未配置（仅统计行业观点）', '计划抽样，不是全量', '0 / 5000', '0 / 0.75', '0 / 3000', '0 / 2000', '0 / 22.5', '0 / 200', '未运行', '不是供应商账单']) assert.ok(s.includes(need), need);
  assert.strictEqual(rowsOf(ui.panel('settings')), 1 + 4);                       // header + the four configured topics
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
  assert.ok(daily.includes('采集不完整') && daily.includes('样本账号过少') && daily.includes('未判定') && daily.includes('分类未完成') && daily.includes('本次采集样本中的账号比例'));
  const pendingCells = ui.panel('daily').all(e => e.tagName === 'tr').find(r => r.textContent.includes('2025-06-05')).all(e => e.tagName === 'td').map(c => c.textContent);
  assert.deepStrictEqual(pendingCells.slice(2, 8), ['—', '—', '—', '0', '60', '0.0%']);      // shares blank, sample accounts shown, progress 0 %: nothing invented
  const unknownRow = ui.panel('daily').all(e => e.tagName === 'tr').find(r => r.textContent.includes('2025-06-03'));
  assert.deepStrictEqual(unknownRow.all(e => e.tagName === 'td').map(c => c.textContent).slice(2, 5), ['—', '—', '—']);   // no 0 %, no neutral, for a day without a sample
  assert.strictEqual(ui.panel('daily').all(e => (e.className || '').includes('xs-bar-empty')).length, 2);
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
