(() => {
  'use strict';
  const root = document.getElementById('official-filings-root');
  if (!root) return;
  const tz = root.dataset.timeZone || 'Asia/Shanghai';
  const el = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; };
  const secLink = (href, text) => {
    // Only official SEC archive links are rendered as links.
    if (typeof href !== 'string' || !href.startsWith('https://www.sec.gov/')) return el('span', 'muted', text);
    const a = el('a', null, text); a.href = href; a.target = '_blank'; a.rel = 'noopener noreferrer'; return a;
  };
  const fmt = (iso) => {
    if (!iso) return '无';
    const t = Date.parse(iso);
    if (!Number.isFinite(t)) return iso;
    return new Intl.DateTimeFormat('zh-CN', {timeZone: tz, year: 'numeric', month: '2-digit', day: '2-digit',
      hour: '2-digit', minute: '2-digit', hour12: false, timeZoneName: 'short'}).format(new Date(t));
  };
  const scopeLabel = {ISSUER: '发行人本身', ISSUER_TRUST_ALL_SERIES: '发行信托（含其他基金）', FUND_TRUST: '基金信托'};
  const statusLabel = {SUCCESS: '成功', PARTIAL: '部分成功', FAILED: '失败', RUNNING: '运行中', NO_DATA: '尚无数据', READ_ERROR: '读取失败'};

  function render(data) {
    const state = document.getElementById('official-source-state');
    const ss = data.source_state;
    const b = data.last_success_batch;
    const parts = [`状态：${statusLabel[data.state] || data.state}`];
    if (ss) {
      parts.push(`最近尝试 ${fmt(ss.last_attempt_at_utc)}`);
      parts.push(`最近成功 ${fmt(ss.last_success_at_utc)}`);
      parts.push(`数据截至（最新申报日）${ss.data_as_of || '无'}`);
      if (ss.last_attempt_status === 'FAILED') parts.push(`最近错误 ${ss.last_error_class}（${fmt(ss.last_error_at_utc)}）· 下列为上次成功的数据`);
    } else {
      parts.push('尚未运行过采集');
    }
    if (b) parts.push(`批次 ${b.batch_id} · 代码 ${String(b.code_version || '').slice(0, 7)}`);
    state.textContent = parts.join(' · ');
    state.className = 'muted ' + (data.state === 'FAILED' || data.state === 'READ_ERROR' ? 'health-warning' : '');

    const inst = document.getElementById('official-instruments');
    const known = new Map((data.instruments || []).map(r => [r.symbol, r]));
    const table = el('table', 'official-table');
    const head = el('tr');
    ['代码', 'SEC 名称', 'CIK', '交易所', '类型 / 行业', '申报关联范围', '来源'].forEach(h => head.append(el('th', null, h)));
    table.append(head);
    for (const sym of data.watchlist || []) {
      const r = known.get(sym);
      const tr = el('tr');
      tr.append(el('td', null, sym));
      if (!r) { const td = el('td', 'muted', '未在 SEC 代码索引找到，或尚未采集'); td.colSpan = 6; tr.append(td); table.append(tr); continue; }
      tr.append(el('td', null, r.sec_name || '无'), el('td', null, String(r.cik)), el('td', null, r.exchange || '无'),
        el('td', null, [r.entity_type, r.sic_description].filter(Boolean).join(' / ') || '无'),
        el('td', null, (scopeLabel[r.association_scope] || r.association_scope) + (r.series_id ? `（${r.series_id}）` : '')));
      const src = el('td'); src.append(secLink(r.source_url, r.index_source)); tr.append(src);
      table.append(tr);
    }
    inst.replaceChildren(table);

    const list = document.getElementById('official-filings');
    const filings = data.filings || [];
    const inWindow = filings.filter(f => f.in_window).length;
    document.getElementById('official-count').textContent = `${inWindow} 条近30天 · ${filings.length} 条合计`;
    if (!filings.length) {
      list.replaceChildren(el('div', 'empty-state', data.state === 'NO_DATA' ? '尚未采集官方申报。' : '所选范围内没有符合条件的申报。'));
      return;
    }
    const ul = el('ul', 'official-list');
    for (const f of filings) {
      const li = el('li', 'official-item');
      const top = el('div', 'official-top');
      top.append(el('strong', null, f.form), el('span', null, ` ${f.filing_date} `),
        el('span', 'small-tag', f.symbols || ''), el('span', 'muted', f.in_window ? '' : ' 窗口外的最近一期定期报告'));
      li.append(top);
      li.append(el('div', null, [f.issuer_name, f.primary_doc_description, f.items ? `Items ${f.items}` : ''].filter(Boolean).join(' · ')));
      const meta = el('div', 'muted');
      meta.append(`SEC 受理时间原字段 ${f.acceptance_datetime_raw || '无'}（${f.acceptance_tz_marker}）· 首次采集 ${fmt(f.first_seen_at_utc)} · `);
      meta.append(secLink(f.document_url, '原文'), ' · ', secLink(f.index_url, '申报索引'));
      li.append(meta);
      ul.append(li);
    }
    list.replaceChildren(ul);
  }

  fetch(root.dataset.url, {credentials: 'same-origin', cache: 'no-store'})
    .then(r => r.ok ? r.json() : Promise.reject(new Error('HTTP ' + r.status)))
    .then(render)
    .catch(err => {
      document.getElementById('official-source-state').textContent = `读取失败：${err.message}`;
      document.getElementById('official-count').textContent = '读取失败';
    });
})();
