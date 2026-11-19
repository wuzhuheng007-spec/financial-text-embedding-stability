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
  // Scope of the link between a watched ticker and an SEC filer.
  const scopeInfo = {
    ISSUER: {label: '发行人本身', badge: null},
    FUND_TRUST: {label: '基金信托层级', badge: '基金信托层级 · 未逐只核对基金归属'},
    ISSUER_TRUST_ALL_SERIES: {label: '发行信托层级（同一信托下含多只基金）', badge: '信托层级 · 非本 ETF 专属'},
  };
  const statusLabel = {SUCCESS: '成功', PARTIAL: '部分成功', FAILED: '失败', RUNNING: '运行中', NO_DATA: '尚无数据', READ_ERROR: '读取失败'};
  const missing = (value, why) => value ? value : `SEC 未提供${why ? '（' + why + '）' : ''}`;

  function sourceStrip(data, research) {
    const strip = document.getElementById('source-status-strip');
    if (!strip) return;
    const ss = data.source_state;
    const sec = el('span', data.state === 'SUCCESS' ? 'health-ok' : 'health-warning',
      `官方公告（SEC EDGAR）：${statusLabel[data.state] || data.state} · 数据截至 ${ss?.data_as_of || '无'} · 最近成功 ${fmt(ss?.last_success_at_utc)}`);
    const parts = [sec];
    if (research) {
      const has = (research.state && !String(research.state).startsWith('NO_')) || (research.chinese && !String(research.chinese.state || '').startsWith('NO_'));
      parts.push(el('span', has ? 'health-ok' : 'health-warning',
        has ? `研究情绪：有缓存 · 最近评分 ${fmt(research.chinese?.latest_scored_at_utc)}` : '研究情绪：此部署无研究缓存（输入来自未启用的 moomoo/金十）· 数据截至 无'));
    }
    parts.push(el('span', 'muted', 'SEC 公告不是新闻采集；新闻与行情状态见上方。'));
    strip.replaceChildren(...parts);
  }

  function filingItem(f) {
    const li = el('li', 'official-item');
    const top = el('div', 'official-top');
    top.append(el('strong', null, f.form), el('span', null, ` ${f.filing_date} `), el('span', 'small-tag', f.symbols || ''));
    const badge = scopeInfo[f.association_scope]?.badge;
    if (badge) top.append(el('span', 'scope-badge', badge));
    li.append(top);
    li.append(el('div', null, [f.issuer_name, f.primary_doc_description, f.items ? `Items ${f.items}` : ''].filter(Boolean).join(' · ')));
    const meta = el('div', 'muted');
    // Report period and accession distinguish same-issuer, same-day, same-form filings; they are never merged.
    meta.append(`${f.report_date ? '报告期 ' + f.report_date + ' · ' : ''}编号 ${f.accession_number} · SEC 受理时间原字段 ${f.acceptance_datetime_raw || '无'}（${f.acceptance_tz_marker}）· 首次采集 ${fmt(f.first_seen_at_utc)} · `);
    meta.append(secLink(f.document_url, '原文'), ' · ', secLink(f.index_url, '申报索引'));
    li.append(meta);
    return li;
  }

  function render(data, research) {
    sourceStrip(data, research);
    const state = document.getElementById('official-source-state');
    const ss = data.source_state;
    const b = data.last_success_batch;
    const parts = [`状态：${statusLabel[data.state] || data.state}`];
    if (ss) {
      parts.push(`最近尝试 ${fmt(ss.last_attempt_at_utc)}`, `最近成功 ${fmt(ss.last_success_at_utc)}`,
        `数据截至（最新申报日）${ss.data_as_of || '无'}`);
      if (ss.last_attempt_status === 'FAILED') parts.push(`最近错误 ${ss.last_error_class}（${fmt(ss.last_error_at_utc)}）· 下列为上次成功的数据`);
      if (ss.last_attempt_status === 'PARTIAL') {
        const failed = (data.latest_data_batch_rejects || []).map(r => `${r.source_key}（${r.reason}${r.retryable ? '，可重试' : ''}）`).join('、');
        parts.push(`最近一次为部分成功（${fmt(ss.last_partial_at_utc)}）· 失败范围：${failed || '见批次记录'} · 最近完整成功仍为 ${fmt(ss.last_success_at_utc)}`);
      }
    } else parts.push('尚未运行过采集');
    if (b) parts.push(`批次 ${b.batch_id} · 代码 ${String(b.code_version || '').slice(0, 7)}`);
    state.textContent = parts.join(' · ');
    state.className = 'muted ' + (data.state === 'FAILED' || data.state === 'READ_ERROR' ? 'health-warning' : '');

    const inst = document.getElementById('official-instruments');
    const known = new Map((data.instruments || []).map(r => [r.symbol, r]));
    const table = el('table', 'official-table');
    const head = el('tr');
    ['代码', 'SEC 申报主体', 'CIK', '交易所', '类型 / 行业', '与该代码的关联范围', '来源'].forEach(h => head.append(el('th', null, h)));
    table.append(head);
    for (const sym of data.watchlist || []) {
      const r = known.get(sym);
      const tr = el('tr');
      tr.append(el('td', null, sym));
      if (!r) { const td = el('td', 'muted', '未在 SEC 代码索引找到，或尚未采集'); td.colSpan = 6; tr.append(td); table.append(tr); continue; }
      const fund = r.entity_type !== 'operating';
      tr.append(el('td', null, r.sec_name || 'SEC 未提供'), el('td', null, String(r.cik)),
        el('td', null, missing(r.exchange, fund ? '基金索引不含交易所' : '')),
        el('td', null, [r.entity_type || 'SEC 未提供', r.sic_description || (fund ? '不适用：基金无 SIC 行业代码' : 'SEC 未提供')].join(' / ')));
      const scope = el('td'); const info = scopeInfo[r.association_scope];
      scope.append(el('span', info?.badge ? 'scope-badge' : null, (info?.label || r.association_scope) + (r.series_id ? `；系列 ${r.series_id}` : '')));
      tr.append(scope);
      const src = el('td'); src.append(secLink(r.source_url, r.index_source)); src.append(el('div', 'muted', `采集 ${fmt(r.retrieved_at_utc)}`)); tr.append(src);
      table.append(tr);
    }
    inst.replaceChildren(table);

    const list = document.getElementById('official-filings');
    const filings = data.filings || [];
    const recent = filings.filter(f => f.in_window);
    const supplementary = filings.filter(f => !f.in_window);
    const w = data.window;
    document.getElementById('official-count').textContent = `近${w?.days || 30}天公告 ${recent.length} 条 · 补充历史报告 ${supplementary.length} 条`;
    if (!filings.length) {
      list.replaceChildren(el('div', 'empty-state', data.state === 'NO_DATA' ? '尚未采集官方申报。' : '所选范围内没有符合条件的申报。'));
      return;
    }
    const blocks = [];
    blocks.push(el('h3', 'fact-group-title', `近 ${w?.days || 30} 天公告（申报日 ≥ ${w?.start_filing_date || '窗口起点'}）· ${recent.length} 条`));
    if (w) blocks.push(el('p', 'muted', `窗口按批次运行时的 UTC 日期 ${w.reference_date_utc} 往前 ${w.days} 天计算（SEC 申报日为美国日期；北京时间可能已是次日）。`));
    if (recent.length) { const ul = el('ul', 'official-list'); recent.forEach(f => ul.append(filingItem(f))); blocks.push(ul); }
    else blocks.push(el('div', 'empty-state', '窗口内没有符合配置表单的申报。'));
    blocks.push(el('h3', 'fact-group-title', `补充历史报告（窗口外，每类定期报告只保留最近一期）· ${supplementary.length} 条`));
    blocks.push(el('p', 'muted', '10-K、10-Q、N-CSR、N-CSRS 等定期报告在窗口内没有新申报时，保留最近一期作背景参考；它们不是近期公告。'));
    if (supplementary.length) { const ul = el('ul', 'official-list'); supplementary.forEach(f => ul.append(filingItem(f))); blocks.push(ul); }
    list.replaceChildren(...blocks);
  }

  const get = (url) => fetch(url, {credentials: 'same-origin', cache: 'no-store'}).then(r => r.ok ? r.json() : Promise.reject(new Error('HTTP ' + r.status)));
  Promise.all([get(root.dataset.url), root.dataset.researchUrl ? get(root.dataset.researchUrl).catch(() => null) : Promise.resolve(null)])
    .then(([data, research]) => render(data, research))
    .catch(err => {
      document.getElementById('official-source-state').textContent = `读取失败：${err.message}`;
      document.getElementById('official-count').textContent = '读取失败';
      const strip = document.getElementById('source-status-strip');
      if (strip) strip.replaceChildren(el('span', 'health-warning', `官方公告（SEC EDGAR）：读取失败 ${err.message}`));
    });
})();
