/* Owner-scoped browser layout preference; supplier and model calls are elsewhere. */
(() => {
  const user=document.documentElement.dataset.userId;
  if (!user || user==='0') return;
  const key=`quant-personal-panels-v15-user-${user}`;
  let saved={};
  try { saved=JSON.parse(localStorage.getItem(key)||'{}'); } catch (_) { saved={}; }
  document.querySelectorAll('details[data-panel]').forEach(panel => {
    if (Object.hasOwn(saved,panel.dataset.panel)) panel.open=Boolean(saved[panel.dataset.panel]);
    const summary=panel.querySelector(':scope > summary');
    if (summary) summary.setAttribute('aria-expanded',String(panel.open));
    panel.addEventListener('toggle',() => {
      if (summary) summary.setAttribute('aria-expanded',String(panel.open));
      saved[panel.dataset.panel]=panel.open;
      localStorage.setItem(key,JSON.stringify(saved));
    });
  });
  window.quantOpenPanel=(name) => {
    const panel=document.querySelector(`details[data-panel="${name}"]`);
    if (panel) panel.open=true;
    return panel;
  };
})();
