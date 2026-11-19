// Run: node tests/terminal_v20_regression.cjs [path/to/terminal_v20.js]
// Tests the production closure with network and clock doubles; no supplier calls.
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const {test} = require('node:test');
const source = fs.readFileSync(process.argv[2] || path.join(__dirname, '../apps/quant_portal/static/core/terminal_v20.js'), 'utf8');
function harness() {
  const requests = [], intervals = [], timers = [];
  const nodes = new Map([['terminal-root', {dataset: {timeZone: 'UTC', timelineUrl: '/timeline', watchlist: '', feedEnabled: '0'}}],
    ['chart-state', {hidden: true, textContent: ''}], ['chart-coverage', {textContent: ''}]]);
  let now = Date.parse('2026-11-19T00:00:00Z');
  class Clock extends Date { static now() { return now; } }
  const context = {Date: Clock, Intl, Number, Set, Map, JSON, Math, String, encodeURIComponent,
    document: {getElementById: id => nodes.get(id) || null, querySelectorAll: () => [], addEventListener() {}},
    window: {addEventListener() {}}, localStorage: {getItem() { return null; }, setItem() {}},
    setInterval: fn => intervals.push(fn), AbortController,
    setTimeout: (fn, ms) => { timers.push({fn, ms}); return timers.length; }, clearTimeout: id => { if (timers[id - 1]) timers[id - 1].fn = () => {}; },
    fetch: (url, opts) => new Promise((resolve, reject) => { requests.push({url, resolve});
      opts?.signal?.addEventListener('abort', () => reject(new Error('aborted'))); }),
  };
  vm.createContext(context);
  const cut = source.indexOf('  window.quantSetSymbols?.');
  assert.ok(cut > 0, 'production bootstrap marker exists');
  vm.runInContext(source.slice(0, cut) + `
    drawChart = () => {};
    globalThis.audit = {chart, loadChart, loadEarliest, connState,
      setFeed(value, received, failed = false) {feed=value; lastFeedOk=received; feedError=failed;}};
  })();`, context);
  const reply = (index, data = {points: []}) => requests[index].resolve({ok: true, json: async () => data});
  return {api: context.audit, requests, intervals, timers, reply, now: () => now, advance: n => {now += n;}};
}
const settle = () => new Promise(resolve => setImmediate(resolve));
test('switching symbols while a request is pending fetches the new symbol immediately after it settles', async () => {
  const h = harness(); h.api.chart.sym = 'NVDA';
  const first = h.api.loadChart(true);
  h.api.chart.sym = 'MU'; await h.api.loadChart(true);
  h.reply(0); await first; await settle();
  assert.equal(h.requests.length, 2);
  assert.match(h.requests[1].url, /symbol=MU/);
  h.reply(1, {points: [{source_at: '2026-11-18T23:59:00Z', price_text: '10', session: 'REGULAR'}]});
  await settle(); assert.equal(h.api.chart.data[0].p, 10);
});
test('switching ranges while a request is pending fetches the final range', async () => {
  const h = harness(); h.api.chart.sym = 'NVDA';
  const first = h.api.loadChart(true);
  h.api.chart.range = '30m'; await h.api.loadChart(true);
  h.reply(0); await first; await settle();
  assert.equal(h.requests.length, 2); assert.match(h.requests[1].url, /range=30m/);
  h.reply(1); await settle();
});
test('late coverage response for a previous symbol cannot reset the current range', async () => {
  const h = harness(); h.api.chart.sym = 'NVDA'; h.api.chart.range = '24h';
  h.api.chart.earliest.set('NVDA', h.now() - 3600e3);
  const old = h.api.loadEarliest('MU');
  h.reply(0, {points: [{source_at: '2026-11-18T23:59:00Z', price_text: '10', session: 'REGULAR'}]});
  await old; assert.equal(h.api.chart.range, '24h');
});
test('a hung poll cannot leave connection green after its freshness deadline', () => {
  const h = harness();
  h.api.setFeed({states: [{provider: 'moomoo', worker_alive: 1, state: 'CONNECTED'}]}, h.now());
  assert.equal(h.api.connState().state, 'ok');
  h.advance(16000); assert.equal(h.api.connState().state, 'down');
});
test('an initial failed request is still reported as down', () => {
  const h = harness(); h.api.setFeed(null, 0, true);
  assert.equal(h.api.connState().state, 'down');
});
test('a hung chart request is aborted by its timeout and the pending selection is then fetched', async () => {
  const h = harness(); h.api.chart.sym = 'NVDA';
  const first = h.api.loadChart(true);
  h.api.chart.sym = 'MU'; await h.api.loadChart(true);
  assert.equal(h.requests.length, 1);
  const t = h.timers.find(x => x.ms === 20e3); assert.ok(t, 'chart request has a bounded timeout');
  t.fn(); await first; await settle();
  assert.equal(h.api.chart.loading, true, 'pending reload started after abort');
  assert.equal(h.requests.length, 2); assert.match(h.requests[1].url, /symbol=MU/);
});
