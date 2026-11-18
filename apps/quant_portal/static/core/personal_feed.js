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
  const eventPanel = document.getElementById('event-filter-panel');
  const companyFilter = document.getElementById('event-company-filter');
  const categoryFilter = document.getElementById('event-category-filter');
  const timeFilter = document.getElementById('event-time-filter');
  const preferenceBox = document.getElementById('event-preference-controls');
  const preferenceStatus = document.getElementById('event-prefs-status');
  const priority = ['TQQQ', 'QQQ', 'SOXX', 'SPY', 'NVDA', 'MU'];
  const stateLabels = {
    CONNECTING: '连接中', CONNECTED: '已连接', CONNECTED_DELAY_UNKNOWN: '已连接 · 延迟未知',
    CONNECTED_MARKET_CLOSED: '已连接 · 休市', REQUEST_BUDGET_PAUSED: '本机请求预算暂停',
    DATA_STALE: '数据陈旧', AUTH_EXPIRED: '认证失效', RATE_LIMITED: '限流',
    DISCONNECTED: '断开', PARTIAL_SYMBOL_UNAVAILABLE: '部分证券不可用',
    NO_SUBSCRIPTIONS: '未选择行情订阅'
  };
  let current = null;
  let filter = 'interest';
  let inFlight = false;
  let quoteSignature = '';
  let selectionSignature = '';
  let interestSignature = '';
  let newsSignature = '';
  let renderedNews = [];
  let eventControlSignature = '';
  const sessionChoice = new Map();
  const language = document.documentElement.dataset.language || 'zh';
  const readingStateKey = 'quant.personal.news.reader.v18';
  let readingRestored = false;
  let lastNewsReading = null;
  const topBox = document.getElementById('personal-top10');
  const sentimentBox = document.getElementById('personal-sentiment');
  let topSignature = '';
  const analysisStates = {NOT_QUEUED:['待分析','Pending'],PENDING:['待分析','Pending'],RUNNING:['分析中','Analyzing'],
    COMPLETE:['完成','Complete'],INSUFFICIENT_EVIDENCE:['证据不足','Insufficient evidence'],
    FAILED:['失败','Failed'],REVIEW_PENDING:['模型已生成，待内容核对','Generated; content review pending'],
    REVIEW_REJECTED:['内容核对未通过','Content review rejected'],
    DRAFT:['AI 草稿 · 自动检查通过','AI draft · automated checks passed'],
    STAGED_PARTIAL:['部分来源字段已核对','Some source fields reviewed'],
    STAGED_REVIEWED:['分步开发草稿已核对','Staged development draft reviewed'],
    FALLBACK:['来源与规则提示可用','Source and rule notes available'],
    SUPERSEDED:['已被更正版本替代','Superseded']};
  const toneLabels = {POSITIVE:['正面','Positive'],NEGATIVE:['负面','Negative'],NEUTRAL:['中性','Neutral'],
    MIXED:['混合','Mixed'],UNKNOWN:['未知','Unknown']};
  const bilingual=(pair) => language === 'en' ? pair[1] : pair[0];
  const sessionLabels = {PREMARKET:'盘前', REGULAR:'常规', AFTERHOURS:'盘后', OVERNIGHT:'夜盘'};
  const sessionEnglish = {PREMARKET:'Pre-market', REGULAR:'Regular', AFTERHOURS:'After-hours', OVERNIGHT:'Overnight'};
  const sessionName = key => (language === 'en' ? sessionEnglish[key] : sessionLabels[key]) || bilingual(['时段未核实','Session unverified']);

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
    if (!value) return bilingual(['未知','Unknown']);
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
  function latestCacheTime(field) {
    const values = Object.values(current?.latest_quotes || {}).map(quote => quote?.[field])
      .filter(value => value && Number.isFinite(Date.parse(value)));
    return values.length ? values.reduce((best, value) => Date.parse(value) > Date.parse(best) ? value : best) : null;
  }
  function quoteLabel(quote, provider, selected=true) {
    if (!quote) return bilingual(['暂无报价','No quote available']);
    if (!selected) return bilingual(['未订阅 · 保留历史缓存','Not subscribed · historical cache retained']);
    const sourceAt = Date.parse(quote.source_at);
    const stale = Number.isFinite(sourceAt) && Date.now() - sourceAt > 10 * 60 * 1000;
    if (provider?.state === 'DISCONNECTED' || provider?.state === 'AUTH_EXPIRED' || provider?.worker_alive === 0) return bilingual(['采集已断开 · 保留最后报价','Collection disconnected · last quote retained']);
    if (stale) return bilingual(['报价来源时间较旧','Quote source time is old']);
    if (provider?.state === 'CONNECTED_MARKET_CLOSED') return bilingual(['连接活跃 · 当前常规市场休市','Connection active · regular market closed']);
    return bilingual(['最新缓存报价 · 实时性未核实','Latest cached quote · real-time status unverified']);
  }
  function renderStates() {
    const snapshot = stateOf('moomoo_snapshot');
    const health=document.getElementById('feed-health-summary');
    if (health) {
      const push=stateOf('moomoo'); const news=stateOf('jin10');
      const recent=(value,seconds) => {const age=Date.now()-Date.parse(value);return Number.isFinite(age)&&age>=0&&age<=seconds*1000;};
      const receiving=push?.worker_alive===1 && recent(push?.last_push_at,20);
      const newsOk=news?.worker_alive===1 && recent(news?.heartbeat_at,90) && recent(news?.last_success_at,180);
      // A provider deliberately not started on this deployment is a gate, not a failure.
      const gates=current?.provider_gates||{};
      const gateText={
        PENDING_SUPPORT_REPLY:['未在此部署启用：等待供应商对云端使用的答复','Not enabled here: awaiting the provider\'s reply on cloud use'],
        PENDING_WRITTEN_PERMISSION:['未在此部署启用：需供应商书面许可','Not enabled here: provider written permission required']};
      const quoteSpan=gates.moomoo
        ? node('span','health-warning',bilingual(['行情（moomoo）：'+gateText[gates.moomoo][0],'Quotes (moomoo): '+gateText[gates.moomoo][1]]))
        : node('span',receiving?'health-ok':'health-warning',bilingual(receiving?['行情：正在接收推送','Quotes: receiving push']:['行情：请检查更新状态','Quotes: check update status']));
      const newsSpan=gates.jin10
        ? node('span','health-warning',bilingual(['新闻（金十）：'+gateText[gates.jin10][0],'News (Jin10): '+gateText[gates.jin10][1]]))
        : node('span',newsOk?'health-ok':'health-warning',bilingual(newsOk?['新闻：采集正常','News: collection active']:['新闻：更新中断或尚无成功记录','News: interrupted or no successful receipt']));
      health.replaceChildren(quoteSpan, newsSpan,
        node('span','muted',bilingual(['报价时间见行情卡；新闻主张以原始来源为准。','Quote times appear on each card; news claims remain attributed to their sources.'])));
    }
    for (const name of ['moomoo', 'jin10']) {
      const state = stateOf(name);
      const pill = document.getElementById(`${name}-state`);
      const times = document.getElementById(`${name}-times`);
      if (!pill || !times) continue;
      pill.textContent = stateLabels[state?.state] || state?.state || '尚未启动';
      const pushAge=Date.now()-Date.parse(state?.last_push_at);
      if (name==='moomoo' && state?.worker_alive===1 && Number.isFinite(pushAge) && pushAge>=0 && pushAge<=20000)
        pill.textContent=bilingual(['正在接收推送','Receiving push']);
      const worker = state?.worker_alive === 0 ? '采集线程已退出' : state?.worker_alive === 1 ? '采集线程运行中' : '线程状态未知';
      times.textContent = name === 'moomoo' ?
        `${worker} · 心跳：${stamp(state?.heartbeat_at)} · 最近推送：${stamp(state?.last_push_at)} · 最新缓存报价收到时间：${stamp(latestCacheTime('received_at'))} · 最新分时段报价来源时间：${stamp(latestCacheTime('source_at'))} · 重复包（v1.1 计数）：${state?.duplicate_quote_packets ?? '未知'}` +
        ` · 分时段定时快照：${stateLabels[snapshot?.state] || snapshot?.state || '未启动'} · 最近成功：${stamp(snapshot?.last_success_at)}` +
        `${state?.last_error_class ? ' · 原因：' + state.last_error_class : ''}` :
        `${worker} · 心跳：${stamp(state?.heartbeat_at)} · 最近业务成功：${stamp(state?.last_success_at)} · 今日尝试（旧阶段仅下界）：${state?.business_attempts_today ?? '未知'} · v1.1业务成功：${state?.business_success_total ?? '未知'} · v1.1失败：${state?.business_failure_total ?? '未知'}${state?.coverage_gap ? ' · 可能有新闻缺口' : ''}${state?.last_error_class ? ' · 原因：' + state.last_error_class : ''}`;
    }
  }
  function quoteElement(symbol, quote, latest, profile, symbolState, provider, snapshotProvider, selected, sessionRows, capabilityRows) {
    const article = node('article', 'snapshot-item');
    const head = node('div');
    head.append(node('strong', null, symbol));
    head.append(node('p', 'muted', profile?.name || quote?.display_name || bilingual(['名称待核实','Name unverified'])));
    const selectedSession = sessionChoice.get(symbol) || 'AUTO';
    const picker = node('select', 'session-picker');
    picker.setAttribute('aria-label', `${symbol} ${bilingual(['报价时段','quote session'])}`);
    for (const [key, label] of [['AUTO',bilingual(['最近有来源时间','Latest with source time'])],...Object.keys(sessionLabels).map(key=>[key,sessionName(key)])]) {
      const option = node('option', null, label); option.value = key; picker.append(option);
    }
    picker.value = selectedSession;
    picker.addEventListener('change', () => { sessionChoice.set(symbol, picker.value); quoteSignature=''; renderQuotes(); });
    head.append(picker);
    const openChart = () => document.dispatchEvent(new CustomEvent('personal-chart-open', {
      detail: {symbol, session: sessionChoice.get(symbol) || 'AUTO'}}));
    const chartButton = node('button', 'button button-outline chart-open-button', bilingual(['查看曲线','View chart']));
    chartButton.type = 'button';
    chartButton.addEventListener('click', openChart);
    head.append(chartButton);
    article.addEventListener('click', event => {
      if (!event.target.closest('button,select,a')) openChart();
    });
    const bySession = new Map(sessionRows.map(x => [x.session,x]));
    const byCapability = new Map(capabilityRows.map(x => [x.session,x]));
    const shown = selectedSession === 'AUTO' ? latest || null : bySession.get(selectedSession) || null;
    const capability = selectedSession === 'AUTO' ? null : byCapability.get(selectedSession);
    const value = node('div', 'snapshot-value');
    value.append(node('strong', null, shown?.price_text ?? bilingual(['暂无报价','No quote available'])));
    value.append(node('span', null, shown?.currency || profile?.currency || bilingual(['供应商未标注币种','Currency not labeled by supplier'])));
    const meta = node('div', 'snapshot-meta');
    let details = null;
    const reason = symbolState?.reason ? ` · ${symbolState.reason}` : '';
    if (shown) {
      const timeText = shown.source_at ? stamp(shown.source_at) : '未知（供应商子报价无独立时间）';
      const dateHint = shown.parent_date_hint && !shown.trade_date ?
        `\n父报价日期线索：${shown.parent_date_hint}（非本时段成交日期）` : '';
      const closed = provider?.state === 'CONNECTED_MARKET_CLOSED' ? ' · 常规市场休市' : '';
      const activeFeed = shown.source_channel === 'REST_STOCK_QUOTE' ? snapshotProvider : provider;
      const feedState = stateLabels[activeFeed?.state] || activeFeed?.state || '未知';
      const marketCode = quote?.market_state || '供应商未提供';
      const dateText = shown.trade_date ? `${shown.trade_date}（供应商日期与来源时间的纽约日期一致；非独立交易日确认）` : '供应商未提供可确认的交易日';
      const delayText = shown.delay_status && shown.delay_status !== 'UNKNOWN' ? shown.delay_status : '供应商未标注延迟';
      const channelLabel = shown.source_channel === 'REST_STOCK_QUOTE' ? bilingual(['定时快照','Scheduled snapshot']) : shown.source_channel === 'WS_QUOTE' ? bilingual(['推送缓存','Push cache']) : bilingual(['历史缓存','Historical cache']);
      meta.textContent = `${sessionName(shown.session)} ${bilingual(['报价','quote'])} · ${channelLabel}${closed}${reason}` +
        `\n${bilingual(['来源时间：','Source time: '])}${timeText} · ${quoteLabel(shown, activeFeed, selected)}`;
      details=node('details','quote-details');
      details.append(node('summary',null,'数据详情'));
      details.append(node('p',null,`本机获取：${stamp(shown.received_at)}\n交易日：${dateText}${dateHint}\n采集：${feedState} · 常规市场状态：${marketCode}\n延迟：${delayText}\n币种依据：${shown.currency ? '供应商报价字段' : profile?.currency ? '证券资料字段' : '供应商未提供'}\n价格字段：${shown.provider_field_path || '未记录'} · 时间字段：${shown.source_time_field_path || '未记录'}`));
      if (!shown.source_at) meta.textContent += '\n来源时间缺失：不能判断报价新鲜度';
    } else {
      meta.textContent = `该时段暂无可用报价${reason}\n接入状态：${capability?.state || '尚未核实'}` +
        `\n最近检查：${stamp(capability?.checked_at)}；空字段不等于无权限或休市`;
    }
    const regular = bySession.get('REGULAR');
    if (regular && selectedSession !== 'REGULAR') {
      meta.textContent += `\n${bilingual(['常规报价参考：','Regular-session reference: '])}${regular.price_text} · ${bilingual(['来源','Source'])} ${stamp(regular.source_at)} ${bilingual(['（独立保留）','(retained separately)'])}`;
    }
    article.append(head, value, meta);
    if (details) article.append(details);
    return article;
  }
  function renderQuotes() {
    const signature = JSON.stringify([current?.quotes,current?.latest_quotes,current?.security_profiles,current?.session_quotes,current?.session_capabilities,
      current?.symbols,current?.selection,stateOf('moomoo')?.state,[...sessionChoice]]);
    if (signature === quoteSignature) return;
    quoteSignature = signature;
    const bySymbol = new Map((current?.quotes || []).map(x => [x.symbol, x]));
    const latest = current?.latest_quotes || {};
    const profiles = current?.security_profiles || {};
    const sessions = current?.session_quotes || [];
    const capabilities = current?.session_capabilities || [];
    const states = new Map((current?.symbols || []).map(x => [x.symbol, x]));
    const selected = new Set((current?.selection || []).filter(x => x.desired).map(x => x.symbol));
    const moomoo = stateOf('moomoo');
    const snapshot = stateOf('moomoo_snapshot');
    const all = [...new Set([...priority, ...(current?.selection || []).filter(x => x.desired).map(x => x.symbol), ...bySymbol.keys()])];
    quoteBox.replaceChildren(...all.map(symbol => quoteElement(symbol, bySymbol.get(symbol), latest[symbol], profiles[symbol], states.get(symbol), moomoo, snapshot,
      selected.has(symbol), sessions.filter(x => x.symbol === symbol),capabilities.filter(x => x.symbol === symbol))));
    document.querySelectorAll('[data-watch-symbol]').forEach(el => {
      const symbol = el.dataset.watchSymbol;
      const quote = latest[symbol];
      const reason = states.get(symbol)?.reason;
      el.textContent = quote ? `${quote.price_text} ${quote.currency || profiles[symbol]?.currency || bilingual(['（供应商未标注币种）','(currency unlabeled)'])} · ${sessionName(quote.session)} · ${quoteLabel(quote, quote.source_channel === 'REST_STOCK_QUOTE' ? snapshot : moomoo, selected.has(symbol))} · ${bilingual(['来源','Source'])} ${stamp(quote.source_at)}` :
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
  function renderEventControls() {
    const choices = current?.event_choices;
    if (!choices) return;
    const signature = JSON.stringify([choices,current?.event_preferences_effective]);
    if (signature === eventControlSignature) return;
    eventControlSignature = signature;
    const oldCompany = companyFilter.value, oldCategory = categoryFilter.value;
    companyFilter.replaceChildren(node('option',null,'全部'));
    companyFilter.firstChild.value='';
    choices.entities.forEach(x => { const option=node('option',null,x.label); option.value=x.id; companyFilter.append(option); });
    categoryFilter.replaceChildren(node('option',null,'全部'));
    categoryFilter.firstChild.value='';
    choices.categories.forEach(x => { const option=node('option',null,x.label); option.value=x.id; categoryFilter.append(option); });
    companyFilter.value=oldCompany; categoryFilter.value=oldCategory;
    preferenceBox.replaceChildren();
    const effective=current.event_preferences_effective || {entities:[],categories:[]};
    for (const [heading,key,rows] of [['关注公司和 ETF','entity',choices.entities],['关注类别','category',choices.categories]]) {
      const section=node('div','event-preference-section'); section.append(node('h4',null,heading));
      const selected=new Set(effective[key === 'entity' ? 'entities' : 'categories']);
      rows.forEach(row => {
        const label=node('label','event-check'); const input=node('input');
        input.type='checkbox'; input.name=key; input.value=row.id; input.checked=selected.has(row.id);
        label.append(input,node('span',null,row.label)); section.append(label);
      });
      preferenceBox.append(section);
    }
  }
  function filteredNews() {
    let rows=(current?.news || []).filter(x => filter === 'all' || (filter === 'interest' ? x.interest : x.focus_eligible));
    if (filter !== 'focus') return rows;
    if (companyFilter.value) rows=rows.filter(x => x.event_labels?.entities.some(e => e.id === companyFilter.value));
    if (categoryFilter.value) rows=rows.filter(x => x.event_labels?.categories.some(c => c.id === categoryFilter.value));
    if (timeFilter.value !== 'all') {
      const hours=timeFilter.value === '24h' ? 24 : 168;
      rows=rows.filter(x => {
        const published=Date.parse(x.published_at_utc || x.received_at || '');
        return Number.isFinite(published) && Date.now()-published <= hours*3600*1000;
      });
    }
    return [...rows].sort((a,b) => (b.rule_priority || 0)-(a.rule_priority || 0) ||
      (Date.parse(b.published_at_utc || b.received_at || '') || 0)-(Date.parse(a.published_at_utc || a.received_at || '') || 0));
  }
  function renderTop10() {
    if (!topBox) return;
    const rows=current?.top10 || [];
    document.getElementById('top10-count').textContent=String(rows.length);
    document.getElementById('news-count').textContent=String(current?.news?.length || 0);
    const signature=JSON.stringify(rows.map(x=>[x.item_key,x.version,x.rank?.score,x.analysis_status,x.headline_en]));
    if (signature===topSignature) return;
    if (topSignature && topBox.getBoundingClientRect().top < -30) {
      let notice=document.getElementById('top10-update');
      if (!notice) { notice=node('button','button button-outline','排名已更新 · 点击查看'); notice.id='top10-update';
        notice.type='button'; topBox.before(notice); }
      notice.hidden=false;
      notice.onclick=()=>{notice.hidden=true;topSignature='';renderTop10();topBox.scrollIntoView({block:'start'});};
      return;
    }
    topSignature=signature;
    if (!rows.length) { topBox.replaceChildren(node('div','empty-state','当前没有符合科技股及相关宏观范围的已缓存事件。')); return; }
    topBox.replaceChildren(...rows.map((row,index)=>{
      const button=node('button','top10-row'); button.type='button';
      const title=language==='en' ? (row.headline_en || `Translation pending — ${row.title}`) : (row.headline_zh || row.title);
      const why=row.scope==='DIRECT_SUBJECT' ? bilingual(['直接涉及关注对象','Directly concerns a followed entity']) :
        row.scope==='INDUSTRY' ? bilingual(['涉及科技产业链','Technology industry chain']) : bilingual(['相关宏观事件','Relevant macro event']);
      const phrase=row.evidence_phrase || bilingual(['匹配依据未知','Match unknown']);
      const evidence=language==='en' && /[\u3400-\u9fff]/.test(phrase) ? `Translation pending — ${phrase}` : phrase;
      button.append(node('span','top10-rank',`#${index+1} · ${title}`),
        node('span','news-meta',`${why} · ${evidence} · ${stamp(row.published_at_utc || row.received_at)}`),
        node('span','analysis-status',bilingual(['供应商报道 · 点击查看来源','Supplier report · open source details']) +
          (row.tone && row.tone!=='UNKNOWN' ? ` · ${bilingual(['文本语气','Text tone'])}: ${bilingual(toneLabels[row.tone]||toneLabels.UNKNOWN)}` : '')));
      button.title=title;
      button.onclick=()=>{
        window.quantOpenPanel?.('news');
        // Reading a news item never enqueues the paused v16 interpretation worker.
        filter='all'; document.querySelector('[data-news-filter="all"]')?.click();
        renderNews(true);
        document.querySelector(`[data-item-key="${row.item_key}"]`)?.scrollIntoView({behavior:'smooth',block:'center'});
      };
      return button;
    }));
  }
  function renderSentiment() {
    if (!sentimentBox) return;
    const rows=current?.sentiment_observation || [];
    document.getElementById('sentiment-count').textContent=rows.length ? `${rows[0].event_count} ${bilingual(['事件','events'])}` : bilingual(['待分析','Pending']);
    const grid=node('div','sentiment-grid');
    rows.forEach(row=>{
      const cell=node('div','sentiment-cell');
      cell.append(node('strong',null,`${row.hours}h · ${row.event_count} ${bilingual(['去重事件','deduplicated events'])}`),
        node('p','muted',`${bilingual(['未分析','Not analyzed'])}: ${row.unanalysed_count}`));
      if (row.insufficient) cell.append(node('p','muted',bilingual(['样本不足','Insufficient sample'])));
      else cell.append(node('p',null,Object.entries(row.news_tone_counts||{}).map(([tone,count])=>`${bilingual(toneLabels[tone]||toneLabels.UNKNOWN)} ${count}`).join(' · ') || bilingual(['暂无已完成分析','No completed analyses'])))
      cell.append(node('p','muted',bilingual(['实际行情变化：未作可比观察','Observed market movement: not assessed on a comparable basis'])));
      grid.append(cell);
    });
    sentimentBox.replaceChildren(grid);
    const worker=document.getElementById('analysis-worker-state');
    if (worker) worker.textContent=`${bilingual(['分析后台','Analysis worker'])}: ${current?.analysis_worker_state||'NOT_STARTED'} · ${bilingual(['旧契约','Old contract'])}: ${current?.analysis_contract_version||'未运行'} · ${bilingual(['分步试验','Staged batch'])}: ${current?.staged_analysis_status||'NOT_READY'} · ${bilingual(['队列','Queue'])}: ${current?.analysis_queue_count ?? 0}`;
    const counts=document.getElementById('news-screening-counts');
    const m=current?.news_screening_metrics||{};
    const legacy=current?.news_screening_counts||{};
    if (counts) counts.textContent=`${bilingual(['新收到','New received'])}: ${m.raw_received_rows ?? 0} · ${bilingual(['相关入库','Relevant stored'])}: ${m.relevant_new_versions ?? 0} · ${bilingual(['待筛','Pending review'])}: ${m.pending_rows ?? 0} · ${bilingual(['排除','Excluded'])}: ${m.excluded_rows ?? 0} · ${bilingual(['旧库已标记','Legacy labeled'])}: ${(legacy.INCLUDE||0)+(legacy.PENDING_REVIEW||0)+(legacy.EXCLUDE||0)}`;
  }
  function analysisCard(item) {
    const wrap=node('div','analysis-card');
    const status=item.analysis_status||'NOT_QUEUED';
    const available=Boolean(item.v18_event_features?.length || item.staged_extraction?.length ||
      item.staged_interpretation || (item.analysis && ['COMPLETE','DRAFT'].includes(status)));
    if (!available) {
      const details=node('details','analysis-unavailable');
      details.append(node('summary',null,bilingual(['解读暂不可用 · 查看原因','Interpretation unavailable · details'])));
      details.append(node('p','muted',`${bilingual(analysisStates[status]||analysisStates.NOT_QUEUED)} · ${bilingual(['当前没有通过内容检查的解读。可展开原文或查看来源页面。','No interpretation has passed the content checks. Read the report or open its source.'])}`));
      return details;
    }
    wrap.append(node('p','analysis-status',`${bilingual(['新闻解读','News interpretation'])}: ${bilingual(analysisStates[status]||analysisStates.NOT_QUEUED)}`));
    const categories=(item.event_labels?.categories || []).filter(x=>x.id!=='OTHER');
    const category=categories[0]?.id;
    const watchHints={
      EARNINGS_GUIDANCE:['关注公司随后披露的业绩或指引原件。','Check the company’s subsequent earnings or guidance filing.'],
      AI_CAPEX:['关注正式资本支出计划、订单和项目进度。','Check formal capital spending plans, orders and project updates.'],
      CHIPS_MEMORY:['关注分产品供需、价格和库存公告。','Check product-specific supply, prices and inventory disclosures.'],
      MACRO_RATES:['关注发布机构的正式数据和政策声明。','Check the issuing agency’s official data and policy statement.'],
      FINANCE_CREDIT:['关注正式融资条款、到期安排或并购公告。','Check formal financing terms, maturities or merger filings.']};
    const sourceHint=watchHints[category] || ['关注原始公告及后续更正。','Check the primary announcement and any correction.'];

    if (item.v18_event_features?.length) {
      wrap.append(node('p','analysis-status',bilingual(['事件与语气字段 · 同团队开发审阅；报道未独立核实',
        'Event and tone fields · same-team development review; report not independently verified'])));
      item.v18_event_features.forEach(row => wrap.append(node('p','analysis-fact',
        `${row.event_id} · ${row.entity_id} · ${bilingual(toneLabels[row.tone]||toneLabels.UNKNOWN)} · `+
        `${bilingual(['不确定性','Uncertainty'])}: ${row.uncertainty || 'UNKNOWN'} · `+
        `${bilingual(['前瞻可用时刻','Prospective availability'])}: ${stamp(row.signal_available_at_utc)}`)));
    }
    if (item.staged_relevance_scope === 'FULL_REPORT_ONLY')
      wrap.append(node('p','muted',bilingual(['筛选词仅来自整篇快讯，未绑定当前子事件；此子事件的相关性待核对。','The screening phrase comes from the full bulletin, not this subevent; this subevent\'s relevance needs review.'])));
    else
      wrap.append(node('p','muted',`${bilingual(['关联依据','Relevance evidence'])}: ${item.screening_v15?.evidence_phrase || item.focus_reason || bilingual(['规则未能定位短语','No rule phrase located'])}`));
    if (item.staged_task_id) {
      if (item.staged_source_candidate_count > 1)
        wrap.append(node('p','muted',bilingual(['这是一条多事件快讯；本次只审阅第一个可分段事件，其余事件尚未分析。','This bulletin contains multiple numbered items. Only the first was reviewed in this batch.'])));
      if (item.staged_extraction?.length) {
        wrap.append(node('p','analysis-status',bilingual(['分步提取：同团队仅核对以下字段；完整提取契约未通过','Staged extraction: only the fields below passed same-team review; the full contract failed'])));
        const labels={actor:['主体','Actor'],attribution:['发言人／署名','Speaker / attribution'],entities:['涉及对象','Mentioned entity'],numbers:['原文数字','Source number']};
        for (const field of item.staged_extraction) {
          const kind=field.field.split('.')[0];
          const line=node('p','analysis-fact');
          const unit=field.unit && !String(field.value||'').includes(field.unit) ? ` ${field.unit}` : '';
          line.append(node('span',null,`${bilingual(labels[kind]||['来源字段','Source field'])}: ${field.value ?? ''}${unit} · ${bilingual(['引文','Quote'])}: `));
          const quote=node('span',null,field.quote); quote.dataset.sourceOriginal='1'; line.append(quote); wrap.append(line);
        }
      }
      const draft=item.staged_interpretation;
      if (draft) {
        wrap.append(node('p','analysis-status',bilingual(['AI 条件式开发草稿 · 同团队审阅；报道本身未独立核实','Conditional AI development draft · same-team review; supplier report unverified'])));
        const translated=item.staged_translation;
        const fields=language==='en' && translated ?
          [translated.interpretation_en,translated.condition_en,translated.gap_en] :
          [draft.interpretation,draft.condition,draft.gap];
        if (language==='en' && !translated)
          wrap.append(node('p','muted',bilingual(['英文待翻译；以下保留原语言草稿。','English translation pending; original-language draft follows.'])));
        const names=[['条件式解读','Conditional interpretation'],['成立条件','Condition'],['证据缺口','Evidence gap']];
        fields.forEach((value,i)=>{if(value) wrap.append(node('p',null,`${bilingual(names[i])}: ${value}`));});
        wrap.append(node('p','muted',`${bilingual(['渠道标签','Channel tag'])}: ${draft.channel || 'UNKNOWN'}`));
      } else {
        wrap.append(node('p','muted',`${bilingual(['规则提示 · 后续观察','Rule note · What to watch'])}: ${bilingual(sourceHint)}`));
      }
      return wrap;
    }
    const a=item.analysis;
    if (!a || !['COMPLETE','DRAFT'].includes(status)) {
      wrap.append(node('p','muted',`${bilingual(['规则提示 · 后续观察','Rule note · What to watch'])}: ${bilingual(sourceHint)}`));
      if (status==='FALLBACK' && current?.v18_mode!=='BOUNDED_DEVELOPMENT_OLD_AUTO_QUEUE_PAUSED') {
        const retry=node('button','button button-outline',bilingual(['受控重试分析','Retry analysis once']));
        retry.type='button';
        retry.addEventListener('click',async()=>{
          try { const result=await postLocal(root.dataset.analysisPriorityUrl,{item_key:item.item_key,action:'retry'});
            retry.textContent=result.result==='RETRY_QUEUED' ? bilingual(['已排队','Queued']) :
              result.result==='ATTEMPT_LIMIT_REACHED' ? bilingual(['本版尝试次数已满','Version attempt limit reached']) : result.result;
          } catch (_) { retry.textContent=bilingual(['重试暂不可用','Retry unavailable']); }
        });
        wrap.append(retry);
      }
      return wrap;
    }
    if (status==='DRAFT') {
      wrap.append(node('p','analysis-status',bilingual(['AI分析草稿 · 自动检查通过 · 未经人工核验','AI draft · automated checks passed · not human verified'])));
      for (const evidence of a.evidence || []) {
        const line=node('p','analysis-fact');
        line.append(node('span',null,`${bilingual(['原文证据','Original evidence'])} ${evidence.id}: `));
        const original=node('span',null,evidence.quote); original.dataset.sourceOriginal='1';
        line.append(original); wrap.append(line);
      }
      const analysis=a[`analysis_${language}`];
      if (analysis) wrap.append(node('p',null,`${bilingual(['条件式解读','Conditional interpretation'])}: ${analysis}`));
      else wrap.append(node('p','muted',bilingual(['该语言解读待完成；可查看原文。','Interpretation in this language pending; see the source.'])));
      wrap.append(node('p','muted',`${bilingual(['后续观察','What to watch'])}: ${a[`watch_${language}`] || bilingual(sourceHint)}`));
      return wrap;
    }
    const value=(stem)=>a[`${stem}_${language}`] || (language==='en' ? `Translation pending — ${a[`${stem}_zh`]||''}` : '证据不足');
    wrap.append(node('p','analysis-status',item.analysis_display_kind === 'CODEX_REVIEWED' ?
      bilingual(['同团队核对通过 · 新闻主张仍待来源核实','Same-team review accepted · supplier claim remains unverified']) :
      bilingual(['机器生成翻译与分析 · 新闻仍待核实','Machine-generated translation and analysis · report unverified'])),
      node('h4',null,value('headline')),
      node('p',null,`${bilingual(['报道事实','Reported claim'])}: ${value('reported')}`));
    if (a.quote) {
      const quote=node('p','analysis-fact');
      quote.append(node('span',null,`${bilingual(['引文','Quote'])}: `));
      const originalQuote=node('span',null,a.quote); originalQuote.dataset.sourceOriginal='1';
      quote.append(originalQuote); wrap.append(quote);
    }
    wrap.append(node('p',null,`${bilingual(['模型推断','Model inference'])}: ${value('analysis')}`));
    for (const target of a.impacts||[]) wrap.append(node('p',null,`${bilingual(['条件性影响','Conditional impact'])} · ${target.name} · ${bilingual(toneLabels[target.direction]||toneLabels.UNKNOWN)}: ${target[`why_${language}`] || `Translation pending — ${target.why_zh}`}`));
    wrap.append(node('p','muted',bilingual(['行情观察：未与该新闻建立可比因果关系','Market observation: no comparable causal attribution established'])));
    return wrap;
  }
  function newsArticle(item) {
    const article = node('article', 'news-item');
    article.dataset.itemKey=item.item_key;
    // The old v16 automatic interpretation queue remains paused in v1.8.
    const published = item.published_at_utc ? stamp(item.published_at_utc) :
      (item.published_raw ? `${item.published_raw}（时区未确认）` : '未知');
    article.append(node('p', 'news-meta', `${bilingual(['来源：金十 · 供应商报道','Source: Jin10 · supplier report'])} · ${published}`));
    const modelTitle=item.analysis?.[`headline_${language}`];
    const sourceTitle=item.display_title || item.title || '原文未提供标题';
    const reviewedTranslation=['MACHINE_TRANSLATION_CODEX_REVIEWED','MACHINE_TRANSLATION_CODEX_CORRECTED'].includes(item.source_translation_status) ? item.source_translation_en : null;
    const translationTitle=reviewedTranslation?.split(/[.!?。！？\n]/,1)[0]?.trim();
    const headline=node('h3', null, language==='en' && translationTitle ? translationTitle :
      modelTitle || (language==='en' ? `Translation pending — ${sourceTitle}` : sourceTitle));
    if (language==='en' && reviewedTranslation) headline.setAttribute('translate','no');
    article.append(headline);
    if (item.display_title_kind === 'SOURCE_FIRST_SENTENCE') article.append(node('p','news-meta',bilingual(['原文首句摘录标题','Title excerpted from the original first sentence'])));
    if (item.display_title_kind === 'SOURCE_EXCERPT_INCOMPLETE') article.append(node('p','news-meta',bilingual(['原文开头摘录，句子不完整','Opening excerpt; sentence incomplete'])));
    const body = item.content || '';
    const original=node('details','news-original');
    original.dataset.sourceOriginal='1';
    original.append(node('summary',null,bilingual(['展开原文','Expand original report'])));
    original.append(node('p','news-body',body));
    original.append(node('p','news-meta',`${bilingual(['收到时间','Received'])}: ${stamp(item.received_at)} · ${bilingual(['版本','Version'])} ${item.version}`));
    article.append(original);
    if (reviewedTranslation) {
      const translated=node(language==='en' ? 'div' : 'details','news-translation');
      if (language==='en') translated.setAttribute('translate','no');
      if (language==='zh') translated.append(node('summary',null,'查看英文机器翻译'));
      translated.append(node('p','news-meta',item.source_translation_status==='MACHINE_TRANSLATION_CODEX_CORRECTED' ?
        bilingual(['原文机译经同团队校订；供应商报道未独立核实',
          'Machine translation of original, corrected in same-team review; supplier report unverified']) :
        bilingual(['原文机器翻译 · 同团队开发审阅；供应商报道未独立核实',
          'Machine translation of original · same-team development review; supplier report unverified'])));
      translated.append(node('p','news-body',reviewedTranslation));
      article.append(translated);
    } else if (language==='en') {
      article.append(node('p','muted','Original English translation pending; the source text remains available above.'));
    }
    article.append(analysisCard(item));
    if (filter === 'focus') {
      const labels=item.event_labels || {};
      article.append(node('p','event-labels',`相关对象：${(labels.entities || []).map(x => x.label).join('、') || '未明确'} · 类别：${(labels.categories || []).map(x => x.label).join('、') || '其他'} · ${item.rule_priority_label || '规则排序'}`));
      article.append(node('p','event-reason',`关注原因：${item.focus_reason || '无'} · 匹配短语：${[...(labels.entities || []),...(labels.categories || [])].map(x => x.phrase).filter(Boolean).join('、') || '无'} · 规则 ${labels.rule_version || '未知'}`));
      if (labels.claim_stage && labels.claim_stage !== 'GENERAL') article.append(node('p','news-meta',`报道类型线索：${labels.claim_stage}；仅为文字规则，未核实事实`));
    }
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
  }
  function renderNews(force=false) {
    const rows = filteredNews();
    const signature = `${filter}|${companyFilter.value}|${categoryFilter.value}|${timeFilter.value}|` +
      rows.map(x => `${x.item_key}:${x.version}:${x.focus_eligible}:${x.rule_priority}:${x.analysis_status}:${x.analysis_completed_at}:${x.source_translation_status}:${x.v18_event_features?.length||0}`).join('|');
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
      newsBox.replaceChildren(node('div', 'empty-state', filter === 'all' ? '暂无已缓存快讯；请查看上方连接状态。' : '当前缓存和筛选条件中没有匹配快讯；可切换“全部快讯”或调整关注范围。'));
      return;
    }
    if (filter !== 'focus') { newsBox.replaceChildren(...rows.map(newsArticle)); restoreReading(); return; }
    const groups=new Map();
    rows.forEach(item => {
      const key=item.event_labels?.event_group_id || item.item_key;
      if (!groups.has(key)) groups.set(key,[]);
      groups.get(key).push(item);
    });
    const entries=[];
    groups.forEach(group => {
      entries.push(newsArticle(group[0]));
      if (group.length > 1) {
        const details=node('details','related-reports');
        details.append(node('summary',null,`疑似相关报道 ${group.length-1} 条（非独立确认）`));
        group.slice(1).forEach(item => details.append(newsArticle(item)));
        entries.push(details);
      }
    });
    newsBox.replaceChildren(...entries);
    restoreReading();
  }
  function captureReading() {
    const bounds=newsBox.getBoundingClientRect();
    if (bounds.top > window.innerHeight * 0.75 || bounds.bottom < window.innerHeight * 0.2) return;
    const visible=[...newsBox.querySelectorAll('article.news-item')].find(el => el.getBoundingClientRect().bottom > 80);
    if (!visible) return;
    const openOriginal=[...newsBox.querySelectorAll('article.news-item')].filter(el=>el.querySelector('details.news-original')?.open).map(el=>el.dataset.itemKey);
    lastNewsReading={scrollY:window.scrollY,anchor:visible.dataset.itemKey,openOriginal};
  }
  function saveReading() {
    captureReading();
    const position=lastNewsReading || {scrollY:window.scrollY,anchor:null,openOriginal:[]};
    try { sessionStorage.setItem(readingStateKey,JSON.stringify({filter,company:companyFilter.value,
      category:categoryFilter.value,time:timeFilter.value,...position})); } catch (_) { /* storage optional */ }
  }
  function restoreReading() {
    if (readingRestored) return;
    let state;
    try { state=JSON.parse(sessionStorage.getItem(readingStateKey)||'null'); } catch (_) { state=null; }
    if (!state) { readingRestored=true; return; }
    for (const key of state.openOriginal||[]) {
      const article=[...newsBox.querySelectorAll('article.news-item')].find(el=>el.dataset.itemKey===key);
      const detail=article?.querySelector('details.news-original'); if (detail) detail.open=true;
    }
    const anchor=[...newsBox.querySelectorAll('article.news-item')].find(el=>el.dataset.itemKey===state.anchor);
    if (anchor) anchor.scrollIntoView({block:'start'});
    else if (Number.isFinite(state.scrollY)) window.scrollTo(0,state.scrollY);
    readingRestored=true;
    try { sessionStorage.removeItem(readingStateKey); } catch (_) { /* storage optional */ }
  }
  document.querySelector('.language-switch')?.addEventListener('submit',saveReading);
  window.addEventListener('scroll',captureReading,{passive:true});
  newsBox.addEventListener('click',()=>requestAnimationFrame(captureReading));
  async function poll() {
    if (inFlight) return;
    inFlight = true;
    try {
      const reply = await fetch(endpoint, {credentials: 'same-origin', cache: 'no-store'});
      if (!reply.ok) throw new Error(`LOCAL_HTTP_${reply.status}`);
      current = await reply.json();
      renderStates(); renderQuotes(); renderSubscriptionControls(); renderInterestControls(); renderEventControls();
      if (!readingRestored) {
        try {
          const state=JSON.parse(sessionStorage.getItem(readingStateKey)||'null');
          if (state) {
            filter=['all','interest','focus'].includes(state.filter) ? state.filter : 'all';
            companyFilter.value=state.company||''; categoryFilter.value=state.category||''; timeFilter.value=state.time||'';
            document.querySelectorAll('[data-news-filter]').forEach(b=>{b.setAttribute('aria-pressed',String(b.dataset.newsFilter===filter));b.classList.toggle('button-primary',b.dataset.newsFilter===filter);});
          }
        } catch (_) { /* storage optional */ }
      }
      renderTop10(); renderSentiment(); renderNews();
    } catch (_) {
      const health=document.getElementById('feed-health-summary');
      if (health) health.replaceChildren(node('span','health-warning',bilingual(['页面数据连接中断，以下为上次缓存。','Page data connection interrupted; showing the previous cache.'])));
      for (const name of ['moomoo', 'jin10']) {
        const pill = document.getElementById(`${name}-state`);
        if (pill) pill.textContent = '本机缓存接口断开';
      }
      // Keep the last visible data, with a disconnected status.
    } finally { inFlight = false; }
  }
  document.querySelectorAll('[data-news-filter]').forEach(button => button.addEventListener('click', () => {
    filter = button.dataset.newsFilter;
    eventPanel.hidden=filter !== 'focus';
    document.querySelectorAll('[data-news-filter]').forEach(b => {
      const selected = b === button;
      b.setAttribute('aria-pressed', String(selected));
      b.classList.toggle('button-primary', selected);
    });
    renderNews(true);
  }));
  for (const select of [companyFilter,categoryFilter,timeFilter]) select.addEventListener('change',() => renderNews(true));
  document.getElementById('event-apply-preset').addEventListener('click',() => {
    const preset=current?.event_choices?.tech_preset;
    if (!preset) return;
    for (const input of preferenceBox.querySelectorAll('input[type="checkbox"]'))
      input.checked=(input.name === 'entity' ? preset.entities : preset.categories).includes(input.value);
    preferenceStatus.textContent='已填入预设，尚未保存';
  });
  document.getElementById('event-save-prefs').addEventListener('click',async () => {
    const data=new URLSearchParams();
    for (const input of preferenceBox.querySelectorAll('input[type="checkbox"]:checked')) data.append(input.name,input.value);
    try {
      const response=await fetch(root.dataset.eventPreferencesUrl,{method:'POST',credentials:'same-origin',
        headers:{'X-CSRFToken':csrfToken(),'Content-Type':'application/x-www-form-urlencoded'},body:data,cache:'no-store'});
      if (!response.ok) throw new Error(`LOCAL_HTTP_${response.status}`);
      preferenceStatus.textContent='已保存'; eventControlSignature=''; newsSignature=''; await poll(); renderNews(true);
    } catch (error) { preferenceStatus.textContent=`保存失败：${error.message}`; }
  });
  newsBanner.addEventListener('click',() => { renderNews(true); newsBox.scrollIntoView({behavior:'smooth',block:'start'}); });
  poll();
  window.setInterval(poll, 5000);
})();
