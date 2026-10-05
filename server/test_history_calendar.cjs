// Run with Node's built-in modules; no browser packages or network calls.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../history-calendar.js'), 'utf8');
const document = { activeElement: null, documentElement: { clientWidth: 390 } };
class Element {
  constructor(tag) {
    this.tag = tag; this.children = []; this.attributes = {}; this.dataset = {};
    this.listeners = {}; this.style = {}; this.textContent = ''; this.className = '';
    this.classList = { toggle: (name, active) => {
      const classes = new Set(this.className.split(' ').filter(Boolean));
      if (active) classes.add(name); else classes.delete(name);
      this.className = [...classes].join(' ');
    } };
  }
  set innerHTML(_) { assert.fail('Calendar strings must be text, never HTML'); }
  setAttribute(name, value) { this.attributes[name] = value; }
  getAttribute(name) { return this.attributes[name] ?? null; }
  addEventListener(name, handler) { this.listeners[name] = handler; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  contains(target) { return this === target || this.children.some(child => child.contains(target)); }
  querySelector(selector) {
    const date = /data-date="([\d-]+)"/.exec(selector)?.[1];
    return this.children.find(child => child.dataset.date === date) || this.children.map(child => child.querySelector(selector)).find(Boolean);
  }
  closest() { return this.dataset.date ? this : null; }
  focus() { document.activeElement = this; }
  showModal() { this.open = true; }
  close() { this.open = false; this.listeners.close?.(); }
  getBoundingClientRect() { return this.tag === 'dialog' ? {width: 352, height: 450, left: 12, right: 364, top: 50, bottom: 500} : {left: 200, right: 240, top: 150, bottom: 190}; }
}
document.createElement = tag => new Element(tag);
const window = { innerHeight: 844, addEventListener() {} };
vm.runInNewContext(source, {window, document, Intl, Date});
const Calendar = window.HistoryCalendar;
for (const date of ['2026-10-05', '2024-02-29', '0001-01-01', '9999-12-31']) assert.ok(Calendar.validDay(date));
for (const date of ['2026-02-29', '2026-04-31', '2026-13-01', '2026-00-01', '2026-01-00', '0000-01-01', '2026-1-01', null, 7, '<script>']) assert.equal(Calendar.validDay(date), false);
assert.equal(Calendar.shiftMonth('2024-01-31', 1), '2024-02-29');
assert.equal(Calendar.shiftMonth('2024-02-29', 12), '2025-02-28');
assert.equal(Calendar.shiftMonth('2026-12-31', 1), '2027-01-31');
assert.equal(Calendar.shiftMonth('0001-01-01', -1), null);
assert.equal(Calendar.shiftDay('0001-01-01', -1), null);
assert.equal(Calendar.shiftDay('2026-03-29', 1), '2026-03-30', 'DST must not shift calendar dates');
assert.equal(Calendar.monthDays('2026-09')[0], '2026-08-31', 'Weeks start on Monday');
assert.equal(Calendar.monthDays('2026-02').filter(day => day?.startsWith('2026-02')).length, 28);
assert.equal(Calendar.monthDays('2024-02').filter(day => day?.startsWith('2024-02')).length, 29);

let locale = 'en-GB';
const selections = [];
const input = {value: '2026-09-23'};
const trigger = new Element('button'), dialog = new Element('dialog');
const calendar = new Calendar({trigger, dialog, input, t: key => key, locale: () => locale, today: () => '2026-10-05', onSelect: day => selections.push(day)});
const dayButton = day => calendar.body.querySelector(`button[data-date="${day}"]`);
calendar.open();
assert.equal(trigger.getAttribute('aria-expanded'), 'true');
assert.equal(document.activeElement.dataset.date, input.value);
assert.equal(calendar.legend.textContent, 'Loading recorded dates…');
assert.ok(dayButton('2026-09-22').getAttribute('aria-label').includes('Loading recorded dates'));
calendar.setAvailability(['2026-09-23', '2026-09-23', '2026-09-24', '2026-10-05', '2026-02-31', null]);
assert.equal(calendar.recordedDays.size, 3, 'Malformed dates and duplicates are ignored');
assert.ok(dayButton('2026-09-23').className.includes('has-readings'));
assert.ok(dayButton('2026-09-23').className.includes('is-selected'));
assert.ok(dayButton('2026-09-22').getAttribute('aria-label').includes('No recorded data'));
assert.equal(dayButton('2026-09-22').disabled, false, 'Dates with no data remain selectable');
assert.equal(document.activeElement.dataset.date, input.value, 'Availability updates retain day focus');
assert.equal(dayButton('2026-09-23').tabIndex, 0);
assert.equal(dayButton('2026-09-24').tabIndex, -1);
const press = (day, key, shiftKey = false) => {
  let prevented = false;
  calendar.navigate({target: dayButton(day), key, shiftKey, preventDefault() { prevented = true; }});
  assert.ok(prevented);
};
press('2026-09-23', 'ArrowRight');
assert.equal(document.activeElement.dataset.date, '2026-09-24');
press('2026-09-24', 'Home');
assert.equal(document.activeElement.dataset.date, '2026-09-21');
press('2026-09-21', 'End');
assert.equal(document.activeElement.dataset.date, '2026-09-27');
press('2026-09-27', 'PageDown');
assert.equal(document.activeElement.dataset.date, '2026-10-05', 'Future keyboard dates clamp to Athens today');
assert.equal(calendar.next.disabled, true);
assert.equal(dayButton('2026-10-06').disabled, true);
assert.equal(dayButton('2026-10-05').getAttribute('aria-current'), 'date');
calendar.todayButton.focus();
let trapped = 0;
calendar.trapFocus({key: 'Tab', shiftKey: false, preventDefault() { trapped += 1; }});
assert.equal(document.activeElement, calendar.previous);
calendar.trapFocus({key: 'Tab', shiftKey: true, preventDefault() { trapped += 1; }});
assert.equal(document.activeElement, calendar.todayButton);
assert.equal(trapped, 2, 'Both ends of the dialog keep keyboard focus inside');
calendar.select('2026-10-06');
assert.equal(selections.length, 0);
assert.equal(dialog.open, true);
calendar.select('2026-10-01');
assert.deepEqual(selections, ['2026-10-01']);
assert.equal(dialog.open, false);
assert.equal(trigger.getAttribute('aria-expanded'), 'false');

calendar.open();
calendar.setAvailability(null, 'error');
assert.equal(calendar.legend.textContent, 'Recorded dates unavailable');
assert.ok(dayButton('2026-09-23').getAttribute('aria-label').includes('Recorded dates unavailable'));
assert.equal(dayButton('2026-09-23').className.includes('has-readings'), false);
calendar.setAvailability([]);
assert.ok(dayButton('2026-09-23').getAttribute('aria-label').includes('No recorded data'));
for (const [language, month] of [['ru-RU', 'сентябрь'], ['el-GR', 'Σεπτέμβριος']]) {
  locale = language; calendar.render();
  assert.ok(calendar.title.textContent.includes(month), calendar.title.textContent);
}
assert.ok(Number.parseFloat(dialog.style.left) >= 12);
assert.ok(Number.parseFloat(dialog.style.left) + 352 <= 390 - 12);
assert.ok(Number.parseFloat(dialog.style.top) >= 12);
console.log('Calendar validation, leap years, DST, availability states, focus, keyboard, selection, localization and positioning checks passed.');
