// Run: node --test tests/ui_conn_i18n_news.cjs
// 2026-11-21: quote connection layers, complete English display, news title entities and stale state.
// Uses the production files with DOM / clock / network doubles. No supplier, no server.
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const {test} = require('node:test');
const core = path.join(__dirname, '../apps/quant_portal/static/core');
const tpl = path.join(__dirname, '../apps/quant_portal/templates');
const i18n = require(path.join(core, 'i18n_v15.js'));
const newsText = require(path.join(core, 'news_text_v1.js'));
const han = /[㐀-鿿]/;
const BS = String.fromCharCode(92);

// ---------- quote connection ----------
function terminal() {
  const source = fs.readFileSync(path.join(core, 'terminal_v20.js'), 'utf8');
  const nodes = new Map([['terminal-root', {dataset: {timeZone: 'UTC', timelineUrl: '/timeline', watchlist: '', feedEnabled: '0'}}]]);
  let now = Date.parse('2026-11-20T15:00:00Z');
  class Clock extends Date { constructor(...a) { if (a.length) super(...a); else super(now); } static now() { return now; } }
  const context = {Date: Clock, Intl, Number, Set, Map, JSON, Math, String, encodeURIComponent,
    document: {getElementById: id => nodes.get(id) || null, querySelectorAll: () => [], addEventListener() {}},
    window: {addEventListener() {}}, localStorage: {getItem() { return null; }, setItem() {}},
    setInterval() {}, setTimeout() { return 1; }, clearTimeout() {}, AbortController, fetch: () => new Promise(() => {})};
  vm.createContext(context);
  const cut = source.indexOf('  window.quantSetSymbols?.');
  assert.ok(cut > 0);
  vm.runInContext(source.slice(0, cut) + `
    drawChart = () => {};
    globalThis.audit = {connState, connLayers, connDetail, setFeed(value, received, failed = false) {feed = value; lastFeedOk = received; feedError = failed;}};
  })();`, context);
  const api = context.audit;
  const feed = (state, extra = {}) => ({states: [{provider: 'moomoo', worker_alive: 1, state, heartbeat_at: '2026-11-20T14:59:58Z', last_received_at: '2026-11-20T10:52:00Z', ...extra}]});
  return {api, feed, now: () => now, advance: n => { now += n; }, poll: (state, extra) => api.setFeed(feed(state, extra), now)};
}

test('each failing layer has its own label; they are no longer one "disconnected"', () => {
  const t = terminal();
  t.poll('CONNECTED_DELAY_UNKNOWN');
  assert.deepEqual({...t.api.connState()}, {state: 'ok', text: '行情已连接', layer: null});
  t.poll('DISCONNECTED', {last_error_class: 'OperationalError'});
  assert.deepEqual({...t.api.connState()}, {state: 'down', text: '行情连接中断', layer: 'provider'});
  t.poll('AUTH_EXPIRED');
  assert.deepEqual({...t.api.connState()}, {state: 'down', text: '授权已过期', layer: 'auth'});
  t.poll('CONNECTED', {worker_alive: 0});
  assert.equal(t.api.connState().text, '采集进程中断');
  t.poll('CONNECTED', {heartbeat_stale: true});
  assert.equal(t.api.connState().layer, 'collector');
  t.poll('CONNECTED'); t.advance(16000);
  assert.deepEqual({...t.api.connState()}, {state: 'down', text: '页面连接中断', layer: 'page'});      // the browser's own polling, not the supplier
  t.api.setFeed(null, 0, true);
  assert.equal(t.api.connState().layer, 'page');
  const labels = ['行情已连接', '行情连接中断', '授权已过期', '采集进程中断', '页面连接中断', '重新连接中', '行情未启用', '未在更新'];
  assert.deepEqual(labels.map(i18n.translate), ['Quotes connected', 'Disconnected', 'Authorization expired', 'Collector stopped', 'Page offline', 'Reconnecting', 'Quotes disabled', 'Not updating']);
});

test('a connect-then-drop loop (the 2026-11-20 incident) reads as reconnecting, never as connected', () => {
  const t = terminal();
  for (let i = 0; i < 6; i++) {                                       // the recorded cycle: connecting -> connected for ~7 s -> disconnected, every ~15 s
    t.poll('CONNECTING'); assert.equal(t.api.connState().text, '重新连接中'); t.advance(5000);
    t.poll('CONNECTED_DELAY_UNKNOWN'); assert.equal(t.api.connState().text, '重新连接中'); assert.equal(t.api.connState().state, 'down'); t.advance(5000);
    t.poll('DISCONNECTED', {last_error_class: 'OperationalError'}); assert.equal(t.api.connState().text, '行情连接中断'); t.advance(5000);
  }
  for (let s = 5; s < 30; s += 5) { t.poll('CONNECTED_DELAY_UNKNOWN'); assert.equal(t.api.connState().state, 'down'); t.advance(5000); }
  t.poll('CONNECTED_DELAY_UNKNOWN');                                  // connected for 30 s without a drop: now it is a real recovery
  assert.equal(t.api.connState().state, 'ok');
});

test('a closed market or an old quote is not a transport failure, and the detail rows name the reason and the last receipt', () => {
  const t = terminal();
  t.poll('CONNECTED', {market_state: 'US_CLOSED', last_received_at: '2026-11-18T21:00:00Z'});
  assert.equal(t.api.connState().state, 'ok');
  t.poll('DISCONNECTED', {last_error_class: 'OperationalError'});
  const d = t.api.connDetail(t.api.connState());
  assert.equal(JSON.stringify(d.rows.map(r => [r[0], r[1]])), JSON.stringify([['页面读取', '正常'], ['采集进程', '运行中'], ['供应商连接', '已断开']]));
  assert.match(d.rows[2][2], /最近收到行情 11\/20 10:52:00 · 原因 OperationalError/);
  assert.match(d.action, /自动重连/);
  for (const s of [...d.rows.flat(), d.action]) assert.equal(han.test(i18n.translate(s)), false, s);
  t.poll('CONNECTED'); t.advance(16000);
  assert.match(t.api.connDetail(t.api.connState()).action, /SSH 隧道/);
});

// ---------- English display ----------
function stripComments(src) {
  let o = '', i = 0, q = null;
  while (i < src.length) {
    const c = src[i], d = src[i + 1];
    if (q) { o += c; if (c === BS) { o += d; i += 2; continue; } if (c === q) q = null; i++; continue; }
    if (c === "'" || c === '"' || c === '`') { q = c; o += c; i++; continue; }
    if (c === '/' && d === '*') { const e = src.indexOf('*/', i + 2); i = e < 0 ? src.length : e + 2; continue; }
    if (c === '/' && d === '/') { const e = src.indexOf('\n', i); i = e < 0 ? src.length : e; continue; }
    o += c; i++;
  }
  return o;
}
function strings(src) {
  const res = []; let i = 0;
  while (i < src.length) {
    const c = src[i];
    if (c === "'" || c === '"' || c === '`') {
      let j = i + 1, s = '';
      while (j < src.length && src[j] !== c) { if (src[j] === BS) { s += src[j] + src[j + 1]; j += 2; } else { s += src[j]; j++; } }
      res.push(s); i = j + 1;
      // a template literal can hold further string literals inside its ${ } expressions (a conditional suffix, a
      // fallback word): those are separate pieces of UI text and must be checked too
      if (c === '`') {
        let k = 0;
        while ((k = s.indexOf('${', k)) >= 0) {
          let depth = 1, e = k + 2;
          while (e < s.length && depth) { if (s[e] === '{') depth++; else if (s[e] === '}') depth--; e++; }
          res.push(...strings(s.slice(k + 2, e - 1))); k = e;
        }
      }
    } else i++;
  }
  return res;
}
function uiLiterals() {
  const out = new Map(), add = (s, f) => { s = s.trim(); if (s && han.test(s)) out.set(s, f); };
  for (const f of fs.readdirSync(core).filter(f => f.endsWith('.js') && f !== 'i18n_v15.js'))
    for (const s of strings(stripComments(fs.readFileSync(path.join(core, f), 'utf8')))) add(s, f);
  const templates = [];                                                 // every template directory (core, registration, ...), not only core
  (function walk(dir, rel) { for (const e of fs.readdirSync(dir, {withFileTypes: true})) { if (e.isDirectory()) walk(path.join(dir, e.name), rel + e.name + '/'); else if (e.name.endsWith('.html')) templates.push(rel + e.name); } })(tpl, '');
  for (const f of templates) {
    const src = fs.readFileSync(path.join(tpl, f), 'utf8').replace(/{#[^]*?#}/g, '').replace(/<script[^]*?<\/script>/g, '').replace(/<style[^]*?<\/style>/g, '').replace(/<!--[^]*?-->/g, '');
    for (const m of src.matchAll(/>([^<>]*)</g)) for (const piece of m[1].split(/{%[^%]*%}|{{[^}]*}}/)) add(piece.replace(/[ \t\r\n]+/g, ' '), f);
    for (const m of src.matchAll(/(?:placeholder|title|aria-label|alt)="([^"]*)"/g)) for (const piece of m[1].split(/{%[^%]*%}|{{[^}]*}}/)) add(piece, f);
    for (const m of src.matchAll(/{% block title %}([^{]*){% endblock %}/g)) add(m[1], f);          // page titles are product text too
  }
  return out;
}

test('every Chinese string in the UI sources has a complete English rendering', () => {
  const allowed = new Set(['中文']);                                   // the language switch names the language in that language
  const missing = [];
  for (const [lit, file] of uiLiterals()) {
    if (allowed.has(lit)) continue;
    let sample = '', k = 0;                                           // replace each ${ } expression (nested braces included) by a number
    while (k < lit.length) {
      if (lit[k] === '$' && lit[k + 1] === '{') { let depth = 1, e = k + 2; while (e < lit.length && depth) { if (lit[e] === '{') depth++; else if (lit[e] === '}') depth--; e++; } sample += '7'; k = e; }
      else sample += lit[k++];
    }
    if (han.test(i18n.translate(sample))) missing.push(`${file}: ${lit}`);
  }
  assert.deepEqual(missing, []);
  assert.ok(uiLiterals().size > 700 && Object.keys(i18n.catalog).length > 900);
});

test('the labels left in Chinese in the reported screenshot are English now', () => {
  const cases = {'搜索标的': 'Search symbols', '自选行情': 'Watchlist', '连接中断': 'Disconnected', '盘前涨跌': 'Pre-market change', '更新 05:52:02': 'Updated 05:52:02',
    '官方公告': 'SEC filings', '8-K 重大事项报告：业绩公告': '8-K · Earnings announcement', '8-K 重大事项报告：董事/高管变动': '8-K · Director / executive changes',
    'N-CSRS 基金半年度报告': 'N-CSRS Fund semiannual report', 'N-CSR 基金年度报告': 'N-CSR Fund annual report', '详情': 'Details', '信托级': 'Trust-level'};
  for (const [zh, en] of Object.entries(cases)) assert.equal(i18n.translate(zh), en);
});

test('a negative or qualified research statement stays negative or qualified in English', () => {
  assert.equal(i18n.translate('未通过收益筛选'), 'Did not pass the return screen');
  assert.equal(i18n.translate('12 项主检验 0 项通过（P1–P3 相对 MA200，单边 10 bps，Holm 校正）'), '0 of 12 primary tests passed (P1–P3 vs. MA200; 10 bps one-way costs; Holm correction)');
  assert.match(i18n.translate('相对 MA200：年化差 -1.20% · Holm p 0.812 · 未通过'), /did not pass$/);
  assert.match(i18n.translate('机械复核：通过。这只说明计算可复现，不说明策略有效。'), /not strategy effectiveness/);
  for (const zh of Object.keys(i18n.catalog).filter(k => /未通过|不通过|失败|未核实|待核实|非独立|未启动|不代表|不是|没有|无法|未经/.test(k))) {
    const en = i18n.catalog[zh].toLowerCase();
    assert.ok(/\b(not|no|none|never|fail\w*|unverified|unconfirmed|without|cannot|unavailable|pending|did not|does not|neither|still need|lacks?|unknown|rejected|missing|unsubscribed|unchecked|incomplete|un\w+)\b/.test(en), `${zh} -> ${i18n.catalog[zh]}`);
  }
});

test('numbers, symbols, hashes and times stay literal; an uncovered sentence is left intact for the audit', () => {
  const v = ' NVDA 142.90 -3.08 -2.11% 05:52:02 sha256:abc123 ';
  assert.equal(i18n.translate(v), v);
  assert.equal(i18n.translate('phase5 输入快照（私有，c5b5ec22）'), 'phase5 input snapshot (private, c5b5ec22)');
  assert.equal(i18n.translate('尚未覆盖的新句子'), '尚未覆盖的新句子');           // not deleted, not half-translated
  assert.equal(i18n.translate('报价来源时间较旧'), 'Quote source time is old');      // composed pieces are separated, not fused
  for (const [zh, en] of Object.entries(i18n.catalog)) { assert.ok(zh && en, zh); assert.equal(han.test(en), false, zh); }
});

test('text, attributes and later updates are translated, originals are kept, source zones are skipped, and the audit lists what is left', () => {
  // minimal DOM double: enough for mount()
  class Text { constructor(v, p) { this.nodeType = 3; this.nodeValue = v; this.parentElement = p; } }
  class El {
    constructor(attrs = {}, skip = false) { this.nodeType = 1; this.attrs = attrs; this.childNodes = []; this.skip = skip; this.parent = null; }
    add(n) { if (n instanceof El) n.parent = this; else n.parentElement = this; this.childNodes.push(n); return n; }
    closest() { for (let e = this; e; e = e.parent) if (e.skip) return e; return null; }
    hasAttribute(n) { return n in this.attrs; } getAttribute(n) { return this.attrs[n] ?? null; } setAttribute(n, v) { this.attrs[n] = v; }
    getClientRects() { return [1]; }
    querySelectorAll() { const all = []; (function w(e) { for (const c of e.childNodes) if (c instanceof El) { all.push(c); w(c); } })(this); return all; }
  }
  const body = new El(); let observer;
  const a = body.add(new El({placeholder: '搜索标的', title: '连接中断'})); const txt = a.add(new Text(' 自选行情 ', a));
  const src = body.add(new El({}, true)); const original = src.add(new Text('原文不动的中文报道', src));
  const gap = body.add(new El()); const unknown = gap.add(new Text('一个没有翻译的新功能', gap));
  const doc = {documentElement: {dataset: {language: 'en'}}, body, defaultView: {MutationObserver: class { constructor(cb) { observer = cb; } observe() {} disconnect() {} }},
    createTreeWalker(root) { const list = []; (function w(e) { for (const c of e.childNodes) { if (c instanceof El) w(c); else list.push(c); } })(root); let i = 0; return {nextNode: () => list[i++] || null}; }};
  const m = i18n.mount(doc);
  assert.equal(txt.nodeValue, ' Watchlist '); assert.deepEqual(a.attrs, {placeholder: 'Search symbols', title: 'Disconnected'});
  assert.equal(original.nodeValue, '原文不动的中文报道'); assert.equal(unknown.nodeValue, '一个没有翻译的新功能');
  assert.deepEqual(m.audit(), ['一个没有翻译的新功能']);                          // the gap is reported, the source zone is not
  txt.nodeValue = '更新 05:52:02'; observer([{type: 'characterData', target: txt}]);
  assert.equal(txt.nodeValue, 'Updated 05:52:02');
  a.attrs.title = '行情已连接'; observer([{type: 'attributes', target: a}]);
  assert.equal(a.attrs.title, 'Quotes connected');
  const late = new El({'aria-label': '筛选时间范围'}); late.add(new Text('读取中…', late)); body.add(late); observer([{type: 'childList', addedNodes: [late]}]);
  assert.equal(late.attrs['aria-label'], 'Filter by time range'); assert.equal(han.test(late.childNodes[0].nodeValue), false);
});

test('the templates no longer hide whole modules from translation', () => {
  const noTranslate = [];
  for (const f of fs.readdirSync(path.join(tpl, 'core'))) for (const m of fs.readFileSync(path.join(tpl, 'core', f), 'utf8').matchAll(/<(\w+)[^>]*translate="no"[^>]*>/g)) noTranslate.push(`${f}: ${m[0].slice(0, 60)}`);
  assert.equal(noTranslate.length, 1); assert.match(noTranslate[0], /base\.html: <form method="post"/);    // only the language switch
});

// ---------- news ----------
class Element {
  constructor() { this.dataset = {}; this.children = []; this.textContent = ''; }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = [...nodes]; }
  addEventListener() {} setAttribute() {} querySelectorAll() { return []; }
  set innerHTML(_) { throw new Error('a title must not be assigned as HTML'); }
}
const settle = () => new Promise(resolve => setImmediate(resolve));
function news() {
  const overview = new Element(); overview.dataset.newsUrl = '/news.json';
  const callbacks = []; let fail = false, raw = null;
  const context = {document: {getElementById: id => id === 'ov-news2' ? overview : null, createElement: () => new Element()},
    window: {quantNewsText: newsText}, Intl, Date, Set, Map, String, URL, JSON, Number, localStorage: {getItem() { return null; }, setItem() {}},
    setInterval: fn => callbacks.push(fn),
    fetch: async () => { if (fail) throw new Error('Network unavailable'); return {ok: true, json: async () => ({items: raw ? [raw] : [], providers: [], connection: {label: 'Connected', kind: 'ok'}})}; }};
  vm.createContext(context); vm.runInContext(fs.readFileSync(path.join(core, 'news_v20.js'), 'utf8'), context);
  return {overview, setItem: v => { raw = v; }, fail: () => { fail = true; }, update: () => callbacks[0]()};
}

test('a title is decoded once for display through textContent; the stored text is untouched', async () => {
  assert.equal(newsText.decodeEntities('&#39;2028 Can Wait,&#39; Party Must &#39;Earn Back&#39; Americans&#39; Trust &amp; Results'), "'2028 Can Wait,' Party Must 'Earn Back' Americans' Trust & Results");
  assert.equal(newsText.decodeEntities('&amp;#39;'), '&#39;');                      // once, not until it "looks right"
  assert.equal(newsText.decodeEntities('A & B &unknown; &#x110000;'), 'A & B &unknown; �');
  const h = news(); await settle();
  const raw = {upstream: 'benzinga', upstream_id: 'fixture', latest_version: 1, title: 'Nikki Haley: &#39;2028 Can Wait&#39; &lt;b&gt; &amp; Results', source: 'Benzinga', relevance: {}};
  h.setItem(raw); await h.update();
  assert.equal(h.overview.children[0].children[0].textContent, "Nikki Haley: '2028 Can Wait' <b> & Results");
  assert.equal(raw.title, 'Nikki Haley: &#39;2028 Can Wait&#39; &lt;b&gt; &amp; Results');
  const src = fs.readFileSync(path.join(core, 'news_v20.js'), 'utf8');
  assert.equal((src.match(/title\(it\)/g) || []).length, 3); assert.equal(/it\.title \|\| '\(无标题\)'\)/.test(src.replace("decodeEntities(it.title || '(无标题)')", '')), false);   // overview, list and reader
});

test('after one good read, a failed read keeps the news on screen but the state becomes down', async () => {
  const h = news(); await settle();
  h.setItem({upstream: 'benzinga', upstream_id: 'fixture', latest_version: 1, title: 'Cached headline', source: 'Benzinga', relevance: {}});
  await h.update(); h.fail(); await h.update();
  assert.equal(h.overview.children[0].children[0].textContent, 'Cached headline');
  const sig = JSON.parse(h.overview.dataset.sig);
  assert.equal(sig[0].kind, 'down'); assert.match(sig[0].label, /新闻读取失败/);
  assert.equal(i18n.translate('新闻读取失败：Network unavailable'), 'Could not load news: Network unavailable');
});
