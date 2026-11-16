/* Local cache polling only. No provider URLs, credentials or HTML injection. */
(() => {
  const root = document.getElementById('personal-feed-root');
  if (!root) return;
  const endpoint = root.dataset.feedUrl;
  const timeZone = root.dataset.timeZone || 'Asia/Shanghai';
  const quoteBox = document.getElementById('personal-quotes');
  const newsBox = document.getElementById('personal-news');
  const priority = ['TQQQ', 'QQQ', 'SOXX', 'SPY', 'NVDA', 'MU'];
  const stateLabels = {
    CONNECTING: '连接中', CONNECTED: '已连接', CONNECTED_DELAY_UNKNOWN: '已连接 · 延迟未知',
    CONNECTED_MARKET_CLOSED: '已连接 · 休市', REQUEST_BUDGET_PAUSED: '本机请求预算暂停',
    DATA_STALE: '数据陈旧', AUTH_EXPIRED: '认证失效', RATE_LIMITED: '限流',
    DISCONNECTED: '断开', PARTIAL_SYMBOL_UNAVAILABLE: '部分证券不可用'
  };
  let current = null;
  let filter = 'all';
  let inFlight = false;

  function stamp(value) {
    if (!value) return '未知';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return value;
    try { return new Intl.DateTimeFormat('zh-CN', {
      timeZone, year: 'numeric', month: '2-digit', day: '2-digit',
      hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false
    }).format(date); } catch (_) { return value; }
  }
  function node(tag, className, value) {
    const el = document.createElement(tag);
    if (className) el.className = className;
    if (value !== undefined && value !== null) el.textContent = String(value);
    return el;
  }
  function stateOf(name) {
    return current?.states?.find(x => x.provider === name) || null;
  }
  function quoteLabel(quote, provider) {
    if (!quote) return '暂无报价';
    const sourceAt = Date.parse(quote.provider_time);
    const stale = Number.isFinite(sourceAt) && Date.now() - sourceAt > 10 * 60 * 1000;
    if (provider?.state === 'DISCONNECTED' || provider?.state === 'AUTH_EXPIRED') return '已断开 · 保留最后报价';
    if (provider?.state === 'CONNECTED_MARKET_CLOSED' || quote.market_state === 'US_11' || quote.market_state === 'US_6') return stale ? '休市最新报价 · 数据陈旧' : '休市最新报价 · 非实时成交';
    if (stale) return '数据陈旧';
    return '最新缓存报价 · 实时性未核实';
  }
  function renderStates() {
    for (const name of ['moomoo', 'jin10']) {
      const state = stateOf(name);
      const pill = document.getElementById(`${name}-state`);
      const times = document.getElementById(`${name}-times`);
      if (!pill || !times) continue;
      pill.textContent = stateLabels[state?.state] || state?.state || '尚未启动';
      times.textContent = `连接心跳：${stamp(state?.heartbeat_at)} · 最近接收：${stamp(state?.last_received_at)} · 来源时间：${stamp(state?.last_source_at)}${state?.last_error_class ? ' · 状态原因：' + state.last_error_class : ''}`;
    }
  }
  function quoteElement(symbol, quote, symbolState, provider) {
    const article = node('article', 'snapshot-item');
    const head = node('div');
    head.append(node('strong', null, symbol));
    head.append(node('p', 'muted', quote?.display_name && quote.display_name !== symbol ? quote.display_name : '已核对证券代码'));
    const value = node('div', 'snapshot-value');
    value.append(node('strong', null, quote?.price_text ?? '暂无报价'));
    value.append(node('span', null, quote?.currency || '币种待核实'));
    const meta = node('div', 'snapshot-meta');
    const reason = symbolState?.reason ? ` · ${symbolState.reason}` : '';
    meta.textContent = `${quoteLabel(quote, provider)}${reason}\n来源时间：${stamp(quote?.provider_time)}\n本机收到：${stamp(quote?.received_at)}\n延迟：${quote?.delay_status || '未知'} · 市场：${quote?.market_state || '未知'}`;
    article.append(head, value, meta);
    return article;
  }
  function renderQuotes() {
    const bySymbol = new Map((current?.quotes || []).map(x => [x.symbol, x]));
    const states = new Map((current?.symbols || []).map(x => [x.symbol, x]));
    const moomoo = stateOf('moomoo');
    const all = [...new Set([...priority, ...bySymbol.keys()])];
    quoteBox.replaceChildren(...all.map(symbol => quoteElement(symbol, bySymbol.get(symbol), states.get(symbol), moomoo)));
    document.querySelectorAll('[data-watch-symbol]').forEach(el => {
      const symbol = el.dataset.watchSymbol;
      const quote = bySymbol.get(symbol);
      const reason = states.get(symbol)?.reason;
      el.textContent = quote ? `${quote.price_text} ${quote.currency || '(币种待核实)'} · ${quoteLabel(quote, moomoo)} · 来源 ${stamp(quote.provider_time)} · 本机收到 ${stamp(quote.received_at)}` :
        `暂无个人报价${reason ? ' · ' + reason : ' · 未在当前六只订阅范围内或尚未收到数据'}`;
    });
  }
  function renderNews() {
    const rows = (current?.news || []).filter(x => filter === 'all' || x.interest);
    if (!rows.length) {
      newsBox.replaceChildren(node('div', 'empty-state', filter === 'all' ? '暂无已缓存快讯；请查看上方连接状态。' : '当前缓存中没有匹配关注规则的快讯；可切换“全部快讯”。'));
      return;
    }
    newsBox.replaceChildren(...rows.map(item => {
      const article = node('article', 'news-item');
      const published = item.published_at_utc ? stamp(item.published_at_utc) :
        (item.published_raw ? `${item.published_raw}（时区未确认）` : '未知');
      article.append(node('p', 'news-meta', `${item.source_status} · 来源发布时间：${published} · 本机收到：${stamp(item.received_at)} · 版本 ${item.version}`));
      if (item.title) article.append(node('h3', null, item.title));
      const body = item.content || '';
      article.append(node('p', 'news-body', body.length > 900 ? `${body.slice(0, 900)}…（页面预览）` : body));
      if (item.source_url) {
        try {
          const url = new URL(item.source_url);
          if (url.protocol === 'https:') {
            const link = node('a', 'source-link', '查看原始来源');
            link.href = url.href; link.target = '_blank'; link.rel = 'noopener noreferrer';
            article.append(link);
          }
        } catch (_) { /* Invalid source links are not rendered. */ }
      }
      return article;
    }));
  }
  async function poll() {
    if (inFlight) return;
    inFlight = true;
    try {
      const reply = await fetch(endpoint, {credentials: 'same-origin', cache: 'no-store'});
      if (!reply.ok) throw new Error(`LOCAL_HTTP_${reply.status}`);
      current = await reply.json();
      renderStates(); renderQuotes(); renderNews();
    } catch (_) {
      for (const name of ['moomoo', 'jin10']) {
        const pill = document.getElementById(`${name}-state`);
        if (pill) pill.textContent = '本机缓存接口断开';
      }
      // Keep the last visible data, with a disconnected status.
    } finally { inFlight = false; }
  }
  document.querySelectorAll('[data-news-filter]').forEach(button => button.addEventListener('click', () => {
    filter = button.dataset.newsFilter;
    document.querySelectorAll('[data-news-filter]').forEach(b => {
      const selected = b === button;
      b.setAttribute('aria-pressed', String(selected));
      b.classList.toggle('button-primary', selected);
    });
    renderNews();
  }));
  poll();
  window.setInterval(poll, 5000);
})();
