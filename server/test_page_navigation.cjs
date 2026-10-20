'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const read = name => fs.readFileSync(path.join(__dirname, '..', name), 'utf8');

for (const [file, current] of [['index.html', 'Overview'], ['history.html', 'History']]) {
  const html = read(file);
  const nav = html.match(/<nav class="page-nav"[^>]*>([\s\S]*?)<\/nav>/)[1];
  const links = [...nav.matchAll(/<a ([^>]+)>([\s\S]*?)<\/a>/g)];
  assert.equal(links.length, 2);
  for (const [index, [, attributes, body]] of links.entries()) {
    const label = index === 0 ? 'Overview' : 'History';
    assert.match(attributes, new RegExp(`href="${index === 0 ? '/' : 'history.html'}"`));
    assert.equal(attributes.includes('aria-current="page"'), label === current);
    assert.doesNotMatch(attributes, /data-i18n/, 'Translation must not remove the icon');
    assert.ok(body.includes(`<span data-i18n="${label}">${label}</span>`));
    assert.match(body, /<svg class="page-nav-icon"[^>]*aria-hidden="true"[^>]*focusable="false"/);
  }
  assert.match(html, /interface\.css\?v=20261020-navigation/);
}
const css = read('interface.css');
assert.match(css, /html \.dashboard \.page-nav a \{[^}]*min-height: 48px/);
assert.match(css, /html \.dashboard \.page-nav a:not\(\[aria-current\]\):hover \{ background: transparent;/);
assert.match(css, /html \.dashboard \.page-nav a\[aria-current=page\] \{ background: transparent;/);
assert.match(css, /html \.dashboard \.page-nav a:focus-visible \{ outline: 2px solid var\(--theme-focus\)/);
assert.match(css, /@media \(prefers-reduced-motion: reduce\) \{\s*html \.dashboard \.page-nav a, \.page-nav a::after \{ transition: none;/);
assert.match(css, /html \.dashboard \.page-nav a \{ flex: 1; padding: 12px; font-size: 14px;/);
console.log('Page navigation: native links, translated labels, decorative icons, hover/focus and reduced motion passed.');
