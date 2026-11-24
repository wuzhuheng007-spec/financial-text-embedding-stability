/* English news: model judgement per target (research page tab). Same-origin JSON only; the page never calls a
 * provider or a model. Articles and posts have their own denominators and are never added together. */
(() => {
  'use strict';
  const root = document.getElementById('x-stance');
  const panel = root && root.querySelector('[data-xpanel="news"]');
  if (!panel || !root.dataset.newsUrl) return;
  const h = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; };
  const pct = v => v == null ? '—' : `${(v * 100).toFixed(1)}%`;
  const OUT = {bullish: '看多', bearish: '看空', neutral: '中性', irrelevant: '无关', insufficient: '原文不足', failed: '判断失败'};
  const fmtT = v => v ? String(v).replace('T', ' ').replace(/(\.\d+)?(Z|\+00:00)$/, ' UTC') : '—';
  const data = v => { const n = h('span', 'num', v == null ? '—' : String(v)); n.setAttribute('translate', 'no'); return n; };
  const quote = t => { const n = h('span', null, t || '—'); n.setAttribute('translate', 'no'); return n; };
  const link = (url, text) => { if (!/^https?:\/\//.test(url || '')) return quote(text); const a = h('a', null, text); a.href = url; a.target = '_blank'; a.rel = 'noopener noreferrer'; a.setAttribute('translate', 'no'); return a; };
  const row = (k, v) => { const r = h('div', 'setting-row'); r.append(h('span', 'label', k), v instanceof Node ? v : h('span', 'num', v == null ? '—' : String(v))); return r; };
  const table = (heads, rows) => { const t = h('table', 'official-table'); const hr = h('tr'); heads.forEach(x => hr.append(h('th', null, x))); t.append(hr);
    rows.forEach(cells => { const tr = h('tr'); cells.forEach(c => { const td = h('td'); td.append(c instanceof Node ? c : document.createTextNode(c == null ? '—' : String(c))); tr.append(td); }); t.append(tr); });
    const w = h('div', 'table-scroll'); w.tabIndex = 0; w.append(t); return w; };
  const bar = a => { const b = h('div', 'xs-bar'); b.setAttribute('role', 'img'); if (!a.n) { b.classList.add('xs-bar-empty'); b.setAttribute('aria-label', '无占比'); return b; }
    b.setAttribute('aria-label', '三类占比'); for (const k of ['bullish', 'neutral', 'bearish']) { const seg = h('span', `xs-seg xs-${k}`); seg.style.width = `${a[k + '_share'] * 100}%`; b.append(seg); } return b; };
  const three = a => `${a.bullish} / ${a.bearish} / ${a.neutral}` + (a.n ? `（${pct(a.bullish_share)} / ${pct(a.bearish_share)} / ${pct(a.neutral_share)}）` : '');
  let target = null, day = null, last = null;

  function render(d) {
    last = d;
    if (d.state === 'READ_ERROR') { panel.replaceChildren(h('p', 'muted', '读取失败')); return; }
    const out = [];
    const head = h('div', 'card'); const t = d.totals || {};
    head.append(h('h3', null, '英文新闻：模型判断'), h('span', 'tag tag-dim', d.note), h('p', 'fine-print', d.rule), h('p', 'fine-print', d.timing),
      row('入选（文章 × 标的）', t.selected_pairs), row('已判断', t.judged_pairs), row('待处理', t.pending_pairs),
      row('看多 / 看空 / 中性', `${t.bullish || 0} / ${t.bearish || 0} / ${t.neutral || 0}`), row('无关', t.irrelevant || 0), row('原文不足', t.insufficient || 0), row('判断失败', t.failed || 0),
      row('模型', data((d.identities || []).map(i => `${i.model_id} · ${i.labels}`).join('；') || '—')), row('提示词版本', data(d.prompt_version)));
    out.push(head);
    if (!(d.targets || []).some(x => x.judged)) { out.push(h('p', 'muted', '还没有已判断的英文新闻。')); panel.replaceChildren(...out); return; }
    const tc = h('div', 'card'); tc.append(h('h3', null, '各标的：文章占比'));
    tc.append(table(['标的', '占比', '看多 / 看空 / 中性（占比）', '分母（文章）', '入选', '无关', '原文不足', '判断失败', '待处理', '查看'], d.targets.map(x => {
      const b = h('button', 'button', '查看'); b.type = 'button'; b.dataset.newsTarget = x.target_id; b.addEventListener('click', () => { target = x.target_id; day = null; render(last); });
      return [x.target, bar(x.articles), three(x.articles), x.articles.n, x.selected, x.irrelevant, x.insufficient, x.failed, x.pending, b]; })));
    tc.append(h('p', 'fine-print', '分母只含已判断且相关的文章。同一篇文章对不同标的各判断一次。新闻与 X 帖子各用自己的分母，不相加。'));
    out.push(tc);
    const cur = d.targets.find(x => x.target_id === target) || d.targets.find(x => x.judged) || d.targets[0]; target = cur.target_id;
    const vc = h('div', 'card'); vc.append(h('h3', null, `${cur.target}：主要观点（代表文章）`));
    for (const k of ['bullish', 'bearish', 'neutral']) {
      const list = (cur.representative || {})[k] || []; const box = h('div', 'xs-view'); box.append(h('p', 'xs-view-title', OUT[k] + '（' + (list.length ? '最近文章数 ' + list.length : '没有') + '）'));
      list.forEach(x => { const r = h('div', 'fine-print'); r.append(link(x.url, x.title), h('span', null, ' — '), quote(x.reason), h('span', null, ' · 依据：'), quote(x.evidence), h('span', null, x.title_only ? ' · 仅有标题' : '')); box.append(r); });
      vc.append(box);
    }
    out.push(vc);
    const days = d.days.filter(x => x.target_id === cur.target_id);
    const dc = h('div', 'card'); dc.append(h('h3', null, `${cur.target}：按发布日期（纽约日期）`));
    dc.append(table(['日期', '占比', '看多 / 看空 / 中性（占比）', '分母（文章）', '无关', '原文不足', '判断失败', '材料'], days.map(x => {
      const b = h('button', 'button', day === x.ny_date ? '收起' : '查看材料'); b.type = 'button'; b.dataset.newsDay = x.ny_date; b.addEventListener('click', () => { day = day === x.ny_date ? null : x.ny_date; render(last); });
      return [x.ny_date, bar(x.articles), three(x.articles), x.articles.n, x.irrelevant, x.insufficient, x.failed, b]; })));
    dc.append(h('p', 'fine-print', '只列出有已判断文章的日期。日期按文章的发布时间归到纽约日期；判断是事后做的。'));
    out.push(dc);
    const items = d.items.filter(x => x.target_id === cur.target_id && (!day || x.ny_date === day));
    const ic = h('div', 'card'); ic.append(h('h3', null, day ? `${cur.target} · ${day} 的全部文章` : `${cur.target}：逐篇结果`), row('显示的条数', items.length));
    ic.append(table(['判断', '英文标题（原文链接）', '简短理由', '依据原文', '备注', '发布时间', '采集时间', '模型完成时间'], items.map(x =>
      [OUT[x.outcome] || x.outcome, link(x.url, x.title), quote(x.reason), quote(x.evidence),
        [x.title_only ? '仅有标题' : '', x.body_truncated ? '正文只读了前一部分' : '', x.found_by === 'SUPPLIER_TAG_ONLY' ? '由供应商标签找到，正文未点名' : '', x.status !== 'OK' && x.status !== 'IRRELEVANT' && x.status !== 'INSUFFICIENT_TEXT' ? x.status : ''].filter(Boolean).join('；') || '—',
        data(fmtT(x.published_at)), data(fmtT(x.first_received_at)), data(fmtT(x.model_completed_at))])));
    out.push(ic);
    panel.replaceChildren(...out);
  }

  const load = () => fetch(root.dataset.newsUrl, {credentials: 'same-origin', cache: 'no-store'}).then(r => { if (!r.ok) throw new Error(String(r.status)); return r.json(); }).then(render)
    .catch(() => { panel.replaceChildren(h('p', 'muted', '读取失败')); });
  const tab = root.querySelector('[data-xtab="news"]');
  if (tab) tab.addEventListener('click', load);
  load();
})();
