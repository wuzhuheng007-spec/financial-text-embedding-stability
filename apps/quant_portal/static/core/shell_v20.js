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

  // Sidebar: no saved choice = automatic (full on wide screens, icon rail between 761 and 1100 CSS px).
  // A click saves an explicit "open" or "collapsed" that then wins at every width (phones use the tab bar).
  const side = document.getElementById('side-toggle');
  const railAuto = window.matchMedia('(min-width: 761px) and (max-width: 1100px)');
  const isCollapsed = () => d.dataset.side === 'collapsed' || (d.dataset.side !== 'open' && railAuto.matches);
  const syncSide = () => { const c = isCollapsed(); side?.setAttribute('aria-pressed', String(c));
    side?.setAttribute('aria-label', c ? '展开侧边栏' : '折叠侧边栏'); const l = side?.querySelector('.nav-label'); if (l) l.textContent = c ? '展开' : '折叠'; };
  syncSide(); railAuto.addEventListener?.('change', syncSide);
  side?.addEventListener('click', () => {
    const next = isCollapsed() ? 'open' : 'collapsed';
    d.dataset.side = next; store.set('quant.side', next); syncSide();
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
