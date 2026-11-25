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
  const TABS = ['settings', 'simple', 'news', 'authors', 'daily', 'views', 'history'];
  const PANEL = {keyword: '关键词样本', watchlist: '重点作者', combined: '合并去重'};
  let scope = 'keyword', last = null;
  const TOPIC = {memory: '存储', technology: '科技 / 半导体', market: '股市 / 宏观', qqq: 'QQQ'};
  const STATE = {bullish: '看多', bearish: '看空', neutral: '中性', UNKNOWN: '未判定'};
  const MODE = {LIVE: '实时留档', BATCH_AFTER_CLOSE: '收盘后批量采集', RETROSPECTIVE_VISIBLE_NOW: '历史回填（现在可见的样本）'};
  const WHY = {HISTORY_SEARCH_NOT_VERIFIED: '历史检索未验证', RECEIPT_FROM_LENIENT_VALIDATOR: '旧预检回执已失效', LOW_SAMPLE: '样本账号过少', NO_VALID_ACCOUNTS: '没有已判定的账号', COLLECTION_PARTIAL: '采集不完整', COLLECTION_MISSING: '没有采集记录',
    TIE_RESOLVED_TO_NEUTRAL: '并列，记为中性', PROVIDER_KEY_MISSING: '缺少供应商密钥', PREFLIGHT_NOT_PASSED: '尚未通过实采预检',
    WORKER_HEALTH_NOT_REPORTED: '采集端还没有上报检查结果', WORKER_HEALTH_STALE_RECHECK_NEEDED: '采集端的检查结果已过期，待复检',
    CLASSIFICATION_INCOMPLETE: '分类未完成', COLLECTION_PLANNED_SAMPLE: '计划抽样'};
  const MISSING = {PROVIDER_KEY: '供应商密钥', PREFLIGHT: '实采预检', WORKER_HEALTH: '采集端自检结果', 'classifier.model_id': '分类模型',
    'classifier.immutable_revision': '分类模型的固定版本', 'classifier.prompt_version': '提示词版本', 'classifier.temperature': '模型温度',
    'market.input_version': '行情输入版本', 'market.adjustment_semantics': '复权口径说明', 'validation.holdout_start': '留出期起点'};
  const OUT = {bullish: '看多', bearish: '看空', neutral: '中性', irrelevant: '无关', failed: '分类失败', not_judged: '未能判断', cut_by_rule: '按规则未送模型', pending: '待分类'};
  const why = c => WHY[c] || c || '';
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

  const data = v => { const n = h('span', 'num', v == null ? '—' : String(v)); n.setAttribute('translate', 'no'); return n; };
  const used = (a, b) => `${a} / ${b}`;

  function renderSettings(d) {
    const p = panel('settings'), st = d.settings, c = d.collection, b = d.budget, cl = d.classification;
    const kw = t => { const n = h('span', null, t.keywords.join(' · ')); n.setAttribute('translate', 'no'); return n; };   // keywords are data, shown as configured
    const topics = table(['关键词主题', '关键词', '对照标的'], st.topics.map(t => [TOPIC[t.id] || t.id, kw(t), t.comparison_asset || '未配置（仅统计行业观点）']));
    const card = h('div', 'card');
    card.append(h('h3', null, '采集概况'), row('供应商（单一主源）', data(st.provider)), row('自动切换到其他付费源', '否'), row('自动充值', '否'),
      row('更新方式', '每日批量'), row('抽样方式', '计划抽样，不是全量'), row('语言', st.languages.join(' / ')), row('词表版本', data(st.vocabulary_version)),
      row('密钥的环境变量名', data(st.key_env_name)),
      row('采集状态', {NOT_STARTED: '尚未采集', HAS_GAPS: '有缺口', COLLECTED: '已采集'}[c.state] || c.state), row('已保存帖子', c.posts), row('样本账号数', c.sample_accounts),
      row('最新帖子时间（UTC）', data(c.last_post_created_at_utc)), row('最后采集时间（UTC）', data(c.last_fetch_at_utc)), row('数据盘空闲（GiB）', c.disk_free_gib));
    const cost = h('div', 'card');
    cost.append(h('h3', null, '费用与额度'),
      row('今日返回条数（含重复）', used(b.today.returned_posts, b.today.cap_posts)), row('今日费用（美元）', used(b.today.usd, b.today.cap_usd)),
      row('近期采集：条数', used(b.pools.recent.returned_posts, b.pools.recent.cap_posts)), row('近期采集：费用（美元）', used(b.pools.recent.usd, b.pools.recent.cap_usd)),
      ...(b.sub_pools && b.sub_pools.watchlist ? [row('其中重点作者：条数', used(b.sub_pools.watchlist.returned_posts, b.sub_pools.watchlist.cap_posts)), row('其中重点作者：费用（美元）', used(b.sub_pools.watchlist.usd, b.sub_pools.watchlist.cap_usd)),
        row('其中关键词检索：条数', used(b.sub_pools.recent.returned_posts, b.sub_pools.recent.cap_posts)), row('其中关键词检索：费用（美元）', used(b.sub_pools.recent.usd, b.sub_pools.recent.cap_usd))] : []),
      row('历史回填：条数', used(b.pools.historical.returned_posts, b.pools.historical.cap_posts)), row('历史回填：费用（美元）', used(b.pools.historical.usd, b.pools.historical.cap_usd)),
      row('滚动 30 天费用（美元）', used(b.rolling.usd, b.rolling.cap_usd)), row('预检累计：条数', used(b.preflight_cumulative.returned_posts, b.preflight_cumulative.cap_posts)),
      row('结果未知而保留额度的请求', b.unknown_held.requests),
      h('p', 'fine-print', '额度按上海自然日计算。费用按公开单价估算，不是供应商账单；条数或金额任一触顶就停止新请求。'));
    if (b.paused) cost.append(chip('已暂停，待人工核对', 'tag-dim'));
    const pre = d.preflight, hq = d.history_queue;
    const prog = h('div', 'card');
    prog.append(h('h3', null, '进度'),
      row('实采预检', {NOT_RUN: '未运行', PASSED: '预检通过', FAILED: '预检未通过', STALE: '预检回执已失效'}[pre.state] || pre.state), row('历史回填是否已解锁', pre.history_unlocked ? '是' : '否'),
      row('翻页是否已验证', {VERIFIED: '已验证', NOT_OBSERVED: '未观察到', FAILED: '失败'}[pre.continuation] || '—'), row('预检返回条数', pre.returned_total),
      row('历史回填范围（纽约日期）', data(hq.range ? `${hq.range.first_ny_date} ~ ${hq.range.last_ny_date}` : null)),
      row('历史回填：已采到的时间片', hq.units_with_any_page == null ? '—' : hq.units_with_any_page),
      row('待补采的样本日期', d.recovery_todo ? (d.recovery_todo.plans.length ? d.recovery_todo.plans.map(x => `${x.sample_date_ny}（${x.workable_units} 个单元）`).join('、') : '无') : '—'),
      row('已匹配主题的帖子', cl.matched_pairs), row('已分类', cl.classified_or_prefiltered), row('待分类', cl.pending),
      row('分类进度', cl.share_done == null ? '—' : pct(cl.share_done)),
      row('点赞不足，未计入样本', cl.filtered_low_likes), row('点赞数未知，未计入样本', cl.filtered_likes_unknown));
    const gate = h('div', 'card');
    const ag = st.acquisition_gate, wh = d.worker_health || {state: 'NOT_REPORTED'};
    gate.append(h('h3', null, '付费采集'), chip(ag.allowed === true ? '已满足条件' : ag.allowed === false ? '未开启' : '未知，待复检', ag.allowed === true ? 'run-succeeded' : 'tag-dim'));
    const gl = h('ul', 'xs-missing'); [...new Set(ag.reasons.map(why))].forEach(t => gl.append(h('li', null, t))); gate.append(gl);
    const yn = v => v === true ? '是' : v === false ? '否' : '未知';
    const fresh = wh.state === 'FRESH';
    gate.append(row('采集端检查时间（UTC）', data(wh.checked_at_utc)), row('检查结果是否仍有效', wh.state === 'NOT_REPORTED' ? '尚未上报' : fresh ? '是' : '已过期，待复检'),
      row('采集端凭据可用', fresh ? yn(wh.credential_available) : '未知'), row('凭据与预检回执一致', fresh ? yn(wh.credential_matches_preflight_receipt) : '未知'),
      h('p', 'fine-print', '网页本身不持有供应商密钥。这里显示的是采集任务最近一次自检写下的结果，不含密钥内容。'));
    gate.append(h('h3', null, '立场分类模型'), chip(st.classifier_gate.allowed ? '已固定版本' : '未固定版本，不运行', st.classifier_gate.allowed ? 'run-succeeded' : 'tag-dim'));
    const model = h('div', 'card');
    model.append(h('h3', null, '分类与行情'), row('立场模型', data(d.classifier.model_id)), row('标签质量', d.classifier.quality === 'UNVALIDATED' ? '未经人工验证' : d.classifier.quality),
      row('行情版本', data(d.market.input_version)), row('行情最后日期', data(d.market.last_date)));
    p.replaceChildren(h('p', 'fine-print', '关键词的修改会生成新的词表版本，从下一批次开始生效；已经冻结的采集计划不会被替换。不同词表版本的占比不能直接相接。'),
      topics, editor(d), rulesCard(d), card, cost, prog, queueCard(d), model, gate);
  }

  // ---------- account rules (like floor, follower-tier and named-account weights) ----------
  function rulesCard(d) {
    const r = d.account_rules || {}, box = h('div', 'card');
    box.append(h('h3', null, '账号权重与点赞过滤'));
    if (!r.enabled) { box.append(h('p', 'muted', '未启用，每个账号按一票计算。')); return box; }
    box.append(row('规则版本', data(r.version)), row('点赞下限（低于此数的帖子不计入样本）', r.min_likes), row('点赞数未知的帖子', r.unknown_likes === 'exclude' ? '不计入样本' : '计入样本'),
      row('粉丝数未知的账号权重', r.unknown_followers_weight));
    if ((r.target_must_be_named || []).length) box.append(row('原文必须点名才计入的标的', data(r.target_must_be_named.join('、'))));
    box.append(table(['粉丝数下限', '权重'], r.follower_tiers.map(t => [data(t.min_followers.toLocaleString('en-US')), t.weight])));
    if (r.named_accounts.length) box.append(table(['指定账号', '权重', '是否已填账号名'], r.named_accounts.map(n => [data(n.label), n.weight, n.identified ? (n.handle ? data(`@${n.handle}`) : '已填写') : '未填写，暂按粉丝数分档'])));
    box.append(h('p', 'fine-print', '点赞数和粉丝数取自帖子被采集到的那一刻，不是历史数值；发帖后很快被采到的帖子点赞偏少，更容易被过滤。权重只影响占比的计算，分类模型看不到这些数字。'));
    return box;
  }

  // ---------- watched authors (platform-side monitoring; nothing is followed on X) ----------
  const IDENT = {SCREENSHOT_HANDLE_PENDING_PROVIDER_ID: '用户名来自截图，稳定编号待首次采集绑定', RESOLVED_FROM_PROVIDER: '已由采集结果绑定稳定编号', AMBIGUOUS_HANDLE_BLOCKED: '用户名有歧义，暂不查询',
    AUTHOR_MISMATCH: '返回的账号不符，未计入', OWNER_CONFIRMED_HANDLE_PENDING_PROVIDER_ID: '用户名已由你确认，稳定编号待绑定'};
  const avail = (a, gate) => { const v = a.availability || '';
    if (v === 'WAITING_FOR_FIRST_COLLECTION') return a.identity_status === 'AMBIGUOUS_HANDLE_BLOCKED' ? '待身份核对' : (gate ? '等待首次采集' : '等待凭据');
    if (v === 'COLLECTED') return '已采集'; if (v === 'NO_VISIBLE_POSTS_IN_WINDOW') return '该日没有可见帖子';
    if (v.startsWith('NOT_REACHED')) return '额度或计划未轮到'; if (v.startsWith('QUERY_FAILED')) return '查询失败';
    if (v.startsWith('AUTHOR_MISMATCH')) return '返回的账号不符'; if (v === 'RETURNED_WITHOUT_USERNAME_ID_NOT_BOUND') return '返回中没有用户名，未绑定'; return v; };
  const WHYNOT = {BELOW_LIKE_FLOOR: '未计入：点赞低于下限', LIKE_COUNT_UNKNOWN: '未计入：点赞数未知', NO_TARGET_KEYWORD_IN_TEXT: '未计入：正文没有提到任何标的关键词', IRRELEVANT: '未计入：模型判为无关',
    RETWEET_NO_NEW_TEXT: '未计入：纯转发', IMAGE_OR_LINK_ONLY: '未计入：只有图片或链接'};
  const COUNTED = {bullish: '已计入：看多', bearish: '已计入：看空', neutral: '已计入：中性'};
  const counted = x => x.state === 'IN_ANALYSIS' ? COUNTED[x.stance] : x.state === 'WAITING_FOR_CLASSIFICATION' ? '待分类' : (WHYNOT[x.reason] || '未计入：无法判定');
  function sendWatch(id, enabled, msg) {
    msg.textContent = '保存中…';
    fetch(root.dataset.watchUrl, {method: 'POST', credentials: 'same-origin', headers: {'Content-Type': 'application/json', 'X-CSRFToken': csrf()}, body: JSON.stringify({watch_id: id, enabled})})
      .then(r => { if (!r.ok) { msg.textContent = '保存失败'; return; } load(); }).catch(() => { msg.textContent = '保存失败'; });
  }
  function renderAuthors(d) {
    const p = panel('authors'); if (!p) return;
    const w = d.watchlist || {}, s = w.summary || {}, gate = d.settings.acquisition_gate.allowed;
    if (!s.imported) { p.replaceChildren(h('p', 'muted', '还没有导入重点作者名单。')); return; }
    const msg = h('p', 'fine-print'); msg.setAttribute('role', 'status');
    const sum = h('div', 'card');
    sum.append(h('h3', null, '重点作者名单'), row('逻辑作者', s.logical_authors), row('已有用户名', s.with_handle), row('待身份核对', s.blocked_ambiguous), row('可以查询', s.ready_for_queries),
      row('已绑定稳定编号', s.stable_ids_bound), row('至少采集成功一次', s.collected_at_least_once), row('名单选定时间（UTC）', data(s.selected_at_utc)),
      h('p', 'fine-print', '这是平台内的只读监测名单：不会在 X 上关注任何人，也不需要你的 X 密码。打开主页链接不等于关注。'),
      h('p', 'fine-print', '名单在导入当天选定；更早日期的结果属于事后选定的作者面板，不能当作当时已确定的名单。'));
    const b = d.budget.sub_pools || {}, plan = w.plan, bud = h('div', 'card');
    bud.append(h('h3', null, '额度分配（近期额度内的划分，不是新增额度）'));
    if (b.watchlist) bud.append(row('重点作者：今日条数', used(b.watchlist.returned_posts, b.watchlist.cap_posts)), row('关键词检索：今日条数', used(b.recent.returned_posts, b.recent.cap_posts)),
      row('历史回填：今日条数', used(d.budget.pools.historical.returned_posts, d.budget.pools.historical.cap_posts)));
    if (plan) bud.append(row('当前冻结计划（纽约日期）', data(plan.ny_date)), row('计划内作者数', plan.authors), row('每位作者最多页数', plan.max_pages_per_author), row('未列入查询的作者', plan.not_asked.length));
    else bud.append(h('p', 'muted', '还没有冻结的采集计划；以上只是计划分配，不是进度。'));
    bud.append(h('p', 'fine-print', '每位作者先各取一页，再用剩余额度轮流加深；额度不足时是计划抽样，不代表读到了作者当天的全部发言。'));
    const name = a => { const box = h('span'); box.append(data(a.display_name)); a.aliases.forEach(x => { const c = chip(x, 'tag-dim'); c.setAttribute('translate', 'no'); box.append(c); }); return box; };
    const handle = a => { if (a.profile_url) { const l = h('a', 'num', `@${a.handle}`); l.href = a.profile_url; l.target = '_blank'; l.rel = 'noopener noreferrer'; l.setAttribute('translate', 'no'); return l; }
      const box = h('span'); box.append(h('span', null, '图片上的写法'), data(` ${a.handle_text_in_image || '—'} `), h('span', null, '候选写法'), data(` ${a.handle_candidates.map(c => c.handle).join(' / ') || '—'}`)); return box; };
    const toggle = a => { const t = h('button', `tag ${a.enabled ? '' : 'tag-dim xs-off'}`, a.enabled ? '监测中' : '已停用'); t.type = 'button'; t.setAttribute('aria-pressed', String(a.enabled));
      t.addEventListener('click', () => sendWatch(a.watch_id, !a.enabled, msg)); return t; };
    const lists = a => { const n = h('span', null, a.source_lists.join(' · ')); n.setAttribute('translate', 'no'); return n; };
    const tbl = table(['作者', '用户名', '来源', '调度优先级', '身份状态', '最近采集结果', '最近发帖时间（UTC）', '已存帖子', '监测'], w.authors.map(a =>
      [name(a), handle(a), lists(a), a.scheduling_priority, IDENT[a.identity_status] || a.identity_status, avail(a, gate), data(a.last_post_created_at_utc), a.stored_posts, toggle(a)]));
    const out = [sum, bud, tbl, msg, h('p', 'fine-print', '调度优先级只决定查询的先后，不是统计权重；上榜不会自动获得权重。')];
    const by = {}; (w.reading || []).forEach(r => { (by[r.watch_id] = by[r.watch_id] || []).push(r); });
    const read = h('section', 'card section'); read.append(h('h3', null, '作者原文（含未计入分析的帖子）'));
    if (!Object.keys(by).length) read.append(h('p', 'muted', gate ? '还没有采集到重点作者的帖子。' : '还没有采集到重点作者的帖子：正在等待凭据。'));
    w.authors.filter(a => by[a.watch_id]).forEach(a => {
      const posts = by[a.watch_id], any = posts.some(r => r.analysis.some(x => x.state === 'IN_ANALYSIS'));
      const head = h('div', 'section-head'); head.append(name(a), chip(any ? '有已计入的观点' : '无达标样本', any ? 'run-succeeded' : 'tag-dim')); read.append(head);
      posts.slice(0, 5).forEach(r => { const item = h('div', 'xs-view'); const t = h('p', 'xs-view-title', r.text); t.setAttribute('translate', 'no'); item.append(t);
        const meta = h('div', 'fine-print'); meta.append(data(`${r.created_at_utc} `), h('span', null, '采集时点赞'), data(` ${r.likes_at_fetch == null ? '—' : r.likes_at_fetch} `));
        if (/^https:\/\/(x|twitter)\.com\//.test(r.url || '')) { const l = h('a', 'num', '原帖'); l.href = r.url; l.target = '_blank'; l.rel = 'noopener noreferrer'; meta.append(l); }
        item.append(meta);
        r.analysis.forEach(x => { const line = h('div', 'fine-print'); if (x.target_id) line.append(data(`${x.target_id} `)); line.append(h('span', null, counted(x))); item.append(line); });
        read.append(item); });
    });
    read.append(h('p', 'fine-print', '这里显示作者被采集到的原文，不论是否进入占比。未计入、待分类和无关都不算中性；谈别的标的不会被算成对 QQQ 的看法。'));
    out.push(read);
    p.replaceChildren(...out);
  }

  // ---------- classification queues ----------
  function queueCard(d) {
    const q = d.queues, box = h('div', 'card');
    box.append(h('h3', null, '分类队列'));
    if (!q) { box.append(h('p', 'muted', '还没有待分类的帖子。')); return box; }
    const hrs = v => v == null ? '—' : v;
    box.append(row('近期队列：待分类', used(q.recent.pending, q.recent.pairs)), row('近期队列：已完成的采样日', used(q.recent.sample_days_complete, q.recent.sample_days)),
      row('近期队列：最早待分类材料已等待（小时）', hrs(q.recent.oldest_pending_age_hours)),
      row('历史队列：待分类', used(q.history.pending, q.history.pairs)), row('历史队列：最早待分类材料已等待（小时）', hrs(q.history.oldest_pending_age_hours)),
      row('近 7 天平均每天收到的帖子', q.rates_last_7_days.posts_received_per_day), row('近 7 天平均每天完成的推理', q.rates_last_7_days.model_inferences_per_day));
    const days = Object.entries(q.recent.by_sample_day).slice(0, 7);
    if (days.length) box.append(table(['采样日（纽约）', '已分类', '分类进度'], days.map(([day, v]) => [data(day), used(v.done, v.pairs), pct(v.share_done)])));
    box.append(h('p', 'fine-print', '近期队列先处理，历史队列只使用剩余的计算时间。待分类的帖子不会被当作中性。'));
    return box;
  }

  // ---------- keyword editing (versioned; effective with the next batch) ----------
  const REJECT = {KEYWORD_EXISTS: '这个关键词已经存在', KEYWORD_LENGTH: '关键词长度需在 1 到 60 个字符之间', KEYWORD_HAS_QUERY_SYNTAX: '关键词里不能包含查询语法或引号括号',
    KEYWORD_NOT_FOUND: '没有找到这个关键词', TOPIC_WOULD_HAVE_NO_KEYWORD: '每个主题至少要保留一个关键词', TOO_MANY_QUERIES_FOR_THE_DAILY_POOL: '关键词太多，超出每日额度能覆盖的查询数',
    NO_EFFECTIVE_CHANGE: '没有实际变化', NO_CHANGES: '没有实际变化', BAD_REQUEST: '请求格式不正确', UNKNOWN_ACTION_OR_TOPIC: '请求格式不正确'};
  const csrf = () => document.querySelector('input[name="csrfmiddlewaretoken"]')?.value || '';
  function send(changes, msg) {
    msg.textContent = '保存中…';
    fetch(root.dataset.vocabUrl, {method: 'POST', credentials: 'same-origin', headers: {'Content-Type': 'application/json', 'X-CSRFToken': csrf()}, body: JSON.stringify({changes})})
      .then(async r => { const j = await r.json().catch(() => ({})); if (!r.ok) { msg.textContent = REJECT[j.reason] || '保存失败'; return; } load(); })
      .catch(() => { msg.textContent = '保存失败'; });
  }
  function editor(d) {
    const v = d.vocabulary, base = v.pending || v.active, box = h('div', 'card xs-editor');
    const msg = h('p', 'fine-print'); msg.setAttribute('role', 'status');
    box.append(h('h3', null, '编辑关键词'));
    box.append(row('当前生效的词表版本', data(v.active.version)));
    if (v.pending) box.append(row('待生效的词表版本', data(v.pending.version)), row('生效日期（纽约）', data(v.pending.effective_from_ny_date)));
    const imp = base.impact;
    box.append(row('查询条数', imp.queries), row('最长查询（上限 512 字符）', imp.max_query_chars_with_time_operators), row('每日时间片数', imp.recent_units_per_day), row('每个时间片的页数', imp.recent_pages_per_unit));
    for (const t of base.topics) {
      const off = new Set(t.disabled_keywords || []);
      const line = h('div', 'xs-edit-topic'); line.append(h('strong', null, TOPIC[t.id] || t.id));
      const chips = h('div', 'xs-chips');
      t.keywords.forEach(k => {
        const b = h('button', `tag ${off.has(k) ? 'tag-dim xs-off' : ''}`); b.type = 'button';
        const label = h('span', null, k); label.setAttribute('translate', 'no');
        b.append(label, h('span', 'xs-chip-act', off.has(k) ? '启用' : '停用'));
        b.addEventListener('click', () => send([{action: off.has(k) ? 'enable' : 'disable', topic_id: t.id, keyword: k}], msg));
        chips.append(b);
      });
      const form = h('form', 'xs-add'); const input = h('input'); input.type = 'text'; input.maxLength = 60; input.setAttribute('aria-label', '新关键词'); input.placeholder = '新关键词';
      const add = h('button', 'button', '添加'); add.type = 'submit';
      form.append(input, add);
      form.addEventListener('submit', e => { e.preventDefault(); const k = input.value.trim(); if (k) send([{action: 'add', topic_id: t.id, keyword: k}], msg); });
      line.append(chips, form); box.append(line);
    }
    box.append(msg, h('p', 'fine-print', '修改不会改变每日额度和总预算，也不会立刻发起采集。'));
    return box;
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

  function scopeBar() {
    const bar = h('div', 'xs-chips');
    Object.keys(PANEL).forEach(k => { const b = h('button', `tag ${k === scope ? '' : 'tag-dim'}`, PANEL[k]); b.type = 'button'; b.setAttribute('aria-pressed', String(k === scope));
      b.addEventListener('click', () => { scope = k; if (last) { renderDaily(last); renderViews(last); } }); bar.append(b); });
    return bar;
  }
  const NOTE = {keyword: '关键词样本：由关键词检索采到、且命中词表的帖子。', watchlist: '重点作者：名单内已确认身份的作者的帖子，同样要求正文提到标的并达到点赞下限。',
    combined: '合并去重：以上两类合并，同一条帖子只算一次，同一个账号只投一票。这是独立的样本版本，不改变另外两个面板的统计口径。'};
  const EMPTY = {NO_SAMPLE: '无样本', PENDING: '待分类', NO_VALID_LABEL: '没有有效标签'};
  const fmtT = v => v ? String(v).replace('T', ' ').replace(/(\.\d+)?Z$/, ' UTC') : '—';
  const xlink = (url, text) => { if (!/^https:\/\/(x|twitter)\.com\//.test(url || '')) return h('span', 'num', text); const a = h('a', 'num', text); a.href = url; a.target = '_blank'; a.rel = 'noopener noreferrer'; return a; };
  const quote = t => { const n = h('span', null, t || '—'); n.setAttribute('translate', 'no'); return n; };
  const cnt = (c, sh) => `${c.bullish} / ${c.bearish} / ${c.neutral}` + (sh && sh.bullish != null ? `（${pct(sh.bullish)} / ${pct(sh.bearish)} / ${pct(sh.neutral)}）` : '');
  let dayKey = null;

  function renderDay(box, j) {
    if (j.state !== 'READY') { box.replaceChildren(h('p', 'muted', j.state === 'READ_ERROR' ? '读取失败' : '这一天没有可核对的汇总记录。')); return; }
    const out = [];
    const head = h('div', 'section-head'); head.append(h('h3', null, `${j.target} · ${j.window_label} · ${PANEL[j.scope]}`), chip(j.note, 'tag-dim'), chip(MODE[j.mode] || j.mode, 'tag-dim')); out.push(head);
    out.push(h('p', 'fine-print', j.timing_note));
    const ex = j.excluded || {}, cl = j.classification || {};
    const card = h('div', 'card');
    card.append(row('分类模型', data(j.model_id)), row('是否为当前生效版本', j.identity_is_active ? '是' : '否（这一天的汇总来自另一版本）'),
      row('帖子：看多 / 看空 / 中性（占比）', cnt(j.posts, j.shares_by_post)), row('分母（有效帖子）', j.posts_total),
      row('账号：看多 / 看空 / 中性（占比）', cnt(j.accounts, j.shares_by_account)), row('分母（有效账号）', j.accounts_total),
      row('无关', ex.IRRELEVANT || 0), row('分类失败', (ex.JSON_INVALID || 0) + (ex.EVIDENCE_NOT_IN_TEXT || 0)),
      row('待分类', cl.pending), row('样本内帖子', cl.matched_posts), row('点赞不足未计入', cl.filtered_low_likes), row('原文未点名标的未计入', cl.filtered_target_not_named || 0),
      row('采集状态', why('COLLECTION_' + j.collection_status)),
      row('逐条判断与每日汇总是否一致', j.check.labels_equal_summary ? '一致' : '不一致，需要重建汇总'),
      row('主要观点引用的帖子是否都在本日材料内且标签相同', j.narratives.length ? (j.check.views_evidence_in_material ? '是' : '否') : '本日没有主要观点'),
      row('汇总生成时间', data(fmtT(j.summary_committed_at_utc))));
    if (j.empty_reason) card.append(row('没有占比的原因', EMPTY[j.empty_reason]));
    out.push(card);
    const rep = h('div', 'card'); rep.append(h('h3', null, '代表帖子'));
    for (const k of ['bullish', 'bearish', 'neutral']) {
      const list = j.representative[k] || []; const line = h('div', 'xs-view'); line.append(h('p', 'xs-view-title', `${STATE[k]}（${list.length ? '最多 3 条' : '本日没有'}）`));
      list.forEach(x => { const r = h('div', 'fine-print'); r.append(xlink(x.source_url, x.author_handle ? `@${x.author_handle} ` : `${x.post_id} `), quote(x.reason), h('span', null, ' · 依据：'), quote(x.evidence)); line.append(r); });
      rep.append(line);
    }
    out.push(rep);
    if (j.narratives.length) {
      const nc = h('div', 'card'); nc.append(h('h3', null, '本日主要观点'));
      j.narratives.forEach(n => { const it = h('div', 'xs-view'); const t = h('p', 'xs-view-title', n.title); t.setAttribute('translate', 'no'); it.append(t);
        const meta = h('div', 'fine-print'); meta.append(h('span', null, `支持账号 ${n.supporting_accounts} · 看多 ${n.stance_composition.bullish} / 看空 ${n.stance_composition.bearish} / 中性 ${n.stance_composition.neutral} · 原帖`));
        n.evidence.forEach(e => meta.append(xlink(e.url, ` ${e.author_handle ? '@' + e.author_handle : e.post_id} `))); it.append(meta); nc.append(it); });
      out.push(nc);
    }
    const ic = h('div', 'card'); ic.append(h('h3', null, '组成这一天的全部帖子'), row('显示 / 总数', `${j.items.length} / ${j.items_total}`));
    ic.append(table(['判断', '计入账号票', '简短理由', '依据原文', '原文', '发布时间', '采集时间', '模型完成时间'], j.items.map(x =>
      [OUT[x.outcome] || x.outcome, x.counts_as_account_vote ? '是' : '否', quote(x.reason), quote(x.evidence), xlink(x.source_url, x.author_handle ? `@${x.author_handle}` : '打开原文'),
        data(fmtT(x.published_at_utc)), data(fmtT(x.first_received_at_utc)), data(fmtT(x.model_completed_at_utc))])));
    ic.append(h('p', 'fine-print', '“计入账号票”：同一账号在窗口内只取最后一条有效帖子。发布时间、采集时间、模型完成时间分别记录；历史回填的日期是后来取得并分类的材料。'));
    out.push(ic);
    box.replaceChildren(...out);
  }

  function openDay(box, target, date) {
    dayKey = `${scope}|${target}|${date}`; const key = dayKey;
    box.hidden = false; box.replaceChildren(h('p', 'muted', '读取中…'));
    fetch(`${root.dataset.dayUrl}?scope=${encodeURIComponent(scope)}&target=${encodeURIComponent(target)}&date=${encodeURIComponent(date)}`, {credentials: 'same-origin', cache: 'no-store'})
      .then(r => { if (!r.ok) throw new Error(String(r.status)); return r.json(); })
      .then(j => { if (key === dayKey) renderDay(box, j); })
      .catch(() => { if (key === dayKey) box.replaceChildren(h('p', 'muted', '读取失败')); });
  }

  function renderDaily(d) {
    const p = panel('daily');
    const pn = {...((d.panels || {})[scope] || {available: true}), ...(scope === 'keyword' ? {daily: d.daily, daily_summary: d.daily_summary} : {})};      // the keyword panel's rows are the top-level ones
    const head = [scopeBar(), h('p', 'fine-print', NOTE[scope])];
    if (scope !== 'keyword' && !pn.available) { p.replaceChildren(...head, h('p', 'muted', '还没有导入重点作者名单。')); return; }
    if (scope === 'keyword' ? !d.has_data : !pn.daily.length) { p.replaceChildren(...head, missingBox(d, '还没有情绪数据。开始采集和分类之前需要以下输入：')); return; }
    const out = head;
    const ds = pn.daily_summary;
    if (ds) { const v = h('p', 'fine-print'); v.append(chip(ds.note, 'tag-dim'), h('span', null, ' 分类模型 '), data((ds.models || []).join(' / ') || '—'),
      h('span', null, ds.all_rows_from_active_identity ? ' · 汇总来自当前生效版本' : ' · 部分汇总不是当前生效版本生成的，需要重建')); out.push(v); }
    if (pn.sample_version) { const v = h('p', 'fine-print'); v.append(h('span', null, '样本版本'), data(` ${pn.sample_version}`)); out.push(v); }
    const sess = pn.daily.filter(r => r.window_kind === 'session' && r.lang_scope === 'all');
    const closes = {}; d.returns.forEach(r => { if (r.h === 1 && r.anchor_close != null) closes[`${r.topic_id}|${r.session_date}`] = r.anchor_close; });
    for (const tid of [...new Set(sess.map(r => r.topic_id))]) {
      const rows = sess.filter(r => r.topic_id === tid).sort((a, b) => a.window_label < b.window_label ? -1 : 1);
      const sec = h('section', 'card section'); const head = h('div', 'section-head'); head.append(h('h3', null, TOPIC[tid] || tid));
      [...new Set(rows.map(r => r.mode))].forEach(m => head.append(chip(MODE[m] || m, m === 'LIVE' ? 'run-succeeded' : 'tag-dim'))); sec.append(head);
      const has = r => { const e = (r.shares_equal_weight && r.shares_equal_weight.bullish != null) ? r.shares_equal_weight : (r.shares || {}); return e.bullish != null; };   // a date has shares only when it has valid labels
      const withData = rows.filter(has);                 // the chart and the table hold only dates that have valid labels
      const empty = {}; rows.filter(r => !has(r)).forEach(r => { const c = r.classification || {}; const k = r.empty_reason || (c.pending || r.state_reason === 'CLASSIFICATION_INCOMPLETE' ? 'PENDING' : c.matched_posts ? 'NO_VALID_LABEL' : 'NO_SAMPLE'); empty[k] = (empty[k] || 0) + 1; });
      if (!withData.length) sec.append(h('p', 'muted', '这个标的还没有带有效标签的日期。'));
      else {
        const cl = {}; withData.forEach(r => { cl[r.window_label] = closes[`${tid}|${r.window_label}`]; });
        sec.append(chart(withData.slice(-60).map(r => ({...r, shares: (r.shares_equal_weight && r.shares_equal_weight.bullish != null) ? r.shares_equal_weight : r.shares})), cl));
        const box = h('div', 'xs-day'); box.hidden = true;
        const pick = r => { const b = h('button', 'button', '查看材料'); b.type = 'button'; b.dataset.day = r.window_label; b.addEventListener('click', () => openDay(box, r.target_id, r.window_label)); return b; };
        sec.append(table(['交易日', '账号占比', '帖子：多 / 空 / 中', '分母（帖子）', '账号：多 / 空 / 中', '分母（账号）', '账号占比：多 / 空 / 中', '帖子占比：多 / 空 / 中', '加权账号占比：多 / 空 / 中', '无关或失败', '待分类', '点赞不足未计入', '来源', '说明', '材料'],
          withData.slice(-30).reverse().map(r => { const e = r.shares_equal_weight || {}, bp = r.shares_by_post || {}, c = r.classification || {};
            const three = c => c ? `${c.bullish} / ${c.bearish} / ${c.neutral}` : '—';
            return [r.window_label, shareBar(e.bullish == null ? r.shares : e), three(r.posts), r.posts_total,
              three(r.accounts), r.accounts_total,
              data(e.bullish == null ? '—' : `${pct(e.bullish)} / ${pct(e.bearish)} / ${pct(e.neutral)}`), data(bp.bullish == null ? '—' : `${pct(bp.bullish)} / ${pct(bp.bearish)} / ${pct(bp.neutral)}`),
              data(r.shares.bullish == null ? '—' : `${pct(r.shares.bullish)} / ${pct(r.shares.bearish)} / ${pct(r.shares.neutral)}`), r.unjudged_total, c.pending, c.filtered_low_likes, MODE[r.mode] || r.mode, [why(r.state_reason), r.low_sample ? '样本账号少于 ' + r.low_sample_threshold : ''].filter(Boolean).join('；'), pick(r)]; })));
        sec.append(box);
      }
      const parts = Object.keys(empty).map(k => (EMPTY[k] || k) + '：' + empty[k]);
      sec.append(h('p', 'fine-print', ['交易日窗口总数 ' + rows.length, '有有效标签并画入图表 ' + withData.length].concat(parts).join('；')));
      out.push(sec);
    }
    if ((d.account_rules || {}).enabled) out.push(h('p', 'fine-print', '“加权账号占比”按账号权重计算（粉丝数分档或指定账号）；“账号占比”是每个账号一票的结果，图表画的是后者。点赞数低于下限的帖子不在样本内。'));
    out.push(h('p', 'fine-print', '图表和表格只包含有有效标签的交易日。占比的分母只含已成功分类且相关的帖子（或账号）；无关、分类失败、待分类单独计数，不算中性。账号占比每个账号一票（取窗口内最后一条有效帖子），帖子占比每条帖子一次。'));
    out.push(h('p', 'fine-print', '这是本次采集样本中的比例，不是全部 X 用户的比例，也不是持仓。交易日窗口为上一交易日收盘到当日收盘。历史回填的日期是后来取得并分类的材料，不能当作当天可用的信号。采集不完整的日期不给出主导状态。'));
    p.replaceChildren(...out);
  }

  function renderViews(d) {
    const p = panel('views');
    const nar = scope === 'keyword' ? d.narratives : (((d.panels || {})[scope] || {}).narratives || []);
    if (!nar.length) { p.replaceChildren(h('p', 'fine-print', NOTE[scope]), d.has_data ? h('p', 'muted', '当前窗口的证据不足，没有可列出的主要观点。') : missingBox(d, '还没有可归纳的观点。需要以下输入：')); return; }
    d = {...d, narratives: nar};
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
        n.evidence.forEach(e => { if (e.author_handle) links.append(data(` @${e.author_handle}`)); if (/^https:\/\/(x|twitter)\.com\//.test(e.url || '')) { const a = h('a', 'num', ` ${e.post_id} `); a.href = e.url; a.target = '_blank'; a.rel = 'noopener noreferrer'; links.append(a); } else links.append(h('span', 'num', ` ${e.post_id} `)); });
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

  const spct = v => v == null ? '—' : (v * 100).toFixed(1) + '%';
  let simpleIdentity = '';
  function renderSimple(d) {
    const p = panel('simple'), s = d.simple;
    if (!s) { p.replaceChildren(h('p', 'muted', '还没有可显示的模型判断。')); return; }
    const out = [];
    const head = h('div', 'card');
    head.append(h('h3', null, '模型判断'), chip(s.note, 'tag-dim'),
      h('p', 'fine-print', '三类占比的分母是：该标的已成功分类且相关的条目数。无关、分类失败、待分类分别计数，不算中性。'),
      h('p', 'fine-print', s.sample_rule));
    const sel = h('select'); sel.setAttribute('aria-label', '分类版本');
    s.identities.forEach(i => { const o = h('option', null, `${i.identity === s.active_identity ? '* ' : ''}${i.model_id || '—'} · ${i.prompt_version || '—'} · ${i.labels}`); o.value = i.identity; o.setAttribute('translate', 'no'); if (i.identity === s.identity) o.selected = true; sel.append(o); });
    if (!s.identities.some(i => i.identity === s.identity)) { const o = h('option', null, '当前生效版本（暂无标签）'); o.value = ''; o.selected = true; sel.prepend(o); }
    sel.addEventListener('change', () => { simpleIdentity = sel.value; load(); });
    head.append(row('分类版本（可切换对照）', sel), row('当前显示的是否为生效版本', s.identity_is_active ? '是' : '否，仅作对照'),
      row('样本内条目', s.totals.items_in_sample), row('已处理', `${s.totals.processed} / ${s.totals.items_in_sample}`), row('分类完成进度', spct(s.totals.progress)),
      row('看多 / 看空 / 中性', `${s.totals.bullish} / ${s.totals.bearish} / ${s.totals.neutral}`), row('无关', s.totals.irrelevant), row('分类失败', s.totals.failed),
      row('未能判断（旧版约定）', s.totals.not_judged), row('按规则未送模型（如文本过长被截断）', s.totals.cut_by_rule), row('待分类', s.totals.pending));
    out.push(head);
    const tc = h('div', 'card'); tc.append(h('h3', null, '各标的：帖子占比与账号占比'));
    tc.append(table(['标的', '看多', '看空', '中性', '分母（帖子）', '账号：看多', '账号：看空', '账号：中性', '分母（账号）', '无关', '分类失败', '待分类', '进度', '说明'],
      s.targets.map(t => [t.target, `${t.posts.bullish}（${spct(t.posts.bullish_share)}）`, `${t.posts.bearish}（${spct(t.posts.bearish_share)}）`, `${t.posts.neutral}（${spct(t.posts.neutral_share)}）`, t.posts.n,
        `${t.accounts.bullish}（${spct(t.accounts.bullish_share)}）`, `${t.accounts.bearish}（${spct(t.accounts.bearish_share)}）`, `${t.accounts.neutral}（${spct(t.accounts.neutral_share)}）`, t.accounts.n,
        t.irrelevant, t.failed, t.pending, spct(t.progress), t.complete ? '已全部分类' : '已分类样本占比'])));
    tc.append(h('p', 'fine-print', '帖子占比：每条帖子算一次。账号占比：每个账号只算一次，取它自己帖子里的多数；发帖频繁的账号不会被当成更多人。部分完成时，这只是已分类样本的占比，不是整天或全市场的情绪。'));
    out.push(tc);
    const ic = h('div', 'card'); ic.append(h('h3', null, '逐条结果'), row('显示的条数 / 已处理的条数', `${s.items_shown} / ${s.items_total}`));
    ic.append(table(['标的', '判断', '简短解释', '依据原文', '原文'], s.items.map(x => { const a = h('a', null, '打开原文'); a.href = x.source_url; a.target = '_blank'; a.rel = 'noopener noreferrer';
      const why = h('span', null, x.reason || (x.cut_reason ? '文本过长，已记录为截断，未送模型' : '—')); why.setAttribute('translate', 'no');
      const ev = h('span', null, x.evidence || '—'); ev.setAttribute('translate', 'no');
      return [x.target, OUT[x.outcome] || x.outcome, why, ev, x.source_url ? a : '—']; })));
    out.push(ic);
    p.replaceChildren(...out);
  }

  const load = () => fetch(root.dataset.url + (simpleIdentity ? `?identity=${encodeURIComponent(simpleIdentity)}` : ''), {credentials: 'same-origin', cache: 'no-store'}).then(r => { if (!r.ok) throw new Error(String(r.status)); return r.json(); }).then(d => {
    const state = document.getElementById('x-stance-state');
    if (d.state === 'READ_ERROR') { state.textContent = '读取失败'; return; }
    state.textContent = d.has_data ? '已有数据' : '尚无数据';
    last = d;
    renderSettings(d); renderSimple(d); renderAuthors(d); renderDaily(d); renderViews(d); renderHistory(d);
  }).catch(() => { document.getElementById('x-stance-state').textContent = '读取失败'; });
  load();
})();
