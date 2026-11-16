/* Owner-only local cache chart. SVG uses saved quotes; no supplier calls. */
(() => {
  const root = document.getElementById('personal-timeline');
  if (!root) return;
  const endpoint = root.dataset.timelineUrl;
  const timeZone = root.dataset.timeZone || 'Asia/Shanghai';
  const symbolBox = document.getElementById('timeline-symbol');
  const sessionBox = document.getElementById('timeline-session');
  const rangeBox = document.getElementById('timeline-range');
  const svg = document.getElementById('timeline-svg');
  const summary = document.getElementById('timeline-summary');
  const coverage = document.getElementById('timeline-coverage');
  const hover = document.getElementById('timeline-hover');
  const newsBox = document.getElementById('timeline-news');
  const latestButton = document.getElementById('timeline-latest');
  const symbols = ['TQQQ', 'QQQ', 'SOXX', 'SPY', 'NVDA', 'MU'];
  const colors = {PREMARKET:'#13816c', REGULAR:'#246ac2', AFTERHOURS:'#bf6e1c', OVERNIGHT:'#8a4faf'};
  const labels = {PREMARKET:'盘前', REGULAR:'常规', AFTERHOURS:'盘后', OVERNIGHT:'夜盘'};
  const ns = 'http://www.w3.org/2000/svg';
  let data = null, inFlight = false, signature = '', followLatest = true;
  let fixedEnd = null, zoom = 1, focusMs = null;

  for (const symbol of symbols) {
    const option = document.createElement('option'); option.value = symbol; option.textContent = symbol;
    symbolBox.append(option);
  }
  function fmt(value) {
    if (!value) return '未知';
    const date = new Date(value);
    if (!Number.isFinite(date.getTime())) return '未知';
    try { return new Intl.DateTimeFormat('zh-CN', {timeZone, year:'numeric', month:'2-digit', day:'2-digit',
      hour:'2-digit', minute:'2-digit', second:'2-digit', hour12:false}).format(date); }
    catch (_) { return '未知'; }
  }
  function el(tag, cls, value) {
    const item = document.createElement(tag);
    if (cls) item.className = cls;
    if (value !== undefined && value !== null) item.textContent = String(value);
    return item;
  }
  function se(tag, attributes = {}) {
    const item = document.createElementNS(ns, tag);
    for (const [key, value] of Object.entries(attributes)) item.setAttribute(key, String(value));
    return item;
  }
  function pointKey(point) { return `${point.session}|${point.source_at}|${point.price_text}`; }
  function showPoint(point) {
    followLatest = false; fixedEnd = data.window_end_utc;
    hover.textContent = `${symbolBox.value} ${labels[point.session]}报价 ${point.price_text} ${point.currency || '币种未知'}` +
      ` · 来源时间 ${fmt(point.source_at)} · 本机收到 ${fmt(point.received_at)}` +
      ` · 渠道 ${point.source_channel === 'REST_STOCK_QUOTE' ? '定时快照' : '推送'}` +
      ` · 延迟 ${point.delay_status || '未知'}` +
      (point.raw_evidence_present ? '' : ' · 早期原始响应已按旧策略清理');
  }
  function domain() {
    const begin = Date.parse(data.window_start_utc), end = Date.parse(data.window_end_utc);
    if (!Number.isFinite(begin) || !Number.isFinite(end)) return [0, 1];
    if (zoom === 1) return [begin, end];
    const duration = (end - begin) / zoom;
    const center = focusMs ?? (begin + end) / 2;
    const left = Math.max(begin, Math.min(center - duration / 2, end - duration));
    return [left, left + duration];
  }
  function svgText(x, y, content, extra = {}) {
    const node = se('text', {x, y, fill:'#597082', 'font-size':12, ...extra});
    node.textContent = content;
    svg.append(node);
  }
  function renderPlot() {
    svg.replaceChildren();
    const [begin, end] = domain();
    const points = (data.points || []).filter(point => {
      const time = Date.parse(point.source_at);
      return Number.isFinite(time) && begin <= time && time <= end && Number(point.price_text) > 0;
    });
    if (!points.length) {
      svgText(450, 150, '该时段与范围内没有带来源时间的已采集报价', {'text-anchor':'middle'});
      return;
    }
    const prices = points.map(point => Number(point.price_text));
    let low = Math.min(...prices), high = Math.max(...prices);
    if (low === high) { const pad = Math.max(low * 0.002, 0.01); low -= pad; high += pad; }
    const x = time => 58 + (time - begin) / (end - begin) * 816;
    const y = price => 269 - (price - low) / (high - low) * 225;
    for (let i = 0; i < 4; i++) {
      const yy = 44 + i * 75;
      svg.append(se('line', {x1:58, x2:874, y1:yy, y2:yy, stroke:'#e3ebf1', 'stroke-width':1}));
      svgText(52, yy + 4, (high - (high-low)*i/3).toFixed(2), {'text-anchor':'end'});
    }
    svgText(58, 305, fmt(new Date(begin).toISOString()));
    svgText(874, 305, fmt(new Date(end).toISOString()), {'text-anchor':'end'});
    const groups = new Map();
    for (const point of points) {
      const key = `${point.session}:${point.segment}`;
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(point);
    }
    for (const group of groups.values()) {
      const first = group[0];
      if (group.length > 1) {
        const path = se('path', {d: group.map((point, index) =>
          `${index ? 'L' : 'M'}${x(Date.parse(point.source_at)).toFixed(2)},${y(Number(point.price_text)).toFixed(2)}`).join(' '),
          fill:'none', stroke:colors[first.session], 'stroke-width':2.2, 'stroke-linecap':'round'});
        svg.append(path);
      }
      for (const point of group) {
        const dot = se('circle', {cx:x(Date.parse(point.source_at)), cy:y(Number(point.price_text)),
          r:group.length === 1 ? 4 : 3, fill:colors[point.session], tabindex:0,
          'aria-label':`${labels[point.session]} ${point.price_text} 来源 ${fmt(point.source_at)}`});
        dot.dataset.pointKey = pointKey(point);
        dot.addEventListener('click', () => showPoint(point));
        dot.addEventListener('focus', () => showPoint(point));
        svg.append(dot);
      }
    }
    const markers = (data.news || []).filter(item => {
      const time = Date.parse(item.published_at_utc || '');
      return Number.isFinite(time) && begin <= time && time <= end;
    }).slice(0, 25);
    markers.forEach((item, index) => {
      const xx = x(Date.parse(item.published_at_utc));
      svg.append(se('line', {x1:xx, x2:xx, y1:22, y2:269, stroke:'#7c5968',
        'stroke-dasharray':'3 5', 'stroke-width':1, opacity:.55}));
      const dot = se('circle', {cx:xx, cy:21, r:6, fill:'#7c5968', tabindex:0,
        'aria-label':`新闻 ${index+1} 来源发布时间 ${fmt(item.published_at_utc)}`});
      dot.addEventListener('click', () => focusNews(item));
      dot.addEventListener('focus', () => { hover.textContent =
        `金十报道／待核实 · 来源发布时间 ${fmt(item.published_at_utc)} · ${item.association_reason}`; });
      svg.append(dot);
    });
  }
  function focusNews(item) {
    if (!item.published_at_utc) {
      hover.textContent = '该报道没有可核实的带时区发布时间，因此未放到图上。'; return;
    }
    const time = Date.parse(item.published_at_utc);
    if (!Number.isFinite(time)) { hover.textContent = '新闻发布时间格式无法定位。'; return; }
    followLatest = false; fixedEnd = data.window_end_utc;
    zoom = Math.max(zoom, 2); focusMs = time;
    const gapMs = (data.gap_seconds || 180) * 1000;
    const covered = (data.point_metrics?.coverage_segments || []).some(segment =>
      Date.parse(segment.first_source_at)-gapMs <= time && time <= Date.parse(segment.last_source_at)+gapMs);
    hover.textContent = `${item.title || item.content_preview || '相关新闻'} · 来源发布时间 ${fmt(item.published_at_utc)}` +
      ` · 本机首次收到 ${fmt(item.first_received_at)} · ${item.association_reason}` +
      (covered ? ' · 附近有已采集报价；仅表示时间接近' : ' · 该时间附近无已采集报价覆盖');
    renderPlot();
    latestButton.textContent = '回到最新';
    root.scrollIntoView({behavior:'smooth', block:'start'});
  }
  function newsArticle(item) {
    const article = el('article', 'timeline-news-item');
    const button = el('button', 'timeline-news-focus', item.title || item.content_preview || '未提供标题的快讯');
    button.type = 'button'; button.addEventListener('click', () => focusNews(item));
    article.append(button);
    article.append(el('p', 'news-meta',
      `金十报道／待核实 · 发布时间：${item.published_at_utc ? fmt(item.published_at_utc) : '未知，未上图'}` +
      ` · 本机首次收到：${fmt(item.first_received_at)} · ${item.association_reason}`));
    if (item.content_preview && item.title) article.append(el('p', 'timeline-news-preview', item.content_preview));
    if (item.source_url) {
      try {
        const url = new URL(item.source_url);
        if (url.protocol === 'https:') {
          const link = el('a', 'source-link', '查看来源页面');
          link.href = url.href; link.target = '_blank'; link.rel = 'noopener noreferrer'; article.append(link);
        }
      } catch (_) { /* Saved URL was invalid. */ }
    } else article.append(el('p', 'news-meta', '原文入口未提供'));
    if ((item.versions || []).length > 1) {
      const details = el('details', 'timeline-versions');
      details.append(el('summary', null, `更正或更新版本 ${item.versions.length} 条`));
      for (const version of item.versions) details.append(el('p', 'news-meta',
        `版本 ${version.version} · 本机收到 ${fmt(version.received_at)} · ${version.title || version.content_preview}`));
      article.append(details);
    }
    return article;
  }
  function renderNews() {
    const rows = data.news || [];
    if (!rows.length) {
      newsBox.replaceChildren(el('div','empty-state','当前范围内无按现有规则关联的已缓存快讯；这不代表外部没有新闻。'));
      return;
    }
    const groups = new Map();
    rows.forEach(item => {
      const key = item.event_group_id || item.item_key;
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(item);
    });
    const entries = [];
    for (const group of groups.values()) {
      entries.push(newsArticle(group[0]));
      if (group.length > 1) {
        const details = el('details', 'timeline-related');
        details.append(el('summary', null, `疑似相关报道 ${group.length-1} 条（非独立确认）`));
        group.slice(1).forEach(item => details.append(newsArticle(item)));
        entries.push(details);
      }
    }
    newsBox.replaceChildren(...entries);
  }
  function render() {
    if (!data) return;
    const metrics = data.point_metrics || {};
    const [begin,end] = domain();
    const visible = (data.points || []).filter(point => {
      const time = Date.parse(point.source_at);
      return Number.isFinite(time) && begin <= time && time <= end;
    });
    const missingRaw = visible.filter(point => !point.raw_evidence_present).length;
    const newest = Math.max(...(data.coverage || []).map(row => Date.parse(row.last_source_at || '')).filter(Number.isFinite), 0);
    const stale = newest && Date.now()-newest > 10*60*1000;
    summary.textContent = `${data.symbol} · ${data.session === 'ALL' ? '全部时段' : labels[data.session]}` +
      ` · 窗口 ${fmt(data.window_start_utc)} 至 ${fmt(data.window_end_utc)}` +
      ` · ${metrics.returned_points || 0}/${metrics.valid_unique_points || 0} 个报价点` +
      (metrics.sampled ? '（按原始时间顺序抽样展示）' : '') +
      (data.scan_truncated ? ' · 扫描达到上限，曲线不完整' : '') +
      (stale ? ' · 最新来源时间较旧' : '') +
      (missingRaw ? ` · ${missingRaw} 个显示点的旧原始响应已清理` : '');
    const coverageText = (data.coverage || []).map(row =>
      `${labels[row.session]} ${row.source_time_known_rows || 0} 条有时间记录（${fmt(row.first_source_at)}—${fmt(row.last_source_at)}）`).join('；') || '暂无记录';
    coverage.textContent = `当前缩放：${fmt(new Date(begin).toISOString())} 至 ${fmt(new Date(end).toISOString())}` +
      `。缓存总覆盖：${coverageText}` +
      '。时段之间和超过3分钟的观测间隔不连线；间隔不自动代表供应商故障。';
    renderPlot(); renderNews();
    latestButton.textContent = !followLatest && newest > Date.parse(fixedEnd || '') ? '有新报价 · 回到最新' : '回到最新';
  }
  async function refresh() {
    if (root.hidden || inFlight) return;
    inFlight = true;
    try {
      const params = new URLSearchParams({symbol:symbolBox.value, session:sessionBox.value, range:rangeBox.value});
      if (!followLatest && fixedEnd) params.set('end', fixedEnd);
      const response = await fetch(`${endpoint}?${params}`, {credentials:'same-origin', cache:'no-store'});
      if (!response.ok) throw new Error(`本机缓存接口 ${response.status}`);
      const next = await response.json();
      if (!followLatest && data) {
        const newest = Math.max(...(next.coverage || []).map(row => Date.parse(row.last_source_at || '')).filter(Number.isFinite), 0);
        if (newest > Date.parse(fixedEnd || '')) latestButton.textContent = '有新报价 · 回到最新';
        return; // Keep the user's zoom, selected point and news reading unchanged.
      }
      const nextSignature = JSON.stringify([next.points,next.news]);
      data = next;
      if (nextSignature !== signature) { signature = nextSignature; render(); }
    } catch (error) {
      summary.textContent = `本机曲线缓存暂不可用：${error.message}；最后一次图表保留，不能据此判断没有新报价。`;
    } finally { inFlight = false; }
  }
  function resetView() {
    followLatest = true; fixedEnd = null; zoom = 1; focusMs = null; signature = '';
    refresh();
  }
  document.addEventListener('personal-chart-open', event => {
    window.quantOpenPanel?.('chart');
    const symbol = event.detail?.symbol;
    if (!symbols.includes(symbol)) return;
    symbolBox.value = symbol;
    sessionBox.value = event.detail.session === 'AUTO' ? 'ALL' : event.detail.session;
    root.hidden = false;
    resetView(); root.scrollIntoView({behavior:'smooth', block:'start'});
  });
  for (const box of [symbolBox,sessionBox,rangeBox]) box.addEventListener('change', resetView);
  document.getElementById('timeline-zoom-in').addEventListener('click', () => {
    if (!data) return;
    followLatest = false; fixedEnd = data.window_end_utc; zoom = Math.min(zoom*2, 16);
    if (focusMs === null) { const [begin,end] = domain(); focusMs = (begin+end)/2; }
    render();
  });
  document.getElementById('timeline-zoom-out').addEventListener('click', () => {
    if (!data) return;
    followLatest = false; fixedEnd = data.window_end_utc; zoom = Math.max(1,zoom/2); render();
  });
  latestButton.addEventListener('click', resetView);
  newsBox.addEventListener('pointerdown', () => {
    if (data && followLatest) { followLatest = false; fixedEnd = data.window_end_utc; }
  });
  newsBox.addEventListener('scroll', () => {
    if (data && followLatest && newsBox.scrollTop > 5) {
      followLatest = false; fixedEnd = data.window_end_utc;
    }
  });
  window.setInterval(refresh, 5000);
})();
