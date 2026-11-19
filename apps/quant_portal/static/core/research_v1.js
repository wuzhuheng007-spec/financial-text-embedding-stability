/* Research workbench (M1): datasets, strategies/factors, tasks, results. Same-origin JSON only.
 * Everything shown comes from the backend registry: eligibility, capabilities and grades are never decided here.
 * Status vocabulary kept apart: software run state / mechanical check / data timing / return screen / replication. */
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
  const pct = (v, d = 1) => v == null || v === '' ? '—' : `${(Number(v) * 100).toFixed(d)}%`;
  const num = (v, d = 2) => v == null || v === '' ? '—' : Number(v).toFixed(d);
  const fmtL = new Intl.DateTimeFormat('zh-CN', {timeZone: tz, month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false});
  const fmtLS = new Intl.DateTimeFormat('zh-CN', {timeZone: tz, year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false});
  const local = (iso, sec) => { const t = Date.parse(iso || ''); return Number.isFinite(t) ? (sec ? fmtLS : fmtL).format(t) : '—'; };
  const store = {get: k => { try { return localStorage.getItem(k); } catch (_) { return null; } }, set: (k, v) => { try { localStorage.setItem(k, v); } catch (_) {} }};
  const STATUS = {QUEUED: '排队中', RUNNING: '运行中', SUCCEEDED: '运行完成', FAILED: '失败', CANCELLED: '已取消'};
  const REASON = {WORKER_LOST: '执行进程失联（已确认）', TIMEOUT: '超过运行时限', CANCELLED_WHILE_RUNNING: '运行中取消', CANCELLED_BEFORE_START: '开始前取消',
    CANCELLED_BEFORE_COMMIT: '提交结果前取消', TERMINATED_BY_SIGNAL: '作业被停止（信号）', ERROR: '运行出错', COMPLETED: ''};
  const SCI = {NOT_EVALUATED: '未做科学评价', BLOCKED_INPUTS: '缺少输入', INSUFFICIENT_SAMPLE: '样本不足', FIRST_SCREEN_NOT_PASSED: '未通过收益筛选',
    FIRST_SCREEN_PASSED_REPLICATION_PENDING: '通过首轮筛选 · 待独立复核'};
  const KIND = {FACTOR_LEGACY: '因子（旧引擎）', STRATEGY_FAMILY: '策略家族', STRATEGY_MEMBER: '策略', TEXT_CANDIDATE: '文本候选'};
  const ELIG = {ELIGIBLE: ['可运行', 'run-succeeded'], BLOCKED_INPUTS: ['缺少输入', 'tag-dim'], BLOCKED: ['暂不能运行', 'tag-dim'], BLOCKED_UNKNOWN_SPEC: ['规格未登记', 'tag-dim']};
  const SEG = {development: '开发段', validation: '验证段', holdout: '后段（已查看过）'};
  const csrf = () => document.querySelector('input[name="csrfmiddlewaretoken"]')?.value || '';
  const fetchJSON = (url, opts) => fetch(url, {credentials: 'same-origin', cache: 'no-store', ...opts}).then(async r => {
    const j = await r.json().catch(() => ({})); if (!r.ok) throw new Error(j.reason || j.result || `HTTP ${r.status}`); return j; });
  const shortId = r => `${local(r.created_at)} · ${r.run_id.slice(-6)}`;
  const reasonText = r => (r || '').replace(/^ENGINE_NOT_BUILT.*/, '执行引擎镜像尚未构建登记').replace(/^BLOCKED_INPUTS: needs (\S+).*/, '缺少 $1 输入')
    .replace(/^INPUT_KIND_MISMATCH: needs (\S+), dataset is (\S+)/, '需要 $1，数据集是 $2').replace(/^WRONG_EVALUATION_MODE.*/, '横截面评价需要单市场历史股票池')
    .replace(/^NOT_POINT_IN_TIME.*/, '数据不是时点数据').replace(/^NO_ENGINE.*/, '没有可执行引擎');
  let reg = null, timer = null, highlight = null;

  // ---------- tabs (hash #research/<tab>, remembered) ----------
  const TABS = ['datasets', 'strategies', 'tasks', 'results'];
  function showTab(name) {
    if (!TABS.includes(name)) name = 'datasets';
    root.querySelectorAll('[data-tab]').forEach(b => b.setAttribute('aria-selected', String(b.dataset.tab === name)));
    root.querySelectorAll('[data-panel]').forEach(p => { p.hidden = p.dataset.panel !== name; });
    store.set('quant.rw.tab', name);
  }
  root.querySelectorAll('[data-tab]').forEach(b => b.addEventListener('click', () => showTab(b.dataset.tab)));
  root.querySelector('.rw-tabs').addEventListener('keydown', e => {
    const i = TABS.indexOf(root.querySelector('[data-tab][aria-selected="true"]').dataset.tab);
    if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') { e.preventDefault(); const n = TABS[(i + (e.key === 'ArrowRight' ? 1 : TABS.length - 1)) % TABS.length]; showTab(n); root.querySelector(`[data-tab="${n}"]`).focus(); }
  });
  const fromHash = () => { const m = location.hash.match(/^#research\/(datasets|strategies|tasks|results)/); return m ? m[1] : null; };
  showTab(fromHash() || store.get('quant.rw.tab') || 'datasets');
  window.addEventListener('hashchange', () => { const t = fromHash(); if (t) showTab(t); });

  function chip(text, cls) { return h('span', `tag ${cls || ''}`, text); }
  function details(summary, rows) {
    const d = h('details', 'rw-errata'); d.append(h('summary', null, summary));
    for (const [k, v] of rows) { const r = h('div', 'setting-row'); r.append(h('span', 'label', k), h('span', 'num fine-print', typeof v === 'string' ? v : JSON.stringify(v))); d.append(r); }
    return d;
  }

  // ---------- datasets ----------
  function renderDatasets() {
    const box = $('rw-datasets');
    if (!reg.datasets.length) { box.replaceChildren(h('div', 'compact-empty', '尚未登记数据集。快照需由后台命令登记（网页不采集数据）。')); return; }
    box.replaceChildren(...reg.datasets.map(d => {
      const c = h('article', 'card rw-card'); const m = d.meta || {};
      const head = h('div', 'card-top'); head.append(h('h3', null, d.title), chip(`v${d.dataset_version}`));
      const line = h('div', 'rw-facts');
      line.append(h('span', null, `${d.start_date} – ${d.end_date}`), h('span', 'num', `${d.rows} 行`), h('span', null, d.frequency === 'DAY' ? '日线' : d.frequency));
      const series = d.manifest?.series;
      const body = [head, line];
      if (series) {
        const t = h('table', 'official-table'); const hr = h('tr'); ['序列', '复权', '行数', '覆盖'].forEach(x => hr.append(h('th', null, x))); t.append(hr);
        for (const x of series) { const tr = h('tr'); tr.append(h('td', 'num', x.symbol), h('td', null, `a${x.autype}`), h('td', 'num', String(x.rows)), h('td', 'num', `${x.start} – ${x.end}`)); t.append(tr); }
        const w = h('div', 'table-scroll'); w.append(t); body.push(w);
        const gaps = d.manifest?.unresolved_dates || [];
        body.push(h('p', 'fine-print', gaps.length ? `缺口：${gaps.length} 个未解决日期（${[...new Set(gaps.map(g => g[0]))].join('、')}），按缺口切段、不填补` : '缺口：无已登记未解决日期'));
      }
      const tags = h('div', 'rw-status');
      if (d.meta_recorded) {
        tags.append(chip(m.history_kind === 'POINT_IN_TIME' ? '时点数据' : '当前版本回填 · 非时点数据', 'tag-dim'));
        if (m.dividend_treatment) tags.append(chip(m.dividend_treatment === 'UNCERTIFIED' ? '分红/总收益未认证' : `分红：${m.dividend_treatment}`, 'tag-dim'));
        const rr = m.rights_scope || {}; tags.append(chip(rr.research_analysis && rr.research_analysis !== 'NOT_GRANTED' ? '可用于本人研究' : '研究用途未确认', 'tag-dim'));
      } else tags.append(chip('v2 元数据未登记（UNKNOWN）', 'tag-dim'));
      body.push(tags);
      body.push(details('数据来源与哈希', [['内容 sha256', d.content_sha256], ['数据种类', m.data_kind], ['来源', m.provider], ['日历', `${m.calendar_id} · ${m.calendar_version}`],
        ['复权', m.adjustment_semantics], ['缺口规则', m.gap_policy], ['可用时间证据', m.availability_evidence], ['来源归档', m.source_manifest_sha256], ['使用范围', m.rights_scope], ['登记审计', d.audit_status]]));
      c.append(...body); return c;
    }));
  }

  // ---------- strategies / factors ----------
  function eligibilityOf(f) {
    const e = Object.values(f.eligibility || {});
    return e.find(x => x.status === 'ELIGIBLE') || e[0] || null;
  }
  function conclusionFor(f) {
    const ext = (reg.external_runs || []).find(x => f.factor_id.startsWith('phase5_') && x.study_id === 'phase5_strategy_v1');
    if (f.spec?.kind === 'TEXT_CANDIDATE') return '缺历史新闻/事件输入，未运行（BLOCKED）';
    if (ext) return `历史导入：${SCI[ext.grade?.scientific_status] || ext.grade?.scientific_status || '—'}（12 项主检验 ${ext.record.results.primary_pass_count} 项通过）`;
    if (f.factor_id === 'mom20') return '工程基线：单标的单因子，未做科学评价';
    return '—';
  }
  function renderFactors() {
    const box = $('rw-factors');
    if (!reg.factors.length) { box.replaceChildren(h('div', 'compact-empty', '尚未登记策略或因子')); return; }
    const order = {STRATEGY_FAMILY: 0, STRATEGY_MEMBER: 1, TEXT_CANDIDATE: 2, FACTOR_LEGACY: 3};
    const items = [...reg.factors].sort((a, b) => (order[a.spec?.kind] ?? 9) - (order[b.spec?.kind] ?? 9));
    box.replaceChildren(...items.map(f => {
      const c = h('article', 'card rw-card'); const sp = f.spec || {};
      const head = h('div', 'card-top'); head.append(h('h3', null, f.title), chip(KIND[sp.kind] || sp.kind || 'UNKNOWN'));
      const el = eligibilityOf(f); const [elText, elCls] = ELIG[el?.status] || ['未知', 'tag-dim'];
      const st = h('div', 'rw-status'); st.append(chip(elText, elCls), chip(sp.evaluation_mode === 'MARKET_TIME_SERIES' ? '市场时序' : sp.evaluation_mode || 'UNKNOWN', 'tag-dim'));
      if (sp.engine && sp.engine !== 'NONE' && sp.engine !== 'UNKNOWN') st.append(chip(`引擎 ${sp.engine}`, 'tag-dim'));
      const p = h('p', 'muted', sp.definition && sp.definition !== 'UNKNOWN' ? sp.definition : f.output_definition);
      const need = h('p', 'fine-print', `需要输入：${sp.input_data_kind || f.input_requirements?.data_kind || '—'}${f.input_requirements?.missing ? ` · 缺：${f.input_requirements.missing}` : ''}`);
      const why = el && el.status !== 'ELIGIBLE' ? h('p', 'fine-print', `不能运行的原因：${el.reasons.map(reasonText).join('；')}`) : null;
      const concl = h('p', 'fine-print', `历史结论：${conclusionFor(f)}`);
      c.append(head, st, p, need, ...(why ? [why] : []), concl,
        details('完整规格', [['规格 sha256', f.spec_sha256 || '未登记 v2 规格'], ['实现包哈希', sp.implementation_package_hash], ['信号可用时间', sp.signal_available_at_rule],
          ['暖机', sp.warmup_rule], ['缺失', sp.missing_rule || f.missing_rule], ['预期方向', sp.expected_direction], ['搜索家族', sp.candidate_search_family], ['参数', f.params]]));
      return c;
    }));
  }

  // ---------- tasks ----------
  function protoById(id) { return reg.protocols.find(p => p.protocol_id === id); }
  function fillForm() {
    const ps = $('rw-proto'), fs = $('rw-fac'), ds = $('rw-ds');
    const keep = (sel, items, val, label) => { const v = sel.value; sel.replaceChildren(...items.map(x => { const o = h('option', null, label(x)); o.value = val(x); return o; })); if ([...sel.options].some(o => o.value === v)) sel.value = v; };
    keep(ps, reg.protocols, p => p.protocol_id, p => `${p.protocol_id} v${p.protocol_version} · ${p.engine}`);
    const p = protoById(ps.value) || reg.protocols[0];
    const facs = reg.factors.filter(f => f.factor_id === p?.factor?.factor_id && f.factor_version === p?.factor?.factor_version);
    keep(fs, facs, f => `${f.factor_id}@${f.factor_version}`, f => f.title);
    keep(ds, reg.datasets, d => `${d.dataset_id}@${d.dataset_version}`, d => `${d.title} v${d.dataset_version}`);
    const f = facs[0]; const e = f?.eligibility?.[ds.value];
    const el = $('rw-elig');
    if (!f) { el.textContent = '该协议的策略未登记'; $('rw-submit').disabled = true; }
    else if (!e) { el.textContent = '资格未知（后端未返回），不能提交'; $('rw-submit').disabled = true; }
    else { el.textContent = e.status === 'ELIGIBLE' ? '后端资格检查：可运行' : `后端资格检查：${ELIG[e.status]?.[0] || e.status} · ${e.reasons.map(reasonText).join('；')}`; $('rw-submit').disabled = e.status !== 'ELIGIBLE'; }
    $('rw-proto-note').textContent = p ? (p.engine === 'phase5_o2o_v1@1'
      ? `收盘信号 → 下一开盘执行 → 再下一开盘估值；扣费后目标权重、现金/单位漂移；每评价期重置、片段末收费平仓；按实际经过时间年化 · 成本 ${p.costs_bps.join('/')} bps · 基准 MA200（同区间）· 冻结配置 ${p.frozen_config_sha256.slice(0, 12)}`
      : `收盘信号、次日开盘执行 · ${p.return_label} · ${p.segments.map(g => `${SEG[g.name] || g.name} ${g.start.slice(0, 4)}–${g.end.slice(0, 4)}`).join(' / ')} · 协议已冻结，网页不能改参数`) : '';
  }
  ['rw-proto', 'rw-fac', 'rw-ds'].forEach(id => $(id).addEventListener('change', fillForm));
  function stageOf(r) {
    if (r.status === 'QUEUED') return '等待后台作业（约每分钟启动一次）';
    if (r.status === 'RUNNING') return r.heartbeat_at ? `执行中 · 最近心跳 ${local(r.heartbeat_at, true)}` : '已领取，等待心跳';
    return REASON[r.exit_reason] ?? (r.error_class || '');
  }
  function renderRuns() {
    const box = $('rw-runs');
    if (!reg.runs.length) { box.replaceChildren(h('div', 'compact-empty', '还没有实验')); return; }
    const t = h('table', 'official-table rw-runs-table'); const hd = h('tr');
    ['实验', '引擎 · 数据 · 策略', '运行状态', '阶段', ''].forEach(x => hd.append(h('th', null, x))); t.append(hd);
    for (const r of reg.runs) {
      const tr = h('tr'); if (r.run_id === highlight) tr.className = 'rw-new';
      const idc = h('td', 'num', shortId(r)); idc.title = r.run_id;
      tr.append(idc, h('td', null, `${r.engine_id} · ${r.dataset_id} · ${r.factor_id}@${r.factor_version}`));
      const st = h('td'); st.append(chip(STATUS[r.status] || r.status, `run-${r.status.toLowerCase()}`));
      if (r.attempt_of) st.append(h('div', 'fine-print', `重跑自 ${r.attempt_of.slice(-6)}`));
      tr.append(st, h('td', 'fine-print', stageOf(r)));
      const act = h('td', 'rw-actions');
      if (!['QUEUED', 'RUNNING'].includes(r.status)) { const b = h('button', 'button button-outline', '查看'); b.type = 'button'; b.onclick = () => { showTab('results'); openRun(r.run_id); }; act.append(b); }
      else { const b = h('button', 'button button-outline', '取消'); b.type = 'button'; b.onclick = () => cancel(r.run_id, b); act.append(b); }
      tr.append(act); t.append(tr);
    }
    const w = h('div', 'table-scroll'); w.append(t); box.replaceChildren(w);
  }
  async function cancel(id, btn) {
    btn.disabled = true;
    try { const j = await fetchJSON(U.runUrl.replace('RUN_ID', id) + '/cancel/', {method: 'POST', headers: {'X-CSRFToken': csrf()}}); btn.textContent = j.result === 'RUNNING' ? '取消中…' : (STATUS[j.result] || j.result); load(); }
    catch (e) { btn.textContent = `失败：${e.message}`; btn.disabled = false; }
  }
  $('rw-create').addEventListener('submit', async e => {
    e.preventDefault(); const msg = $('rw-msg'); $('rw-submit').disabled = true; msg.textContent = '提交中…';
    try {
      const j = await fetchJSON(U.createUrl, {method: 'POST', headers: {'X-CSRFToken': csrf()}, body: new URLSearchParams({dataset: $('rw-ds').value, factor: $('rw-fac').value, protocol: $('rw-proto').value})});
      highlight = j.run_id; msg.textContent = `已排队：${j.run_id.slice(-6)}。由后台作业执行，网页只提交请求。`; await load();
    } catch (err) { msg.textContent = `提交被拒绝：${err.message}`; }
    finally { fillForm(); }
  });

  // ---------- results ----------
  function gradeChips(g, sw) {
    const box = h('div', 'rw-status');
    box.append(chip(`软件：${sw}`, 'tag-dim'));
    if (g) {
      box.append(chip(`机械复核：${/PASS|RECONCILED|TOLERANCE/.test(g.mechanical_check) ? '通过' : g.mechanical_check}`, 'tag-dim'),
        chip(g.data_pit_eligibility === 'CURRENT_VINTAGE_NOT_POINT_IN_TIME' ? '数据：当前版本回填，非时点' : `数据：${g.data_pit_eligibility}`, 'tag-dim'),
        chip(`收益筛选：${SCI[g.scientific_status] || g.scientific_status}`, 'tag-dim'));
      box.append(h('div', 'fine-print rw-grade-note', `复制范围：${g.replication_scope}`));
    }
    return box;
  }
  function renderResults() {
    const box = $('rw-results'); const rows = [];
    for (const x of reg.external_runs || []) rows.push({kind: 'import', id: x.import_id, title: x.title, when: x.record.original_run.finished_at_utc, sci: x.grade?.scientific_status, tag: '历史导入'});
    for (const r of reg.runs.filter(r => !['QUEUED', 'RUNNING'].includes(r.status))) rows.push({kind: 'run', id: r.run_id, title: `${r.factor_id}@${r.factor_version} · ${r.dataset_id}`, when: r.finished_at, status: r.status, sci: r.grade?.scientific_status, tag: '平台运行'});
    if (!rows.length) { box.replaceChildren(h('div', 'compact-empty', '还没有结果')); return; }
    rows.sort((a, b) => String(b.when).localeCompare(String(a.when)));
    box.replaceChildren(...rows.map(x => {
      const b = h('button', 'nv-item rw-result-row'); b.type = 'button';
      const m = h('span', 'm'); m.append(chip(x.tag, x.kind === 'import' ? 'tag-dim' : ''), h('span', 'num', local(x.when, true)));
      const concl = x.kind === 'run' ? (x.status === 'SUCCEEDED' ? (x.sci ? `运行完成 · ${SCI[x.sci] || x.sci}` : '运行完成') : `${STATUS[x.status]}`) : `${SCI[x.sci] || x.sci || ''}`;
      m.append(h('span', null, concl));
      b.append(h('span', 't', x.title), m);
      b.onclick = () => x.kind === 'import' ? openImport(x.id) : openRun(x.id);
      return b;
    }));
  }
  const MKT = {'US.QQQ': 'QQQ', 'US..SOX': 'SOX', 'SH.000001': '上证综指', 'SH.000688': '科创50'};
  const STRAT = ['BUY_HOLD', 'MA200', 'P1_TREND', 'P2_RECOVERY', 'P3_RISK_BUDGET'];
  const STRAT_ZH = {BUY_HOLD: '买入持有', MA200: 'MA200 基准', P1_TREND: 'P1 趋势', P2_RECOVERY: 'P2 修复', P3_RISK_BUDGET: 'P3 波动预算'};
  function phase5Tables(metrics, tests, cost) {
    const out = [];
    const t = h('table', 'official-table'); const hr = h('tr'); ['市场', '策略', '年化（自然时间）', '最大回撤', '平均暴露', '累计费用', 'Sharpe（描述）'].forEach(x => hr.append(h('th', null, x))); t.append(hr);
    for (const sym of Object.keys(MKT)) for (const st of STRAT) {
      const m = metrics.find(x => x.symbol === sym && x.strategy === st && String(x.cost_bps) === String(cost)); if (!m) continue;
      const tr = h('tr'); tr.append(h('td', null, st === 'BUY_HOLD' ? MKT[sym] : ''), h('td', null, STRAT_ZH[st]), h('td', 'num', pct(m.cagr, 2)), h('td', 'num', pct(m.max_drawdown)),
        h('td', 'num', pct(m.mean_exposure)), h('td', 'num', pct(m.cost_fraction_sum, 2)), h('td', 'num', num(m.descriptive_sharpe_sqrt252))); t.append(tr);
    }
    const w1 = h('div', 'table-scroll'); w1.append(t); out.push(h('h3', 'fact-group-title', `主评价期 2015–2025 · 单边 ${cost} bps · 现金 0 收益`), w1);
    const t2 = h('table', 'official-table'); const hr2 = h('tr'); ['市场', '候选', '相对 MA200 年化算术差', '日均差 95% 区间', 'Holm p', '25 bps 仍为正', '首轮筛选'].forEach(x => hr2.append(h('th', null, x))); t2.append(hr2);
    for (const c of tests) {
      const tr = h('tr'); const pass = String(c.first_screen_return_increment_pass ?? c.pass) === 'True' || c.first_screen_return_increment_pass === true;
      tr.append(h('td', null, MKT[c.symbol] || c.symbol), h('td', null, STRAT_ZH[c.strategy] || c.strategy), h('td', 'num', pct(Number(c.mean_daily_net_delta) * 252, 2)),
        h('td', 'num', `[${Number(c.ci_low_daily).toExponential(1)}, ${Number(c.ci_high_daily).toExponential(1)}]`), h('td', 'num', num(c.holm_p, 3)),
        h('td', null, Number(c.mean_daily_delta_cost25) > 0 ? '是' : '否'), h('td', null, pass ? '通过' : '未通过')); t2.append(tr);
    }
    const w2 = h('div', 'table-scroll'); w2.append(t2);
    out.push(h('h3', 'fact-group-title', '12 项主检验（循环区块 126，1999 次，Holm）'), w2);
    return out;
  }
  async function openImport(id) {
    let x; try { x = await fetchJSON(U.importUrl.replace('IMPORT_ID', id)); } catch (e) { $('rw-state').textContent = `读取失败：${e.message}`; return; }
    const box = $('rw-detail'); box.hidden = false; $('rw-d-title').textContent = `${x.title}`;
    const body = $('rw-d-body'); const rec = x.record; let cost = '10';
    const head = [h('p', 'muted', `这是外部历史运行的导入记录，不是本平台执行的任务。原运行 ${local(rec.original_run.started_at_utc, true)}，冻结提交 ${rec.freeze_commit.slice(0, 10)}，输入 ${rec.inputs.input_db_sha256.slice(0, 12)}…（截至 ${rec.inputs.data_end}）。`),
      gradeChips(x.grade, '历史导入（非平台执行）')];
    const draw = () => {
      const seg = h('div', 'seg'); seg.setAttribute('role', 'group'); seg.setAttribute('aria-label', '成本情景');
      for (const c of ['0', '10', '25']) { const b = h('button', null, `${c} bps`); b.type = 'button'; b.setAttribute('aria-pressed', String(c === cost)); b.onclick = () => { cost = c; draw(); }; seg.append(b); }
      const lim = h('div', 'rw-limits'); lim.append(h('strong', null, '结论与限制'), h('p', null, `12 项主检验 ${rec.results.primary_pass_count} 项通过。QQQ 为 ETF（a1 前复权，未认证总收益）；其余三个是指数代理（不可直接交易）；数据为当前版本回填，非时点；历史已看过，不是未见样本。T1/T2：缺历史新闻/事件输入。`));
      body.replaceChildren(...head, seg, ...phase5Tables(rec.results.primary_metrics, rec.results.primary_tests, cost), lim);
    };
    draw();
    const tech = $('rw-d-tech'); tech.replaceChildren(details('导入记录', [['import_id', x.import_id], ['记录 sha256', x.record_sha256], ['配置 sha256', rec.code.config_sha256], ['代码 sha256', rec.code.code_sha256],
      ['协议 sha256', rec.code.protocol_sha256], ['原环境', `Python ${rec.original_run.python} · numpy ${rec.original_run.numpy} · pandas ${rec.original_run.pandas}`], ['复核分层', rec.validation_layers], ['证据文件', rec.evidence_files]]));
    box.scrollIntoView({block: 'start'});
  }
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
    for (const sr of series) ser.append(s('path', {d: sr.values.map((v, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(''), stroke: sr.color, 'stroke-dasharray': sr.dash || ''}));
    svg.replaceChildren(grid, g, ser);
  }
  const ddOf = vals => { let p = -Infinity; return vals.map(v => { p = Math.max(p, v); return v / p - 1; }); };
  function actionsFor(r) {
    const bar = h('div', 'rw-dl');
    if (!['QUEUED', 'RUNNING'].includes(r.status)) {
      const again = h('button', 'button button-primary', r.status === 'SUCCEEDED' ? '用相同冻结配置重跑（新运行）' : '重试（新运行）'); again.type = 'button';
      again.onclick = async () => { again.disabled = true;
        try { const j = await fetchJSON(U.runUrl.replace('RUN_ID', r.run_id) + '/rerun/', {method: 'POST', headers: {'X-CSRFToken': csrf()}}); highlight = j.run_id; showTab('tasks'); await load(); }
        catch (e) { again.textContent = `失败：${e.message}`; again.disabled = false; } };
      bar.append(again);
    }
    return bar;
  }
  function renderPhase5Run(d, body) {
    const rep = d.report; let cost = '10', mkt = 'US.QQQ';
    const draw = () => {
      const seg = h('div', 'seg'); seg.setAttribute('aria-label', '成本情景');
      for (const c of ['0', '10', '25']) { const b = h('button', null, `${c} bps`); b.type = 'button'; b.setAttribute('aria-pressed', String(c === cost)); b.onclick = () => { cost = c; draw(); }; seg.append(b); }
      const ms = h('div', 'seg'); ms.setAttribute('aria-label', '市场');
      for (const k of Object.keys(MKT)) { const b = h('button', null, MKT[k]); b.type = 'button'; b.setAttribute('aria-pressed', String(k === mkt)); b.onclick = () => { mkt = k; draw(); }; ms.append(b); }
      const eq = s('svg', {class: 'rw-chart', role: 'img', 'aria-label': '净值（对数坐标）'}); const dd = s('svg', {class: 'rw-chart rw-chart-sm', role: 'img', 'aria-label': '回撤'});
      const legend = h('div', 'chart-foot'); legend.append(h('span', 'rw-key', 'P1'), h('span', 'rw-key rw-key-p2', 'P2'), h('span', 'rw-key rw-key-p3', 'P3'), h('span', 'rw-key rw-key-b', 'MA200 基准'), h('span', 'rw-key rw-key-bh', '买入持有'),
        h('span', null, '主评价期；曲线抽样显示，精确值以账本为准'));
      body.replaceChildren(actionsFor(d.run), gradeChips(d.run.grade, STATUS[d.run.status]), h('p', 'muted', rep.summary_line), seg, ms, eq, legend, h('div', 'fine-print', '回撤'), dd,
        ...phase5Tables(rep.primary_metrics, rep.primary_tests, cost), dlBar(d), limits(rep));
      const cv = rep.curves?.[mkt]?.[cost];
      if (cv) requestAnimationFrame(() => {
        const col = {P1_TREND: 'var(--accent)', P2_RECOVERY: 'var(--warn)', P3_RISK_BUDGET: 'var(--ok)', MA200: 'var(--text-3)', BUY_HOLD: 'var(--text-2)'};
        const se = Object.keys(cv.nav).map(k => ({values: cv.nav[k], color: col[k], dash: k === 'BUY_HOLD' ? '4 3' : ''})); se.dates = cv.date; lineChart(eq, se, {log: true});
        const sd = Object.keys(cv.nav).map(k => ({values: ddOf(cv.nav[k]), color: col[k], dash: k === 'BUY_HOLD' ? '4 3' : ''})); sd.dates = cv.date; lineChart(dd, sd, {pctAxis: true});
      });
    };
    draw();
  }
  function dlBar(d) {
    const dl = h('div', 'rw-dl'); dl.append(h('span', 'muted', '产物下载：'));
    for (const a of d.run.artifacts || []) { const l = h('a', 'button button-outline', a.name); l.href = `${U.runUrl.replace('RUN_ID', d.run.run_id)}/artifacts/${encodeURIComponent(a.name)}`; dl.append(l); }
    return dl;
  }
  function limits(rep) {
    const lim = h('div', 'rw-limits'); lim.append(h('strong', null, '结论与限制'), h('p', null, rep.limits_zh || '')); return lim;
  }
  function renderMom20(d, body) {
    const r = d.run, m = d.metrics; let bps = '0';
    if (!m) { body.replaceChildren(actionsFor(r), gradeChips(r.grade, STATUS[r.status]), h('p', 'muted', r.error_class ? `原因：${REASON[r.exit_reason] || r.error_class}${r.error_detail ? ' · ' + r.error_detail : ''}` : '暂无结果文件')); return; }
    const draw = () => {
      const sc = m.scenarios.find(x => String(x.cost_bps) === bps) || m.scenarios[0];
      const seg = h('div', 'seg');
      for (const x of m.scenarios) { const b = h('button', null, `${x.cost_bps} bps`); b.type = 'button'; b.setAttribute('aria-pressed', String(String(x.cost_bps) === String(sc.cost_bps))); b.onclick = () => { bps = String(x.cost_bps); draw(); }; seg.append(b); }
      const eq = s('svg', {class: 'rw-chart', role: 'img', 'aria-label': '策略与基准净值（对数坐标）'}); const dd = s('svg', {class: 'rw-chart rw-chart-sm', role: 'img', 'aria-label': '回撤'});
      const t = h('table', 'official-table'); const hr = h('tr'); ['区间', '', '总收益', '年化（252/n）', '最大回撤', '交易次数', '持仓比例', '天数'].forEach(x => hr.append(h('th', null, x))); t.append(hr);
      for (const g of [...sc.segments, {name: '全期', start: sc.first_trade_date, end: sc.last_date, ...sc.full_period}]) for (const side of ['strategy', 'benchmark']) {
        const v = g[side]; const tr = h('tr');
        tr.append(h('td', null, side === 'strategy' ? `${SEG[g.name] || g.name} ${g.start || ''}–${g.end || ''}` : ''), h('td', null, side === 'strategy' ? '策略' : '基准'), h('td', 'num', pct(v.total_return)),
          h('td', 'num', pct(v.cagr)), h('td', 'num', pct(v.max_drawdown)), h('td', 'num', String(v.trades)), h('td', 'num', v.exposure == null ? '—' : pct(v.exposure)), h('td', 'num', String(v.days))); t.append(tr);
      }
      const tw = h('div', 'table-scroll'); tw.append(t);
      const dl = h('div', 'rw-dl'); dl.append(h('span', 'muted', '逐日账本：'));
      for (const x of m.scenarios) { const a2 = h('a', 'button button-outline', `${x.cost_bps} bps CSV`); a2.href = `${U.runUrl.replace('RUN_ID', r.run_id)}/ledger.csv?bps=${x.cost_bps}`; dl.append(a2); }
      const lim = h('div', 'rw-limits'); lim.append(h('strong', null, '数据与结论限制'), h('p', null, '旧引擎（legacy_mom20_v1）：隔夜/日内拆分、当日收盘估值、按 252/交易日数年化；与 phase5 引擎口径不同，不可直接比较。前复权价格收益非总收益；非时点数据；单标的单因子工程基线，不说明规则有效。'));
      if (d.errata) { const e = h('details', 'rw-errata'); e.append(h('summary', null, `勘误 ${d.errata.erratum_id}`)); d.errata.corrections.forEach(c => e.append(h('p', 'fine-print', c))); lim.append(e); }
      const a = d.latest_audit;
      body.replaceChildren(actionsFor(r), gradeChips(r.grade || {mechanical_check: a?.ok ? 'PASS' : (a ? 'FAIL' : 'LEDGER_V1_ONLY'), data_pit_eligibility: 'CURRENT_VINTAGE_NOT_POINT_IN_TIME',
        scientific_status: 'NOT_EVALUATED', replication_scope: '工程基线，无复制'}, STATUS[r.status]),
        h('p', 'muted', `${m.dataset.dataset_id} v${m.dataset.dataset_version} · ${sc.first_trade_date} 至 ${sc.last_date} · ${sc.rows} 个交易日 · 因子 ${m.factor.factor_id} v${m.factor.factor_version}`), seg, eq, dd, tw, dl, lim);
      const curve = d.curves?.[String(sc.cost_bps)];
      if (curve) requestAnimationFrame(() => {
        const se = [{values: curve.nav, color: 'var(--accent)'}, {values: curve.benchmark_nav, color: 'var(--text-3)'}]; se.dates = curve.date; lineChart(eq, se, {log: true});
        const sd = [{values: ddOf(curve.nav), color: 'var(--accent)'}, {values: ddOf(curve.benchmark_nav), color: 'var(--text-3)'}]; sd.dates = curve.date; lineChart(dd, sd, {pctAxis: true});
      });
    };
    draw();
  }
  async function openRun(id) {
    let d; try { d = await fetchJSON(U.runUrl.replace('RUN_ID', id) + '.json'); } catch (e) { $('rw-state').textContent = `结果读取失败：${e.message}`; return; }
    const box = $('rw-detail'); box.hidden = false; const r = d.run;
    $('rw-d-title').textContent = `实验 ${shortId(r)} · ${STATUS[r.status] || r.status}`;
    const body = $('rw-d-body');
    if (r.engine_id === 'phase5_o2o_v1' && d.report) renderPhase5Run(d, body);
    else if (r.engine_id === 'phase5_o2o_v1') body.replaceChildren(actionsFor(r), gradeChips(r.grade, STATUS[r.status]), h('p', 'muted', r.error_class ? `原因：${REASON[r.exit_reason] || r.error_class}${r.error_detail ? ' · ' + r.error_detail : ''}` : '没有报告文件：运行未完成或缺少产物，不显示为成功结果'), dlBar(d));
    else renderMom20(d, body);
    const tech = $('rw-d-tech'); tech.replaceChildren();
    const kv = [['run_id', r.run_id], ['引擎', `${r.engine_id}@${r.engine_version}`], ['契约', r.contracts || '旧运行：按旧引擎推导'], ['提交 / 开始 / 结束（UTC）', `${r.created_at || '—'} / ${r.started_at || '—'} / ${r.finished_at || '—'}`],
      ['执行者', r.worker_id || '—'], ['镜像', r.image_id || '—'], ['代码版本', r.code_version || '—'], ['退出原因', r.exit_reason || r.error_class || '—'], ['重试自', r.attempt_of || '—'],
      ['配置 sha256', r.config_sha256], ['样本 mask', r.dataset_mask_hash || '—'], ['结果摘要', r.results_digest || '—'], ['产物', (r.artifacts || []).map(a => `${a.name} ${a.sha256.slice(0, 12)}`).join('\n') || '—']];
    for (const [k, v] of kv) { const row = h('div', 'setting-row'); row.append(h('span', 'label', k), h('span', 'num fine-print', typeof v === 'string' ? v : JSON.stringify(v))); tech.append(row); }
    const log = h('pre', 'rw-log'); log.textContent = (d.log_tail || []).join('\n'); tech.append(log);
    box.scrollIntoView({block: 'start'});
  }

  async function load() {
    try {
      reg = await fetchJSON(U.registryUrl);
      reg.external_runs = reg.external_runs || [];
      $('rw-state').textContent = reg.state === 'NOT_INITIALIZED' ? '研究登记库尚未初始化' : '';
      renderDatasets(); renderFactors(); fillForm(); renderRuns(); renderResults();
    } catch (e) { $('rw-state').textContent = `研究登记读取失败：${e.message}，稍后自动重试`; clearTimeout(timer); timer = setTimeout(load, 10000); return; }
    clearTimeout(timer);
    if (reg.runs.some(r => r.status === 'QUEUED' || r.status === 'RUNNING')) timer = setTimeout(load, 4000);
  }
  load();
})();
