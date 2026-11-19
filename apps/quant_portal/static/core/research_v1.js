/* Research workbench: registered datasets/factors, background runs, results. Same-origin JSON only. */
(() => {
  'use strict';
  const root = document.getElementById('research-workbench');
  if (!root) return;
  const U = root.dataset;
  const tz = document.getElementById('terminal-root')?.dataset.timeZone || 'Asia/Shanghai';
  const $ = id => document.getElementById(id);
  const h = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; };
  const svgNS = 'http://www.w3.org/2000/svg';
  const s = (tag, a) => { const n = document.createElementNS(svgNS, tag); for (const k in a || {}) n.setAttribute(k, a[k]); return n; };
  const pct = v => v == null ? '—' : `${(v * 100).toFixed(1)}%`;
  const num = (v, d = 2) => v == null ? '—' : Number(v).toFixed(d);
  const fmtLocal = new Intl.DateTimeFormat('zh-CN', {timeZone: tz, month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false});
  const fmtLocalS = new Intl.DateTimeFormat('zh-CN', {timeZone: tz, year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false});
  const local = (iso, withSec) => { const t = Date.parse(iso || ''); return Number.isFinite(t) ? (withSec ? fmtLocalS : fmtLocal).format(t) : '—'; };
  const STATUS = {QUEUED: '排队中', RUNNING: '运行中', SUCCEEDED: '运行完成', FAILED: '失败', CANCELLED: '已取消'};
  const REASON = {WORKER_LOST: '执行进程失联（已确认）', TIMEOUT: '超过运行时限', CANCELLED_WHILE_RUNNING: '运行中取消', CANCELLED_BEFORE_START: '开始前取消',
    CANCELLED_BEFORE_COMMIT: '提交结果前取消', ERROR: '运行出错', COMPLETED: ''};
  const SEG = {development: '开发段', validation: '验证段', holdout: '后段（已查看过）'};
  const csrf = () => document.querySelector('input[name="csrfmiddlewaretoken"]')?.value || '';
  const fetchJSON = (url, opts) => fetch(url, {credentials: 'same-origin', cache: 'no-store', ...opts}).then(async r => {
    const j = await r.json().catch(() => ({})); if (!r.ok) throw new Error(j.reason || j.result || `HTTP ${r.status}`); return j; });
  const shortId = r => `${local(r.created_at)} · ${r.run_id.slice(-6)}`;
  let reg = null, openBps = '0', timer = null, highlight = null;

  function verificationText(r) {
    const a = r.latest_audit;
    if (r.status !== 'SUCCEEDED') return '';
    if (a) return a.ok ? `独立复算通过（${a.verifier_version}）` : `独立复算未通过（${a.verifier_version}）`;
    return '仅账本内部一致性检查（v1），未做独立复算';
  }
  function renderLists() {
    $('rw-datasets').replaceChildren(...(reg.datasets.length ? reg.datasets.map(d => {
      const row = h('div', 'list-row');
      const m = h('span', 'm'); m.append(h('span', 'tag', d.frequency === 'DAY' ? '日线' : d.frequency), h('span', null, `${d.start_date} – ${d.end_date}`),
        h('span', 'num', `${d.rows} 行`), h('span', null, d.manifest?.currency || ''), h('span', null, `v${d.dataset_version}`));
      row.append(h('span', 't', d.title), m, h('span', 'm', d.audit_status.startsWith('AUDITED') ? '数据集已审计（作者自检，非独立审计）· 非 point-in-time' : d.audit_status));
      return row;
    }) : [h('div', 'compact-empty', '尚未登记数据集')]));
    $('rw-factors').replaceChildren(...(reg.factors.length ? reg.factors.map(f => {
      const row = h('div', 'list-row');
      const m = h('span', 'm'); m.append(h('span', 'tag', `v${f.factor_version}`), h('span', null, Object.entries(f.params).map(([k, v]) => `${k}=${v}`).join(' ')),
        h('span', null, `输入：${f.input_requirements.frequency === 'DAY' ? '日线' : ''} ${f.input_requirements.fields.join('/')}`));
      row.append(h('span', 't', f.title), m, h('span', 'm', f.output_definition)); return row;
    }) : [h('div', 'compact-empty', '尚未登记因子')]));
    const fill = (sel, items, key, label) => { const v = sel.value; sel.replaceChildren(...items.map(x => { const o = h('option', null, label(x)); o.value = key(x); return o; })); if (v) sel.value = v; };
    fill($('rw-ds'), reg.datasets, d => `${d.dataset_id}@${d.dataset_version}`, d => `${d.title} v${d.dataset_version}`);
    fill($('rw-fac'), reg.factors, f => `${f.factor_id}@${f.factor_version}`, f => `${f.title} v${f.factor_version}`);
    fill($('rw-proto'), reg.protocols, p => p.protocol_id, p => `${p.protocol_id} v${p.protocol_version} · 成本 ${p.costs_bps.join('/')} bps`);
    const p = reg.protocols[0];
    $('rw-proto-note').textContent = p ? `收盘信号、次日开盘执行 · ${p.return_label} · ${p.segments.map(g => `${SEG[g.name] || g.name} ${g.start.slice(0, 4)}–${g.end.slice(0, 4)}`).join(' / ')} · 协议已冻结，网页不能改参数` : '';
    $('rw-submit').disabled = !(reg.datasets.length && reg.factors.length && reg.protocols.length);
  }
  function renderRuns() {
    const box = $('rw-runs');
    if (!reg.runs.length) { box.replaceChildren(h('div', 'compact-empty', '还没有实验')); return; }
    const t = h('table', 'official-table rw-runs-table'); const hd = h('tr');
    ['实验', '数据 · 因子', '运行状态', '数值校验', '后段 5 bps', ''].forEach(x => hd.append(h('th', null, x))); t.append(hd);
    for (const r of reg.runs) {
      const tr = h('tr'); tr.dataset.runId = r.run_id; if (r.run_id === highlight) tr.className = 'rw-new';
      const idc = h('td', 'num', shortId(r)); idc.title = r.run_id; tr.append(idc, h('td', null, `${r.dataset_id} · ${r.factor_id}@${r.factor_version}`));
      const st = h('td'); st.append(h('span', `tag run-${r.status.toLowerCase()}`, STATUS[r.status] || r.status));
      const why = REASON[r.exit_reason] ?? (r.error_class || '');
      if (why) st.append(h('div', 'fine-print', why));
      if (r.attempt_of) st.append(h('div', 'fine-print', `重试自 ${r.attempt_of.slice(-6)}`));
      tr.append(st, h('td', 'fine-print', verificationText(r)));
      const hl = r.summary?.headline?.find(x => String(x.cost_bps) === '5')?.segments?.find(g => g.name === 'holdout');
      tr.append(h('td', 'num', hl ? `策略 ${pct(hl.strategy_total_return)} / 基准 ${pct(hl.benchmark_total_return)}` : ''));
      const act = h('td', 'rw-actions');
      if (r.status === 'SUCCEEDED' || r.status === 'FAILED' || r.status === 'CANCELLED') { const b = h('button', 'button button-outline', '查看'); b.type = 'button'; b.onclick = () => openDetail(r.run_id); act.append(b); }
      if (r.status === 'QUEUED' || r.status === 'RUNNING') { const b = h('button', 'button button-outline', '取消'); b.type = 'button'; b.onclick = () => cancel(r.run_id, b); act.append(b); }
      tr.append(act); t.append(tr);
    }
    const wrap = h('div', 'table-scroll'); wrap.append(t); box.replaceChildren(wrap);
    if (highlight) box.querySelector('.rw-new')?.scrollIntoView({block: 'nearest'});
  }
  async function load() {
    try { reg = await fetchJSON(U.registryUrl); $('rw-state').textContent = ''; renderLists(); renderRuns(); }
    catch (e) { $('rw-state').textContent = `研究登记读取失败：${e.message}，稍后自动重试`; timer = setTimeout(load, 10000); return; }
    clearTimeout(timer);
    if (reg?.runs?.some(r => r.status === 'QUEUED' || r.status === 'RUNNING')) timer = setTimeout(load, 4000);
  }
  async function cancel(id, btn) {
    btn.disabled = true;
    try { const j = await fetchJSON(U.runUrl.replace('RUN_ID', id) + '/cancel/', {method: 'POST', headers: {'X-CSRFToken': csrf()}}); btn.textContent = j.result === 'RUNNING' ? '取消中…' : (STATUS[j.result] || j.result); load(); }
    catch (e) { btn.textContent = `失败：${e.message}`; btn.disabled = false; }
  }
  async function submit(body, msg) {
    const j = await fetchJSON(U.createUrl, {method: 'POST', headers: {'X-CSRFToken': csrf()}, body});
    highlight = j.run_id; msg.textContent = `已排队：${j.run_id.slice(-6)}。后台约每分钟启动一次，状态见下方列表。`;
    await load(); $('rw-runs').scrollIntoView({block: 'start'});
  }
  $('rw-create').addEventListener('submit', async e => {
    e.preventDefault(); const msg = $('rw-msg'); $('rw-submit').disabled = true; msg.textContent = '提交中…';
    try { await submit(new URLSearchParams({dataset: $('rw-ds').value, factor: $('rw-fac').value, protocol: $('rw-proto').value}), msg); }
    catch (err) { msg.textContent = `提交失败：${err.message}`; }
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
    for (let k = 0; k <= 4; k++) { const i = Math.round((n - 1) * k / 4); const t = s('text', {x: x(i), y: H - 6, 'text-anchor': k === 0 ? 'start' : k === 4 ? 'end' : 'middle'}); t.textContent = (series.dates?.[i] || '').slice(0, 7); g.append(t); }
    const ser = s('g', {class: 'series'});
    for (const sr of series) ser.append(s('path', {d: sr.values.map((v, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(''), stroke: sr.color}));
    svg.replaceChildren(grid, g, ser);
  }
  const ddOf = vals => { let p = -Infinity; return vals.map(v => { p = Math.max(p, v); return v / p - 1; }); };
  function actionsFor(r) {
    const bar = h('div', 'rw-dl');
    const back = h('button', 'button button-outline', '← 返回实验列表'); back.type = 'button';
    back.onclick = () => { $('rw-detail').hidden = true; $('rw-runs').scrollIntoView({block: 'start'}); };
    bar.append(back);
    if (r.status !== 'QUEUED' && r.status !== 'RUNNING') {
      const again = h('button', 'button button-primary', r.status === 'SUCCEEDED' ? '用相同配置新建运行' : '重试（新运行）'); again.type = 'button';
      again.onclick = async () => {
        again.disabled = true;
        try { const j = await fetchJSON(U.runUrl.replace('RUN_ID', r.run_id) + '/rerun/', {method: 'POST', headers: {'X-CSRFToken': csrf()}});
          highlight = j.run_id; $('rw-detail').hidden = true; await load(); $('rw-runs').scrollIntoView({block: 'start'}); }
        catch (e) { again.textContent = `失败：${e.message}`; again.disabled = false; }
      };
      bar.append(again);
    }
    return bar;
  }
  function renderDetail(d) {
    const box = $('rw-detail'); box.hidden = false;
    const r = d.run, m = d.metrics;
    $('rw-d-title').textContent = `实验 ${shortId(r)} · ${STATUS[r.status] || r.status}`;
    const body = $('rw-d-body'); body.replaceChildren(actionsFor(r));
    const statusLine = h('div', 'rw-status');
    statusLine.append(h('span', `tag run-${r.status.toLowerCase()}`, `运行：${STATUS[r.status] || r.status}${REASON[r.exit_reason] ? ' · ' + REASON[r.exit_reason] : ''}`));
    const a = d.latest_audit;
    statusLine.append(h('span', `tag ${a?.ok ? 'run-succeeded' : 'run-failed'}`, a ? `数值校验：${a.ok ? '独立复算一致' : '独立复算不一致'}（${a.verifier_version}）` : '数值校验：仅账本内部一致性（v1）'));
    body.append(statusLine);
    if (!m) {
      body.append(h('p', 'muted', r.error_class ? `原因：${REASON[r.exit_reason] || r.error_class}${r.error_detail ? ' · ' + r.error_detail : ''}` : '暂无结果文件'));
      fillTech(d); return;
    }
    const sc = m.scenarios.find(x => String(x.cost_bps) === openBps) || m.scenarios[0];
    body.append(h('p', 'muted', `${m.dataset.dataset_id} v${m.dataset.dataset_version} · ${sc.first_trade_date} 至 ${sc.last_date} · ${sc.rows} 个交易日 · 因子 ${m.factor.factor_id} v${m.factor.factor_version}（${m.factor.params.lookback_days} 日）· 收盘信号、次日开盘执行 · 现金收益 0`));
    const seg = h('div', 'seg'); seg.setAttribute('role', 'group'); seg.setAttribute('aria-label', '成本情景');
    for (const x of m.scenarios) { const b = h('button', null, `${x.cost_bps} bps`); b.type = 'button'; b.setAttribute('aria-pressed', String(String(x.cost_bps) === String(sc.cost_bps))); b.onclick = () => { openBps = String(x.cost_bps); renderDetail(d); }; seg.append(b); }
    const eq = s('svg', {class: 'rw-chart', role: 'img', 'aria-label': '策略与基准净值（对数坐标）'});
    const dd = s('svg', {class: 'rw-chart rw-chart-sm', role: 'img', 'aria-label': '回撤'});
    const legend = h('div', 'chart-foot'); legend.append(h('span', 'rw-key rw-key-s', '策略净值'), h('span', 'rw-key rw-key-b', '持有基准'), h('span', null, '对数坐标；曲线抽样显示，精确值以账本和指标表为准'));
    const t = h('table', 'official-table'); const hr = h('tr');
    ['区间', '', '总收益', '年化', '波动', 'Sharpe(rf=0)', '最大回撤', '交易次数', '持仓比例', '天数'].forEach(x => hr.append(h('th', null, x))); t.append(hr);
    for (const g of [...sc.segments, {name: '全期', start: sc.first_trade_date, end: sc.last_date, ...sc.full_period}]) for (const side of ['strategy', 'benchmark']) {
      const v = g[side]; const tr = h('tr');
      tr.append(h('td', null, side === 'strategy' ? `${SEG[g.name] || g.name} ${g.start || ''}–${g.end || ''}` : ''), h('td', null, side === 'strategy' ? '策略' : '基准'),
        h('td', 'num', pct(v.total_return)), h('td', 'num', pct(v.cagr)), h('td', 'num', pct(v.ann_vol)), h('td', 'num', num(v.sharpe_rf0)),
        h('td', 'num', pct(v.max_drawdown)), h('td', 'num', String(v.trades)), h('td', 'num', v.exposure == null ? '—' : pct(v.exposure)), h('td', 'num', String(v.days)));
      t.append(tr);
    }
    const dl = h('div', 'rw-dl'); dl.append(h('span', 'muted', '逐日账本：'));
    for (const x of m.scenarios) { const a2 = h('a', 'button button-outline', `${x.cost_bps} bps CSV`); a2.href = `${U.runUrl.replace('RUN_ID', r.run_id)}/ledger.csv?bps=${x.cost_bps}`; dl.append(a2); }
    const tw = h('div', 'table-scroll'); tw.append(t);
    const limits = h('div', 'rw-limits');
    limits.append(h('strong', null, '数据与结论限制'), h('p', null,
      '供应商前复权价格收益（分红处理未经文档确认，不是总收益）；非 point-in-time；交易日为供应商序列中的日期，未与交易所完整日历独立核对；“后段”是已查看过的历史区间，不是未见留出集；单标的单因子工程基线，不说明规则有效。'));
    if (d.errata) { const e = h('details', 'rw-errata'); e.append(h('summary', null, `勘误 ${d.errata.erratum_id}`)); d.errata.corrections.forEach(c => e.append(h('p', 'fine-print', c))); limits.append(e); }
    body.append(seg, eq, legend, h('div', 'fine-print', '回撤（策略 / 基准）'), dd, tw, dl, limits);
    const curve = d.curves?.[String(sc.cost_bps)];
    if (curve) requestAnimationFrame(() => {
      const se = [{values: curve.nav, color: 'var(--accent)'}, {values: curve.benchmark_nav, color: 'var(--text-3)'}]; se.dates = curve.date; lineChart(eq, se, {log: true});
      const sd = [{values: ddOf(curve.nav), color: 'var(--accent)'}, {values: ddOf(curve.benchmark_nav), color: 'var(--text-3)'}]; sd.dates = curve.date; lineChart(dd, sd, {pctAxis: true});
    });
    fillTech(d);
  }
  function fillTech(d) {
    const r = d.run, m = d.metrics, a = d.latest_audit, tech = $('rw-d-tech'); tech.replaceChildren();
    const kv = [['run_id', r.run_id], ['提交 / 开始 / 结束（UTC）', `${r.created_at || '—'} / ${r.started_at || '—'} / ${r.finished_at || '—'}`],
      ['提交 / 结束（本地时区）', `${local(r.created_at, true)} / ${local(r.finished_at, true)}`], ['执行者', r.worker_id || '—'],
      ['退出原因', r.exit_reason || r.error_class || '—'], ['重试自', r.attempt_of || '—']];
    if (m) kv.push(['数据集', `${m.dataset.dataset_id}@${m.dataset.dataset_version}`], ['数据内容 sha256', m.dataset.content_sha256],
      ['因子', `${m.factor.factor_id}@${m.factor.factor_version} ${JSON.stringify(m.factor.params)}`], ['因子实现指纹', m.factor.impl_sha256 || '未记录（旧运行）'],
      ['协议', `${m.protocol.protocol_id} v${m.protocol.protocol_version}`], ['配置 sha256', m.protocol.config_sha256],
      ['代码版本', m.runtime?.code_version || r.code_version || '—'], ['镜像', m.runtime?.image_id || '未记录（旧运行）'], ['结果摘要 results_digest', m.results_digest]);
    kv.push(['最新独立复算', a ? `${a.ok ? '一致' : '不一致'} · ${a.verifier_version} · ${a.audited_at_utc} · ${a.file}${a.problems.length ? ' · ' + a.problems.join('；') : ''}` : '无'],
      ['账本内部一致性（v1）', d.verify ? (d.verify.ok ? '通过' : '未通过') : '—'],
      ['执行溯源', a?.provenance ? (a.provenance.complete ? '完整（指纹、配置、数据、代码与镜像均已绑定）' : a.provenance.note) : '—']);
    for (const [k, v] of kv) { const row = h('div', 'setting-row'); row.append(h('span', 'label', k), h('span', 'num fine-print', v)); tech.append(row); }
    const log = h('pre', 'rw-log'); log.textContent = (d.log_tail || []).join('\n'); tech.append(log);
  }
  async function openDetail(id) {
    try { const d = await fetchJSON(U.runUrl.replace('RUN_ID', id) + '.json'); renderDetail(d); $('rw-detail').scrollIntoView({block: 'start'}); }
    catch (e) { $('rw-state').textContent = `结果读取失败：${e.message}`; }
  }
  load();
})();
