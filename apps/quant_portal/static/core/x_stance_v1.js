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
  const SHOWN_PANELS = ['keyword', 'combined'];      // 2026-11-27: the watched-author panel had 1 QQQ post in 22 groups; it is no longer a card or a scope (the list stays under 重点作者)
  let scope = 'keyword', last = null, grain = 'session3', shown = 'session3';
  const GRAIN = {session3: '每三个交易日', session: '每个交易日'};
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

  // No tabs: the overview (headline per panel + one chart) is always on top; every other panel sits in a closed <details>.
  const fold = (title, kids, open) => { const d = h('details', 'disclosure section xs-fold'); if (open) d.open = true; const sm = h('summary', null, title); const body = h('div', 'disclosure-body'); body.append(...kids); d.append(sm, body); return d; };

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
    const up = d.updates || {state: 'NOT_REPORTED'}, upc = h('div', 'card');
    upc.append(h('h3', null, '自动更新'), chip(up.state === 'NOT_REPORTED' ? '尚未上报' : up.state === 'FRESH' ? '按计划运行' : '已超过两个周期未更新', up.state === 'FRESH' ? 'run-succeeded' : 'tag-dim'));
    if (up.credit_hold) upc.append(h('p', 'xs-warn', '新采集已暂停：供应商账户额度不足（HTTP 402）。已入库的帖子继续分类和更新；充值并确认后恢复采集。'), row('暂停开始时间（UTC）', data(up.credit_hold.since_utc)));
    if (up.state !== 'NOT_REPORTED') {
      const pe = up.pending || {}, co = up.collection || {};
      upc.append(row('最后成功更新时间（UTC）', data(up.last_successful_update_at_utc)), row('最后成功采集时间（UTC）', data(up.last_successful_collection_at_utc)),
        row('最近一次分类写入时间（UTC）', data(up.last_label_written_at_utc)), row('本轮采集', co.status === 'PAUSED_PROVIDER_CREDITS' ? '已暂停，未发请求' : co.status === 'RAN' ? '已运行' : (co.status || '—')),
        row('本轮新入库帖子', co.new_posts == null ? '—' : co.new_posts), row('待分类：新帖', pe.new_posts_queue), row('待分类：历史积压', pe.history_backlog),
        row('更新周期（小时）', up.interval_hours));
    }
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
    p.replaceChildren(
      fold('额度与费用', [cost]),
      fold('采集与分类进度', [upc, card, prog, queueCard(d)]),
      fold('关键词与账号规则（可修改）', [h('p', 'fine-print', '关键词的修改会生成新的词表版本，从下一批次开始生效；已经冻结的采集计划不会被替换。不同词表版本的占比不能直接相接。'), topics, editor(d), rulesCard(d)]),
      fold('采集端与模型诊断', [gate, model]));
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
  const WHYNOT = {BELOW_LIKE_FLOOR: '未计入：点赞低于下限', LIKE_COUNT_UNKNOWN: '未计入：点赞数未知', NO_TARGET_KEYWORD_IN_TEXT: '未计入：正文没有提到任何标的关键词', TARGET_NOT_NAMED: '未计入：正文没有点名该标的', IRRELEVANT: '未计入：模型判为无关',
    RETWEET_NO_NEW_TEXT: '未计入：纯转发', IMAGE_OR_LINK_ONLY: '未计入：只有图片或链接', EVIDENCE_NOT_IN_TEXT: '未计入：模型给出的依据在原文里找不到（分类失败）', JSON_INVALID: '未计入：模型输出格式错误（分类失败）'};
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

  function grainBar() {
    const bar = h('div', 'xs-chips');
    Object.keys(GRAIN).forEach(k => { const b = h('button', `tag ${k === grain ? '' : 'tag-dim'}`, GRAIN[k]); b.type = 'button'; b.setAttribute('aria-pressed', String(k === grain));
      b.addEventListener('click', () => { grain = k; if (last) renderDaily(last); }); bar.append(b); });
    return bar;
  }
  const span = r => (r.group_sessions && r.group_sessions.length > 1) ? `${r.group_sessions[0]} – ${r.group_sessions[r.group_sessions.length - 1]}` : r.window_label;
  function scopeBar() {
    const bar = h('div', 'xs-chips');
    SHOWN_PANELS.forEach(k => { const b = h('button', `tag ${k === scope ? '' : 'tag-dim'}`, PANEL[k]); b.type = 'button'; b.setAttribute('aria-pressed', String(k === scope));
      b.addEventListener('click', () => { scope = k; if (last) { renderOverview(last); renderDaily(last); renderViews(last); } }); bar.append(b); });
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
    const head = h('div', 'section-head'); head.append(h('h3', null, `${j.target} · ${span(j)} · ${PANEL[j.scope]}`), chip(j.note, 'tag-dim'), chip(MODE[j.mode] || j.mode, 'tag-dim')); out.push(head);
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
    const ic = h('div', 'card'); ic.append(h('h3', null, j.window_kind === 'session3' ? '组成这一组的全部帖子' : '组成这一天的全部帖子'), row('显示 / 总数', `${j.items.length} / ${j.items_total}`));
    ic.append(table(['判断', '计入账号票', '简短理由', '依据原文', '原文', '发布时间', '采集时间', '模型完成时间'], j.items.map(x =>
      [OUT[x.outcome] || x.outcome, x.counts_as_account_vote ? '是' : '否', quote(x.reason), quote(x.evidence), xlink(x.source_url, x.author_handle ? `@${x.author_handle}` : '打开原文'),
        data(fmtT(x.published_at_utc)), data(fmtT(x.first_received_at_utc)), data(fmtT(x.model_completed_at_utc))])));
    ic.append(h('p', 'fine-print', '“计入账号票”：同一账号在窗口内只取最后一条有效帖子。发布时间、采集时间、模型完成时间分别记录；历史回填的日期是后来取得并分类的材料。'));
    out.push(ic);
    box.replaceChildren(...out);
  }

  function openDay(box, target, date) {
    dayKey = `${scope}|${shown}|${target}|${date}`; const key = dayKey;
    box.hidden = false; box.replaceChildren(h('p', 'muted', '读取中…'));
    fetch(`${root.dataset.dayUrl}?scope=${encodeURIComponent(scope)}&kind=${encodeURIComponent(shown)}&target=${encodeURIComponent(target)}&date=${encodeURIComponent(date)}`, {credentials: 'same-origin', cache: 'no-store'})
      .then(r => { if (!r.ok) throw new Error(String(r.status)); return r.json(); })
      .then(j => { if (key === dayKey) renderDay(box, j); })
      .catch(() => { if (key === dayKey) box.replaceChildren(h('p', 'muted', '读取失败')); });
  }

  function renderDaily(d) {
    const p = panel('daily');
    const pn = {...((d.panels || {})[scope] || {available: true}), ...(scope === 'keyword' ? {daily: d.daily, daily_summary: d.daily_summary} : {})};      // the keyword panel's rows are the top-level ones
    const head = [scopeBar(), grainBar(), h('p', 'fine-print', NOTE[scope])];
    if (grain === 'session3') head.push(h('p', 'fine-print', '每三个交易日为一组：从 2026-08-24 起依次分组，一组的窗口是第一个交易日之前的收盘到第三个交易日的收盘。同一账号在一组内只算一票（取最后一条有效帖子）。只列出三个交易日都已收盘的组。'));
    if (scope !== 'keyword' && !pn.available) { p.replaceChildren(...head, h('p', 'muted', '还没有导入重点作者名单。')); return; }
    if (scope === 'keyword' ? !d.has_data : !pn.daily.length) { p.replaceChildren(...head, missingBox(d, '还没有情绪数据。开始采集和分类之前需要以下输入：')); return; }
    const out = head;
    const ds = pn.daily_summary;
    if (ds) { const v = h('p', 'fine-print'); v.append(chip(ds.note, 'tag-dim'), h('span', null, ' 分类模型 '), data((ds.models || []).join(' / ') || '—'),
      h('span', null, ds.all_rows_from_active_identity ? ' · 汇总来自当前生效版本' : ' · 部分汇总不是当前生效版本生成的，需要重建')); out.push(v); }
    if (pn.sample_version) { const v = h('p', 'fine-print'); v.append(h('span', null, '样本版本'), data(` ${pn.sample_version}`)); out.push(v); }
    const up = d.updates || {state: 'NOT_REPORTED'};
    if (up.state !== 'NOT_REPORTED') { const v = h('p', 'fine-print'); v.append(h('span', null, '最后成功更新时间（UTC）'), data(' ' + (up.last_successful_update_at_utc || '—')), h('span', null, ' · '), h('span', null, '最后成功采集时间（UTC）'), data(' ' + (up.last_successful_collection_at_utc || '—')));
      if (up.credit_hold) v.append(h('span', null, ' · '), h('span', null, '新采集已暂停：供应商账户额度不足')); out.push(v); }
    shown = grain === 'session3' && !pn.daily.some(r => r.window_kind === 'session3') ? 'session' : grain;
    if (shown !== grain) out.push(h('p', 'muted', '还没有按三个交易日分组的汇总，需要先重建；下面先显示每个交易日的数据。'));
    const sess = pn.daily.filter(r => r.window_kind === shown && r.lang_scope === 'all');
    const closes = closeMap(d);
    for (const tid of [...new Set(sess.map(r => r.topic_id))]) {
      const rows = sess.filter(r => r.topic_id === tid).sort((a, b) => a.window_label < b.window_label ? -1 : 1);
      const sec = h('section', 'card section'); const head = h('div', 'section-head'); head.append(h('h3', null, TOPIC[tid] || tid));
      [...new Set(rows.map(r => r.mode))].forEach(m => head.append(chip(MODE[m] || m, m === 'LIVE' ? 'run-succeeded' : 'tag-dim'))); sec.append(head);
      const has = r => { const e = (r.shares_equal_weight && r.shares_equal_weight.bullish != null) ? r.shares_equal_weight : (r.shares || {}); return e.bullish != null; };   // a date has shares only when it has valid labels
      const withData = rows.filter(has);                 // the chart and the table hold only dates that have valid labels
      const empty = {}; rows.filter(r => !has(r)).forEach(r => { const c = r.classification || {}; const k = r.empty_reason || (c.pending || r.state_reason === 'CLASSIFICATION_INCOMPLETE' ? 'PENDING' : c.matched_posts ? 'NO_VALID_LABEL' : 'NO_SAMPLE'); empty[k] = (empty[k] || 0) + 1; });
      if (!withData.length) sec.append(h('p', 'muted', '这个标的还没有带有效标签的日期。'));
      else {
        const cl = {}; withData.forEach(r => { cl[r.window_label] = closeOf(closes, tid, r).v; });
        if (tid === PRIMARY && panel('overview')) sec.append(h('p', 'fine-print', '这个标的的图在页面顶部的主图里。'));
        else sec.append(chart(withData.slice(-60).map(r => ({...r, shares: (r.shares_equal_weight && r.shares_equal_weight.bullish != null) ? r.shares_equal_weight : r.shares})), cl));
        const box = h('div', 'xs-day'); box.hidden = true;
        const pick = r => { const b = h('button', 'button', '查看材料'); b.type = 'button'; b.dataset.day = r.window_label; b.addEventListener('click', () => openDay(box, r.target_id, r.window_label)); return b; };
        // nine columns: the shares cell carries the per-post and weighted shares as small lines; the rest is under 查看材料
        const weighted = (d.account_rules || {}).enabled, sh3 = x => `${pct(x.bullish)} / ${pct(x.bearish)} / ${pct(x.neutral)}`;
        const shareCell = r => { const e = r.shares_equal_weight || {}, bp = r.shares_by_post || {}, b = h('div', 'xs-cell-sh');
          b.append(data(e.bullish == null ? '—' : sh3(e)));
          if (bp.bullish != null) { const l = h('span', 'xs-cell-sub'); l.append(h('span', null, '按帖'), data(` ${sh3(bp)}`)); b.append(l); }
          if (weighted && (r.shares || {}).bullish != null) { const l = h('span', 'xs-cell-sub'); l.append(h('span', null, '加权'), data(` ${sh3(r.shares)}`)); b.append(l); }
          return b; };
        const leftOut = r => { const c = r.classification || {}, b = h('div', 'xs-cell-sh');
          const l1 = h('span', 'xs-cell-sub'); l1.append(h('span', null, '无关或失败'), data(` ${r.unjudged_total == null ? '—' : r.unjudged_total}`));
          const l2 = h('span', 'xs-cell-sub'); l2.append(h('span', null, '点赞不足'), data(` ${c.filtered_low_likes == null ? '—' : c.filtered_low_likes}`)); b.append(l1, l2);
          if (c.pending) { const l3 = h('span', 'xs-cell-sub xs-cell-warn'); l3.append(h('span', null, '待分类'), data(` ${c.pending}`)); b.append(l3); }
          return b; };
        const stateCell = r => { const b = h('div', 'xs-cell-sh'); b.append(h('span', null, MODE[r.mode] || r.mode));
          [why(r.state_reason), r.low_sample ? '样本账号少于 ' + r.low_sample_threshold : ''].filter(Boolean).forEach(t => b.append(h('span', 'xs-cell-sub', t))); return b; };
        const shifts = withData.map((r, i) => bearShift(withData, i));
        const t = table([shown === 'session3' ? '交易日组' : '交易日', '账号占比', '看多 / 看空 / 中性（每人一票）', '作者', '有效帖子', '看空偏离', '未计入', '来源与说明', '材料'],
          withData.map((r, i) => [span(r), shareBar((r.shares_equal_weight || {}).bullish == null ? r.shares : r.shares_equal_weight), shareCell(r), r.accounts_total, r.posts_total,
            shiftCell(shifts[i]), leftOut(r), stateCell(r), pick(r)]).slice(-30).reverse());
        t.className = 'table-scroll xs-table-wrap'; sec.append(t);
        sec.append(box);
      }
      const parts = Object.keys(empty).map(k => (EMPTY[k] || k) + '：' + empty[k]);
      sec.append(h('p', 'fine-print', [(shown === 'session3' ? '分组总数 ' : '交易日窗口总数 ') + rows.length, '有有效标签并画入图表 ' + withData.length].concat(parts).join('；')));
      out.push(sec);
    }
    if ((d.account_rules || {}).enabled) out.push(h('p', 'fine-print', '“加权”一行按账号权重计算（粉丝数分档或指定账号）；主数字是每个账号一票的结果，图表画的是后者。点赞数低于下限的帖子不在样本内。'));
    out.push(h('p', 'fine-print', SHIFT_NOTE));
    out.push(h('p', 'fine-print', '图表和表格只包含有有效标签的交易日。占比的分母只含已成功分类且相关的帖子（或账号）；无关、分类失败、待分类单独计数，不算中性。账号占比每个账号一票（取窗口内最后一条有效帖子），帖子占比每条帖子一次。'));
    out.push(h('p', 'fine-print', '这是本次采集样本中的比例，不是全部 X 用户的比例，也不是持仓。交易日窗口为上一交易日收盘到当日收盘。历史回填的日期是后来取得并分类的材料，不能当作当天可用的信号。采集不完整的日期不给出主导状态。'));
    p.replaceChildren(...out);
  }

  function renderViews(d) {
    const p = panel('views');
    const all = scope === 'keyword' ? d.narratives : (((d.panels || {})[scope] || {}).narratives || []);
    const nar = all.some(n => n.window_kind === 'session3') ? all.filter(n => n.window_kind === 'session3') : all;      // per group of three sessions when the build has made them
    if (!nar.length) { p.replaceChildren(h('p', 'fine-print', NOTE[scope]), d.has_data ? h('p', 'muted', '当前窗口的证据不足，没有可列出的主要观点。') : missingBox(d, '还没有可归纳的观点。需要以下输入：')); return; }
    d = {...d, narratives: nar};
    const latest = {}; d.narratives.forEach(n => { const k = `${n.topic_id}`; if (!latest[k] || n.window_label > latest[k]) latest[k] = n.window_label; });
    const out = [];
    for (const tid of Object.keys(latest)) {
      const sec = h('section', 'card section'); const head = h('div', 'section-head'); const one = d.narratives.find(n => n.topic_id === tid && n.window_label === latest[tid]) || {}; head.append(h('h3', null, TOPIC[tid] || tid), h('span', 'count', span({...one, window_label: latest[tid]}))); sec.append(head);
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

  const CELL_WHY = {SERIES_SHORTER_THAN_TWO_BLOCKS: '序列短于两个区块，无法估计区间', TOO_FEW_STATE_DAYS: '这个状态的交易日太少'};
  function verdict(c) {                                     // an inconclusive cell names the machine reason and the days behind it
    if (c.status !== 'INCONCLUSIVE') return '已估计';
    const b = h('span'); b.append(h('span', null, '样本不足，无结论'));
    if (c.status_reason) b.append(h('span', null, '：'), h('span', null, CELL_WHY[c.status_reason] || ''), data(` (${c.status_reason})`));
    b.append(h('span', null, ' · 有收益的状态日'), data(` ${c.n_dates == null ? '—' : c.n_dates}`), h('span', null, ' · 没有收益的状态日'), data(` ${c.state_days_without_return == null ? '—' : c.state_days_without_return}`));
    return b;
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
        c.mean_ci95 ? `${pct(c.mean_ci95[0])} ~ ${pct(c.mean_ci95[1])}` : '—', verdict(c)])));
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

  // ---------- overview: the update receipt, one card per panel, then the one primary chart ----------
  // A card answers, in this order: which sample, which period, how many authors, what the evidence is. Shares are one
  // vote per author (shares_equal_weight) and, separately, one per valid post (shares_by_post); a row without the
  // equal-weight shares shows a missing state and never borrows the weighted ones.
  const PRIMARY = 'qqq', LOW = 30, TARGET_POSTS = 500;
  const RANGES = [[10, '最近 10 组'], [20, '最近 20 组'], [0, '全部']];
  let range = 0, picked = null, view = 'share';
  const VIEWS = [['share', '占比'], ['shift', '看空偏离']];
  const lastDay = r => { const g = r.group_sessions || []; return g.length ? g[g.length - 1] : r.window_label; };
  function closeMap(d) {                                    // close, source and the last priced date per topic; a group is priced at its last session
    const m = {}, src = {}, meta = d.closes_meta || {}, cut = meta.frozen_input_last_date, fill = meta.display_fill, last = {};
    const put = (t, day, c, s_) => { m[`${t}|${day}`] = c; src[`${t}|${day}`] = s_; if (!last[t] || day > last[t]) last[t] = day; };
    if (d.closes) Object.entries(d.closes).forEach(([t, by]) => Object.entries(by || {}).forEach(([day, c]) => put(t, day, c, fill && cut && day > cut ? 'display' : 'research')));
    else (d.returns || []).forEach(r => { if (r.h === 1 && r.anchor_close != null) put(r.topic_id, r.session_date, r.anchor_close, 'research'); });
    return {m, src, last, meta};
  }
  function closeOf(cm, tid, r) {
    for (const k of [`${tid}|${lastDay(r)}`, `${tid}|${r.window_label}`]) if (cm.m[k] != null) return {v: cm.m[k], src: cm.src[k], reason: null};
    const why = !cm.last[tid] ? 'NO_PRICE_SERIES' : lastDay(r) > cm.last[tid] ? 'AFTER_PRICE_INPUT_END' : 'NO_CLOSE_FOR_SESSION';
    return {v: null, src: null, reason: why};
  }
  const PRICE_WHY = {NO_PRICE_SERIES: '没有可配对的价格序列', AFTER_PRICE_INPUT_END: '这一组的最后交易日晚于价格数据的最后日期', NO_CLOSE_FOR_SESSION: '价格序列里没有这一天的收盘价'};
  const BASIS = {research: '复权研究收盘价（冻结研究序列，参与收益计算）', display: '未复权展示收盘价（moomoo 日线，只用于画图）'};
  const eqOf = r => r.shares_equal_weight || {};
  const hasEq = r => eqOf(r).bullish != null;
  // Bear shift: a group's bearish share (one vote per author) minus the mean of the previous BASE_N groups that have at least
  // LOW authors. It shows change against the sample's own level; it does not reweight or alter any stored share.
  const BASE_N = 10, BASE_MIN = 3;
  const SHIFT_NOTE = '看空偏离 = 这一组的看空占比减去之前最多 10 组（每组至少 30 位作者）的平均看空占比，单位是百分点；之前不足 3 组或本组作者少于 30 位时不计算。它只显示相对自身水平的变化，原始占比不加权、不改动。';
  function bearShift(rows, i) {
    const r = rows[i]; if (!r || !hasEq(r) || (r.accounts_total || 0) < LOW) return null;
    const prev = rows.slice(0, i).filter(x => hasEq(x) && (x.accounts_total || 0) >= LOW).slice(-BASE_N);
    if (prev.length < BASE_MIN) return null;
    const base = prev.reduce((a, x) => a + eqOf(x).bearish, 0) / prev.length;
    return {now: eqOf(r).bearish, base, diff: eqOf(r).bearish - base, n: prev.length};
  }
  const pp = v => `${v >= 0 ? '+' : '−'}${Math.abs(v * 100).toFixed(1)}`;
  function shiftCell(x) {
    if (!x) return h('span', 'muted', '—');
    const b = h('span', `xs-shift ${x.diff >= 0 ? 'xs-shift-bear' : 'xs-shift-bull'}`); b.append(data(pp(x.diff)), h('span', null, ' 个百分点')); return b;
  }
  function status(r) {
    const c = r.classification || {};
    if (hasEq(r)) return (c.pending > 0 || r.state_reason === 'CLASSIFICATION_INCOMPLETE') ? 'partial' : 'ok';
    if (r.shares && r.shares.bullish != null) return 'noeq';
    const k = r.empty_reason || (c.pending || r.state_reason === 'CLASSIFICATION_INCOMPLETE' ? 'PENDING' : c.matched_posts ? 'NO_VALID_LABEL' : 'NO_SAMPLE');
    return {PENDING: 'pending', NO_VALID_LABEL: 'novalid'}[k] || 'nosample';
  }
  const STATUS = {ok: '已全部分类', partial: '分类未完成，占比还会变', noeq: '等权占比缺失', pending: '待分类，暂无占比', novalid: '没有有效标签', nosample: '无样本'};
  const MARK = {partial: '*', noeq: '≠', pending: '…', novalid: '×', nosample: '∅'};
  const DESC = {keyword: '关键词检索采到、命中词表、正文点名 QQQ 并达到点赞下限的帖子', watchlist: '重点作者名单内作者的帖子，同样要求点名 QQQ 并达到点赞下限',
    combined: '关键词样本与重点作者两路合并，跨来源去重：同一条帖子只算一次，同一作者只投一票'};
  const bj = v => { const t = Date.parse(v || ''); if (!v || isNaN(t)) return null; const x = new Date(t + 8 * 3600e3), z = n => String(n).padStart(2, '0');
    return `${x.getUTCFullYear()}-${z(x.getUTCMonth() + 1)}-${z(x.getUTCDate())} ${z(x.getUTCHours())}:${z(x.getUTCMinutes())}`; };
  const when = v => { const n = data(bj(v) || '—'); if (v) { n.title = String(v); n.dataset.utc = String(v); } return n; };       // Beijing time shown, the stored UTC kept on the node
  const nyDate = v => { const t = Date.parse(v || ''); if (isNaN(t)) return null; try { return new Date(t).toLocaleDateString('en-CA', {timeZone: 'America/New_York'}); } catch (e) { return new Date(t - 5 * 3600e3).toISOString().slice(0, 10); } };
  const panelDaily = (d, sc) => { if (sc === 'keyword') return {available: true, daily: d.daily || []}; const pn = (d.panels || {})[sc] || {}; return {...pn, daily: pn.daily || []}; };
  function series(d, sc) {                                  // the QQQ rows of one panel, all languages, per group of three sessions when the build has them
    const q = panelDaily(d, sc).daily.filter(r => r.lang_scope === 'all' && r.topic_id === PRIMARY);
    const kind = q.some(r => r.window_kind === 'session3') ? 'session3' : 'session';
    let rows = q.filter(r => r.window_kind === kind).sort((a, b) => a.window_label < b.window_label ? -1 : 1);
    const sel = sc === 'watchlist' ? nyDate(((d.watchlist || {}).summary || {}).selected_at_utc) : null;
    if (sel) rows = rows.filter(r => lastDay(r) >= sel);     // the list exists from the day it was selected; earlier groups are not part of this panel
    return {kind, rows, sel};
  }
  function triple(sh, n, withPct) {                       // stance words stay translatable; numbers are data
    const out = [];
    ['bullish', 'bearish', 'neutral'].forEach((k, i) => { if (i) out.push(h('span', null, ' · ')); out.push(h('span', null, STATE[k]), data(withPct ? ` ${pct(sh[k])} (${n[k] == null ? '—' : n[k]})` : ` ${n[k] == null ? '—' : n[k]}`)); });
    return out;
  }
  const tag = (t, cls) => chip(t, cls || 'xs-tag-warn');
  const fact = (k, v) => { const b = h('div', 'xs-fact'); b.append(h('span', 'xs-fact-k', k), v instanceof Node ? v : data(v == null ? '—' : v)); return b; };
  function outcome(up) {                                    // what the last round did, from the receipt the host script wrote
    if (up.state === 'NOT_REPORTED') return [['更新回执：尚未上报', 'tag-dim']];
    const ex = Object.values(up.stage_exits || {}), anyOk = ex.some(v => v === 0), anyBad = ex.some(v => v !== 0 && v != null);
    const out = [up.ok === false ? (anyOk ? ['本轮部分成功：汇总没有完成', 'xs-tag-warn'] : ['本轮失败', 'xs-tag-bad']) : anyBad ? ['本轮部分成功：有步骤出错', 'xs-tag-warn'] : ['本轮成功', 'run-succeeded']];
    if (up.credit_hold || (up.collection || {}).status === 'PAUSED_PROVIDER_CREDITS') out.push(['供应商暂停：额度不足，没有发出采集请求', 'xs-tag-warn']);
    if (up.state === 'STALE') out.push(['数据过期：超过两个周期没有新回执', 'xs-tag-bad']);
    return out;
  }
  function receiptStrip(d) {
    const up = d.updates || {state: 'NOT_REPORTED'}, box = h('div', 'card xs-receipt');
    const chips = h('div', 'xs-fresh'); chips.append(h('strong', null, '更新状态')); outcome(up).forEach(([t, c]) => chips.append(chip(t, c))); box.append(chips);
    if (up.state !== 'NOT_REPORTED') {
      const g = h('div', 'xs-facts');
      g.append(fact('本轮结束', when(up.round_finished_at_utc)), fact('最后成功运行', when(up.last_successful_update_at_utc)), fact('最后成功采集', when(up.last_successful_collection_at_utc)),
        fact('最后完成分类', when(up.last_label_written_at_utc)), fact('最后成功汇总', when(up.last_summary_committed_at_utc)), fact('最新可见帖子（发布时间）', when((d.collection || {}).last_post_created_at_utc)));
      box.append(g);
    }
    box.append(h('p', 'fine-print', '时间均为北京时间；鼠标停在时间上可看原始 UTC 记录。'));
    return box;
  }
  function stages(d, r, cm) {                               // the stage each number is at, written next to it
    const out = [], c = r.classification || {}, st = status(r);
    if (r.collection_status && r.collection_status !== 'COMPLETE') out.push(tag('覆盖不全'));
    if ((c.pending || 0) > 0) out.push(tag('分类未完成'));
    if (st === 'novalid' || st === 'nosample' || st === 'noeq') out.push(tag(STATUS[st]));
    if (closeOf(cm, PRIMARY, r).v == null) out.push(tag('行情不完整'));
    if ((d.classifier || {}).quality !== 'VALIDATED') out.push(tag('模型未人工验证', 'xs-tag-label'));
    const b = h('div', 'xs-stages'); b.append(...out); return b;
  }
  function cardDetail(d, r, extra) {                        // votes, progress, identity, versions and the time chain, one click away
    const det = h('details', 'xs-card-detail'); det.append(h('summary', null, extra ? '详情：票数、进度、模型与时间' : '详情：模型、版本与时间'), ...(extra || []));
    const act = r.activation || {}, c = r.classification || {}, ex = r.excluded_or_unjudged || {}, g = h('div', 'xs-facts');
    const short = v => v ? String(v).slice(0, 16) : '—';
    g.append(fact('窗口开始', when(r.window_start_utc)), fact('窗口结束', when(r.window_end_utc)), fact('汇总提交', when(r.signal_committed_at_utc || r.first_committed_at_utc)),
      fact('模型', act.model_id || (d.classifier || {}).model_id || '—'), fact('模型版本', short(act.model_revision || (d.classifier || {}).revision)), fact('提示词哈希', short(act.prompt_hash)),
      fact('推理身份', short(act.identity)), fact('样本版本', act.sample_version || c.sample_version || '—'), fact('快照', short(r.snapshot_id)),
      fact('无关', ex.IRRELEVANT || 0), fact('分类失败', (ex.EVIDENCE_NOT_IN_TEXT || 0) + (ex.JSON_INVALID || 0)), fact('点赞不足未计入', c.filtered_low_likes == null ? '—' : c.filtered_low_likes),
      fact('原文未点名未计入', c.filtered_target_not_named == null ? '—' : c.filtered_target_not_named));
    det.append(g); return det;
  }
  function headline(d, sc, cm) {
    const card = h('div', 'card xs-head');
    const top = h('div', 'xs-head-top'); const tg = h('span', 'tag tag-dim', 'QQQ'); tg.setAttribute('translate', 'no');
    top.append(h('h3', null, PANEL[sc]), tg); card.append(top, h('p', 'xs-desc', DESC[sc]));
    const pn = panelDaily(d, sc), label = chip('模型判断，待人工验证', 'xs-tag-label');
    if (sc !== 'keyword' && !pn.available) { card.append(h('p', 'muted', '还没有导入重点作者名单。'), label); return card; }
    const {kind, rows, sel} = series(d, sc);
    if (sel) { const p = h('p', 'fine-print'); p.append(h('span', null, '名单选定于'), h('span', null, ' '), when(((d.watchlist || {}).summary || {}).selected_at_utc), h('span', null, '；更早的分组不属于这个面板。')); card.append(p); }
    if (!rows.length) { card.append(h('p', 'muted', '还没有 QQQ 的占比数据。'), label); return card; }
    const latest = rows[rows.length - 1], valued = rows.filter(hasEq), r = valued[valued.length - 1], st = status(latest);
    const w1 = h('p', 'xs-when'); w1.append(h('span', null, kind === 'session3' ? '最新一组（三个交易日）' : '最新交易日（还没有三日分组）'), data(` ${span(latest)} `), chip(STATUS[st], st === 'ok' ? 'run-succeeded' : 'xs-tag-warn')); card.append(w1);
    if (r && r !== latest) { const w2 = h('p', 'xs-when'); w2.append(h('span', null, '下面的数字来自最近有值的一组'), data(` ${span(r)}`)); card.append(w2); }
    if (!r) { card.append(h('p', 'muted', st === 'noeq' ? '等权占比缺失：这一组只有加权结果，这里不显示。' : '还没有带等权占比的分组。'), stages(d, latest, cm), label); return card; }
    const n = r.accounts_total || 0, sh = eqOf(r), na = r.accounts || {}, np = r.posts || {}, sp = r.shares_by_post || {};
    if (n < LOW) {                                           // few authors: the count and the state are the news, the percentages are not
      const m = h('p', 'xs-main-low'); m.append(data(String(n)), h('span', null, ' 位作者 · 样本不足')); card.append(m);
    } else {
      const big = h('div', 'xs-stats');
      for (const k of ['bullish', 'bearish', 'neutral']) { const b = h('div', `xs-stat xs-t-${k}`); const v = data(pct(sh[k])); v.className = 'num xs-stat-v'; b.append(h('span', 'xs-stat-k', STATE[k]), v); big.append(b); }
      const bar = shareBar(sh); bar.classList.add('xs-bar-lg'); card.append(big, bar);
    }
    if (n >= LOW) {                                          // the bear share against this panel's own recent level
      const sx = bearShift(rows, rows.indexOf(r)), sl = h('p', 'xs-shift-line');
      if (sx) sl.append(h('span', 'xs-line-k', '看空较之前'), data(` ${sx.n} `), h('span', 'xs-line-k', '组均值'), h('span', null, ' '), shiftCell(sx), h('span', 'xs-line-k', ' · 均值'), data(` ${pct(sx.base)}`));
      else sl.append(h('span', 'xs-line-k', '之前的分组不足 3 组，暂不计算看空偏离'));
      card.append(sl);
    }
    const meta = h('p', 'xs-meta'); meta.append(data(String(n)), h('span', null, ' 位作者'), h('span', null, ' · '), data(String(r.posts_total || 0)), h('span', null, ' 条有效帖子'));
    if ((r.posts_total || 0) < TARGET_POSTS) meta.append(h('span', null, ' · '), h('span', null, '距每组 500 条还差'), data(` ${TARGET_POSTS - (r.posts_total || 0)}`));
    card.append(meta, stages(d, r, cm));
    if (n < LOW) card.append(h('p', 'xs-warn', '作者少于 30 位：几位作者就能改变比例，这里只列票数，不给比例结论。'));
    if (status(r) === 'partial') card.append(h('p', 'xs-note', '这一组还有待分类的帖子：数字是已分类部分的结果，还会变。'));
    const g = h('div', 'xs-split');
    const line = (k, s_, c_, total, unit) => { const p = h('p', 'xs-line'); p.append(h('span', 'xs-line-k', k), h('span', null, ' '), ...triple(s_, c_, n >= LOW), h('span', null, ' · '), h('span', null, '共'), data(` ${total} `), h('span', null, unit)); return p; };
    g.append(line('按作者（每人一票）', sh, na, n, '位作者'));
    if (sp.bullish != null) g.append(line('按有效帖子（每帖一次）', sp, np, r.posts_total, '条有效帖子'));
    const c = r.classification || {};
    const prog = h('p', 'xs-line'); prog.append(h('span', 'xs-line-k', '已处理判断'), data(` ${c.classified_or_prefiltered == null ? '—' : c.classified_or_prefiltered} / ${c.matched_posts == null ? '—' : c.matched_posts} `),
      h('span', 'fine-print', '（含有效、无关、分类失败和规则预筛）'), h('span', null, ' · 模型待分类'), data(` ${c.pending == null ? '—' : c.pending}`));
    g.append(prog);
    card.append(cardDetail(d, r, [g]));
    return card;
  }
  function slotInfo(r, p, sx) {                                  // the line under the chart for the selected group
    const box = h('div'), st = status(r);
    const head = h('p', 'xs-pick-head'); head.append(data(span(r)), h('span', null, ' · '), h('span', null, STATUS[st])); box.append(head);
    if (st === 'ok' || st === 'partial') {
      const a = h('p', 'xs-line'); a.append(h('span', 'xs-line-k', '按作者'), h('span', null, ' '), ...triple(eqOf(r), r.accounts || {}, (r.accounts_total || 0) >= LOW), h('span', null, ' · '), h('span', null, '共'), data(` ${r.accounts_total} `), h('span', null, '位作者'));
      box.append(a);
      if ((r.accounts_total || 0) < LOW) box.append(h('p', 'xs-note', '作者少于 30 位，只作参考。'));
      if (sx) { const b = h('p', 'xs-line'); b.append(h('span', 'xs-line-k', '看空偏离'), h('span', null, ' '), shiftCell(sx), h('span', 'xs-line-k', ' · 之前均值'), data(` ${pct(sx.base)}`), h('span', 'xs-line-k', ' · 本组'), data(` ${pct(sx.now)}`)); box.append(b); }
    } else box.append(h('p', 'xs-note', {pending: '这一组的帖子还在等模型分类，所以没有占比。', nosample: '这一组没有采到符合条件的帖子。', novalid: '这一组的帖子都无关或分类失败，没有有效标签。', noeq: '这一组只有加权结果，没有等权占比。'}[st]));
    const pr = h('p', 'xs-line');
    if (p.v != null) pr.append(h('span', 'xs-line-k', '收盘价'), data(` ${p.v.toFixed(2)} USD `), h('span', null, BASIS[p.src]), h('span', null, ' · 交易日'), data(` ${lastDay(r)}`));
    else pr.append(h('span', 'xs-line-k', '缺价'), h('span', null, ` ${PRICE_WHY[p.reason]} `), data(`(${p.reason})`));
    box.append(pr);
    return box;
  }
  function mainChart(rows, prices, onPick, shifts) {
    // shifts (optional): the bear-shift view - each group a bar up (more bearish than its recent mean) or down, scaled to
    // the largest shift shown; a group without a shift gets the empty marker. The price strip is the same in both views.
    // every group on the axis in order: account shares (one vote per author, stacked, 0-100 %) with the author count
    // above, a marker where a group has no shares, and the QQQ close strip below, broken at gaps and at a source change.
    // Each group is a focusable slot: arrow keys, Home / End, Enter or a tap / click select it and fill the line under the chart.
    // Hovering does not change the selection, so scrolling past the chart or a resting pointer never moves it.
    const W = 720, H = 300, L = 8, R = 8, bt = 22, bb = 172, pt = 196, pb = 262, n = rows.length, bw = (W - L - R) / Math.max(n, 1);
    const svg = s('svg', {viewBox: `0 0 ${W} ${H}`, class: 'xs-chart xs-main', role: 'group', 'aria-label': '账号占比、账号数与收盘价'});
    svg.append(s('line', {x1: L, x2: W - R, y1: (bt + bb) / 2, y2: (bt + bb) / 2, class: 'xs-c-grid'}));
    const slots = [], mid = (bt + bb) / 2, half = (bb - bt) / 2;
    const maxAbs = shifts ? Math.max(0.05, ...shifts.filter(Boolean).map(x => Math.abs(x.diff))) : 0;
    if (shifts) {
      const a = s('text', {x: L + 2, y: bb + 15, class: 'xs-c-ax'}); a.textContent = `±${(maxAbs * 100).toFixed(1)} pp`; svg.append(a);   // the scale, in the gap above the price strip, clear of every bar
    }
    rows.forEach((r, i) => {
      const x = L + i * bw, w = Math.max(bw - 2, 1), st = status(r), cx = x + w / 2, g = s('g', {class: 'xs-c-slot', tabindex: '-1', role: 'button', 'aria-label': STATUS[st]}), ttl = s('title'); ttl.textContent = span(r); g.append(ttl);
      const lab = s('text', {x: cx, y: bt - 7, 'text-anchor': 'middle'});
      if (shifts) {
        const sx = shifts[i];
        if (sx) { const hh = Math.max(Math.abs(sx.diff) / maxAbs * half, 1); g.append(s('rect', {x, y: sx.diff >= 0 ? mid - hh : mid, width: w, height: hh, class: sx.diff >= 0 ? 'xs-c-bearish' : 'xs-c-bullish'})); }
        else { g.append(s('rect', {x, y: bt, width: w, height: bb - bt, class: 'xs-c-gap'})); const m = s('text', {x: cx, y: mid + 4, 'text-anchor': 'middle', class: 'xs-c-mark'}); m.textContent = '–'; g.append(m); }
        lab.setAttribute('class', `xs-c-n${(r.accounts_total || 0) < LOW ? ' xs-c-nlow' : ''}`); lab.textContent = String(r.accounts_total || 0);
      } else if (st === 'ok' || st === 'partial') {
        const sh = eqOf(r), low = r.accounts_total == null || r.accounts_total < LOW; let y = bt;
        for (const k of ['bullish', 'neutral', 'bearish']) { const hh = (bb - bt) * sh[k]; g.append(s('rect', {x, y, width: w, height: hh, class: `xs-c-${k}${low ? ' xs-c-low' : ''}`})); y += hh; }
        lab.setAttribute('class', `xs-c-n${low ? ' xs-c-nlow' : ''}`); lab.textContent = `${r.accounts_total == null ? '—' : r.accounts_total}${st === 'partial' ? MARK.partial : ''}`;
      } else {
        g.append(s('rect', {x, y: bt, width: w, height: bb - bt, class: 'xs-c-gap'}));
        const m = s('text', {x: cx, y: (bt + bb) / 2 + 4, 'text-anchor': 'middle', class: 'xs-c-mark'}); m.textContent = MARK[st]; g.append(m);
        lab.setAttribute('class', 'xs-c-n xs-c-nlow'); lab.textContent = String(r.accounts_total || 0);
      }
      g.append(lab, s('rect', {x: x - 1, y: 4, width: w + 2, height: H - 30, class: 'xs-c-hit'}));
      const go = j => { const k = Math.max(0, Math.min(n - 1, j)); onPick(k, true); };
      g.addEventListener('keydown', e => { const k = {ArrowLeft: i - 1, ArrowRight: i + 1, Home: 0, End: n - 1}[e.key]; if (k != null) { if (e.preventDefault) e.preventDefault(); go(k); } else if (e.key === 'Enter' || e.key === ' ') { if (e.preventDefault) e.preventDefault(); onPick(i, false); } });
      g.addEventListener('click', () => onPick(i, false));
      slots.push(g); svg.append(g);
    });
    svg.append(s('rect', {x: L, y: pt - 8, width: W - L - R, height: pb - pt + 16, rx: 4, class: 'xs-c-strip'}));
    const pts = prices.map((p, i) => ({x: L + i * bw + bw / 2, ...p})), have = pts.filter(p => p.v != null);
    let gaps = false, display = false, dashed = false, research = false;
    if (have.length) {
      const lo = Math.min(...have.map(p => p.v)), hi = Math.max(...have.map(p => p.v)), sp = hi - lo || 1, Y = v => pb - (v - lo) / sp * (pb - pt);
      let seg = [];
      const flush = () => { if (seg.length > 1) { if (seg[0].src === 'display') dashed = true; svg.append(s('polyline', {class: seg[0].src === 'display' ? 'xs-c-price xs-c-price-disp' : 'xs-c-price', fill: 'none', points: seg.map(p => `${p.x.toFixed(1)},${Y(p.v).toFixed(1)}`).join(' ')})); } seg = []; };
      pts.forEach(p => {
        if (p.v == null) { if (have.some(q => q.x < p.x) && have.some(q => q.x > p.x)) gaps = true; flush(); return; }
        if (seg.length && seg[seg.length - 1].src !== p.src) flush();
        if (p.src === 'display') display = true; else research = true;
        seg.push(p); svg.append(s('circle', {cx: p.x.toFixed(1), cy: Y(p.v).toFixed(1), r: 2.2, class: p.src === 'display' ? 'xs-c-dot xs-c-dot-disp' : 'xs-c-dot'}));
      });
      flush();
      const a = s('text', {x: L + 4, y: pt + 4, class: 'xs-c-ax'}); a.textContent = hi.toFixed(2);
      const b = s('text', {x: L + 4, y: pb + 4, class: 'xs-c-ax'}); b.textContent = lo.toFixed(2); svg.append(a, b);
    }
    [0, Math.floor((n - 1) / 2), n - 1].filter((v, i, a) => v >= 0 && a.indexOf(v) === i).forEach(i => {
      const t = s('text', {x: L + i * bw + bw / 2, y: H - 12, class: 'xs-c-ax', 'text-anchor': n === 1 ? 'middle' : i === 0 ? 'start' : i === n - 1 ? 'end' : 'middle'}); t.textContent = lastDay(rows[i]); svg.append(t); });
    return {svg, slots, priced: have.length > 0, gaps, display, dashed, research};
  }
  function chartBox(d, cm) {
    const box = h('section', 'card section xs-primary'), top = h('div', 'section-head');
    top.append(h('h3', null, 'QQQ：作者占比、作者数与收盘价'), scopeBar()); box.append(top);
    const pn = panelDaily(d, scope);
    if (scope !== 'keyword' && !pn.available) { box.append(h('p', 'muted', '还没有导入重点作者名单。')); return box; }
    const {kind, rows} = series(d, scope);
    if (!rows.length) { box.append(d.has_data ? h('p', 'muted', '这个标的还没有带有效标签的日期。') : missingBox(d, '还没有情绪数据。开始采集和分类之前需要以下输入：')); return box; }
    const shown = range ? rows.slice(-range) : rows;
    const allShifts = rows.map((_, i) => bearShift(rows, i)), shifts = range ? allShifts.slice(-range) : allShifts;   // the baseline uses every earlier group, not only the ones shown
    const bar = h('div', 'xs-range'), rb = h('div', 'xs-chips'); rb.setAttribute('role', 'group'); rb.setAttribute('aria-label', '显示范围');
    RANGES.forEach(([k, t]) => { const b = h('button', `tag ${k === range ? '' : 'tag-dim'}`, t); b.type = 'button'; b.setAttribute('aria-pressed', String(k === range)); b.addEventListener('click', () => { range = k; if (last) renderOverview(last); }); rb.append(b); });
    const mb = h('div', 'xs-chips'); mb.setAttribute('role', 'group'); mb.setAttribute('aria-label', '图表视图');
    VIEWS.forEach(([k, t]) => { const b = h('button', `tag ${k === view ? '' : 'tag-dim'}`, t); b.type = 'button'; b.setAttribute('aria-pressed', String(k === view)); b.addEventListener('click', () => { view = k; if (last) renderOverview(last); }); mb.append(b); });
    const span_ = h('p', 'xs-when'); span_.append(h('span', null, '显示'), data(` ${(shown[0].group_sessions || [shown[0].window_label])[0]} – ${lastDay(shown[shown.length - 1])} `), h('span', null, '共'), data(` ${shown.length} `), h('span', null, kind === 'session3' ? '组' : '个交易日'), h('span', null, '（全部'), data(` ${rows.length} `), h('span', null, '）'));
    bar.append(rb, mb, span_); box.append(bar);
    const prices = shown.map(r => closeOf(cm, PRIMARY, r)), info = h('div', 'xs-pick'); info.setAttribute('aria-live', 'polite');
    let idx = Math.max(0, shown.findIndex(r => r.window_label === picked)); if (picked == null || shown[idx].window_label !== picked) idx = shown.length - 1;
    const chart = mainChart(shown, prices, (i, focus) => {
      chart.slots.forEach((g, j) => { g.setAttribute('tabindex', j === i ? '0' : '-1'); g.setAttribute('aria-pressed', String(j === i)); g.setAttribute('class', j === i ? 'xs-c-slot xs-c-on' : 'xs-c-slot'); });
      picked = shown[i].window_label; info.replaceChildren(slotInfo(shown[i], prices[i], shifts[i]));
      if (focus && chart.slots[i].focus) chart.slots[i].focus();
    }, view === 'shift' ? shifts : null);
    const key = h('div', 'xs-key');
    if (view === 'shift') {
      const up = h('span', 'xs-key-i'); up.append(h('i', 'xs-sw xs-bearish'), h('span', null, '向上：看空高于之前均值')); const dn = h('span', 'xs-key-i'); dn.append(h('i', 'xs-sw xs-bullish'), h('span', null, '向下：看空低于之前均值'));
      key.append(up, dn, h('span', 'xs-key-i', '柱上数字：作者数'), h('span', 'xs-key-i', '–：之前不足 3 组或作者少于 30 位'));
    } else {
      for (const k of ['bullish', 'neutral', 'bearish']) { const it = h('span', 'xs-key-i'); it.append(h('i', `xs-sw xs-${k}`), h('span', null, STATE[k])); key.append(it); }
      key.append(h('span', 'xs-key-i', '柱上数字：作者数'), h('span', 'xs-key-i', '浅色柱：作者少于 30 位'), h('span', 'xs-key-i', '数字后的 *：分类未完成'));
    }
    const kr = h('span', 'xs-key-i'); kr.append(h('i', 'xs-sw-line'), h('span', null, '实线和实心点：复权研究收盘价（USD）')); key.append(kr);
    if (chart.display) { const kd = h('span', 'xs-key-i'); kd.append(h('i', 'xs-sw-line xs-sw-dash'), h('span', null, '虚线和空心点：未复权展示收盘价（USD，只画图）')); key.append(kd); }
    const fig = h('figure', 'xs-figure'); fig.append(chart.svg, info);
    const notes = h('ul', 'xs-notes');
    [kind === 'session3' ? '每根柱是一组三个交易日，按顺序全部保留在轴上；同一作者在一组内只投一票。' : '还没有三日分组：每根柱是一个交易日，按顺序全部保留；同一作者只投一票。',
      '空柱：… 待分类 · ∅ 无样本 · × 没有有效标签 · ≠ 等权占比缺失。',
      '休市日不在轴上；交易日有记录但缺收盘价时，价格线在那里断开，并在选中这一组时写出原因。',
      '价格取每组最后一个交易日的收盘价；研究序列与展示序列之间断线，不相连。',
      '选中一组：点按柱子，或用 Tab 进入后按左右方向键、Home、End。鼠标经过不会切换。'].forEach(t => notes.append(h('li', null, t)));
    if (view === 'shift') notes.append(h('li', null, SHIFT_NOTE));
    if (!chart.priced) notes.append(h('li', null, '这一段没有可配对的收盘价，价格区留空。'));
    const fill = (cm.meta || {}).display_fill;
    if (chart.display && fill) { const li = h('li'); li.append(h('span', null, chart.dashed ? '虚线和空心点：' : '空心点：'), data(fill.after), h('span', null, ' '), h('span', null, '之后的收盘价来自 moomoo 展示用日线（未复权，只用于画图，不参与收益配对）。')); notes.append(li); }
    box.append(key, fig, notes, h('p', 'fine-print', '这是本次采集样本中的比例，不是全部 X 用户的比例，也不是预测；价格只作日期对照。分母、帖子口径和逐条材料在下方“每组明细与分母”里。'));
    chart.slots.length && (() => { const i = idx; chart.slots.forEach((g, j) => g.setAttribute('tabindex', j === i ? '0' : '-1')); picked = shown[i].window_label; info.replaceChildren(slotInfo(shown[i], prices[i], shifts[i])); chart.slots[i].setAttribute('class', 'xs-c-slot xs-c-on'); chart.slots[i].setAttribute('aria-pressed', 'true'); })();
    return box;
  }
  const BIAS_NOTE = '读数提醒：11-26 对 150 条 QQQ 帖子做了盲审（由第二个模型复核，不是人工）。按样本池估计，模型把约 64% 判为看多，复核只有约 30%；复核认为中性的帖子，模型大多判成了看多或看空。只看有方向的帖子，复核也有约 3/4 看多。所以偏多一部分是样本本身，一部分是模型放大；绝对比例要打折看，组与组之间的变化更可靠。';
  const biasNote = () => { const p = h('p', 'xs-bias'); p.append(h('span', null, BIAS_NOTE)); return p; };   // the blind review is in local_only/x_qqq_audit_20261126/blind_eval_150.json
  function renderOverview(d) {
    const p = panel('overview'); if (!p) return;
    const cm = closeMap(d), heads = h('div', 'xs-heads'); SHOWN_PANELS.forEach(k => heads.append(headline(d, k, cm)));
    p.replaceChildren(...[receiptStrip(d), heads, d.has_data ? biasNote() : null, chartBox(d, cm)].filter(Boolean));     // the review note only next to real shares
  }

  const load = () => fetch(root.dataset.url + (simpleIdentity ? `?identity=${encodeURIComponent(simpleIdentity)}` : ''), {credentials: 'same-origin', cache: 'no-store'}).then(r => { if (!r.ok) throw new Error(String(r.status)); return r.json(); }).then(d => {
    const state = document.getElementById('x-stance-state');
    if (d.state === 'READ_ERROR') { state.textContent = '读取失败'; const o = panel('overview'); if (o) o.replaceChildren(h('p', 'muted', '读取失败')); return; }
    state.textContent = d.has_data ? '已有数据' : '尚无数据';
    last = d;
    renderOverview(d); renderSettings(d); renderSimple(d); renderAuthors(d); renderDaily(d); renderViews(d); renderHistory(d);
  }).catch(() => { document.getElementById('x-stance-state').textContent = '读取失败'; });
  load();
})();
