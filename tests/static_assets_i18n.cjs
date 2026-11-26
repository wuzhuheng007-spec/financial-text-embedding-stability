// Release hygiene for the portal's static files:
// - every template that loads the same static file asks for the same ?v= version, so one page cannot keep an old copy;
// - the translation catalog has no duplicate keys (a later duplicate silently wins in a JS object literal).
const test = require('node:test'), assert = require('node:assert'), fs = require('fs'), path = require('path');
const root = path.join(__dirname, '..', 'apps', 'quant_portal');

function files(dir, ext, out = []) {
  for (const e of fs.readdirSync(dir, {withFileTypes: true})) {
    const p = path.join(dir, e.name);
    if (e.isDirectory()) files(p, ext, out); else if (p.endsWith(ext)) out.push(p);
  }
  return out;
}

test('a static file is loaded with one version across all templates', () => {
  const seen = {};
  for (const f of files(path.join(root, 'templates'), '.html')) {
    for (const m of fs.readFileSync(f, 'utf8').matchAll(/\{%\s*static\s+'([^']+)'\s*%\}\?v=([^"'\s>]+)/g)) {
      (seen[m[1]] = seen[m[1]] || new Set()).add(m[2]);
    }
  }
  const mixed = Object.entries(seen).filter(([, v]) => v.size > 1).map(([k, v]) => `${k}: ${[...v].join(', ')}`);
  assert.ok(Object.keys(seen).length > 5, 'no versioned static files found');
  assert.deepStrictEqual(mixed, []);
});

test('the translation catalog has no duplicate keys', () => {
  const src = fs.readFileSync(path.join(root, 'static', 'core', 'i18n_v15.js'), 'utf8');
  const a = src.indexOf('const catalog = {'), b = src.indexOf('\n  };', a);
  assert.ok(a > 0 && b > a, 'catalog not found');
  const keys = [...src.slice(a, b).matchAll(/^\s*("(?:[^"\\]|\\.)*")\s*:/gm)].map(m => JSON.parse(m[1]));
  const count = {}; keys.forEach(k => { count[k] = (count[k] || 0) + 1; });
  assert.ok(keys.length > 500, `only ${keys.length} keys parsed`);
  assert.deepStrictEqual(Object.entries(count).filter(([, n]) => n > 1).map(([k]) => k), []);
});
