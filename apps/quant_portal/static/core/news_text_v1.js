/* Decode a title once and render with textContent. No HTML insertion or network. */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.quantNewsText = api;
})(typeof window === 'object' ? window : {}, function () {
  'use strict';
  const named = {amp: '&', quot: '"', apos: "'", lt: '<', gt: '>', nbsp: '\u00a0', ndash: '–', mdash: '—', lsquo: '‘', rsquo: '’', ldquo: '“', rdquo: '”', hellip: '…'};
  function decodeEntities(value) {
    return String(value == null ? '' : value).replace(/&(#x[0-9a-f]+|#\d+|[a-z]+);/gi, (whole, entity) => {
      if (Object.prototype.hasOwnProperty.call(named, entity)) return named[entity];
      if (entity[0] !== '#') return whole;
      const hex = entity[1].toLowerCase() === 'x';
      const n = Number.parseInt(entity.slice(hex ? 2 : 1), hex ? 16 : 10);
      if (!Number.isInteger(n) || n < 1 || n > 0x10ffff || (n >= 0xd800 && n <= 0xdfff)) return '\ufffd';
      return String.fromCodePoint(n);
    });
  }
  function renderTitle(el, raw) { el.textContent = decodeEntities(raw || '(Untitled)'); }
  return {decodeEntities, renderTitle};
});
