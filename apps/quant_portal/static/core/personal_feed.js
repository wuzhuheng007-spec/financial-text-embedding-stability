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
  let filter = 'all';
  let inFlight = false;
  let quoteSignature = '';
  let selectionSignature = '';
  let interestSignature = '';
  let newsSignature = '';
  let renderedNews = [];
  let eventControlSignature = '';
  const sessionChoice = new Map();
  const language = document.documentElement.dataset.language || 'zh';
  const topBox = document.getElementById('personal-top10');
  const sentimentBox = document.getElementById('personal-sentiment');
  let topSignature = '';
  const analysisStates = {NOT_QUEUED:['待分析','Pending'],PENDING:['待分析','Pending'],RUNNING:['分析中','Analyzing'],
    COMPLETE:['完成','Complete'],INSUFFICIENT_EVIDENCE:['证据不足','Insufficient evidence'],
    FAILED:['失败','Failed'],REVIEW_PENDING:['模型已生成，待内容核对','Generated; content review pending'],
    REVIEW_REJECTED:['内容核对未通过','Content review rejected'],
    SUPERSEDED:['已被更正版本替代','Superseded']};
  const toneLabels = {POSITIVE:['正面','Positive'],NEGATIVE:['负面','Negative'],NEUTRAL:['中性','Neutral'],
    MIXED:['混合','Mixed'],UNKNOWN:['未知','Unknown']};
  const bilingual=(pair) => language === 'en' ? pair[1] : pair[0];
  const sessionLabels = {PREMARKET:'盘前', REGULAR:'常规', AFTERHOURS:'盘后', OVERNIGHT:'夜盘'};

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
    if (provider?.state === 'CONNECTED_MARKET_CLOSED' || quote.market_state === 'US_6') return '连接活跃 · 休市报价未变化（非实时成交）';
    if (quote.market_state === 'US_11') return '盘后已结束 · 夜盘状态未核实';
    if (stale) return '连接状态见上方 · 报价来源时间较旧';
    return '最新缓存报价 · 实时性未核实';
  }
  function renderStates() {
    const snapshot = stateOf('moomoo_snapshot');
    for (const name of ['moomoo', 'jin10']) {
      const state = stateOf(name);
      const pill = document.getElementById(`${name}-state`);
      const times = document.getElementById(`${name}-times`);
      if (!pill || !times) continue;
      pill.textContent = stateLabels[state?.state] || state?.state || '尚未启动';
      const worker = state?.worker_alive === 0 ? '采集线程已退出' : state?.worker_alive === 1 ? '采集线程运行中' : '线程状态未知';
      times.textContent = name === 'moomoo' ?
        `${worker} · 心跳：${stamp(state?.heartbeat_at)} · 最近推送：${stamp(state?.last_push_at)} · 最近新增报价（v1.1）：${stamp(state?.last_quote_saved_at)} · 最新报价来源时间：${stamp(state?.last_source_at)} · 重复包（v1.1）：${state?.duplicate_quote_packets ?? '未知'}` +
        ` · 分时段定时快照：${stateLabels[snapshot?.state] || snapshot?.state || '未启动'} · 最近成功：${stamp(snapshot?.last_success_at)}` +
        `${state?.last_error_class ? ' · 原因：' + state.last_error_class : ''}` :
        `${worker} · 心跳：${stamp(state?.heartbeat_at)} · 最近业务成功：${stamp(state?.last_success_at)} · 今日尝试（旧阶段仅下界）：${state?.business_attempts_today ?? '未知'} · v1.1业务成功：${state?.business_success_total ?? '未知'} · v1.1失败：${state?.business_failure_total ?? '未知'}${state?.coverage_gap ? ' · 可能有新闻缺口' : ''}${state?.last_error_class ? ' · 原因：' + state.last_error_class : ''}`;
    }
  }
  function quoteElement(symbol, quote, symbolState, provider, snapshotProvider, selected, sessionRows, capabilityRows) {
    const article = node('article', 'snapshot-item');
    const head = node('div');
    head.append(node('strong', null, symbol));
    head.append(node('p', 'muted', quote?.display_name && quote.display_name !== symbol ? quote.display_name : '已核对证券代码'));
    const selectedSession = sessionChoice.get(symbol) || 'AUTO';
    const picker = node('select', 'session-picker');
    picker.setAttribute('aria-label', `${symbol} 报价时段`);
    for (const [key, label] of [['AUTO','最近有来源时间'],...Object.entries(sessionLabels)]) {
      const option = node('option', null, label); option.value = key; picker.append(option);
    }
    picker.value = selectedSession;
    picker.addEventListener('change', () => { sessionChoice.set(symbol, picker.value); quoteSignature=''; renderQuotes(); });
    head.append(picker);
    const openChart = () => document.dispatchEvent(new CustomEvent('personal-chart-open', {
      detail: {symbol, session: sessionChoice.get(symbol) || 'AUTO'}}));
    const chartButton = node('button', 'button button-outline chart-open-button', '查看曲线');
    chartButton.type = 'button';
    chartButton.addEventListener('click', openChart);
    head.append(chartButton);
    article.addEventListener('click', event => {
      if (!event.target.closest('button,select,a')) openChart();
    });
    const bySession = new Map(sessionRows.map(x => [x.session,x]));
    const byCapability = new Map(capabilityRows.map(x => [x.session,x]));
    const sourceRank = {REGULAR:4,OVERNIGHT:3,AFTERHOURS:2,PREMARKET:1};
    const sourced = sessionRows.filter(x => x.source_at && Number.isFinite(Date.parse(x.source_at)))
      .sort((a,b) => Date.parse(b.source_at)-Date.parse(a.source_at) ||
        (sourceRank[b.session] || 0)-(sourceRank[a.session] || 0));
    const shown = selectedSession === 'AUTO' ? sourced[0] || null : bySession.get(selectedSession) || null;
    const capability = selectedSession === 'AUTO' ? null : byCapability.get(selectedSession);
    const value = node('div', 'snapshot-value');
    value.append(node('strong', null, shown?.price_text ?? (selectedSession === 'AUTO' ? quote?.price_text : null) ?? '暂无报价'));
    value.append(node('span', null, shown?.currency || quote?.currency || '币种待核实'));
    const meta = node('div', 'snapshot-meta');
    const reason = symbolState?.reason ? ` · ${symbolState.reason}` : '';
    if (shown) {
      const timeText = shown.source_at ? stamp(shown.source_at) : '未知（供应商子报价无独立时间）';
      const dateHint = shown.parent_date_hint && !shown.trade_date ?
        `\n父报价日期线索：${shown.parent_date_hint}（非本时段成交日期）` : '';
      const closed = provider?.state === 'CONNECTED_MARKET_CLOSED' ? ' · 当前市场休市，非实时成交' : '';
      const activeFeed = shown.source_channel === 'REST_STOCK_QUOTE' ? snapshotProvider : provider;
      const feedState = stateLabels[activeFeed?.state] || activeFeed?.state || '未知';
      const marketCode = quote?.market_state === 'US_11' ? 'US_11（盘后结束；夜盘另行判断）' :
        quote?.market_state || '未知';
      meta.textContent = `${sessionLabels[shown.session]}报价 · ${shown.source_channel === 'REST_STOCK_QUOTE' ? '定时快照' : '原有推送'}${closed}${reason}` +
        `\n来源时间：${timeText}\n本机获取：${stamp(shown.received_at)}\n交易日：${shown.trade_date || '未知'}` +
        `${dateHint}\n采集：${feedState} · 市场状态：${marketCode}` +
        `\n延迟：${shown.delay_status || 'UNKNOWN'} · 币种：${shown.currency || '未知'}` +
        `\n价格字段：${shown.provider_field_path} · 时间字段：${shown.source_time_field_path || '未知'}`;
      if (!shown.source_at) meta.textContent += '\n时间证据不足：不能判定此报价仍属当前时段或实时更新';
    } else if (selectedSession === 'AUTO' && quote) {
      meta.textContent = `${quoteLabel(quote, provider, selected)} · 原有未分时段缓存${reason}` +
        `\n来源时间：${stamp(quote.provider_time)}\n本机收到：${stamp(quote.received_at)}\n时段：未核实 · 延迟：${quote.delay_status || '未知'}`;
    } else {
      meta.textContent = `该时段暂无可用报价${reason}\n接入状态：${capability?.state || '尚未核实'}` +
        `\n最近检查：${stamp(capability?.checked_at)}；空字段不等于无权限或休市`;
    }
    const regular = bySession.get('REGULAR');
    if (regular && selectedSession !== 'REGULAR') {
      meta.textContent += `\n常规报价参考：${regular.price_text} · 来源 ${stamp(regular.source_at)}（独立保留）`;
    }
    article.append(head, value, meta);
    return article;
  }
  function renderQuotes() {
    const signature = JSON.stringify([current?.quotes,current?.session_quotes,current?.session_capabilities,
      current?.symbols,current?.selection,stateOf('moomoo')?.state,[...sessionChoice]]);
    if (signature === quoteSignature) return;
    quoteSignature = signature;
    const bySymbol = new Map((current?.quotes || []).map(x => [x.symbol, x]));
    const sessions = current?.session_quotes || [];
    const capabilities = current?.session_capabilities || [];
    const states = new Map((current?.symbols || []).map(x => [x.symbol, x]));
    const selected = new Set((current?.selection || []).filter(x => x.desired).map(x => x.symbol));
    const moomoo = stateOf('moomoo');
    const snapshot = stateOf('moomoo_snapshot');
    const all = [...new Set([...priority, ...(current?.selection || []).filter(x => x.desired).map(x => x.symbol), ...bySymbol.keys()])];
    quoteBox.replaceChildren(...all.map(symbol => quoteElement(symbol, bySymbol.get(symbol), states.get(symbol), moomoo, snapshot,
      selected.has(symbol), sessions.filter(x => x.symbol === symbol),capabilities.filter(x => x.symbol === symbol))));
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
      button.append(node('span','top10-rank',`#${index+1} · ${title}`),
        node('span','news-meta',`${why} · ${row.evidence_phrase || bilingual(['匹配依据未知','Match unknown'])} · ${stamp(row.published_at_utc || row.received_at)}`),
        node('span','analysis-status',`${bilingual(['供应商报道／待核实','Supplier report / unverified'])} · ${row.claim_stage || 'GENERAL'} · ${bilingual(['分析','Analysis'])}: ${bilingual(analysisStates[row.analysis_status]||analysisStates.NOT_QUEUED)} · ${bilingual(['新闻语气','News tone'])}: ${bilingual(toneLabels[row.tone]||toneLabels.UNKNOWN)} · ${bilingual(['规则排序','Rule order'])} ${row.rank?.score ?? ''}`));
      button.onclick=()=>{
        window.quantOpenPanel?.('news');
        postLocal(root.dataset.analysisPriorityUrl,{item_key:row.item_key}).catch(()=>{});
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
    if (worker) worker.textContent=`${bilingual(['分析后台','Analysis worker'])}: ${current?.analysis_worker_state||'NOT_STARTED'} · ${bilingual(['队列','Queue'])}: ${current?.analysis_queue_count ?? 0}`;
    const counts=document.getElementById('news-screening-counts');
    const m=current?.news_screening_metrics||{};
    const legacy=current?.news_screening_counts||{};
    if (counts) counts.textContent=`${bilingual(['新收到','New received'])}: ${m.raw_received_rows ?? 0} · ${bilingual(['相关入库','Relevant stored'])}: ${m.relevant_new_versions ?? 0} · ${bilingual(['待筛','Pending review'])}: ${m.pending_rows ?? 0} · ${bilingual(['排除','Excluded'])}: ${m.excluded_rows ?? 0} · ${bilingual(['旧库已标记','Legacy labeled'])}: ${(legacy.INCLUDE||0)+(legacy.PENDING_REVIEW||0)+(legacy.EXCLUDE||0)}`;
  }
  function analysisCard(item) {
    const wrap=node('div','analysis-card');
    const status=item.analysis_status||'NOT_QUEUED';
    wrap.append(node('p','analysis-status',`${bilingual(['模型分析','Model analysis'])}: ${bilingual(analysisStates[status]||analysisStates.NOT_QUEUED)}`));
    const a=item.analysis;
    if (!a || status!=='COMPLETE') {
      if (status==='FAILED' || status==='REVIEW_REJECTED') wrap.append(node('p','muted',bilingual(['分析暂不可用；原始报道仍可查看。','Analysis unavailable; original report remains available.'])));
      return wrap;
    }
    const value=(stem)=>a[`${stem}_${language}`] || (language==='en' ? `Translation pending — ${a[`${stem}_zh`]||''}` : '证据不足');
    wrap.append(node('p','analysis-status',bilingual(['机器生成翻译与分析 · 新闻仍待核实','Machine-generated translation and analysis · report unverified'])),
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
    article.addEventListener('click',() => {
      if (item.analysis_status==='PENDING' || item.analysis_status==='NOT_QUEUED')
        postLocal(root.dataset.analysisPriorityUrl,{item_key:item.item_key}).catch(()=>{});
    },{once:true});
    const published = item.published_at_utc ? stamp(item.published_at_utc) :
      (item.published_raw ? `${item.published_raw}（时区未确认）` : '未知');
    article.append(node('p', 'news-meta', `${item.source_status} · 来源发布时间：${published} · 本机收到：${stamp(item.received_at)} · 版本 ${item.version}`));
    const modelTitle=item.analysis?.[`headline_${language}`];
    if (item.title || modelTitle) article.append(node('h3', null, modelTitle || (language==='en' ? `Translation pending — ${item.title || ''}` : item.title)));
    const body = item.content || '';
    const original=node('details','news-original');
    original.dataset.sourceOriginal='1';
    original.append(node('summary',null,bilingual(['展开原文','Expand original report'])));
    original.append(node('p','news-body',body));
    article.append(original);
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
      rows.map(x => `${x.item_key}:${x.version}:${x.focus_eligible}:${x.rule_priority}:${x.analysis_status}:${x.analysis_completed_at}`).join('|');
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
    if (filter !== 'focus') { newsBox.replaceChildren(...rows.map(newsArticle)); return; }
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
  }
  async function poll() {
    if (inFlight) return;
    inFlight = true;
    try {
      const reply = await fetch(endpoint, {credentials: 'same-origin', cache: 'no-store'});
      if (!reply.ok) throw new Error(`LOCAL_HTTP_${reply.status}`);
      current = await reply.json();
      renderStates(); renderQuotes(); renderSubscriptionControls(); renderInterestControls(); renderEventControls(); renderTop10(); renderSentiment(); renderNews();
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
