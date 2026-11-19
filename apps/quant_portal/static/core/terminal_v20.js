/* Quant terminal v20 UI. Reads only same-origin JSON; never calls a provider.
 * Quote data arrives through the 'quant-feed' event broadcast by personal_feed.js (one poll per page).
 * Prices keep supplier precision underneath; display rounds to 2 dp. No interpolation of gaps. */
(() => {
  'use strict';
  const root = document.getElementById('terminal-root');
  if (!root) return;
  const tz = root.dataset.timeZone || 'Asia/Shanghai';
  const feedEnabled = root.dataset.feedEnabled === '1';
  const watch = (root.dataset.watchlist || '').split(',').filter(Boolean);
  const PRIORITY = ['TQQQ', 'QQQ', 'SOXX', 'SPY', 'NVDA', 'MU'];
  const SESS = {PREMARKET: '盘前', REGULAR: '常规', AFTERHOURS: '盘后', OVERNIGHT: '夜盘'};
  const SESS_COLOR = {PREMARKET: 'var(--s-pre)', REGULAR: 'var(--s-reg)', AFTERHOURS: 'var(--s-after)', OVERNIGHT: 'var(--s-night)'};
  const BASIS = {REGULAR_LAST_SAME_MESSAGE: '较常规时段价', PREV_CLOSE_SAME_MESSAGE: '较昨收'};
  // Short, always-visible comparison label (no hover): session move vs regular price, or vs previous close.
  const basisLabel = (c, q) => !c ? '' : c.base_kind === 'PREV_CLOSE_SAME_MESSAGE' ? '较昨收'
    : ({AFTERHOURS: '盘后涨跌', PREMARKET: '盘前涨跌', OVERNIGHT: '夜盘涨跌'}[q?.session] || '较常规时段价');
  const VIEWS = {overview: '总览', markets: '行情', news: '新闻', research: '研究', settings: '设置'};
  const RANGE_MS = {'30m': 30 * 60e3, '2h': 2 * 3600e3, '24h': 24 * 3600e3, all: 7 * 24 * 3600e3};
  const store = {
    get: k => { try { return localStorage.getItem(k); } catch (_) { return null; } },
    set: (k, v) => { try { localStorage.setItem(k, v); } catch (_) { /* optional */ } },
  };
  const $ = id => document.getElementById(id);
  const h = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; };
  // Bounded same-origin fetch: a hung request is aborted so later reloads are not blocked forever.
  const timedFetch = (url, ms, external) => {
    const opts = {credentials: 'same-origin', cache: 'no-store'};
    if (typeof AbortController === 'undefined' || typeof setTimeout === 'undefined') return fetch(url, opts);
    const ac = external || new AbortController(); const timer = setTimeout(() => ac.abort(), ms);
    return fetch(url, {...opts, signal: ac.signal}).finally(() => clearTimeout(timer));
  };
  const svgNS = 'http://www.w3.org/2000/svg';
  const s = (tag, attrs) => { const n = document.createElementNS(svgNS, tag); for (const k in attrs || {}) n.setAttribute(k, attrs[k]); return n; };

  // ---------- formatting ----------
  const nf2 = new Intl.NumberFormat('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2});
  const fmtT = new Intl.DateTimeFormat('zh-CN', {timeZone: tz, hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false});
  const fmtHM = new Intl.DateTimeFormat('zh-CN', {timeZone: tz, hour: '2-digit', minute: '2-digit', hour12: false});
  const fmtMDHM = new Intl.DateTimeFormat('zh-CN', {timeZone: tz, month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false});
  const fmtMDHMS = new Intl.DateTimeFormat('zh-CN', {timeZone: tz, month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false});
  const fmtD = new Intl.DateTimeFormat('zh-CN', {timeZone: tz, year: 'numeric', month: '2-digit', day: '2-digit'});
  const ms = iso => { const t = Date.parse(iso || ''); return Number.isFinite(t) ? t : null; };
  const timeOf = iso => { const t = ms(iso); return t == null ? '' : fmtT.format(t); };
  const money = (text, currency) => {
    const n = Number(text);
    if (!Number.isFinite(n)) return '—';
    return currency === 'USD' ? `$${nf2.format(n)}` : nf2.format(n);
  };
  const signed = text => { const n = Number(text); return !Number.isFinite(n) ? '' : (n > 0 ? '+' : '') + nf2.format(n); };
  const dirOf = text => { const n = Number(text); return !Number.isFinite(n) || n === 0 ? 'flat' : n > 0 ? 'rise' : 'fall'; };

  // ---------- state ----------
  let feed = null;
  let lastFeedOk = 0;
  let feedError = false;
  const cards = new Map();
  const listItems = new Map();
  const spark = new Map();          // symbol -> [{t,p,seg,s}]
  let selected = store.get('quant.selected') || null;
  let currentView = 'overview';

  const symbols = () => {
    const set = new Set(watch);
    const ordered = PRIORITY.filter(x => set.has(x));
    return [...ordered, ...watch.filter(x => !ordered.includes(x))];
  };
  const metaOf = sym => feed?.instrument_metadata?.[sym] || null;
  const nameOf = sym => metaOf(sym)?.short_name || feed?.security_profiles?.[sym]?.name || feed?.latest_quotes?.[sym]?.display_name || '';
  const quoteOf = sym => feed?.latest_quotes?.[sym] || null;
  const changeOf = sym => feed?.quote_changes?.[sym] || null;
  const currencyOf = sym => quoteOf(sym)?.currency || metaOf(sym)?.currency || null;

  // ---------- connection ----------
  function connState() {
    if (!feed && !feedError) return {state: 'idle', text: ''};
    if ((feed || feedError) && Date.now() - lastFeedOk > 15e3) return {state: 'down', text: '连接中断'};
    if (feed?.provider_gates?.moomoo) return {state: 'idle', text: '行情未启用'};
    const m = (feed?.states || []).find(x => x.provider === 'moomoo');
    if (!m) return {state: 'idle', text: ''};
    if (m.state === 'AUTH_EXPIRED') return {state: 'down', text: '授权已过期'};
    if (m.worker_alive !== 1 || m.state === 'DISCONNECTED' || m.heartbeat_stale) return {state: 'down', text: '连接中断'};
    return {state: 'ok', text: '行情已连接'};
  }
  function renderConn() {
    const c = connState();
    const el = $('conn');
    if (el) { el.dataset.state = c.state; el.querySelector('.conn-text').textContent = c.text; el.title = c.text; }
    return c;
  }

  // ---------- quote cards ----------
  function buildCard(sym) {
    const card = h('button', 'qcard'); card.type = 'button'; card.dataset.symbol = sym;
    const id = h('div', 'q-id'); const symEl = h('span', 'q-sym', sym); const nameEl = h('span', 'q-name'); id.append(symEl, nameEl);
    const row = h('div', 'q-price-row'); const price = h('span', 'q-price', '—'); const chg = h('span', 'q-change'); const basis = h('span', 'q-basis'); row.append(price, chg, basis);
    const sp = s('svg', {class: 'q-spark', viewBox: '0 0 112 46', preserveAspectRatio: 'none', 'aria-hidden': 'true'});
    const meta = h('div', 'q-meta'); const sess = h('span', 'sess'); const stale = h('span', 'stale-tag', '连接中断'); stale.hidden = true;
    const time = h('span', 'q-time'); meta.append(sess, stale, time);
    card.append(id, row, sp, meta);
    card.addEventListener('click', () => { location.hash = `#markets/${sym}`; });
    const ref = {card, nameEl, price, chg, basis, sp, sess, stale, time, last: null};
    cards.set(sym, ref);
    return ref;
  }
  function setText(el, text) { if (el.textContent !== text) el.textContent = text; }
  function updateCard(sym, down) {
    const ref = cards.get(sym) || buildCard(sym);
    const q = quoteOf(sym); const c = changeOf(sym); const cur = currencyOf(sym);
    setText(ref.nameEl, nameOf(sym));
    const priceText = q ? money(q.price_text, cur) : '—';
    if (ref.last && q && ref.last !== q.price_text && !down) {
      const up = Number(q.price_text) > Number(ref.last);
      ref.card.classList.remove('flash-up', 'flash-down'); void ref.card.offsetWidth;
      ref.card.classList.add(up ? 'flash-up' : 'flash-down');
    }
    ref.last = q?.price_text || null;
    setText(ref.price, priceText);
    ref.price.title = q ? `精确值 ${q.price_text}${cur ? ' ' + cur : ''}` : '';
    if (c) {
      setText(ref.chg, `${signed(c.change_text)}  ${signed(c.change_pct_text)}%`);
      ref.chg.className = `q-change ${dirOf(c.change_text)}`;
      ref.chg.title = `${BASIS[c.base_kind] || '较参考价'} ${money(c.base_text, cur)}`;
      setText(ref.basis, basisLabel(c, q));
    } else { setText(ref.chg, ''); ref.chg.title = ''; setText(ref.basis, ''); }
    const sess = q?.session;
    if (SESS[sess]) { ref.sess.hidden = false; ref.sess.dataset.s = sess; setText(ref.sess, SESS[sess]); } else ref.sess.hidden = true;
    setText(ref.time, q?.source_at ? `更新 ${timeOf(q.source_at)}` : '');
    ref.stale.hidden = !down;
    ref.card.classList.toggle('is-stale', down);
    ref.card.classList.toggle('is-selected', currentView === 'markets' && sym === selected);
    ref.card.setAttribute('aria-label', `${sym} ${priceText} ${c ? signed(c.change_pct_text) + '%' : ''}`);
    drawSpark(sym);
    return ref.card;
  }
  function renderCards(down) {
    const grid = $('quote-grid');
    if (!grid) return;
    const list = symbols().map(sym => updateCard(sym, down));
    if (grid.children.length !== list.length || [...grid.children].some((n, i) => n !== list[i])) grid.replaceChildren(...list);
    setText($('quote-count'), String(list.length));
  }

  // ---------- sparklines ----------
  function pathsFor(points, x, y, gapMs) {
    const segs = []; let cur = [];
    for (let i = 0; i < points.length; i++) {
      const p = points[i], prev = points[i - 1];
      if (prev && (p.seg !== prev.seg || p.s !== prev.s || p.t - prev.t > gapMs)) { if (cur.length) segs.push(cur); cur = []; }
      cur.push(p);
    }
    if (cur.length) segs.push(cur);
    return segs.map(seg => ({s: seg[0].s, d: seg.map((p, i) => `${i ? 'L' : 'M'}${x(p.t).toFixed(1)},${y(p.p).toFixed(1)}`).join('')}));
  }
  function drawSpark(sym) {
    const ref = cards.get(sym); if (!ref) return;
    const pts = spark.get(sym) || [];
    if (pts.length < 2) { ref.sp.replaceChildren(); return; }
    const step = Math.max(1, Math.floor(pts.length / 90));
    const sample = pts.filter((_, i) => i % step === 0 || i === pts.length - 1);
    let lo = Infinity, hi = -Infinity; for (const p of sample) { lo = Math.min(lo, p.p); hi = Math.max(hi, p.p); }
    if (hi === lo) { hi += 1e-9; lo -= 1e-9; }
    const t0 = sample[0].t, t1 = sample[sample.length - 1].t || t0 + 1;
    const x = t => 2 + (t - t0) / Math.max(1, t1 - t0) * 108, y = v => 42 - (v - lo) / (hi - lo) * 38;
    const c = changeOf(sym); const color = c ? `var(--${dirOf(c.change_text) === 'fall' ? 'fall' : dirOf(c.change_text) === 'rise' ? 'rise' : 'text-2'})` : 'var(--accent)';
    ref.sp.replaceChildren(...pathsFor(sample, x, y, 180e3).map(p => s('path', {d: p.d, stroke: color})));
  }
  // In-flight timeline requests shared by (symbol, session, range): identical queries are not re-sent.
  const inflight = new Map();
  function getTimeline(sym, range, ctrl) {
    const key = `${sym}|ALL|${range}`;
    if (inflight.has(key)) return inflight.get(key);
    const p = timedFetch(`${root.dataset.timelineUrl}?symbol=${encodeURIComponent(sym)}&session=ALL&range=${range}`, 20e3, ctrl)
      .then(r => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); })
      .finally(() => inflight.delete(key));
    inflight.set(key, p);
    return p;
  }
  const toPoints = rows => (rows || []).map(r => ({t: ms(r.source_at), p: Number(r.price_text), seg: r.segment ?? 0, s: r.session}))
    .filter(p => p.t != null && Number.isFinite(p.p)).sort((a, b) => a.t - b.t);
  async function loadSpark(sym) {
    if (!root.dataset.timelineUrl) return;
    try {
      spark.set(sym, toPoints((await getTimeline(sym, '2h')).points));
      drawSpark(sym);
    } catch (_) { /* keep last sparkline */ }
  }
  function appendLive(sym) {
    const q = quoteOf(sym); const t = ms(q?.source_at); const p = Number(q?.price_text);
    if (t == null || !Number.isFinite(p) || !SESS[q.session]) return;
    const arr = spark.get(sym); if (!arr) return;
    const last = arr[arr.length - 1];
    if (!last || t > last.t) { arr.push({t, p, seg: last && last.s === q.session ? last.seg : (last?.seg ?? 0) + 1, s: q.session}); while (arr.length && arr[0].t < t - RANGE_MS['2h']) arr.shift(); }
  }

  // ---------- markets list + detail ----------
  function renderList(down) {
    const box = $('mk-list'); if (!box) return;
    const nodes = symbols().map(sym => {
      let it = listItems.get(sym);
      if (!it) {
        const b = h('button', 'mk-item'); b.type = 'button'; b.setAttribute('role', 'option');
        const sEl = h('span', 's', sym), p = h('span', 'p num'), n = h('span', 'n'), c = h('span', 'c num');
        b.append(sEl, p, n, c); b.addEventListener('click', () => { location.hash = `#markets/${sym}`; });
        it = {b, p, n, c}; listItems.set(sym, it);
      }
      const q = quoteOf(sym), ch = changeOf(sym);
      setText(it.p, q ? money(q.price_text, currencyOf(sym)) : '—');
      setText(it.n, nameOf(sym));
      setText(it.c, ch ? `${basisLabel(ch, q).replace('涨跌', '')} ${signed(ch.change_pct_text)}%` : '');
      it.c.className = `c num ${ch ? dirOf(ch.change_text) : ''}`;
      it.b.setAttribute('aria-current', String(sym === selected));
      it.b.setAttribute('aria-selected', String(sym === selected));
      it.b.classList.toggle('is-stale', down);
      return it.b;
    });
    if (box.children.length !== nodes.length || [...box.children].some((n, i) => n !== nodes[i])) box.replaceChildren(...nodes);
    nodes.forEach(b => b.setAttribute('tabindex', b.getAttribute('aria-selected') === 'true' ? '0' : '-1'));
    if (selected !== listShown) { listShown = selected; revealSelected(); }
  }
  let listShown = null;
  // Scroll only the horizontal symbol strip (never the page) so the selected symbol is visible.
  function revealSelected() {
    const box = $('mk-list'), it = listItems.get(selected)?.b;
    if (!box || !it || box.scrollWidth <= box.clientWidth + 1) return;
    const left = it.offsetLeft - box.offsetLeft - (box.clientWidth - it.offsetWidth) / 2;
    const reduce = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
    box.scrollTo({left: Math.max(0, left), behavior: reduce ? 'auto' : 'smooth'});
  }
  function bindListKeys() {
    const box = $('mk-list'); if (!box) return;
    box.addEventListener('keydown', e => {
      const list = symbols(); const i = list.indexOf(selected); let j = null;
      if (e.key === 'ArrowRight' || e.key === 'ArrowDown') j = Math.min(list.length - 1, i + 1);
      else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') j = Math.max(0, i - 1);
      else if (e.key === 'Home') j = 0; else if (e.key === 'End') j = list.length - 1;
      if (j == null || j === i) return;
      e.preventDefault(); location.hash = `#markets/${list[j]}`;
      requestAnimationFrame(() => listItems.get(list[j])?.b.focus({preventScroll: true}));
    });
  }
  function renderDetailHead(down) {
    if (!selected || !$('d-sym')) return;
    const q = quoteOf(selected), c = changeOf(selected), cur = currencyOf(selected);
    setText($('d-sym'), selected); setText($('d-name'), nameOf(selected));
    setText($('d-price'), q ? money(q.price_text, cur) : '—');
    $('d-price').title = q ? `精确值 ${q.price_text}${cur ? ' ' + cur : ''}` : '';
    const dc = $('d-change');
    setText(dc, c ? `${signed(c.change_text)}  ${signed(c.change_pct_text)}%` : '');
    dc.className = `d-change num ${c ? dirOf(c.change_text) : ''}`;
    const se = $('d-sess');
    if (SESS[q?.session]) { se.hidden = false; se.dataset.s = q.session; setText(se, SESS[q.session]); } else se.hidden = true;
    setText($('d-basis'), c ? (c.base_kind === 'PREV_CLOSE_SAME_MESSAGE' ? `较昨收 ${money(c.base_text, cur)}`
      : `${basisLabel(c, q)} · 基准为同一报价中的常规时段价 ${money(c.base_text, cur)}，不是全天涨跌`) : '');
    setText($('d-time'), q?.source_at ? `更新 ${fmtMDHMS.format(ms(q.source_at))}` : '');
    $('d-stale').hidden = !down;
  }

  // ---------- chart ----------
  const chart = {sym: null, range: store.get('quant.range') || '2h', sessions: new Set(Object.keys(SESS)), data: [], gapMs: 180e3,
    domain: null, hoverX: null, loading: false, pendingLoad: false, ctrl: null, lastLoad: 0, earliest: new Map(), drag: null};
  function rangeAvailable(r) {
    const e = chart.earliest.get(chart.sym);
    if (e == null) return r === '30m' || r === '2h';
    const now = Date.now();
    return r === '30m' || (r === '2h' && e < now - RANGE_MS['30m']) || (r === '24h' && e < now - RANGE_MS['2h']) || (r === 'all' && e < now - RANGE_MS['24h']);
  }
  function renderRangeButtons() {
    document.querySelectorAll('#range-seg [data-range]').forEach(b => {
      b.disabled = !rangeAvailable(b.dataset.range);
      b.title = b.disabled ? '该范围内没有更早的数据' : '';
      b.setAttribute('aria-pressed', String(b.dataset.range === chart.range));
    });
  }
  async function loadEarliest(sym) {
    if (chart.earliest.has(sym) || !root.dataset.timelineUrl) return;
    try {
      const j = await getTimeline(sym, 'all');
      const firsts = (j.point_metrics?.coverage_segments || []).map(x => ms(x.first_source_at)).filter(x => x != null);
      const pts = toPoints(j.points);
      const e = firsts.length ? Math.min(...firsts) : pts[0]?.t;
      if (e != null) chart.earliest.set(sym, e);
      if (sym !== chart.sym) return;
      if (!rangeAvailable(chart.range)) chart.range = ['all', '24h', '2h', '30m'].find(rangeAvailable) || '2h';
      renderRangeButtons();
    } catch (_) { /* range buttons stay conservative */ }
  }
  async function loadChart(force) {
    if (!chart.sym || !root.dataset.timelineUrl) return;
    // The same symbol/range already in flight: wait for it rather than queue an identical reload.
    if (chart.loading) { if (force && chart.loadingKey !== `${chart.sym}|${chart.range}`) chart.pendingLoad = true; return; }
    if (!force && Date.now() - chart.lastLoad < 5e3) return;
    chart.loading = true; const sym = chart.sym, range = chart.range; chart.loadingKey = `${sym}|${range}`;
    chart.ctrl = typeof AbortController === 'function' ? new AbortController() : null;
    const state = $('chart-state');
    if (!chart.data.length) { state.hidden = false; state.textContent = '加载中…'; }
    try {
      const j = await getTimeline(sym, range, chart.ctrl);
      if (sym !== chart.sym || range !== chart.range) return;
      chart.data = toPoints(j.points); chart.gapMs = Math.max(60e3, (j.gap_seconds || 180) * 1000);
      chart.lastLoad = Date.now();
      const m = j.point_metrics || {};
      const segs = m.coverage_segments || [];
      setText($('chart-coverage'), chart.data.length ? `数据 ${fmtMDHM.format(chart.data[0].t)} – ${fmtMDHM.format(chart.data[chart.data.length - 1].t)}${m.sampled ? ` · 按图宽显示 ${chart.data.length} / ${m.valid_unique_points} 个报价` : ''}${segs.length > 1 ? ` · ${segs.length} 段` : ''}` : '');
      state.hidden = chart.data.length > 0; state.textContent = '该范围暂无报价';
      drawChart();
    } catch (_) {
      if (!chart.data.length) { state.hidden = false; state.textContent = '走势暂不可用'; }
    } finally {
      chart.loading = false; chart.ctrl = null; chart.loadingKey = null;
      if (chart.pendingLoad) { chart.pendingLoad = false; void loadChart(true); }
    }
  }
  // A selection change makes the in-flight chart request obsolete: abort it now so the
  // queued reload for the new selection is not held up by the old request's timeout.
  function cancelChart() {
    if (chart.loading && chart.loadingKey !== `${chart.sym}|${chart.range}`) { chart.pendingLoad = true; chart.ctrl?.abort(); }
  }
  function selectSymbol(sym) {
    if (!sym) return;
    const changed = sym !== chart.sym;
    selected = sym; store.set('quant.selected', sym);
    if (changed) { chart.sym = sym; chart.data = []; chart.domain = null; chart.hoverX = null; $('chart-svg')?.replaceChildren();
      setText($('chart-coverage'), ''); cancelChart(); loadEarliest(sym).then(() => loadChart(true)); }
    renderRangeButtons(); renderList(connState().state === 'down'); renderDetailHead(connState().state === 'down');
  }
  function visiblePoints() { return chart.data.filter(p => chart.sessions.has(p.s)); }
  function downsample(pts, x, width) {
    // Keep each pixel column's first, min, max and last point (time order): true extremes, no smoothing.
    if (pts.length <= width * 2) return pts;
    const out = []; let col = null, bucket = [];
    const flush = () => {
      if (!bucket.length) return;
      let mn = bucket[0], mx = bucket[0]; for (const p of bucket) { if (p.p < mn.p) mn = p; if (p.p > mx.p) mx = p; }
      const keep = [...new Set([bucket[0], mn, mx, bucket[bucket.length - 1]])].sort((a, b) => a.t - b.t);
      out.push(...keep); bucket = [];
    };
    for (const p of pts) { const c = Math.floor(x(p.t)); if (c !== col) { flush(); col = c; } bucket.push(p); }
    flush(); return out;
  }
  function niceTicks(lo, hi, n) {
    const span = hi - lo; if (!(span > 0)) return [lo];
    const step0 = span / n, mag = 10 ** Math.floor(Math.log10(step0)), err = step0 / mag;
    const step = (err >= 7.5 ? 10 : err >= 3.5 ? 5 : err >= 1.5 ? 2 : 1) * mag;
    const out = []; for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) out.push(v);
    return out;
  }
  function drawChart() {
    const svg = $('chart-svg'), wrap = $('chart'); if (!svg || !wrap) return;
    const W = wrap.clientWidth, H = wrap.clientHeight; if (!W || !H) return;
    svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
    const pad = {l: 8, r: 62, t: 10, b: 24};
    const all = visiblePoints();
    if (!all.length) { svg.replaceChildren(); const st = $('chart-state'); if (chart.data.length) { st.hidden = false; st.textContent = '已隐藏全部时段'; } return; }
    const full = [all[0].t, all[all.length - 1].t === all[0].t ? all[0].t + 60e3 : all[all.length - 1].t];
    let [d0, d1] = chart.domain || full;
    if (chart.domain) { const span = d1 - d0; if (d1 > full[1]) { d1 = full[1]; d0 = d1 - span; } if (d0 < full[0]) { d0 = full[0]; d1 = Math.min(full[1], d0 + span); } chart.domain = [d0, d1]; }
    const pts = all.filter(p => p.t >= d0 && p.t <= d1);
    const x = t => pad.l + (t - d0) / Math.max(1, d1 - d0) * (W - pad.l - pad.r);
    let lo = Infinity, hi = -Infinity; for (const p of pts) { lo = Math.min(lo, p.p); hi = Math.max(hi, p.p); }
    if (!Number.isFinite(lo)) { lo = all[all.length - 1].p; hi = lo; }
    const padV = (hi - lo) * 0.08 || Math.abs(hi) * 0.001 || 1; lo -= padV; hi += padV;
    const y = v => pad.t + (hi - v) / (hi - lo) * (H - pad.t - pad.b);
    const grid = s('g', {class: 'grid'}), axis = s('g', {class: 'axis'});
    for (const v of niceTicks(lo, hi, 5)) {
      grid.append(s('line', {x1: pad.l, x2: W - pad.r, y1: y(v), y2: y(v)}));
      const t = s('text', {x: W - pad.r + 8, y: y(v) + 4}); t.textContent = nf2.format(v); axis.append(t);
    }
    const ticks = Math.max(2, Math.floor((W - pad.l - pad.r) / 110));
    const multiDay = d1 - d0 > 20 * 3600e3;
    for (let i = 0; i <= ticks; i++) {
      const t = d0 + (d1 - d0) * i / ticks; const tx = s('text', {x: x(t), y: H - 6, 'text-anchor': i === 0 ? 'start' : i === ticks ? 'end' : 'middle'});
      tx.textContent = multiDay ? fmtMDHM.format(t) : fmtHM.format(t); axis.append(tx);
    }
    const series = s('g', {class: 'series'});
    for (const p of pathsFor(downsample(pts, x, W), x, y, chart.gapMs)) series.append(s('path', {d: p.d, stroke: SESS_COLOR[p.s] || 'var(--accent)'}));
    const cross = s('g', {class: 'cross'}); cross.style.display = 'none';
    const vline = s('line', {y1: pad.t, y2: H - pad.b}), dot = s('circle', {r: 4});
    cross.append(vline, dot);
    svg.replaceChildren(grid, axis, series, cross);
    const tip = $('chart-tip');
    const showHover = px => {
      if (px == null || !pts.length) { cross.style.display = 'none'; tip.hidden = true; return; }
      const t = d0 + (px - pad.l) / (W - pad.l - pad.r) * (d1 - d0);
      let lo2 = 0, hi2 = pts.length - 1;
      while (hi2 - lo2 > 1) { const m = (lo2 + hi2) >> 1; if (pts[m].t < t) lo2 = m; else hi2 = m; }
      const p = Math.abs(pts[lo2].t - t) <= Math.abs(pts[hi2].t - t) ? pts[lo2] : pts[hi2];
      const cx = x(p.t), cy = y(p.p);
      cross.style.display = ''; vline.setAttribute('x1', cx); vline.setAttribute('x2', cx);
      dot.setAttribute('cx', cx); dot.setAttribute('cy', cy); dot.setAttribute('fill', SESS_COLOR[p.s] || 'var(--accent)');
      const cur = currencyOf(chart.sym);
      tip.replaceChildren(h('div', 'tp', `${chart.sym}  ${money(p.p, cur)}`), h('div', 'tt', `${fmtMDHMS.format(p.t)} · ${SESS[p.s] || ''}`));
      tip.hidden = false;
      const tw = tip.offsetWidth; tip.style.left = `${Math.min(W - tw - 4, Math.max(4, cx + 12 + tw > W - pad.r ? cx - tw - 12 : cx + 12))}px`;
      tip.style.top = `${Math.max(4, Math.min(H - 60, cy - 46))}px`;
    };
    chart.showHover = showHover; chart.geom = {W, pad, d0, d1};
    showHover(chart.hoverX);
  }
  function bindChart() {
    const svg = $('chart-svg'); if (!svg) return;
    const pxOf = e => { const r = svg.getBoundingClientRect(); return (e.clientX - r.left) * (svg.viewBox.baseVal.width / r.width || 1); };
    svg.addEventListener('pointermove', e => {
      if (chart.drag && chart.geom) {
        const {W, pad} = chart.geom; const [a, b] = chart.drag.domain;
        const dt = (pxOf(e) - chart.drag.x) / (W - pad.l - pad.r) * (b - a);
        chart.domain = [a - dt, b - dt]; drawChart(); return;
      }
      chart.hoverX = pxOf(e); chart.showHover?.(chart.hoverX);
    });
    svg.addEventListener('pointerleave', () => { chart.hoverX = null; chart.showHover?.(null); });
    svg.addEventListener('pointerdown', e => { if (chart.domain && e.pointerType === 'mouse') { chart.drag = {x: pxOf(e), domain: [...chart.domain]}; svg.setPointerCapture(e.pointerId); } });
    svg.addEventListener('pointerup', () => { chart.drag = null; });
    const zoom = f => {
      const all = visiblePoints(); if (all.length < 2) return;
      const full = [all[0].t, all[all.length - 1].t];
      const [a, b] = chart.domain || full; const span = Math.max(60e3, (b - a) * f);
      if (span >= full[1] - full[0]) { chart.domain = null; drawChart(); return; }
      const following = !chart.domain || b >= full[1] - 1;
      chart.domain = following ? [full[1] - span, full[1]] : [(a + b) / 2 - span / 2, (a + b) / 2 + span / 2];
      drawChart();
    };
    $('zoom-in')?.addEventListener('click', () => zoom(0.5));
    $('zoom-out')?.addEventListener('click', () => zoom(2));
    $('zoom-reset')?.addEventListener('click', () => { chart.domain = null; drawChart(); });
    document.querySelectorAll('#range-seg [data-range]').forEach(b => b.addEventListener('click', () => {
      if (b.disabled || b.dataset.range === chart.range) return;
      chart.range = b.dataset.range; store.set('quant.range', chart.range); chart.domain = null; chart.data = [];
      setText($('chart-coverage'), ''); cancelChart();
      renderRangeButtons(); loadChart(true);
    }));
    document.querySelectorAll('#session-legend [data-session]').forEach(b => b.addEventListener('click', () => {
      const k = b.dataset.session; if (chart.sessions.has(k)) chart.sessions.delete(k); else chart.sessions.add(k);
      b.setAttribute('aria-pressed', String(chart.sessions.has(k))); drawChart();
    }));
    if ('ResizeObserver' in window) new ResizeObserver(() => drawChart()).observe($('chart'));
  }

  // ---------- news source state ----------
  function newsState() {
    if (feed?.provider_gates?.jin10) return {k: 'off', text: '新闻源未连接'};
    const j = (feed?.states || []).find(x => x.provider === 'jin10');
    if (!j || j.worker_alive !== 1 || j.state === 'DISCONNECTED' || j.heartbeat_stale)
      return j?.last_success_at ? {k: 'down', text: `新闻采集中断 · 最近成功 ${fmtMDHM.format(ms(j.last_success_at))}`} : {k: 'off', text: '新闻源未连接'};
    return {k: 'ok', text: '采集正常 · 当前窗口暂无新闻'};
  }
  function compactState(text) {
    const box = h('div', 'compact-empty'); const a = h('a', 'button button-outline', '查看设置'); a.href = '#settings/sources';
    box.append(h('span', null, text), a); return box;
  }
  let newsStateSig = '';
  function renderNewsState() {
    const st = newsState(); const hasNews = (feed?.news || []).length > 0;
    const sig = `${st.text}|${hasNews}`; if (sig === newsStateSig) return; newsStateSig = sig;
    const box = $('news-source-state'); if (!box) return;
    box.hidden = st.k === 'ok'; if (!box.hidden) box.replaceChildren(compactState(st.text));
    // Without an input source, 0-count panels say nothing: hide them; keep cached items visible if any exist.
    for (const id of ['news-top10-block', 'news-sentiment-block']) { const el = $(id); if (el) el.hidden = st.k !== 'ok'; }
    const body = $('personal-feed-root'); if (body) body.hidden = st.k !== 'ok' && !hasNews;
  }

  // ---------- overview lists ----------
  let newsSig = '';
  function renderOverviewNews() {
    const box = $('ov-news'); if (!box) return;
    const top = feed?.top10 || [];
    const rows = (top.length ? top : (feed?.news || [])).slice(0, 6);
    const st = newsState();
    const sig = JSON.stringify([st.text, rows.map(r => [r.item_key, r.version])]);
    if (sig === newsSig) return; newsSig = sig;
    if (!rows.length) { box.replaceChildren(st.k === 'ok' ? h('div', 'compact-empty', st.text) : compactState(st.text)); return; }
    box.replaceChildren(...rows.map(r => {
      const a = h('a', 'list-row'); a.href = '#news';
      const t = r.headline_zh || r.display_title || r.title || (r.content || '').slice(0, 60);
      const when = ms(r.published_at_utc || r.received_at);
      const m = h('span', 'm'); m.append(h('span', null, '金十'), h('span', 'num', when ? fmtMDHM.format(when) : ''));
      a.append(h('span', 't', t), m); return a;
    }));
  }
  const SEC_SCOPE = {FUND_TRUST: '基金信托层级', ISSUER_TRUST_ALL_SERIES: '信托层级 · 非本 ETF 专属'};
  async function loadOverviewSec() {
    const box = $('ov-sec'); if (!box || !root.dataset.officialUrl) { if (box) box.replaceChildren(h('div', 'compact-empty', '暂无公告')); return; }
    try {
      const r = await timedFetch(root.dataset.officialUrl, 20e3);
      if (!r.ok) throw new Error();
      const j = await r.json();
      const rows = (j.filings || []).filter(f => f.in_window).sort((a, b) => String(b.filing_date).localeCompare(String(a.filing_date))).slice(0, 6);
      if (!rows.length) { box.replaceChildren(h('div', 'compact-empty', '近 30 天没有新公告')); return; }
      box.replaceChildren(...rows.map(f => {
        const ok = typeof f.document_url === 'string' && f.document_url.startsWith('https://www.sec.gov/');
        const a = h(ok ? 'a' : 'div', 'list-row'); if (ok) { a.href = f.document_url; a.target = '_blank'; a.rel = 'noopener noreferrer'; }
        const m = h('span', 'm'); m.append(h('span', 'tag', f.form), h('span', null, f.symbols || ''), h('span', 'num', f.filing_date || ''));
        if (f.report_date) m.append(h('span', 'num', `报告期 ${f.report_date}`));
        m.append(h('span', 'num', `编号 ${f.accession_number}`));
        const scope = SEC_SCOPE[f.association_scope]; const title = h('span', 't', f.issuer_name || f.form);
        if (scope) title.append(h('span', 'scope-badge', scope));
        a.append(title, m); return a;
      }));
    } catch (_) { box.replaceChildren(h('div', 'compact-empty', '公告暂不可用')); }
  }

  // ---------- views / router ----------
  function showView(hash) {
    const [view0, arg] = (hash || '').replace(/^#/, '').split('/');
    const view = VIEWS[view0] ? view0 : 'overview';
    currentView = view;
    document.querySelectorAll('.view').forEach(v => v.classList.toggle('is-active', v.dataset.view === view));
    setText($('page-title'), VIEWS[view]); document.title = `Quant · ${VIEWS[view]}`;
    window.quantMarkNav?.(view);
    if (view === 'markets') {
      const list = symbols();
      const sym = (arg && list.includes(arg.toUpperCase())) ? arg.toUpperCase() : (list.includes(selected) ? selected : list[0]);
      selectSymbol(sym); requestAnimationFrame(drawChart);
    }
    if (view === 'settings' && arg === 'sources') { const d = $('sources'); if (d) { d.open = true; d.scrollIntoView({block: 'start'}); } }
    else if (view === 'research' && arg === 'sec') $('official-filings-root')?.scrollIntoView({block: 'start'});
    else window.scrollTo(0, 0);
    renderCards(connState().state === 'down');
  }
  window.addEventListener('hashchange', () => showView(location.hash));

  // ---------- settings controls ----------
  function syncSettingButtons() {
    const d = document.documentElement;
    document.querySelectorAll('[data-set-theme]').forEach(b => b.setAttribute('aria-pressed', String((d.dataset.theme || 'dark') === b.dataset.setTheme)));
    document.querySelectorAll('[data-set-updown]').forEach(b => b.setAttribute('aria-pressed', String((d.dataset.updown || 'cn') === b.dataset.setUpdown)));
  }
  document.querySelectorAll('[data-set-theme]').forEach(b => b.addEventListener('click', () => {
    store.set('quant.theme', b.dataset.setTheme); document.documentElement.dataset.theme = b.dataset.setTheme;
    document.dispatchEvent(new CustomEvent('quant-theme')); syncSettingButtons();
  }));
  document.querySelectorAll('[data-set-updown]').forEach(b => b.addEventListener('click', () => {
    store.set('quant.updown', b.dataset.setUpdown);
    if (b.dataset.setUpdown === 'us') document.documentElement.dataset.updown = 'us'; else delete document.documentElement.dataset.updown;
    syncSettingButtons();
  }));
  document.addEventListener('quant-theme', syncSettingButtons);

  // ---------- feed wiring ----------
  function onFeed() {
    const down = renderConn().state === 'down';
    for (const sym of symbols()) appendLive(sym);
    renderCards(down); renderList(down); renderDetailHead(down); renderOverviewNews(); renderNewsState();
    if (currentView === 'markets' && chart.sym) {
      const q = quoteOf(chart.sym); const t = ms(q?.source_at); const last = chart.data[chart.data.length - 1]?.t;
      if (t != null && (last == null || t > last) && !chart.drag) loadChart(false);
    }
  }
  document.addEventListener('quant-feed', e => { feed = e.detail; feedError = false; lastFeedOk = Date.now(); onFeed(); });
  document.addEventListener('quant-feed-error', () => { feedError = true; onFeed(); });
  setInterval(() => {
    if (!feed) return;
    const wasDown = $('conn')?.dataset.state === 'down';
    const down = renderConn().state === 'down';
    if (down !== wasDown) { renderCards(down); renderList(down); renderDetailHead(down); }
  }, 5e3);

  window.quantSetSymbols?.(symbols().map(sym => ({symbol: sym})));
  syncSettingButtons(); bindChart(); bindListKeys();
  showView(location.hash);
  if (feedEnabled) {
    symbols().forEach((sym, i) => setTimeout(() => loadSpark(sym), 300 * i));
    setInterval(() => symbols().forEach((sym, i) => setTimeout(() => loadSpark(sym), 400 * i)), 60e3);
    loadOverviewSec(); setInterval(loadOverviewSec, 10 * 60e3);
  } else loadOverviewSec();
})();
