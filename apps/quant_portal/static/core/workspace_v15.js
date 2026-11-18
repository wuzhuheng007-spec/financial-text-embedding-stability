/* View/panel preferences only. No supplier or model requests. */
(() => {
  const user=document.documentElement.dataset.userId;
  if (!user || user==='0') return;
  const key=`quant-personal-panels-v19-user-${user}`;
  const nav=document.querySelector('.workspace-nav');
  const groups={overview:['top10','quotes','chart'],news:['official','news','sentiment'],
    research:['research-sentiment','facts'],manage:['watchlist','advanced','diagnostics']};
  let saved={};
  try { saved=JSON.parse(localStorage.getItem(key)||'{}')||{}; } catch (_) { saved={}; }
  const persist=()=>{try {localStorage.setItem(key,JSON.stringify(saved));} catch (_) {/* private mode */}};
  const panels=[...document.querySelectorAll('details[data-panel]')];
  const showView=(view) => {
    if (!nav || !groups[view]) return;
    panels.forEach(panel=>{panel.hidden=!groups[view].includes(panel.dataset.panel);});
    nav.querySelectorAll('[data-view]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.view===view)));
    saved.view=view;persist();
  };
  panels.forEach(panel => {
    if (Object.hasOwn(saved,panel.dataset.panel)) panel.open=Boolean(saved[panel.dataset.panel]);
    else panel.open=['top10','quotes','official','news','research-sentiment','watchlist'].includes(panel.dataset.panel);
    const summary=panel.querySelector(':scope > summary');
    if (summary) summary.setAttribute('aria-expanded',String(panel.open));
    panel.addEventListener('toggle',() => {
      if (summary) summary.setAttribute('aria-expanded',String(panel.open));
      saved[panel.dataset.panel]=panel.open;persist();
    });
  });
  if (nav) {
    nav.hidden=false;
    nav.addEventListener('click',event=>{const button=event.target.closest('[data-view]');if(button) showView(button.dataset.view);});
    showView(groups[saved.view]?saved.view:'overview');
  }
  window.quantOpenPanel=(name) => {
    const panel=panels.find(p=>p.dataset.panel===name);
    if (panel) {
      const view=Object.keys(groups).find(v=>groups[v].includes(name));
      if(view) showView(view);
      panel.open=true;
    }
    return panel;
  };
})();
