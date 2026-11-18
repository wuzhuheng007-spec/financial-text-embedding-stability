/* Shell: theme, sidebar, account menu, symbol search, nav state. Display preferences only. */
(() => {
  const d = document.documentElement;
  const store = {
    get: k => { try { return localStorage.getItem(k); } catch (_) { return null; } },
    set: (k, v) => { try { localStorage.setItem(k, v); } catch (_) { /* storage optional */ } },
  };
  const theme = document.getElementById('theme-toggle');
  const metaTheme = document.querySelector('meta[name="theme-color"]');
  const applyTheme = t => { d.dataset.theme = t; if (metaTheme) metaTheme.content = t === 'light' ? '#f3f6fa' : '#080d16';
    theme?.setAttribute('aria-pressed', String(t === 'light')); document.dispatchEvent(new CustomEvent('quant-theme')); };
  applyTheme(d.dataset.theme === 'light' ? 'light' : 'dark');
  theme?.addEventListener('click', () => { const t = d.dataset.theme === 'light' ? 'dark' : 'light'; store.set('quant.theme', t); applyTheme(t); });

  const side = document.getElementById('side-toggle');
  side?.setAttribute('aria-pressed', String(d.dataset.side === 'collapsed'));
  side?.addEventListener('click', () => {
    const collapsed = d.dataset.side !== 'collapsed';
    if (collapsed) d.dataset.side = 'collapsed'; else delete d.dataset.side;
    store.set('quant.side', collapsed ? 'collapsed' : 'open');
    side.setAttribute('aria-pressed', String(collapsed));
    document.dispatchEvent(new CustomEvent('quant-resize'));
  });

  const accBtn = document.getElementById('account-toggle');
  const accMenu = document.getElementById('account-menu');
  const closeAcc = () => { if (accMenu) accMenu.hidden = true; accBtn?.setAttribute('aria-expanded', 'false'); };
  accBtn?.addEventListener('click', e => { e.stopPropagation(); const open = accMenu.hidden; accMenu.hidden = !open; accBtn.setAttribute('aria-expanded', String(open)); });
  document.addEventListener('click', e => { if (accMenu && !accMenu.hidden && !accMenu.contains(e.target)) closeAcc(); });
  document.addEventListener('keydown', e => { if (e.key === 'Escape') closeAcc(); });

  // Symbol search: options come from the watchlist (filled by the dashboard), else static list.
  const search = document.getElementById('symbol-search');
  const options = document.getElementById('symbol-options');
  window.quantSetSymbols = list => {
    if (!options) return;
    options.replaceChildren(...list.map(s => { const o = document.createElement('option'); o.value = s.symbol; if (s.name) o.label = s.name; return o; }));
  };
  const go = () => {
    const sym = (search.value || '').trim().toUpperCase();
    if (!sym) return;
    const known = [...(options?.options || [])].map(o => o.value);
    const match = known.find(s => s === sym) || known.find(s => s.startsWith(sym));
    if (!match) { search.setCustomValidity('自选中没有该标的'); search.reportValidity(); return; }
    search.setCustomValidity(''); search.value = ''; search.blur();
    const home = search.dataset.home || '/';
    if (location.pathname === home) location.hash = `#markets/${match}`;
    else location.href = `${home}#markets/${match}`;
  };
  search?.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); go(); } });
  search?.addEventListener('change', () => { if ([...(options?.options || [])].some(o => o.value === search.value.trim().toUpperCase())) go(); });
  search?.addEventListener('input', () => search.setCustomValidity(''));
  document.addEventListener('keydown', e => {
    if (e.key === '/' && !/INPUT|TEXTAREA|SELECT/.test(document.activeElement?.tagName || '')) { e.preventDefault(); search?.focus(); }
  });

  // Highlight the current nav entry (dashboard views use the hash).
  window.quantMarkNav = view => {
    document.querySelectorAll('[data-nav]').forEach(a => {
      if (a.dataset.nav === view) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current');
    });
  };
})();
