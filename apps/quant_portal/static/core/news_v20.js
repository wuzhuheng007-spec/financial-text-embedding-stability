/* News (v20): overview list, news page (filters + list + reader) and the news-source card in Settings.
 * Reads only /news.json (the server collector's cache); the browser never connects to a provider.
 * Times: provider publication/update time is shown; receipt and latency details live in Settings. */
(() => {
  'use strict';
  const nv = document.getElementById('nv');
  const ov = document.getElementById('ov-news2');
  const card = document.getElementById('news-src-card');
  const url = (nv || ov || card)?.dataset.newsUrl;
  if (!url) return;
  const tz = document.getElementById('terminal-root')?.dataset.timeZone || 'Asia/Shanghai';
  const $ = id => document.getElementById(id);
  const h = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; };
  const fmt = new Intl.DateTimeFormat('zh-CN', {timeZone: tz, month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false});
  const fmtS = new Intl.DateTimeFormat('zh-CN', {timeZone: tz, year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false});
  const t = (iso, sec) => { const v = Date.parse(iso || ''); return Number.isFinite(v) ? (sec ? fmtS : fmt).format(v) : '—'; };
  const key = it => `${it.upstream}:${it.upstream_id}`;
  const store = {get: k => { try { return localStorage.getItem(k); } catch (_) { return null; } }, set: (k, v) => { try { localStorage.setItem(k, v); } catch (_) {} }};
  const safeUrl = u => { try { const x = new URL(u); return x.protocol === 'https:' ? x.href : null; } catch (_) { return null; } };
  let data = null, shown = new Set(), pending = 0, selected = store.get('quant.news.selected');
  const filter = {scope: store.get('quant.news.scope') || 'watch', symbol: store.get('quant.news.symbol') || ''};

  function stripHtml(s) { const d = document.createElement('div'); d.innerHTML = s || ''; return (d.textContent || '').trim(); }
  function filtered() {
    let items = (data?.items || []).filter(i => !i.deleted);
    if (filter.scope === 'watch') items = items.filter(i => i.relevance?.direct_watch?.length);
    else if (filter.scope === 'macro') items = items.filter(i => i.relevance?.macro);
    if (filter.symbol) items = items.filter(i => (i.symbols || []).includes(filter.symbol));
    return items;
  }
  function tags(it) {
    const box = h('span', 'nv-tags');
    for (const s of it.relevance?.direct_watch || []) box.append(h('span', 'tag', s));
    if (it.relevance?.macro) box.append(h('span', 'tag tag-dim', '宏观'));
    return box;
  }
  function stateLine() {
    const c = data?.connection || {label: '新闻源未连接', kind: 'off'};
    if (c.kind === 'ok') return null;  // a working source needs no status line
    const row = h('div', `compact-empty nv-state nv-${c.kind}`);
    row.append(h('span', null, c.label));
    if (c.kind !== 'ok') { const a = h('a', 'button button-outline', c.kind === 'off' ? '连接新闻源' : '查看数据源'); a.href = '#settings/sources'; row.append(a); }
    return row;
  }

  // ---------- overview ----------
  function renderOverview() {
    if (!ov) return;
    const items = (data?.items || []).filter(i => !i.deleted).slice(0, 6);
    const sig = JSON.stringify([data?.connection, items.map(i => [key(i), i.latest_version])]);
    if (sig === ov.dataset.sig) return; ov.dataset.sig = sig;
    if (!items.length) { ov.replaceChildren(stateLine() || h('div', 'compact-empty', '暂无新闻')); return; }
    ov.replaceChildren(...items.map(it => {
      const a = h('a', 'list-row'); a.href = '#news'; a.addEventListener('click', () => { selected = key(it); store.set('quant.news.selected', selected); });
      const m = h('span', 'm'); m.append(h('span', null, it.source || ''), h('span', 'num', t(it.provider_created_at || it.first_seen_at)), tags(it));
      a.append(h('span', 't', it.title || '(无标题)'), m); return a;
    }));
  }

  // ---------- news page ----------
  function readerFor(it) {
    const r = $('nv-reader'); if (!r) return;
    if (!it) { r.replaceChildren(h('div', 'nv-reader-empty', data?.items?.length ? '选择左侧一条新闻阅读' : '')); return; }
    const head = h('header', 'nv-r-head');
    head.append(h('h2', null, it.title || '(无标题)'));
    const meta = h('div', 'nv-r-meta');
    meta.append(h('span', null, it.source || ''), h('span', 'num', `发布 ${t(it.provider_created_at, true)}`));
    if (it.provider_updated_at && it.provider_updated_at !== it.provider_created_at) meta.append(h('span', 'num', `更新 ${t(it.provider_updated_at, true)}`));
    if (it.latest_version > 1) meta.append(h('span', 'tag tag-dim', `第 ${it.latest_version} 版`));
    meta.append(tags(it));
    head.append(meta);
    const body = h('div', 'nv-r-body');
    const text = stripHtml(it.body) || stripHtml(it.teaser);
    body.append(h('p', text ? null : 'muted', text || '供应商未提供正文，仅有标题；请打开原文。'));
    const foot = h('div', 'nv-r-foot');
    const link = safeUrl(it.url);
    if (link) { const a = h('a', 'button button-outline', '打开原文 ↗'); a.href = link; a.target = '_blank'; a.rel = 'noopener noreferrer'; foot.append(a); }
    foot.append(h('span', 'fine-print', it.relevance?.note || ''));
    r.replaceChildren(head, body, foot);
  }
  function renderList(force) {
    if (!nv) return;
    const list = $('nv-list'), items = filtered();
    const state = stateLine();
    $('nv-state').replaceChildren(...(state ? [state] : []));
    const active = nv.closest('.view')?.classList.contains('is-active');
    const reading = active && (list.scrollTop > 40 || window.scrollY > 120 || nv.contains(document.activeElement));
    const fresh = items.filter(i => !shown.has(key(i)));
    const sig = JSON.stringify([data?.connection, filter, items.map(i => [key(i), i.latest_version])]);
    if (!force && sig === nv.dataset.sig) return;
    if (!force && shown.size && fresh.length && reading) {
      pending = fresh.length; const b = $('nv-banner'); b.hidden = false; b.textContent = `有 ${pending} 条新消息 · 点击查看`; return;
    }
    nv.dataset.sig = sig; shown = new Set(items.map(key)); pending = 0; $('nv-banner').hidden = true;
    if (!items.length) {
      list.replaceChildren(h('div', 'nv-empty', data?.items?.length ? '当前筛选没有匹配的新闻' : (state ? '' : '暂无新闻')));
      readerFor(null); return;
    }
    list.replaceChildren(...items.map(it => {
      const b = h('button', 'nv-item'); b.type = 'button'; b.dataset.key = key(it);
      b.setAttribute('aria-selected', String(key(it) === selected));
      const m = h('span', 'm'); m.append(h('span', 'num', t(it.provider_created_at || it.first_seen_at)), h('span', null, it.source || ''), tags(it));
      b.append(h('span', 't', it.title || '(无标题)'), m);
      b.addEventListener('click', () => { selected = key(it); store.set('quant.news.selected', selected);
        list.querySelectorAll('.nv-item').forEach(x => x.setAttribute('aria-selected', String(x === b))); readerFor(it);
        if (window.matchMedia('(max-width: 900px)').matches) $('nv-reader').scrollIntoView({block: 'start'}); });
      return b;
    }));
    readerFor(items.find(i => key(i) === selected) || null);
  }
  function bindPage() {
    if (!nv) return;
    nv.querySelectorAll('[data-scope]').forEach(b => {
      b.setAttribute('aria-pressed', String(b.dataset.scope === filter.scope));
      b.addEventListener('click', () => { filter.scope = b.dataset.scope; store.set('quant.news.scope', filter.scope);
        nv.querySelectorAll('[data-scope]').forEach(x => x.setAttribute('aria-pressed', String(x === b))); renderList(true); });
    });
    const sel = $('nv-symbol');
    sel.addEventListener('change', () => { filter.symbol = sel.value; store.set('quant.news.symbol', filter.symbol); renderList(true); });
    $('nv-banner').addEventListener('click', () => { renderList(true); $('nv-list').scrollTo({top: 0}); });
    $('nv-list').addEventListener('keydown', e => {
      const items = [...$('nv-list').querySelectorAll('.nv-item')]; const i = items.indexOf(document.activeElement);
      if (i < 0) return;
      if (e.key === 'ArrowDown' && items[i + 1]) { e.preventDefault(); items[i + 1].focus(); }
      if (e.key === 'ArrowUp' && items[i - 1]) { e.preventDefault(); items[i - 1].focus(); }
    });
  }
  function fillSymbols() {
    const sel = $('nv-symbol'); if (!sel || sel.options.length > 1) return;
    for (const s of data?.watchlist || []) { const o = h('option', null, s); o.value = s; sel.append(o); }
    sel.value = filter.symbol;
  }

  // ---------- settings card ----------
  function renderCard() {
    if (!card) return;
    const c = data?.connection || {}, p = (data?.providers || []).find(x => x.provider === c.provider);
    const rows = [['状态', c.label || '新闻源未连接'], ['主源', c.provider ? (data.sources?.[c.provider]?.title || c.provider) : '未配置']];
    if (p) {
      rows.push(['最近心跳', t(p.last_heartbeat_at, true)], ['最近收到消息', t(p.last_message_at, true)], ['最近新闻入库', t(p.last_news_at, true)],
        ['计数', Object.entries(p.counters || {}).map(([k, v]) => `${k} ${v}`).join(' · ') || '—'],
        ['最近错误', p.last_error_class ? `${p.last_error_class} · ${t(p.last_error_at, true)}` : '无']);
      const lags = (data.items || []).filter(i => i.provider === c.provider && i.provider_created_at && i.first_online_seen_at)
        .slice(0, 50).map(i => (Date.parse(i.first_online_seen_at) - Date.parse(i.provider_created_at)) / 1000).filter(Number.isFinite).sort((a, b) => a - b);
      if (lags.length) rows.push(['发布→首次在线收到（近 ' + lags.length + ' 条，中位）', `${lags[Math.floor(lags.length / 2)].toFixed(1)} 秒 · 不是事件发生后的总延迟`]);
    }
    const box = h('div');
    for (const [k, v] of rows) { const r = h('div', 'setting-row'); r.append(h('span', 'label', k), h('span', 'fine-print', v)); box.append(r); }
    const rights = h('details', 'rw-errata'); rights.append(h('summary', null, '各来源授权登记（展示 / 保存 / 研究分析 / 模型训练）'));
    for (const [id, s] of Object.entries(data?.sources || {})) rights.append(h('p', 'fine-print', `${s.title}：${Object.values(s.rights).join(' / ')} · ${s.notes}`));
    rights.append(h('p', 'fine-print', '配置方式：在服务器私有文件 /srv/quant/secrets/news.env 写入 NEWS_PROVIDER 与对应密钥，再启用新闻采集服务；聊天中只需告知已开通的产品。未确认训练权限的数据不进入训练任务。'));
    box.append(rights);
    card.querySelector('.nv-card-body').replaceChildren(box);
    const tag = card.querySelector('.tag'); tag.textContent = c.kind === 'ok' ? '已接通' : c.kind === 'off' ? '未配置' : c.kind === 'down' ? '异常' : '待验证';
  }

  async function load() {
    try {
      const r = await fetch(url, {credentials: 'same-origin', cache: 'no-store'});
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      data = await r.json();
    } catch (e) {
      data = data || {items: [], providers: [], connection: {label: `新闻读取失败：${e.message}`, kind: 'down'}};
    }
    fillSymbols(); renderOverview(); renderList(false); renderCard();
  }
  bindPage(); load(); setInterval(load, 15000);
})();
