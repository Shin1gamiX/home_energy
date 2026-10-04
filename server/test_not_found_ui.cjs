// No browser, network, telemetry or device access is needed for these checks.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.join(__dirname, '..');
const html = fs.readFileSync(path.join(root, '404.html'), 'utf8');
const source = fs.readFileSync(path.join(root, '404.js'), 'utf8');
const css = fs.readFileSync(path.join(root, '404.css'), 'utf8');

class Element {
  constructor(key, text = '') {
    this.dataset = { notFoundText: key };
    this.textContent = text;
    this.value = '';
    this.hidden = true;
    this.attributes = {};
    this.listeners = {};
  }

  set innerHTML(_) { assert.fail('Localization must use textContent, never HTML'); }
  setAttribute(name, value) { this.attributes[name] = value; }
  addEventListener(name, listener) { this.listeners[name] = listener; }
}

function fixture({ saved = null, blockedRead = false, blockedWrite = false, blockedStorage = false } = {}) {
  const texts = Object.fromEntries([...html.matchAll(/data-not-found-text="([^"]+)"[^>]*>([^<]*)</g)]
    .map(([, key, text]) => [key, new Element(key, text)]));
  texts.unexpected = new Element('constructor', 'Keep this text');
  texts.untrusted = new Element('<img src=x onerror=alert(1)>', 'Keep this too');
  const nodes = {
    '#not-found-language': new Element(),
    '#not-found-language-control': new Element(),
    '#not-found-brand': new Element()
  };
  const writes = [];
  const reads = [];
  const document = {
    documentElement: { lang: 'en' },
    title: html.match(/<title>([^<]+)<\/title>/)[1],
    querySelector(selector) {
      assert.ok(Object.hasOwn(nodes, selector), `Unexpected DOM query: ${selector}`);
      return nodes[selector];
    },
    querySelectorAll(selector) {
      assert.equal(selector, '[data-not-found-text]');
      return Object.values(texts);
    }
  };
  const storage = {
    getItem(key) {
      reads.push(key);
      if (blockedRead) throw new Error('Storage read blocked');
      return saved;
    },
    setItem(key, value) {
      if (blockedWrite) throw new Error('Storage write blocked');
      writes.push([key, value]);
    }
  };
  const context = { document };
  Object.defineProperty(context, 'localStorage', {
    get() {
      if (blockedStorage) throw new Error('Storage access blocked');
      return storage;
    }
  });
  for (const name of ['fetch', 'XMLHttpRequest', 'WebSocket', 'EventSource', 'setTimeout', 'setInterval', 'location', 'navigator']) {
    Object.defineProperty(context, name, { get() { assert.fail(`404 page must not access ${name}`); } });
  }
  vm.runInNewContext(source, context);
  return {
    document, nodes, texts, reads, writes,
    change(value) {
      nodes['#not-found-language'].value = value;
      nodes['#not-found-language'].listeners.change();
    }
  };
}

const expected = {
  en: ['Page not found.', 'Back to overview', 'View history', 'Language', 'Home energy overview'],
  ru: ['Страница не найдена.', 'Вернуться к обзору', 'Посмотреть историю', 'Язык', 'Обзор энергии дома'],
  el: ['Η σελίδα δεν βρέθηκε.', 'Επιστροφή στην επισκόπηση', 'Προβολή ιστορικού', 'Γλώσσα', 'Επισκόπηση ενέργειας σπιτιού']
};

function assertLanguage(f, language) {
  const [heading, overview, history, label, brandLabel] = expected[language];
  assert.equal(f.document.documentElement.lang, language);
  assert.equal(f.nodes['#not-found-language'].value, language);
  assert.equal(f.texts.heading.textContent, heading);
  assert.equal(f.texts.overview.textContent, overview);
  assert.equal(f.texts.history.textContent, history);
  assert.equal(f.texts.language.textContent, label);
  assert.equal(f.nodes['#not-found-brand'].attributes['aria-label'], brandLabel);
  assert.ok(f.document.title.startsWith(heading.slice(0, -1)));
  assert.equal(f.nodes['#not-found-language-control'].hidden, false);
  assert.equal(f.texts.unexpected.textContent, 'Keep this text');
  assert.equal(f.texts.untrusted.textContent, 'Keep this too');
}

for (const language of Object.keys(expected)) {
  const f = fixture({ saved: language });
  assertLanguage(f, language);
  assert.deepEqual(f.reads, ['homeenergy-language']);
  assert.deepEqual(f.writes, [], 'Loading the error page must not rewrite preferences');
}
for (const saved of [null, '', 'de', 'EN', '__proto__', 'constructor', '<script>alert(1)</script>']) {
  assertLanguage(fixture({ saved }), 'en');
}

const switching = fixture();
for (const language of ['ru', 'el', 'en']) {
  switching.change(language);
  assertLanguage(switching, language);
}
assert.deepEqual(switching.writes, [['homeenergy-language', 'ru'], ['homeenergy-language', 'el'], ['homeenergy-language', 'en']]);
for (const value of ['__proto__', 'constructor', '', '<img src=x onerror=alert(1)>']) switching.change(value);
assertLanguage(switching, 'en');
assert.equal(switching.writes.length, 3, 'Unexpected language values must not be stored');

for (const options of [{ blockedRead: true }, { blockedWrite: true }, { blockedStorage: true }]) {
  const f = fixture(options);
  assertLanguage(f, 'en');
  f.change('el');
  assertLanguage(f, 'el');
}
console.log('404 localization, allowlist, safe-text and blocked-storage checks passed.');

assert.match(html, /<html lang="en">/);
assert.match(html, /<h1[^>]+>Page not found\.<\/h1>/);
assert.match(html, /data-not-found-text="description">This page may have moved, or the address may be incorrect\.<\/p>/);
const anchors = [...html.matchAll(/<a\b([^>]*)>([\s\S]*?)<\/a>/g)];
assert.equal(anchors.length, 3);
assert.deepEqual(anchors.map(([, attrs]) => attrs.match(/href="([^"]+)"/)[1]), ['/', '/', '/history.html']);
assert.match(anchors[1][2], /Back to overview/);
assert.match(anchors[2][2], /View history/);
assert.doesNotMatch(html, /\son\w+\s*=|javascript:|<base\b|<iframe\b|<form\b/i);
assert.doesNotMatch(source, /innerHTML|outerHTML|insertAdjacentHTML|\/api\/|collector|app\.js/);
const scripts = [...html.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script>/g)];
assert.equal(scripts.length, 1);
assert.match(scripts[0][1], /src="\/404\.js\?v=[\d-]+"/);
assert.match(scripts[0][1], /\bdefer\b/);
assert.equal(scripts[0][2].trim(), '', 'Scripts must remain external for CSP');
const resourceLinks = [...html.matchAll(/<link\b[^>]+>/g)].map(([tag]) => tag);
assert.equal(resourceLinks.length, 2);
assert.ok(resourceLinks.some(tag => /rel="stylesheet" href="\/404\.css\?v=[\d-]+"/.test(tag)));
assert.ok(resourceLinks.some(tag => /rel="icon"/.test(tag) && /href="data:image\/svg\+xml,/.test(tag)));
for (const [svg] of html.matchAll(/<svg\b[^>]*>/g)) {
  assert.match(svg, /aria-hidden="true"/);
  assert.match(svg, /focusable="false"/);
}
assert.doesNotMatch(html, /\stabindex\s*=/i, 'Decorative assets must not add keyboard stops');
assert.match(html, /<label[^>]+id="not-found-language-control"[^>]+hidden>[\s\S]*?<span[^>]+data-not-found-text="language">Language<\/span>[\s\S]*?<select id="not-found-language">/);
assert.match(css, /\.not-found a:focus-visible/);
assert.match(css, /\.not-found select:focus-visible/);
assert.doesNotMatch(css, /@import|url\s*\(/i, 'The error page must not request external assets');
console.log('404 no-JavaScript navigation, absolute assets, CSP and decorative-SVG checks passed.');
