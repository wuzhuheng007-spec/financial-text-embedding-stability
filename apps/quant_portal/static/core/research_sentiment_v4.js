/* Owner-only local cache read. Never calls a supplier or a model. */
(() => {
  const root = document.getElementById('panel-research-sentiment');
  if (!root) return;
  const content = document.getElementById('research-sentiment-content');
  const count = document.getElementById('research-sentiment-count');
  const heading = document.getElementById('research-sentiment-heading');
  const explain = document.getElementById('research-sentiment-explain');
  const en = () => document.documentElement.dataset.language === 'en';
  let previous = '';
  function line(text) {
    const p = document.createElement('p');
    p.className = 'muted';
    p.textContent = text;
    content.appendChild(p);
  }
  function render(data) {
    heading.textContent = en() ? 'Research news tone' : '研究情绪';
    explain.textContent = en()
      ? 'Local English-headline research scores. Text tone does not verify a report or predict returns. Historical and prospective records are separate.'
      : '英文新闻标题的本机研究评分；文字语气不代表新闻属实、未来涨跌或交易建议。历史与前瞻分开记录。';
    count.textContent = en() ? `${data.completed_scores} scored` : `${data.completed_scores} 条已评分`;
    content.replaceChildren();
    if (data.state !== 'READY') {
      line(en() ? 'Research cache is unavailable.' : '研究缓存暂不可用。');
      return;
    }
    line(en()
      ? `Observed article versions: ${data.article_versions}; English title candidates: ${data.english_titles}; prospective versions: ${data.prospective_versions}; prospective scores: ${data.qualified_prospective_scores}.`
      : `已观察文章版本 ${data.article_versions}；英文标题候选 ${data.english_titles}；前瞻版本 ${data.prospective_versions}；前瞻评分 ${data.qualified_prospective_scores}。`);
    if (!data.rows.length) {
      line(en() ? 'No eligible English news has been scored. Existing Chinese supplier reports are not sent to the English model.'
                : '暂无合格英文新闻评分。现有中文供应商报道没有送入英文模型。');
      return;
    }
    const list = document.createElement('ul');
    list.className = 'research-score-list';
    data.rows.forEach(row => {
      const li = document.createElement('li');
      const value = Number(row.score);
      const score = Number.isFinite(value) ? value.toFixed(3) : (en() ? 'unknown' : '未知');
      const origin = row.score_origin === 'PROSPECTIVE_RECEIPT'
        ? (en() ? 'prospective receipt' : '前瞻收到')
        : (en() ? 'retrospective observation' : '回顾性观察');
      li.textContent = `${row.provider}${row.publisher ? ' / ' + row.publisher : ''} · ${en() ? 'tone' : '语气'} ${score} · ${row.scored_at_utc || ''} · ${origin}`;
      list.appendChild(li);
    });
    content.appendChild(list);
  }
  async function refresh() {
    try {
      const response = await fetch(root.dataset.researchUrl, {credentials: 'same-origin', cache: 'no-store'});
      if (!response.ok) throw new Error('CACHE_HTTP');
      const data = await response.json();
      const signature = JSON.stringify([data, en()]);
      if (signature !== previous) { previous = signature; render(data); }
    } catch (_) {
      render({state:'CACHE_UNAVAILABLE',completed_scores:0,rows:[]});
    }
  }
  refresh();
  window.setInterval(refresh, 5000);
})();
