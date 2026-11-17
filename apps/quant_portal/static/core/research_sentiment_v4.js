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
      ? 'Local English v4 and Chinese v4.1 research candidates. Text tone does not verify reports or predict returns; replay and new receipts remain separate.'
      : '英文 v4 与中文 v4.1 的本机研究候选。文字语气不代表报道属实或预测收益；缓存回放与新收到版本分开。';
    const chinese = data.chinese || {state:'NO_CHINESE_RESEARCH_CACHE',scored_segments:0,rows:[]};
    count.textContent = en() ? `${chinese.scored_segments} Chinese units` : `中文 ${chinese.scored_segments} 个片段`;
    content.replaceChildren();
    const subhead = document.createElement('h4');
    subhead.textContent = en() ? 'Chinese news · research candidate' : '中文新闻 · 研究候选';
    content.appendChild(subhead);
    if (chinese.state !== 'READY') {
      line(en() ? 'Chinese research cache is unavailable.' : '中文研究缓存暂不可用。');
    } else {
      const providers = Object.entries(chinese.providers || {}).map(([name, number]) => `${name} ${number}`).join(' / ');
      line(en()
        ? `Observed versions: ${chinese.article_versions} (${providers}); scored units: ${chinese.scored_segments}; new versions: ${chinese.prospective_article_versions}; new-version units: ${chinese.prospective_scored_segments}. Source coverage is incomplete.`
        : `已观察版本 ${chinese.article_versions}（${providers}）；评分片段 ${chinese.scored_segments}；新收到版本 ${chinese.prospective_article_versions}；新版本评分片段 ${chinese.prospective_scored_segments}。来源覆盖不完整。`);
      const worker = chinese.worker || {};
      line(en()
        ? `Scoring worker: ${worker.state || 'unknown'}; last check: ${worker.last_checked_at_utc || 'not available'}; daily limit: ${worker.daily_limit ?? 'unknown'} new versions.`
        : `评分进程：${worker.state || '未知'}；上次检查：${worker.last_checked_at_utc || '无'}；每日上限：${worker.daily_limit ?? '未知'} 个新版本。`);
      if (chinese.rows && chinese.rows.length) {
        const list = document.createElement('ul');
        list.className = 'research-score-list';
        chinese.rows.forEach(row => {
          const li = document.createElement('li');
          const score = typeof row.text_tone_score === 'number' ? row.text_tone_score.toFixed(3) : (en() ? 'unavailable' : '无');
          const origin = row.origin === 'PROSPECTIVE_RECEIPT'
            ? (en() ? 'new receipt' : '新收到版本') : (en() ? 'cache replay' : '缓存回放');
          const refs = (row.company_refs || []).map(x => `${x.entity_id} (${x.matched_phrase})`).join(', ');
          li.textContent = `${row.provider} · ${en() ? 'text tone' : '文字语气'} ${score} · ${origin} · ${en() ? 'scored' : '评分'} ${row.scored_at_utc || ''} · ${en() ? 'company clue' : '公司关联依据'} ${refs || (en() ? 'none' : '无')}`;
          if (row.source_url && row.source_url.startsWith('https://')) {
            const a = document.createElement('a');
            a.href = row.source_url;
            a.target = '_blank';
            a.rel = 'noopener noreferrer';
            a.textContent = en() ? 'Source link' : '来源链接';
            li.append(' · ', a);
          }
          list.appendChild(li);
        });
        content.appendChild(list);
      } else {
        line(en() ? 'No Chinese news has been scored.' : '暂无中文新闻评分。');
      }
    }
    const englishHead = document.createElement('h4');
    englishHead.textContent = en() ? 'English v4 · separate pending line' : '英文 v4 · 独立待办';
    content.appendChild(englishHead);
    if (data.state !== 'READY') {
      line(en() ? 'English research cache is unavailable.' : '英文研究缓存暂不可用。');
    } else {
      line(en()
        ? `Observed versions: ${data.article_versions}; English title candidates: ${data.english_titles}; prospective scores: ${data.qualified_prospective_scores}.`
        : `已观察版本 ${data.article_versions}；英文标题候选 ${data.english_titles}；前瞻评分 ${data.qualified_prospective_scores}。`);
      if (data.rows && data.rows.length) {
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
      } else {
        line(en() ? 'No eligible English news has been scored. Existing Chinese supplier reports are not sent to the English model.'
                  : '暂无合格英文新闻评分。现有中文供应商报道没有送入英文模型。');
      }
    }
  }
  async function refresh() {
    try {
      const response = await fetch(root.dataset.researchUrl, {credentials: 'same-origin', cache: 'no-store'});
      if (!response.ok) throw new Error('CACHE_HTTP');
      const data = await response.json();
      const signature = JSON.stringify([data, en()]);
      if (signature !== previous) { previous = signature; render(data); }
    } catch (_) {
      render({state:'CACHE_UNAVAILABLE',completed_scores:0,rows:[],chinese:{state:'CACHE_UNAVAILABLE',scored_segments:0,rows:[]}});
    }
  }
  refresh();
  window.setInterval(refresh, 5000);
})();
