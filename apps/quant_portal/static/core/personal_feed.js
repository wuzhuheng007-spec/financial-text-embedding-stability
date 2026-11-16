/* Local cache polling only. No provider URLs, credentials or HTML injection. */
(() => {
  const root = document.getElementById('personal-feed-root');
  if (!root) return;
  const endpoint = root.dataset.feedUrl;
  const timeZone = root.dataset.timeZone || 'Asia/Shanghai';
  const quoteBox = document.getElementById('personal-quotes');
  const newsBox = document.getElementById('personal-news');
  const newsBanner = document.getElementById('new-news-banner');
  const interestBox = document.getElementById('news-interest-controls');
  const priority = ['TQQQ', 'QQQ', 'SOXX', 'SPY', 'NVDA', 'MU'];
  const stateLabels = {
    CONNECTING: '连接中', CONNECTED: '已连接', CONNECTED_DELAY_UNKNOWN: '已连接 · 延迟未知',
    CONNECTED_MARKET_CLOSED: '已连接 · 休市', REQUEST_BUDGET_PAUSED: '本机请求预算暂停',
    DATA_STALE: '数据陈旧', AUTH_EXPIRED: '认证失效', RATE_LIMITED: '限流',
    DISCONNECTED: '断开', PARTIAL_SYMBOL_UNAVAILABLE: '部分证券不可用',
    NO_SUBSCRIPTIONS: '未选择行情订阅'
  };
  let current = null;
  let filter = 'all';
  let inFlight = false;
  let quoteSignature = '';
  let selectionSignature = '';
  let interestSignature = '';
  let newsSignature = '';
  let renderedNews = [];

  function csrfToken() {
    return document.querySelector('input[name="csrfmiddlewaretoken"]')?.value || '';
  }
  async function postLocal(url, data) {
    const response = await fetch(url, {method: 'POST', credentials: 'same-origin',
      headers: {'X-CSRFToken': csrfToken(), 'Content-Type': 'application/x-www-form-urlencoded'},
      body: new URLSearchParams(data), cache: 'no-store'});
    const result = await response.json();
    if (!response.ok) throw new Error(result.result || `LOCAL_HTTP_${response.status}`);
    return result;
  }

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
  function quoteLabel(quote, provider, selected=true) {
    if (!quote) return '暂无报价';
    if (!selected) return '未订阅 · 保留历史缓存';
    const sourceAt = Date.parse(quote.provider_time);
    const stale = Number.isFinite(sourceAt) && Date.now() - sourceAt > 10 * 60 * 1000;
    if (provider?.state === 'DISCONNECTED' || provider?.state === 'AUTH_EXPIRED' || provider?.worker_alive === 0) return '采集已断开 · 保留最后报价';
    if (provider?.state === 'CONNECTED_MARKET_CLOSED' || quote.market_state === 'US_11' || quote.market_state === 'US_6') return '连接活跃 · 休市报价未变化（非实时成交）';
    if (stale) return '连接状态见上方 · 报价来源时间较旧';
    return '最新缓存报价 · 实时性未核实';
  }
  function renderStates() {
    for (const name of ['moomoo', 'jin10']) {
      const state = stateOf(name);
      const pill = document.getElementById(`${name}-state`);
      const times = document.getElementById(`${name}-times`);
      if (!pill || !times) continue;
      pill.textContent = stateLabels[state?.state] || state?.state || '尚未启动';
      const worker = state?.worker_alive === 0 ? '采集线程已退出' : state?.worker_alive === 1 ? '采集线程运行中' : '线程状态未知';
      times.textContent = name === 'moomoo' ?
        `${worker} · 心跳：${stamp(state?.heartbeat_at)} · 最近推送：${stamp(state?.last_push_at)} · 最近新增报价（v1.1）：${stamp(state?.last_quote_saved_at)} · 最新报价来源时间：${stamp(state?.last_source_at)} · 重复包（v1.1）：${state?.duplicate_quote_packets ?? '未知'}${state?.last_error_class ? ' · 原因：' + state.last_error_class : ''}` :
        `${worker} · 心跳：${stamp(state?.heartbeat_at)} · 最近业务成功：${stamp(state?.last_success_at)} · 今日尝试（旧阶段仅下界）：${state?.business_attempts_today ?? '未知'} · v1.1业务成功：${state?.business_success_total ?? '未知'} · v1.1失败：${state?.business_failure_total ?? '未知'}${state?.coverage_gap ? ' · 可能有新闻缺口' : ''}${state?.last_error_class ? ' · 原因：' + state.last_error_class : ''}`;
    }
  }
  function quoteElement(symbol, quote, symbolState, provider, selected) {
    const article = node('article', 'snapshot-item');
    const head = node('div');
    head.append(node('strong', null, symbol));
    head.append(node('p', 'muted', quote?.display_name && quote.display_name !== symbol ? quote.display_name : '已核对证券代码'));
    const value = node('div', 'snapshot-value');
    value.append(node('strong', null, quote?.price_text ?? '暂无报价'));
    value.append(node('span', null, quote?.currency || '币种待核实'));
    const meta = node('div', 'snapshot-meta');
    const reason = symbolState?.reason ? ` · ${symbolState.reason}` : '';
    meta.textContent = `${quoteLabel(quote, provider, selected)}${reason}\n来源时间：${stamp(quote?.provider_time)}\n本机收到：${stamp(quote?.received_at)}\n延迟：${quote?.delay_status || '未知'} · 市场：${quote?.market_state || '未知'}`;
    article.append(head, value, meta);
    return article;
  }
  function renderQuotes() {
    const signature = JSON.stringify([current?.quotes,current?.symbols,current?.selection,stateOf('moomoo')?.state]);
    if (signature === quoteSignature) return;
    quoteSignature = signature;
    const bySymbol = new Map((current?.quotes || []).map(x => [x.symbol, x]));
    const states = new Map((current?.symbols || []).map(x => [x.symbol, x]));
    const selected = new Set((current?.selection || []).filter(x => x.desired).map(x => x.symbol));
    const moomoo = stateOf('moomoo');
    const all = [...new Set([...priority, ...(current?.selection || []).filter(x => x.desired).map(x => x.symbol), ...bySymbol.keys()])];
    quoteBox.replaceChildren(...all.map(symbol => quoteElement(symbol, bySymbol.get(symbol), states.get(symbol), moomoo, selected.has(symbol))));
    document.querySelectorAll('[data-watch-symbol]').forEach(el => {
      const symbol = el.dataset.watchSymbol;
      const quote = bySymbol.get(symbol);
      const reason = states.get(symbol)?.reason;
      el.textContent = quote ? `${quote.price_text} ${quote.currency || '(币种未知)'} · ${quoteLabel(quote, moomoo, selected.has(symbol))} · 来源 ${stamp(quote.provider_time)} · 本机收到 ${stamp(quote.received_at)}` :
        `暂无个人报价${reason ? ' · ' + reason : ' · 未订阅或尚未收到推送'}`;
    });
  }
  function renderSubscriptionControls() {
    const signature = JSON.stringify([current?.selection,current?.symbols,current?.verified_symbols,current?.subscription_budget]);
    if (signature === selectionSignature) return;
    selectionSignature = signature;
    const selected = (current?.selection || []).filter(x => x.desired).map(x => x.symbol);
    const states = new Map((current?.symbols || []).map(x => [x.symbol,x]));
    const verified = current?.verified_symbols || {};
    document.querySelectorAll('[data-subscription-control]').forEach(box => {
      const symbol = box.dataset.subscriptionControl;
      const isSelected = selected.includes(symbol);
      const state = states.get(symbol)?.state;
      const label = state === 'WAITING_STOP' ? '等待停止订阅确认' : isSelected ? (state === 'RECEIVED' || state === 'SUBSCRIBED' ? '已订阅 · 已验证代码' :
        state === 'UNAVAILABLE' ? `供应商拒绝 · ${states.get(symbol)?.reason || '原因未知'}` : '等待订阅确认 · 已验证代码') :
        verified[symbol] ? '已加入自选 · 已验证代码 · 未订阅' : '已加入自选 · 暂无已核实可用代码';
      const status = node('span','subscription-status',label);
      box.replaceChildren(status);
      if (state === 'WAITING_STOP' || state === 'WAITING_SUBSCRIPTION') {
        // Wait for the collector to acknowledge the requested state.
      } else if (isSelected) {
        const button = node('button','button button-outline','停止订阅'); button.type = 'button';
        button.addEventListener('click', () => changeSelection(box,symbol,'unsubscribe'));
        box.append(button);
      } else if (verified[symbol]) {
        let select = null;
        if (selected.length >= (current?.subscription_budget || 6)) {
          select = node('select'); select.setAttribute('aria-label',`替换哪只证券以订阅 ${symbol}`);
          const placeholder = node('option',null,'选择要替换的订阅'); placeholder.value=''; select.append(placeholder);
          selected.forEach(old => { const option=node('option',null,old); option.value=old; select.append(option); });
          box.append(select);
        }
        const button = node('button','button button-outline','订阅行情'); button.type='button';
        button.addEventListener('click', () => changeSelection(box,symbol,'subscribe',select?.value || ''));
        box.append(button);
      }
    });
  }
  async function changeSelection(box,symbol,action,replace) {
    const status = box.querySelector('.subscription-status');
    if (action === 'subscribe' && (current?.selection || []).filter(x => x.desired).length >=
        (current?.subscription_budget || 6) && !replace) {
      status.textContent='本机六键预算已满；请先选要替换的证券'; return;
    }
    try {
      const result = await postLocal(root.dataset.selectionUrl,{symbol,action,replace_symbol:replace});
      status.textContent = result.result === 'PENDING_SUBSCRIBE' ? '等待服务端订阅确认' :
        result.result === 'PENDING_STOP' ? '等待服务端停止确认' : result.result;
      selectionSignature=''; quoteSignature=''; await poll();
    } catch (error) { status.textContent=`操作失败：${error.message}`; }
  }
  function renderInterestControls() {
    const signature=JSON.stringify(current?.news_interests || []);
    if (signature === interestSignature) return;
    interestSignature=signature;
    interestBox.replaceChildren(node('span','subscription-status','独立新闻关注：'));
    const names={SNDK:'闪迪',SKHYNIX:'SK 海力士',SPACEX:'SpaceX'};
    (current?.news_interests || []).forEach(row => {
      const button=node('button',`button ${row.enabled ? 'button-primary' : 'button-outline'}`,names[row.interest_key] || row.interest_key);
      button.type='button'; button.setAttribute('aria-pressed',String(Boolean(row.enabled)));
      button.addEventListener('click',async () => {
        try { await postLocal(root.dataset.interestUrl,{interest_key:row.interest_key,enabled:row.enabled ? '0':'1'});
          interestSignature=''; newsSignature=''; await poll(); renderNews(true);
        } catch (error) { button.textContent=`失败：${error.message}`; }
      });
      interestBox.append(button);
    });
  }
  function filteredNews() { return (current?.news || []).filter(x => filter === 'all' || x.interest); }
  function renderNews(force=false) {
    const rows = filteredNews();
    const signature = rows.map(x => `${x.item_key}:${x.version}`).join('|');
    if (!force && signature === newsSignature) return;
    const readingOlder = newsSignature && newsBox.getBoundingClientRect().top < -20;
    if (!force && readingOlder) {
      const oldKeys = new Set(renderedNews.map(x => x.item_key));
      const count = rows.filter(x => !oldKeys.has(x.item_key)).length;
      newsBanner.textContent = count ? `有 ${count} 条新消息 · 点击回到最新` : '快讯已更新 · 点击回到最新';
      newsBanner.hidden = false;
      return;
    }
    newsSignature=signature; renderedNews=rows; newsBanner.hidden=true;
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
            const link = node('a', 'source-link', '查看来源页面');
            link.href = url.href; link.target = '_blank'; link.rel = 'noopener noreferrer';
            article.append(link);
          }
        } catch (_) { /* Invalid source links are not rendered. */ }
      } else article.append(node('p','news-meta','原文入口未提供'));
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
      renderStates(); renderQuotes(); renderSubscriptionControls(); renderInterestControls(); renderNews();
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
    renderNews(true);
  }));
  newsBanner.addEventListener('click',() => { renderNews(true); newsBox.scrollIntoView({behavior:'smooth',block:'start'}); });
  poll();
  window.setInterval(poll, 5000);
})();
