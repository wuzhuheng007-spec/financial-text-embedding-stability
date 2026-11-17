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
        ? `Previous v4.1 worker (retired): ${worker.state || 'unknown'}; last check: ${worker.last_checked_at_utc || 'not available'}. Current v4.2 state follows.`
        : `旧 v4.1 队列（已停用）：${worker.state || '未知'}；上次检查：${worker.last_checked_at_utc || '无'}。当前 v4.2 状态见下方。`);
      const recovery = chinese.v42 || {};
      const reliable = recovery.worker || {};
      line(en()
        ? `v4.2 recovery: ${reliable.state || 'not started'}; heartbeat ${reliable.heartbeat_at_utc || 'none'}; last score ${reliable.last_success_at_utc || 'none'}; backlog ${reliable.backlog ?? 'unknown'}; daily attempts ${reliable.daily_attempts ?? 'unknown'} / ${reliable.daily_limit ?? 500}.`
        : `v4.2 恢复队列：${reliable.state || '未启动'}；心跳 ${reliable.heartbeat_at_utc || '无'}；最近评分 ${reliable.last_success_at_utc || '无'}；积压 ${reliable.backlog ?? '未知'}；当日尝试 ${reliable.daily_attempts ?? '未知'} / ${reliable.daily_limit ?? 500}。`);
      line(en()
        ? `Company candidates ${recovery.company_candidate_count ?? 0}; Jin10 continuity ${recovery.coverage_gap || 'unknown'}; snapshot ${reliable.snapshot_state || 'pending'}; next cutoff ${reliable.next_cutoff_utc || 'not scheduled'}.`
        : `公司候选 ${recovery.company_candidate_count ?? 0}；金十覆盖 ${recovery.coverage_gap || '未知'}；快照 ${reliable.snapshot_state || '待观察'}；下次截止 ${reliable.next_cutoff_utc || '未安排'}。`);
      if (reliable.pause_reason) line(en() ? `Pause reason: ${reliable.pause_reason}` : `暂停原因：${reliable.pause_reason}`);
      if (recovery.latest_snapshot) {
        const snap = recovery.latest_snapshot;
        line(en()
          ? `Legacy v4.2 cumulative ${snap.kind}: cutoff ${snap.as_of_utc}; generated ${snap.generated_at_utc}. Kept as a separate development definition.`
          : `旧版 v4.2 累计口径 ${snap.kind}：截止 ${snap.as_of_utc}；生成 ${snap.generated_at_utc}。与日度信号分开保存。`);
      }
      const daily = chinese.v43 || {};
      const dailyHead = document.createElement('h4');
      dailyHead.textContent = en() ? 'Daily new-event tone · v4.3' : '日度新增事件语气 · v4.3';
      content.appendChild(dailyHead);
      line(en()
        ? `Status: ${daily.state || 'pending'}; worker rule ${reliable.daily_snapshot_rule_version || 'not yet loaded'}; next cutoff ${reliable.daily_next_cutoff_utc || 'not scheduled'}.`
        : `状态：${daily.state || '待首次快照'}；运行规则 ${reliable.daily_snapshot_rule_version || '尚未加载'}；下次截止 ${reliable.daily_next_cutoff_utc || '未安排'}。`);
      const signal = daily.daily;
      if (signal) {
        const observed = (signal.companies || []).filter(x => x.n2_observed > 0);
        line(en()
          ? `Published ${signal.kind}: window (${signal.window_start_utc}, ${signal.scheduled_cutoff_utc}]; completed ${signal.completion_recorded_at_utc}; safely available ${signal.signal_available_at_utc}. ${observed.length} companies have newly observed groups. Coverage: ${signal.coverage?.initial_window || 'unknown'} / ${signal.coverage?.coverage || 'unknown'}. Labels: ${JSON.stringify(signal.label_status_counts || {})}.`
          : `已发布 ${signal.kind}：窗口（${signal.window_start_utc}，${signal.scheduled_cutoff_utc}]；完成 ${signal.completion_recorded_at_utc}；可安全使用 ${signal.signal_available_at_utc}。${observed.length} 家公司有新增观察事件组。覆盖：${signal.coverage?.initial_window || '未知'} / ${signal.coverage?.coverage || '未知'}。标签：${JSON.stringify(signal.label_status_counts || {})}。`);
        if (observed.length) {
          const ul = document.createElement('ul');
          ul.className = 'research-score-list';
          observed.forEach(x => {
            const li = document.createElement('li');
            const tone = x.n1 === null ? (en() ? 'missing' : '缺失') : Number(x.n1).toFixed(3);
            li.textContent = `${x.entity_id} · N1 ${tone} · N2 ${x.n2_observed} (${en() ? 'observed only' : '仅观察计数'}) · ${en() ? 'updates' : '修订'} ${x.update_count}`;
            ul.appendChild(li);
          });
          content.appendChild(ul);
        } else line(en() ? 'No new directly linked company group in this daily window; N1 is missing.' : '本日度窗口没有新增明确公司关联事件组；N1 为缺失。');
      } else {
        line(en() ? 'First real daily snapshot awaits the verified 16:15 New York cutoff. No replay is labeled prospective.'
                  : '首次真实日度快照等待纽约时间 16:15 的已核验截止；开发回放不标为前瞻。');
      }
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
