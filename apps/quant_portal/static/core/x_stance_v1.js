/* X keyword stance: settings, daily shares, main views in the sample, history. Same-origin JSON only; the page
 * never calls a provider or a model. With no data it lists the inputs that are missing - it never draws sample rows.
 * Retrospective backfill and live capture are always labelled apart. */
(() => {
  'use strict';
  const root = document.getElementById('x-stance');
  if (!root) return;
  const h = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; };
  const svgNS = 'http://www.w3.org/2000/svg';
  const s = (tag, a) => { const n = document.createElementNS(svgNS, tag); for (const k in a || {}) n.setAttribute(k, a[k]); return n; };
  const pct = v => v == null ? '—' : `${(v * 100).toFixed(1)}%`;
  const TABS = ['settings', 'daily', 'views', 'history'];
  const TOPIC = {memory: '存储', technology: '科技 / 半导体', market: '股市 / 宏观', qqq: 'QQQ'};
  const STATE = {bullish: '看多', bearish: '看空', neutral: '中性', UNKNOWN: '未判定'};
  const MODE = {LIVE: '实时留档', RETROSPECTIVE_VISIBLE_NOW: '历史回填（现在可见的样本）'};
  const WHY = {LOW_SAMPLE: '样本账号过少', NO_VALID_ACCOUNTS: '没有已判定的账号', COLLECTION_PARTIAL: '采集不完整', COLLECTION_MISSING: '没有采集记录',
    TIE_RESOLVED_TO_NEUTRAL: '并列，记为中性', DRY_RUN: '离线演练模式', LIVE_NOT_ENABLED: '实采未启用', PROVIDER_KEY_MISSING: '缺少供应商密钥'};
  const MISSING = {PROVIDER_KEY: '供应商密钥', 'acquisition.history_start': '回填起始日期', 'acquisition.history_end_exclusive': '回填结束日期',
    'acquisition.daily_budget_usd': '每日预算', 'acquisition.total_budget_usd': '总预算', 'classifier.model_id': '分类模型',
    'classifier.immutable_revision': '分类模型的固定版本', 'classifier.prompt_version': '提示词版本', 'classifier.temperature': '模型温度',
    'market.input_version': '行情输入版本', 'market.adjustment_semantics': '复权口径说明', 'validation.holdout_start': '留出期起点'};
  const why = c => WHY[c] || (c && c.startsWith('BUDGET_MISSING') ? '预算未设定' : c && c.startsWith('WINDOW_NOT_FROZEN') ? '采集日期未设定' : c || '');
  const chip = (text, cls) => h('span', `tag ${cls || ''}`, text);
  const row = (k, v) => { const r = h('div', 'setting-row'); r.append(h('span', 'label', k), v instanceof Node ? v : h('span', 'num', v == null ? '—' : String(v))); return r; };
  const table = (heads, rows) => { const t = h('table', 'official-table'); const hr = h('tr'); heads.forEach(x => hr.append(h('th', null, x))); t.append(hr);
    rows.forEach(cells => { const tr = h('tr'); cells.forEach(c => { const td = h('td'); td.append(c instanceof Node ? c : document.createTextNode(c == null ? '—' : String(c))); tr.append(td); }); t.append(tr); });
    const w = h('div', 'table-scroll'); w.tabIndex = 0; w.append(t); return w; };
  const panel = name => root.querySelector(`[data-xpanel="${name}"]`);

  function showTab(name) {
    if (!TABS.includes(name)) name = 'settings';
    root.querySelectorAll('[data-xtab]').forEach(b => b.setAttribute('aria-selected', String(b.dataset.xtab === name)));
    root.querySelectorAll('[data-xpanel]').forEach(p => { p.hidden = p.dataset.xpanel !== name; });
  }
  root.querySelectorAll('[data-xtab]').forEach(b => b.addEventListener('click', () => showTab(b.dataset.xtab)));

  function missingBox(d, lead) {
    const box = h('div', 'card xs-empty');
    box.append(h('p', 'muted', lead));
    const ul = h('ul', 'xs-missing');
    const names = [...new Set(d.missing_inputs.map(m => MISSING[m.input] || (m.input.endsWith('.comparison_asset') ? '部分主题的对照标的' : m.input)))];
    names.forEach(n => ul.append(h('li', null, n)));
    box.append(ul);
    return box;
  }

  function renderSettings(d) {
    const p = panel('settings'), st = d.settings, c = d.collection;
    const kw = t => { const n = h('span', null, t.keywords.join(' · ')); n.setAttribute('translate', 'no'); return n; };   // keywords are data, shown as configured
    const topics = table(['关键词主题', '关键词', '对照标的'], st.topics.map(t => [TOPIC[t.id] || t.id, kw(t), t.comparison_asset || '未配置（仅统计行业观点）']));
    const card = h('div', 'card');
    card.append(row('供应商（单一主源）', st.provider), row('自动切换到其他付费源', '否'), row('语言', st.languages.join(' / ')), row('词表版本', st.vocabulary_version),
      row('每日预算（美元）', st.budget.daily_usd == null ? '未设定' : st.budget.daily_usd), row('总预算（美元）', st.budget.total_usd == null ? '未设定' : st.budget.total_usd),
      row('采集状态', {NOT_STARTED: '尚未采集', HAS_GAPS: '有缺口', COLLECTED: '已采集'}[c.state] || c.state), row('已保存帖子', c.posts),
      row('费用估算（美元，非账单）', c.estimated_cost_usd), row('数据盘空闲（GiB）', c.disk_free_gib));
    const gate = h('div', 'card');
    gate.append(h('h3', null, '付费采集'), chip(st.acquisition_gate.allowed ? '已满足条件' : '未开启', st.acquisition_gate.allowed ? 'run-succeeded' : 'tag-dim'));
    const gl = h('ul', 'xs-missing'); [...new Set(st.acquisition_gate.reasons.map(why))].forEach(t => gl.append(h('li', null, t))); gate.append(gl);
    gate.append(h('h3', null, '立场分类模型'), chip(st.classifier_gate.allowed ? '已固定版本' : '未固定版本，不运行', st.classifier_gate.allowed ? 'run-succeeded' : 'tag-dim'));
    p.replaceChildren(h('p', 'fine-print', '词表通过带版本的配置文件修改；更换词表后的占比不能与旧版本直接相接。'), topics, card, gate);
  }

  function shareBar(sh) {
    const bar = h('div', 'xs-bar'); bar.setAttribute('role', 'img');
    if (sh.bullish == null) { bar.classList.add('xs-bar-empty'); bar.setAttribute('aria-label', '无占比'); return bar; }
    bar.setAttribute('aria-label', '三类占比');
    for (const k of ['bullish', 'neutral', 'bearish']) { const seg = h('span', `xs-seg xs-${k}`); seg.style.width = `${sh[k] * 100}%`; bar.append(seg); }
    return bar;
  }

  function chart(rows, closes) {
    // two stacked zones on one date axis: shares on top (0-100 %), adjusted close below (its own scale)
    const W = 640, H = 220, top = 110, pad = 28, n = rows.length;
    const svg = s('svg', {viewBox: `0 0 ${W} ${H}`, class: 'xs-chart', role: 'img', 'aria-label': '占比与收盘价的日期对照'});
    const bw = (W - pad * 2) / Math.max(n, 1);
    rows.forEach((r, i) => {
      let y = 8; const x = pad + i * bw;
      if (r.shares.bullish == null) { svg.append(s('rect', {x, y, width: Math.max(bw - 1, 1), height: top - 8, class: 'xs-c-unknown'})); return; }
      for (const k of ['bullish', 'neutral', 'bearish']) { const hh = (top - 8) * r.shares[k]; svg.append(s('rect', {x, y, width: Math.max(bw - 1, 1), height: hh, class: `xs-c-${k}`})); y += hh; }
    });
    const pts = rows.map((r, i) => [pad + i * bw + bw / 2, closes[r.window_label]]).filter(p => p[1] != null);
    if (pts.length > 1) {
      const lo = Math.min(...pts.map(p => p[1])), hi = Math.max(...pts.map(p => p[1])), span = hi - lo || 1;
      svg.append(s('polyline', {class: 'xs-c-price', fill: 'none', points: pts.map(p => `${p[0].toFixed(1)},${(H - 14 - (p[1] - lo) / span * (H - top - 34)).toFixed(1)}`).join(' ')}));
    }
    const wrap = h('figure', 'xs-figure'); wrap.append(svg);
    wrap.append(h('figcaption', 'fine-print', pts.length > 1 ? '上：三类账号占比（0–100%）；下：对照标的复权收盘价（单独刻度）。' : '上：三类账号占比（0–100%）。没有可配对的行情，价格区留空。'));
    return wrap;
  }

  function renderDaily(d) {
    const p = panel('daily');
    if (!d.has_data) { p.replaceChildren(missingBox(d, '还没有情绪数据。开始采集和分类之前需要以下输入：')); return; }
    const out = [];
    const sess = d.daily.filter(r => r.window_kind === 'session' && r.lang_scope === 'all');
    const closes = {}; d.returns.forEach(r => { if (r.h === 1 && r.anchor_close != null) closes[`${r.topic_id}|${r.session_date}`] = r.anchor_close; });
    for (const tid of [...new Set(sess.map(r => r.topic_id))]) {
      const rows = sess.filter(r => r.topic_id === tid).sort((a, b) => a.window_label < b.window_label ? -1 : 1);
      const sec = h('section', 'card section'); const head = h('div', 'section-head'); head.append(h('h3', null, TOPIC[tid] || tid));
      [...new Set(rows.map(r => r.mode))].forEach(m => head.append(chip(MODE[m] || m, m === 'LIVE' ? 'run-succeeded' : 'tag-dim'))); sec.append(head);
      const cl = {}; rows.forEach(r => { cl[r.window_label] = closes[`${tid}|${r.window_label}`]; });
      sec.append(chart(rows.slice(-60), cl));
      sec.append(table(['交易日', '占比', '看多', '中性', '看空', '账号数', '帖子数', '未判定', '主导状态', '说明'], rows.slice(-20).reverse().map(r =>
        [r.window_label, shareBar(r.shares), pct(r.shares.bullish), pct(r.shares.neutral), pct(r.shares.bearish), r.accounts_total, r.posts_total, r.unjudged_total, STATE[r.state] || r.state, why(r.state_reason)])));
      out.push(sec);
    }
    out.push(h('p', 'fine-print', '占比按样本内可识别账号计算（每个账号取窗口内最后一条有效帖子），不是全部 X 用户的比例，也不是持仓。交易日窗口为上一交易日收盘到当日收盘。'));
    p.replaceChildren(...out);
  }

  function renderViews(d) {
    const p = panel('views');
    if (!d.narratives.length) { p.replaceChildren(d.has_data ? h('p', 'muted', '当前窗口的证据不足，没有可列出的主要观点。') : missingBox(d, '还没有可归纳的观点。需要以下输入：')); return; }
    const latest = {}; d.narratives.forEach(n => { const k = `${n.topic_id}`; if (!latest[k] || n.window_label > latest[k]) latest[k] = n.window_label; });
    const out = [];
    for (const tid of Object.keys(latest)) {
      const sec = h('section', 'card section'); const head = h('div', 'section-head'); head.append(h('h3', null, TOPIC[tid] || tid), h('span', 'count', latest[tid])); sec.append(head);
      d.narratives.filter(n => n.topic_id === tid && n.window_label === latest[tid]).sort((a, b) => a.rank - b.rank).slice(0, 5).forEach(n => {
        const item = h('div', 'xs-view'); const title = h('p', 'xs-view-title', n.title); title.setAttribute('translate', 'no'); item.append(title);   // quoted post text is data
        const meta = h('div', 'fine-print');
        meta.append(h('span', null, '支持账号'), h('span', 'num', ` ${n.supporting_accounts} `), h('span', null, '样本占比'), h('span', 'num', ` ${pct(n.share_of_sample_accounts)} `),
          h('span', null, STATE.bullish), h('span', 'num', ` ${n.stance_composition.bullish} `), h('span', null, STATE.neutral), h('span', 'num', ` ${n.stance_composition.neutral} `),
          h('span', null, STATE.bearish), h('span', 'num', ` ${n.stance_composition.bearish} `));
        item.append(meta);
        const links = h('div', 'fine-print'); links.append(h('span', null, '原帖'));
        n.evidence.forEach(e => { if (/^https:\/\/(x|twitter)\.com\//.test(e.url || '')) { const a = h('a', 'num', ` ${e.post_id} `); a.href = e.url; a.target = '_blank'; a.rel = 'noopener noreferrer'; links.append(a); } else links.append(h('span', 'num', ` ${e.post_id} `)); });
        item.append(links); sec.append(item);
      });
      out.push(sec);
    }
    out.push(h('p', 'fine-print', '这是本次样本里的主要观点，不是全 X 的排行。标题取自代表性原帖的原文；观点中的事实未经核验。'));
    p.replaceChildren(...out);
  }

  function renderHistory(d) {
    const p = panel('history');
    if (!d.has_data) { p.replaceChildren(missingBox(d, '还没有历史对照。需要以下输入：')); return; }
    const out = [];
    const eps = table(['关键词主题', '状态', '起始交易日', '最后观察交易日', '交易日数', '自然日数', '起点未观察到', '结束未观察到'], d.episodes.slice(0, 60).map(e =>
      [TOPIC[e.topic_id] || e.topic_id, STATE[e.state] || e.state, e.start_session, e.end_session, e.sessions, e.calendar_days, e.left_censored ? '是' : '否', e.right_censored ? '是' : '否']));
    const s1 = h('section', 'card section'); s1.append(h('h3', null, '情绪事件'), eps, h('p', 'fine-print', '起点或结束未观察到的事件，长度只是下限。'));
    out.push(s1);
    const cells = (d.conditional || []).filter(c => c.kind === 'desc_return');
    const s2 = h('section', 'card section'); s2.append(h('h3', null, '六个期限的条件收益（收盘对收盘，描述性，不是可成交收益）'));
    if (!cells.length) s2.append(h('p', 'muted', '没有可配对的行情或样本，条件收益表为空。'));
    else s2.append(table(['关键词主题', '状态', '期限（交易日）', '日期数', '平均', '中位数', '上涨比例', '同期无条件平均', '95% 区间', '结论'], cells.map(c =>
      [TOPIC[c.topic_id] || c.topic_id, STATE[c.state], c.h, c.n_dates, pct(c.mean), pct(c.median), pct(c.up_share), pct(c.unconditional.mean),
        c.mean_ci95 ? `${pct(c.mean_ci95[0])} ~ ${pct(c.mean_ci95[1])}` : '—', c.status === 'INCONCLUSIVE' ? '样本不足，无结论' : '已估计'])));
    s2.append(h('p', 'fine-print', '历史回填只说明现在还能取到的帖子，不证明当时系统能同样收到。这里没有预测，也没有策略收益。'));
    out.push(s2);
    p.replaceChildren(...out);
  }

  fetch(root.dataset.url, {credentials: 'same-origin', cache: 'no-store'}).then(r => { if (!r.ok) throw new Error(String(r.status)); return r.json(); }).then(d => {
    const state = document.getElementById('x-stance-state');
    if (d.state === 'READ_ERROR') { state.textContent = '读取失败'; return; }
    state.textContent = d.has_data ? '已有数据' : '尚无数据';
    renderSettings(d); renderDaily(d); renderViews(d); renderHistory(d);
  }).catch(() => { document.getElementById('x-stance-state').textContent = '读取失败'; });
})();
