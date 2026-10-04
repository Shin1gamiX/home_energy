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
    '#not-found-language-name': new Element(),
    '#not-found-language-menu': new Element(),
    '#not-found-language-control': new Element(),
    '#not-found-brand': new Element()
  };
  const writes = [];
  const reads = [];
  const options = ['en', 'ru', 'el'].map(language => {
    const option = new Element();
    option.dataset.language = language;
    return option;
  });
  const events = {};
  const document = {
    activeElement: null,
    documentElement: { lang: 'en' },
    title: html.match(/<title>([^<]+)<\/title>/)[1],
    querySelector(selector) {
      assert.ok(Object.hasOwn(nodes, selector), `Unexpected DOM query: ${selector}`);
      return nodes[selector];
    },
    addEventListener(name, listener) { events[name] = listener; },
    querySelectorAll(selector) {
      assert.equal(selector, '[data-not-found-text]');
      return Object.values(texts);
    }
  };
  for (const element of [...Object.values(nodes), ...options]) {
    element.focus = () => { document.activeElement = element; };
  }
  nodes['#not-found-language-control'].contains = element => [...Object.values(nodes), ...options].includes(element);
  nodes['#not-found-language-menu'].querySelectorAll = selector => {
    assert.equal(selector, '[data-language]');
    return options;
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
    document, nodes, texts, reads, writes, options, events,
    change(value) {
      const option = options.find(item => item.dataset.language === value) || options[0];
      const original = option.dataset.language;
      option.dataset.language = value;
      option.listeners.click();
      option.dataset.language = original;
    },
    key(selector, key, extra = {}) {
      const event = { key, prevented: false, preventDefault() { this.prevented = true; }, ...extra };
      nodes[selector].listeners.keydown(event);
      return event;
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
  assert.equal(f.nodes['#not-found-language-name'].textContent, { en: 'English', ru: 'Русский', el: 'Ελληνικά' }[language]);
  assert.equal(f.nodes['#not-found-language-name'].attributes.lang, language);
  assert.deepEqual(f.options.map(option => option.attributes['aria-checked']), f.options.map(option => String(option.dataset.language === language)));
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

const keyboard = fixture();
const trigger = keyboard.nodes['#not-found-language'];
const menu = keyboard.nodes['#not-found-language-menu'];
const control = keyboard.nodes['#not-found-language-control'];
function assertClosed() {
  assert.equal(menu.hidden, true);
  assert.equal(trigger.attributes['aria-expanded'], 'false');
}
trigger.listeners.click();
assert.equal(menu.hidden, false);
assert.equal(trigger.attributes['aria-expanded'], 'true');
assert.equal(keyboard.document.activeElement, keyboard.options[0]);
for (const [key, index] of [['ArrowUp', 2], ['ArrowDown', 0], ['End', 2], ['Home', 0], ['r', 1], ['ε', 2]]) {
  assert.equal(keyboard.key('#not-found-language-menu', key).prevented, true);
  assert.equal(keyboard.document.activeElement, keyboard.options[index]);
}
assert.deepEqual(keyboard.writes, [], 'Moving focus must not change the selected language');
keyboard.key('#not-found-language-menu', 'Escape');
assertClosed();
assert.equal(keyboard.document.activeElement, trigger);
keyboard.key('#not-found-language', 'ArrowUp');
assert.equal(keyboard.document.activeElement, keyboard.options[2]);
assert.equal(keyboard.key('#not-found-language-menu', 'Tab').prevented, false);
assertClosed();
assert.equal(keyboard.document.activeElement, trigger);
keyboard.key('#not-found-language', 'ArrowDown');
keyboard.change('ru');
assertLanguage(keyboard, 'ru');
assertClosed();
assert.equal(keyboard.document.activeElement, trigger);
trigger.listeners.click();
keyboard.events.pointerdown({ target: keyboard.options[1] });
assert.equal(menu.hidden, false, 'Clicking inside must not dismiss before selection');
const outside = new Element();
outside.focus = () => { keyboard.document.activeElement = outside; };
outside.focus();
keyboard.events.pointerdown({ target: outside });
assertClosed();
assert.equal(keyboard.document.activeElement, outside, 'Outside dismissal must not steal focus');
trigger.listeners.click();
control.listeners.focusout({ relatedTarget: keyboard.options[1] });
assert.equal(menu.hidden, false);
control.listeners.focusout({ relatedTarget: outside });
assertClosed();
trigger.listeners.click();
trigger.listeners.click();
assertClosed();
console.log('404 menu selection, keyboard navigation, Escape, Tab, outside-click and focus dismissal checks passed.');

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
assert.equal([...html.matchAll(/tabindex="-1"/g)].length, 3, 'Only menu options need programmatic focus');
assert.doesNotMatch(html, /<select\b|<option\b/);
assert.match(html, /id="not-found-language-control" hidden/);
assert.match(html, /aria-haspopup="menu" aria-expanded="false" aria-controls="not-found-language-menu"/);
assert.match(html, /id="not-found-language-menu" role="menu" aria-labelledby="not-found-language-label" hidden/);
assert.equal([...html.matchAll(/role="menuitemradio"/g)].length, 3);
assert.match(css, /\.not-found a:focus-visible/);
assert.match(css, /\.not-found-language-trigger:focus-visible/);
assert.match(css, /\.not-found-language-menu button:focus-visible/);
assert.match(css, /prefers-reduced-motion: reduce/);
assert.doesNotMatch(css, /@import|url\s*\(/i, 'The error page must not request external assets');
console.log('404 no-JavaScript navigation, absolute assets, CSP and decorative-SVG checks passed.');
