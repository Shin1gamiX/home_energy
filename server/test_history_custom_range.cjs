'use strict';
// Run with Node only. Fixed dates and synthetic readings; no files or network are written.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.join(__dirname, '..');
const listeners = {};
const document = { documentElement: { lang: 'en' } };
const window = { energyI18n: { t: key => key, locale: 'en-GB' },
  addEventListener: (event, action) => { listeners[event] = action; } };
const context = vm.createContext({ window, document, Intl, Date, timezone: 'Europe/Athens' });
vm.runInContext(fs.readFileSync(path.join(root, 'history-calendar.js'), 'utf8'), context);
vm.runInContext(fs.readFileSync(path.join(root, 'history-custom-range.js'), 'utf8'), context);
const Custom = window.HistoryCustomRange;
const source = fs.readFileSync(path.join(root, 'history.js'), 'utf8');
for (const name of ['midnight', 'shift', 'getRange', 'energyTotals', 'aggregate', 'createCustomRange']) {
  const start = source.indexOf(`function ${name}(`);
  vm.runInContext(source.slice(start, source.indexOf('\n}', start) + 2), context);
}
document.querySelector = () => null;
const savedHelper = window.HistoryCustomRange;
delete window.HistoryCustomRange;
const legacyControls = context.createCustomRange();
assert.equal(legacyControls.opened, false);
assert.doesNotThrow(() => { legacyControls.hide(); legacyControls.closeCalendars(); legacyControls.setAvailability([]); });
window.HistoryCustomRange = savedHelper;
const validate = (start, end, today = '2026-11-17') => Custom.validate(start, end, today);
assert.ok(validate('17/11/2026', '17/11/2026').valid, 'Same-day ranges are allowed');
assert.ok(validate('29/02/2024', '01/03/2024').valid);
assert.ok(validate(' 01/11/2026 ', '17/11/2026').valid, 'Pasted surrounding whitespace is harmless');
for (const start of ['', '1/11/2026', '31/04/2026', '29/02/2026', '00/11/2026', '17/13/2026', '01/01/0000', '<script>']) {
  assert.equal(validate(start, '17/11/2026').valid, false, start);
}
assert.equal(validate('18/11/2026', '18/11/2026').errors.start, 'Choose today or an earlier date.');
assert.equal(validate('17/11/2026', '18/11/2026').errors.end, 'Choose today or an earlier date.');
const reverse = validate('17/11/2026', '16/11/2026');
assert.equal(reverse.valid, false); assert.equal(reverse.start, '2026-11-17'); assert.equal(reverse.end, '2026-11-16');
assert.equal(reverse.errors.end, 'End date must be on or after the start date.');

function range(start, end) {
  Object.assign(context, { linkedContext: null, period: 'custom', customRange: { applied: { start, end } } });
  return context.getRange();
}
for (const [start, end, hours, days] of [
  ['2026-03-29', '2026-03-29', 23, 1], ['2026-10-25', '2026-10-25', 25, 1],
  ['2026-10-24', '2026-10-25', 49, 2], ['2025-12-31', '2026-01-01', 48, 2],
  ['2024-02-28', '2024-03-01', 72, 3], ['2026-10-01', '2026-10-31', 745, 31],
]) {
  const span = range(start, end);
  assert.equal((span.to - span.from) / 3600, hours);
  assert.equal(Custom.dayCount(start, end), days);
  assert.equal(span.end, window.HistoryCalendar.shiftDay(end, 1));
}
assert.ok(Number.isFinite(range('1800-01-01', '1800-01-01').from), 'Historical offset minutes must not produce NaN');
assert.equal(range('2026-10-24', '2026-10-25').step, 60, 'Two calendar days keep minute detail even across a 25-hour day');
assert.equal(range('2026-10-01', '2026-10-07').step, 900);
assert.equal(range('2026-09-01', '2026-11-17').step, 3600);
assert.equal(range('2026-01-01', '2026-11-17').step, 21600);

const bounds = range('2026-10-24', '2026-10-25');
const row = t => ({ t, values: { pv: 1000, load: 500, grid: 0, battery: 0 }, counts: { pv: 1, load: 1 } });
const rows = [row(bounds.from - 60), row(bounds.from), row(bounds.from + 120), row(bounds.to - 60), row(bounds.to)];
const totals = context.energyTotals(rows, bounds.from, bounds.to, bounds.to);
assert.equal(totals.pv.seconds, 180, 'Outside rows excluded; missing minute stays a gap');
assert.ok(Math.abs(totals.pv.kwh - .05) < 1e-10, 'Both dates included through the last minute of the end date');
assert.equal(context.energyTotals(rows, bounds.from, bounds.to, bounds.from + 30).pv.seconds, 30, 'Today stops at the snapshot cutoff');
assert.equal(context.energyTotals([], bounds.from, bounds.to, bounds.to).pv.seconds, 0);
context.range = bounds; context.metrics = { pv: {}, load: {} };
for (const step of [60, 900, 3600, 21600]) {
  context.aggregate(rows, step);
  assert.equal(context.energyTotals(rows, bounds.from, bounds.to, bounds.to).pv.kwh, totals.pv.kwh, 'Averaging never changes raw energy totals');
}

class Element {
  constructor() { this.value = ''; this.attributes = {}; this.listeners = {}; this.dataset = {}; this.textContent = ''; }
  setAttribute(key, value) { this.attributes[key] = value; }
  addEventListener(event, action) { this.listeners[event] = action; }
  focus() { document.activeElement = this; }
}
const nodes = new Map();
const node = selector => { if (!nodes.has(selector)) nodes.set(selector, new Element()); return nodes.get(selector); };
document.querySelector = node;
const labels = ['Start date', 'End date', 'Apply range', 'Both dates included · Athens time.', 'Today includes only the readings recorded so far.'].map(key => {
  const element = new Element(); element.dataset.customI18n = key; return element;
});
const form = new Element(); form.querySelector = node; form.querySelectorAll = () => labels;
const Calendar = window.HistoryCalendar;
window.HistoryCalendar = class {
  static validDay = Calendar.validDay;
  static shiftDay = Calendar.shiftDay;
  constructor(options) { this.options = options; }
  close() {}
  setAvailability(days) { this.days = days; }
};
let applies = 0;
const control = new Custom({ form, today: () => '2026-11-17', onApply: () => { applies++; } });
control.show({ start: '2026-11-01', end: '2026-11-17' });
assert.equal(form.hidden, false); assert.equal(control.opened, true); assert.equal(applies, 0);
assert.equal(node('#custom-start').value, '01/11/2026');
assert.equal(control.calendars.end.options.minimum(), '2026-11-01');
node('#custom-end').value = '31/10/2026';
node('#custom-end').listeners.input(); node('#custom-end').listeners.blur();
assert.equal(node('[type="submit"]').disabled, true);
assert.equal(node('#custom-end').attributes['aria-invalid'], 'true');
form.listeners.submit({ preventDefault() {} });
assert.equal(applies, 0); assert.equal(document.activeElement, node('#custom-end'));
control.calendars.end.options.onSelect('2026-11-15');
assert.equal(applies, 0, 'Selecting a calendar date edits the draft only');
assert.equal(control.applied, null);
form.listeners.submit({ preventDefault() {} });
assert.equal(applies, 1); assert.equal(control.applied.end, '2026-11-15');
node('#custom-start').value = '10/11/2026'; node('#custom-start').listeners.input();
assert.equal(control.applied.start, '2026-11-01', 'Editing leaves the applied range intact');
assert.ok(node('.custom-range-status').textContent.includes('Changes not applied'));
control.setAvailability(['2026-11-01']);
assert.deepEqual(control.calendars.start.days, ['2026-11-01']);
for (const [language, label] of [['el', 'Από'], ['ru', 'Начальная дата'], ['en', 'Start date']]) {
  document.documentElement.lang = language; listeners.languagechange();
  assert.equal(labels[0].textContent, label);
  assert.equal(node('#custom-start').value, '10/11/2026'); assert.equal(applies, 1);
  for (const key of Object.keys(Custom.messages.el)) {
    assert.ok(language === 'en' || Custom.messages[language][key], `${language}: missing ${key}`);
  }
}
control.hide(); assert.equal(control.opened, false); assert.equal(form.hidden, true);
control.show({ start: '2026-11-17', end: '2026-11-17' });
assert.equal(node('#custom-start').value, '10/11/2026', 'Returning to Custom keeps the draft');
assert.ok(node('.custom-range-status').textContent.includes('Select dates'), 'Returning from a preset must not claim the saved range is already showing');
assert.equal(applies, 1, 'Language, calendar and mode changes do not submit');
console.log('Custom History: strict dates, inclusive bounds, Athens DST, raw totals, missing/partial data, draft/apply isolation, calendars and three languages passed.');
