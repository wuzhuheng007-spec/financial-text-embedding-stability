/* Research workbench: registered datasets/factors, background runs, results. Same-origin JSON only. */
(() => {
  'use strict';
  const root = document.getElementById('research-workbench');
  if (!root) return;
  const U = root.dataset;
  const $ = id => document.getElementById(id);
  const h = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; };
  const svgNS = 'http://www.w3.org/2000/svg';
  const s = (tag, a) => { const n = document.createElementNS(svgNS, tag); for (const k in a || {}) n.setAttribute(k, a[k]); return n; };
  const pct = v => v == null ? '—' : `${(v * 100).toFixed(1)}%`;
  const num = (v, d = 2) => v == null ? '—' : Number(v).toFixed(d);
  const STATUS = {QUEUED: '排队中', RUNNING: '运行中', SUCCEEDED: '成功', FAILED: '失败', CANCELLED: '已取消'};
  const SEG = {development: '开发段', validation: '验证段', holdout: '留出段'};
  const csrf = () => document.querySelector('input[name="csrfmiddlewaretoken"]')?.value || '';
  const fetchJSON = (url, opts) => fetch(url, {credentials: 'same-origin', cache: 'no-store', ...opts}).then(async r => {
    const j = await r.json().catch(() => ({})); if (!r.ok) throw new Error(j.reason || j.result || `HTTP ${r.status}`); return j; });
  let reg = null, openRun = null, openBps = '0', timer = null;

  function renderLists() {
    const ds = $('rw-datasets'), fs = $('rw-factors');
    ds.replaceChildren(...(reg.datasets.length ? reg.datasets.map(d => {
      const row = h('div', 'list-row');
      const m = h('span', 'm'); m.append(h('span', 'tag', d.frequency === 'DAY' ? '日线' : d.frequency), h('span', null, `${d.start_date} – ${d.end_date}`),
        h('span', 'num', `${d.rows} 行`), h('span', null, d.manifest?.currency || ''), h('span', null, `v${d.dataset_version}`));
      const audit = h('span', 'm'); audit.append(h('span', null, d.audit_status.startsWith('AUDITED') ? '已审计（作者自检，非独立审计）' : d.audit_status));
      row.append(h('span', 't', d.title), m, audit); return row;
    }) : [h('div', 'compact-empty', '尚未登记数据集')]));
    fs.replaceChildren(...(reg.factors.length ? reg.factors.map(f => {
      const row = h('div', 'list-row');
      const m = h('span', 'm'); m.append(h('span', 'tag', `v${f.factor_version}`), h('span', null, Object.entries(f.params).map(([k, v]) => `${k}=${v}`).join(' ')),
        h('span', null, `需要 ${f.input_requirements.frequency === 'DAY' ? '日线' : ''} ${f.input_requirements.fields.join('/')}`));
      row.append(h('span', 't', f.title), m, h('span', 'm', f.output_definition)); return row;
    }) : [h('div', 'compact-empty', '尚未登记因子')]));
    const fill = (sel, items, key, label) => { const v = sel.value; sel.replaceChildren(...items.map(x => { const o = h('option', null, label(x)); o.value = key(x); return o; })); if (v) sel.value = v; };
    fill($('rw-ds'), reg.datasets, d => `${d.dataset_id}@${d.dataset_version}`, d => `${d.title} v${d.dataset_version}`);
    fill($('rw-fac'), reg.factors, f => `${f.factor_id}@${f.factor_version}`, f => `${f.title} v${f.factor_version}`);
    fill($('rw-proto'), reg.protocols, p => p.protocol_id, p => `${p.protocol_id} v${p.protocol_version} · 成本 ${p.costs_bps.join('/')} bps`);
    const p = reg.protocols[0];
    $('rw-proto-note').textContent = p ? `次日开盘执行 · ${p.return_label} · ${p.segments.map(g => `${SEG[g.name] || g.name} ${g.start.slice(0, 4)}–${g.end.slice(0, 4)}`).join(' / ')} · 协议已冻结，不可在网页修改参数` : '';
    $('rw-submit').disabled = !(reg.datasets.length && reg.factors.length && reg.protocols.length);
  }
  function renderRuns() {
    const box = $('rw-runs');
    if (!reg.runs.length) { box.replaceChildren(h('div', 'compact-empty', '还没有实验')); return; }
    const t = h('table', 'official-table'); const hd = h('tr');
    ['实验', '数据 · 因子', '状态', '提交', '结果', ''].forEach(x => hd.append(h('th', null, x))); t.append(hd);
    for (const r of reg.runs) {
      const tr = h('tr');
      tr.append(h('td', 'num', r.run_id.replace(/^run-/, '')), h('td', null, `${r.dataset_id} · ${r.factor_id}@${r.factor_version}`));
      const st = h('td'); const chip = h('span', `tag run-${r.status.toLowerCase()}`, STATUS[r.status] || r.status); st.append(chip);
      if (r.status === 'FAILED' && r.error_class) st.append(h('div', 'fine-print', r.error_class)); tr.append(st);
      tr.append(h('td', 'num', (r.created_at || '').replace('T', ' ').replace('Z', ' UTC')));
      const hl = r.summary?.headline?.find(x => String(x.cost_bps) === '5')?.segments?.find(g => g.name === 'holdout');
      tr.append(h('td', 'num', hl ? `留出段 5bps：策略 ${pct(hl.strategy_total_return)} / 基准 ${pct(hl.benchmark_total_return)}` : ''));
      const act = h('td');
      if (r.status === 'SUCCEEDED' || r.status === 'FAILED') { const b = h('button', 'button button-outline', '查看'); b.type = 'button'; b.onclick = () => openDetail(r.run_id); act.append(b); }
      if (r.status === 'QUEUED' || r.status === 'RUNNING') { const b = h('button', 'button button-outline', '取消'); b.type = 'button'; b.onclick = () => cancel(r.run_id, b); act.append(b); }
      tr.append(act); t.append(tr);
    }
    const wrap = h('div', 'table-scroll'); wrap.append(t); box.replaceChildren(wrap);
  }
  async function load() {
    try { reg = await fetchJSON(U.registryUrl); $('rw-state').textContent = ''; renderLists(); renderRuns(); }
    catch (e) { $('rw-state').textContent = `研究登记读取失败：${e.message}`; }
    clearTimeout(timer);
    if (reg?.runs?.some(r => r.status === 'QUEUED' || r.status === 'RUNNING')) timer = setTimeout(load, 5000);
  }
  async function cancel(id, btn) {
    btn.disabled = true;
    try { const j = await fetchJSON(U.runUrl.replace('RUN_ID', id) + '/cancel/', {method: 'POST', headers: {'X-CSRFToken': csrf()}}); btn.textContent = STATUS[j.result] || j.result; load(); }
    catch (e) { btn.textContent = `失败：${e.message}`; }
  }
  $('rw-create').addEventListener('submit', async e => {
    e.preventDefault(); const msg = $('rw-msg'); $('rw-submit').disabled = true; msg.textContent = '提交中…';
    try {
      const body = new URLSearchParams({dataset: $('rw-ds').value, factor: $('rw-fac').value, protocol: $('rw-proto').value});
      const j = await fetchJSON(U.createUrl, {method: 'POST', headers: {'X-CSRFToken': csrf()}, body});
      msg.textContent = `已排队 ${j.run_id}，后台任务约每分钟启动一次`; load();
    } catch (err) { msg.textContent = `提交失败：${err.message}`; }
    finally { $('rw-submit').disabled = false; }
  });

  // ---------- result detail ----------
  function lineChart(svg, series, {log = false, pctAxis = false} = {}) {
    const W = svg.clientWidth || 640, H = svg.clientHeight || 220; svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
    const pad = {l: 8, r: 58, t: 8, b: 22};
    const all = series.flatMap(x => x.values).filter(v => v != null && (!log || v > 0));
    if (!all.length) { svg.replaceChildren(); return; }
    const tf = v => log ? Math.log(v) : v;
    let lo = Math.min(...all.map(tf)), hi = Math.max(...all.map(tf)); if (hi === lo) hi = lo + 1e-9;
    const n = series[0].values.length;
    const x = i => pad.l + i / Math.max(1, n - 1) * (W - pad.l - pad.r), y = v => pad.t + (hi - tf(v)) / (hi - lo) * (H - pad.t - pad.b);
    const g = s('g', {class: 'axis'}); const grid = s('g', {class: 'grid'});
    for (let k = 0; k <= 4; k++) {
      const tv = lo + (hi - lo) * k / 4, v = log ? Math.exp(tv) : tv, yy = pad.t + (hi - tv) / (hi - lo) * (H - pad.t - pad.b);
      grid.append(s('line', {x1: pad.l, x2: W - pad.r, y1: yy, y2: yy}));
      const t = s('text', {x: W - pad.r + 6, y: yy + 4}); t.textContent = pctAxis ? `${(v * 100).toFixed(0)}%` : v.toFixed(2); g.append(t);
    }
    const dates = series.dates;
    for (let k = 0; k <= 4; k++) { const i = Math.round((n - 1) * k / 4); const t = s('text', {x: x(i), y: H - 6, 'text-anchor': k === 0 ? 'start' : k === 4 ? 'end' : 'middle'}); t.textContent = (dates?.[i] || '').slice(0, 7); g.append(t); }
    const ser = s('g', {class: 'series'});
    for (const sr of series) ser.append(s('path', {d: sr.values.map((v, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(''), stroke: sr.color}));
    svg.replaceChildren(grid, g, ser);
  }
  const ddOf = vals => { let p = -Infinity; return vals.map(v => { p = Math.max(p, v); return v / p - 1; }); };
  function renderDetail(d) {
    const box = $('rw-detail'); box.hidden = false;
    const r = d.run, m = d.metrics;
    $('rw-d-title').textContent = `${r.run_id} · ${STATUS[r.status] || r.status}`;
    if (!m) { $('rw-d-body').replaceChildren(h('p', 'muted', r.error_class ? `失败：${r.error_class} ${r.error_detail || ''}` : '暂无结果')); $('rw-d-tech').replaceChildren(...(d.log_tail || []).map(l => h('div', 'num fine-print', l))); return; }
    const body = $('rw-d-body'); body.replaceChildren();
    const sc = m.scenarios.find(x => String(x.cost_bps) === openBps) || m.scenarios[0];
    const head = h('p', 'muted', `${m.dataset.dataset_id} v${m.dataset.dataset_version}（${sc.first_trade_date} – ${sc.last_date}，${sc.rows} 个交易日）· 因子 ${m.factor.factor_id} v${m.factor.factor_version}（lookback ${m.factor.params.lookback_days}）· ${m.return_label} · 次日开盘执行 · 现金收益 0`);
    const seg = h('div', 'seg'); seg.setAttribute('role', 'group'); seg.setAttribute('aria-label', '成本情景');
    for (const x of m.scenarios) { const b = h('button', null, `${x.cost_bps} bps`); b.type = 'button'; b.setAttribute('aria-pressed', String(String(x.cost_bps) === String(sc.cost_bps))); b.onclick = () => { openBps = String(x.cost_bps); renderDetail(d); }; seg.append(b); }
    const curve = d.curves?.[String(sc.cost_bps)];
    const eq = s('svg', {class: 'rw-chart', role: 'img', 'aria-label': '策略与基准净值（对数坐标）'});
    const dd = s('svg', {class: 'rw-chart rw-chart-sm', role: 'img', 'aria-label': '回撤'});
    const legend = h('div', 'chart-foot'); legend.append(h('span', 'rw-key rw-key-s', '策略净值'), h('span', 'rw-key rw-key-b', '持有基准'), h('span', null, '对数坐标；曲线按显示点抽样，精确值以账本和指标表为准'));
    const t = h('table', 'official-table'); const hr = h('tr');
    ['区间', '', '总收益', '年化', '波动', 'Sharpe(rf=0)', '最大回撤', '交易次数', '持仓比例', '天数'].forEach(x => hr.append(h('th', null, x))); t.append(hr);
    const rows = [...sc.segments, {name: '全期', start: sc.first_trade_date, end: sc.last_date, ...sc.full_period}];
    for (const g of rows) for (const side of ['strategy', 'benchmark']) {
      const v = g[side]; const tr = h('tr');
      tr.append(h('td', null, side === 'strategy' ? `${SEG[g.name] || g.name} ${g.start || ''}–${g.end || ''}` : ''), h('td', null, side === 'strategy' ? '策略' : '基准'),
        h('td', 'num', pct(v.total_return)), h('td', 'num', pct(v.cagr)), h('td', 'num', pct(v.ann_vol)), h('td', 'num', num(v.sharpe_rf0)),
        h('td', 'num', pct(v.max_drawdown)), h('td', 'num', String(v.trades)), h('td', 'num', v.exposure == null ? '—' : pct(v.exposure)), h('td', 'num', String(v.days)));
      if (v.reason) tr.append(h('td', 'fine-print', '样本不足'));
      t.append(tr);
    }
    const dl = h('div', 'rw-dl'); dl.append(h('span', 'muted', '逐日账本：'));
    for (const x of m.scenarios) { const a = h('a', 'button button-outline', `${x.cost_bps} bps CSV`); a.href = `${U.runUrl.replace('RUN_ID', r.run_id)}/ledger.csv?bps=${x.cost_bps}`; dl.append(a); }
    const tw = h('div', 'table-scroll'); tw.append(t);
    body.append(head, seg, eq, legend, h('div', 'fine-print', '回撤（策略 / 基准）'), dd, tw, dl,
      h('p', 'fine-print', '工程基线：单标的、单因子、无拟合参数；各成本情景与各区间全部保存展示。结果不代表策略有效或可推广。'));
    if (curve) {
      const series = [{values: curve.nav, color: 'var(--accent)'}, {values: curve.benchmark_nav, color: 'var(--text-3)'}]; series.dates = curve.date;
      requestAnimationFrame(() => { lineChart(eq, series, {log: true});
        const dser = [{values: ddOf(curve.nav), color: 'var(--accent)'}, {values: ddOf(curve.benchmark_nav), color: 'var(--text-3)'}]; dser.dates = curve.date; lineChart(dd, dser, {pctAxis: true}); });
    }
    const tech = $('rw-d-tech'); tech.replaceChildren();
    const kv = [['run_id', r.run_id], ['数据集', `${m.dataset.dataset_id}@${m.dataset.dataset_version}`], ['数据内容 sha256', m.dataset.content_sha256],
      ['因子', `${m.factor.factor_id}@${m.factor.factor_version} ${JSON.stringify(m.factor.params)}`], ['协议', `${m.protocol.protocol_id} v${m.protocol.protocol_version}`],
      ['配置 sha256', m.protocol.config_sha256], ['代码版本', r.code_version || '—'], ['结果摘要 (results_digest)', m.results_digest],
      ['独立复算', d.verify?.ok ? `通过（${Object.entries(d.verify.scenarios).map(([k, v]) => `${k}bps ${v.rows} 行 0 差异`).join('，')}）` : '未通过'],
      ['开始 / 结束 (UTC)', `${r.started_at || ''} / ${r.finished_at || ''}`]];
    for (const [k, v] of kv) { const row = h('div', 'setting-row'); row.append(h('span', 'label', k), h('span', 'num fine-print', v)); tech.append(row); }
    const log = h('pre', 'rw-log'); log.textContent = (d.log_tail || []).join('\n'); tech.append(log);
  }
  async function openDetail(id) {
    openRun = id;
    try { const d = await fetchJSON(U.runUrl.replace('RUN_ID', id) + '.json'); if (openRun === id) { renderDetail(d); $('rw-detail').scrollIntoView({block: 'start'}); } }
    catch (e) { $('rw-state').textContent = `结果读取失败：${e.message}`; }
  }
  load();
})();
