// Isolate shared localization; never run polling, preview data or device controls.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.join(__dirname, '..');
const app = fs.readFileSync(path.join(root, 'app.js'), 'utf8');
const source = app.slice(app.indexOf('// Shared localization'), app.indexOf("if (document.querySelector('.energy-scene'))"));
assert.ok(source.startsWith('// Shared localization') && source.includes('return { t,'));

function fixture({ saved = null, blockedRead = false, blockedWrite = false, page = 'overview' } = {}) {
  const nodes = [];
  const writes = [];
  const events = [];
  class Element {
    constructor(tag) {
      this.tagName = tag;
      this.attributes = {};
      this.dataset = {};
      this.childNodes = [];
      this.listeners = {};
      this.hidden = false;
      nodes.push(this);
    }
    set innerHTML(_) { assert.fail('Language text must not be interpreted as HTML'); }
    setAttribute(key, value) { this.attributes[key] = value; }
    getAttribute(key) { return this.attributes[key]; }
    hasAttribute(key) { return Object.hasOwn(this.attributes, key); }
    append(...children) { this.childNodes.push(...children); }
    addEventListener(name, handler) { this.listeners[name] = handler; }
    focus() { document.activeElement = this; }
    contains(target) { return this === target || this.childNodes.some(child => child.contains?.(target)); }
  }
  const header = new Element('header');
  const heading = new Element('h1');
  heading.append({ nodeType: 3, textContent: 'Energy history' });
  const nav = new Element('nav');
  nav.setAttribute('aria-label', 'Main navigation');
  const link = new Element('a');
  link.dataset.i18n = 'Overview';
  const averaging = new Element('select');
  averaging.id = 'averaging';
  averaging.value = '1';
  const originalTitle = page === 'overview' ? 'Home · Energy' : 'Energy history · Home';
  const document = {
    documentElement: { lang: 'en' }, title: originalTitle, activeElement: null, listeners: {},
    createElement: tag => new Element(tag),
    createElementNS(namespace, tag) {
      assert.equal(namespace, 'http://www.w3.org/2000/svg');
      return new Element(tag);
    },
    querySelector(selector) {
      assert.equal(selector, 'header');
      return header;
    },
    querySelectorAll(selector) {
      if (selector === '.intro h1') return [heading];
      if (selector === '[aria-label], [title]') return [nav];
      if (selector === '[data-i18n]') return [link];
      return [];
    },
    addEventListener(name, handler) { this.listeners[name] = handler; }
  };
  const context = {
    document, Node: { TEXT_NODE: 3 }, Event: class { constructor(type) { this.type = type; } },
    window: { dispatchEvent(event) { events.push(event.type); } },
    localStorage: {
      getItem(key) {
        assert.equal(key, 'homeenergy-language');
        if (blockedRead) throw new Error('Storage is unavailable');
        return saved;
      },
      setItem(key, value) {
        if (blockedWrite) throw new Error('Storage is unavailable');
        writes.push([key, value]);
      }
    }
  };
  for (const name of ['fetch', 'XMLHttpRequest', 'WebSocket', 'setTimeout', 'setInterval']) {
    Object.defineProperty(context, name, { get() { assert.fail(`Picker must not access ${name}`); } });
  }
  vm.runInNewContext(source, context);
  const trigger = nodes.find(node => node.id === 'language');
  const menu = nodes.find(node => node.id === 'language-menu');
  const control = nodes.find(node => node.className === 'language-control');
  const options = nodes.filter(node => node.dataset.language);
  assert.equal(header.childNodes.length, 1);
  assert.equal(trigger.tagName, 'button');
  assert.equal(trigger.type, 'button');
  assert.equal(trigger.attributes['aria-haspopup'], 'menu');
  assert.equal(trigger.attributes['aria-controls'], menu.id);
  assert.equal(menu.attributes.role, 'menu');
  assert.equal(averaging.value, '1');
  assert.equal(nodes.filter(node => node.tagName === 'select').length, 1, 'Only the existing averaging select remains');
  assert.deepEqual(options.map(node => node.attributes.role), Array(3).fill('menuitemradio'));
  assert.deepEqual(options.map(node => node.tabIndex), [-1, -1, -1]);
  return {
    document, trigger, menu, control, options, writes, events, heading, link, nav,
    i18n: context.window.energyI18n,
    select(language) { options.find(option => option.dataset.language === language).listeners.click(); },
    key(element, key, extra = {}) {
      const event = { key, prevented: false, preventDefault() { this.prevented = true; }, ...extra };
      element.listeners.keydown(event);
      return event;
    }
  };
}

const expected = {
  en: ['English', 'Language', 'Energy history', 'Overview', 'Main navigation', 'en-GB'],
  ru: ['Русский', 'Язык', 'История энергии', 'Обзор', 'Основная навигация', 'ru-RU'],
  el: ['Ελληνικά', 'Γλώσσα', 'Ιστορικό ενέργειας', 'Επισκόπηση', 'Κύρια πλοήγηση', 'el-GR']
};
function assertLanguage(f, language) {
  const [name, label, heading, overview, nav, locale] = expected[language];
  assert.equal(f.document.documentElement.lang, language);
  assert.equal(f.trigger.childNodes[1].textContent, name);
  assert.equal(f.trigger.childNodes[1].attributes.lang, language);
  assert.equal(f.trigger.attributes['aria-label'], `${label}: ${name}`);
  assert.equal(f.menu.attributes['aria-label'], label);
  assert.equal(f.heading.childNodes[0].textContent, heading);
  assert.equal(f.link.textContent, overview);
  assert.equal(f.nav.attributes['aria-label'], nav);
  assert.equal(f.i18n.locale, locale);
  assert.deepEqual(f.options.map(option => option.attributes['aria-checked']), f.options.map(option => String(option.dataset.language === language)));
}

for (const page of ['overview', 'history']) {
  for (const saved of ['en', 'ru', 'el']) {
    const f = fixture({ page, saved });
    assertLanguage(f, saved);
    const title = page === 'overview' ? 'Home · Energy' : 'Energy history · Home';
    assert.equal(f.document.title, f.i18n.t(title));
    assert.deepEqual(f.writes, []);
    assert.deepEqual(f.events, []);
  }
}
for (const saved of [null, '', 'de', '__proto__', 'constructor', '<script>alert(1)</script>']) assertLanguage(fixture({ saved }), 'en');
for (const options of [{}, { blockedRead: true }, { blockedWrite: true }, { blockedRead: true, blockedWrite: true }]) {
  const f = fixture(options);
  for (const language of ['ru', 'el', 'en']) {
    f.select(language);
    assertLanguage(f, language);
    assert.equal(f.menu.hidden, true);
    assert.equal(f.document.activeElement, f.trigger);
  }
  assert.deepEqual(f.events, Array(3).fill('languagechange'), 'Keep chart and restart-dialog listeners notified exactly once per selection');
  if (!options.blockedWrite) assert.deepEqual(f.writes, ['ru', 'el', 'en'].map(language => ['homeenergy-language', language]));
  f.options[0].dataset.language = '__proto__';
  f.options[0].listeners.click();
  assert.equal(f.document.documentElement.lang, 'en');
  assert.equal(f.events.length, 3);
}
const keyboard = fixture();
function assertClosed() {
  assert.equal(keyboard.menu.hidden, true);
  assert.equal(keyboard.trigger.attributes['aria-expanded'], 'false');
}
assertClosed();
keyboard.trigger.listeners.click();
assert.equal(keyboard.menu.hidden, false);
assert.equal(keyboard.trigger.attributes['aria-expanded'], 'true');
assert.equal(keyboard.document.activeElement, keyboard.options[0]);
for (const [key, index] of [['ArrowUp', 2], ['ArrowDown', 0], ['End', 2], ['Home', 0], ['r', 1], ['ε', 2]]) {
  assert.equal(keyboard.key(keyboard.menu, key).prevented, true);
  assert.equal(keyboard.document.activeElement, keyboard.options[index]);
}
assert.deepEqual(keyboard.events, []);
assert.deepEqual(keyboard.writes, [], 'Keyboard focus must not select a language');
assert.equal(keyboard.key(keyboard.menu, 'Escape').prevented, true);
assertClosed();
assert.equal(keyboard.document.activeElement, keyboard.trigger);
keyboard.key(keyboard.trigger, 'ArrowUp');
assert.equal(keyboard.document.activeElement, keyboard.options[2]);
assert.equal(keyboard.key(keyboard.menu, 'Tab').prevented, false);
assertClosed();
assert.equal(keyboard.document.activeElement, keyboard.trigger);
keyboard.key(keyboard.trigger, 'ArrowDown');
keyboard.key(keyboard.menu, 'Tab', { shiftKey: true });
assertClosed();
keyboard.trigger.listeners.click();
keyboard.document.listeners.pointerdown({ target: keyboard.options[1] });
assert.equal(keyboard.menu.hidden, false);
keyboard.control.listeners.focusout({ relatedTarget: keyboard.options[1] });
assert.equal(keyboard.menu.hidden, false);
keyboard.document.listeners.pointerdown({ target: keyboard.heading });
assertClosed();
keyboard.trigger.listeners.click();
keyboard.control.listeners.focusout({ relatedTarget: null });
assertClosed();
console.log('Shared language picker: both pages, all locales, safe storage, event compatibility, keyboard and dismissal checks passed.');
