'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const read = name => fs.readFileSync(path.join(__dirname, '..', name), 'utf8');
const source = read('app.js');

class Element {
  constructor(tag) {
    this.tagName = tag;
    this.dataset = {};
    this.attributes = {};
    this.children = [];
  }
  setAttribute(name, value) { this.attributes[name] = value; }
  set textContent(value) { this.text = String(value); this.children = []; }
  get textContent() { return this.text ?? ''; }
  append(...children) {
    for (const child of children) { child.parentElement = this; this.children.push(child); }
  }
  prepend(child) { child.parentElement = this; this.children.unshift(child); }
  get nextElementSibling() {
    const siblings = this.parentElement.children;
    return siblings.slice(siblings.indexOf(this) + 1).find(node => node instanceof Element);
  }
}
const readings = [];
const context = {
  document: {
    createElement: tag => new Element(tag),
    createElementNS: (_, tag) => new Element(tag),
    createTextNode: text => ({ textContent: text }),
    querySelectorAll: () => readings,
  },
  window: { energyI18n: { locale: 'en-GB' } },
  t: key => `translated ${key}`,
  pvChannelViews: [],
};
vm.createContext(context);
vm.runInContext(source.slice(source.indexOf('function createChannelReading('),
  source.indexOf("for (const selector of ['.solar-label>div'")), context);
vm.runInContext(source.slice(source.indexOf('const svgNS ='),
  source.indexOf('for (const [name, d] of Object.entries(icons))')), context);
const keys = ['pv', 'grid', 'grid_voltage', 'soc', 'battery', 'battery_voltage',
  'battery_current', 'load', 'load_current', ...[1, 2].flatMap(channel =>
    ['power', 'voltage', 'current'].map(quantity => `pv${channel}_${quantity}`))];
const rows = new Map();
for (const key of keys) {
  const unit = key === 'soc' ? '%' : key.endsWith('_voltage') ? 'V'
    : key.endsWith('_current') ? 'A' : 'W';
  const row = context.createChannelReading(key, unit);
  rows.set(key, row);
  readings.push(row.children[0]);
}
for (const channel of [1, 2]) context.pvChannelViews.push({
  channel, ...Object.fromEntries(['power', 'voltage', 'current'].map(key => [key, rows.get(`pv${channel}_${key}`)])),
});
vm.runInContext(source.slice(source.indexOf('const measurementPaths ='),
  source.indexOf('const flowAnimation =')), context);
for (const value of readings) {
  const icon = value.parentElement.children[0];
  assert.equal(icon.tagName, 'svg');
  assert.equal(icon.attributes['aria-hidden'], 'true');
  assert.equal(icon.attributes.focusable, 'false');
  assert.ok(icon.children[0].attributes.d, 'Every quantity has an authored vector path');
  assert.equal(value.nextElementSibling.tagName, 'small', 'Power formatter still targets its adjacent unit');
}
assert.equal(context.measurementKind('battery_voltage'), 'voltage');
assert.equal(context.measurementKind('pv2_current'), 'current');
assert.equal(context.measurementKind('soc'), 'charge');
assert.equal(context.measurementKind('battery'), 'power');
vm.runInContext(source.slice(source.indexOf('function formatPower('), source.indexOf('function render(')), context);
const update = source.slice(source.indexOf('  const values = sample;', source.indexOf('function render(')),
  source.indexOf("  document.querySelectorAll('#charge-bar"));
const iconReferences = readings.map(value => value.parentElement.children[0]);
function render(values) {
  context.sample = values;
  vm.runInContext(`(() => { ${update} })()`, context);
}
render(Object.fromEntries(keys.map(key => [key, 0])));
for (const value of readings) assert.equal(value.textContent, '0', `${value.dataset.value}: zero remains visible`);
render({ pv: 1580, battery: -1250, battery_voltage: 52.6, battery_current: -9.5,
  soc: 84, pv1_power: 1000, pv1_voltage: 382.9, pv1_current: 0, pv2_power: null,
  pv2_voltage: NaN, pv2_current: Infinity });
const reading = key => readings.find(value => value.dataset.value === key);
assert.equal(reading('pv').textContent, '1.58');
assert.equal(reading('pv').nextElementSibling.textContent, 'kW');
assert.equal(reading('battery').textContent, '-1.25');
assert.equal(reading('battery').nextElementSibling.textContent, 'kW');
assert.equal(reading('battery_voltage').textContent, '52.6');
assert.equal(reading('battery_current').textContent, '-9.5');
for (const key of ['grid', 'pv2_power', 'pv2_voltage', 'pv2_current']) assert.equal(reading(key).textContent, '—');
assert.equal(rows.get('pv1_voltage').title, 'translated PV1 voltage');
for (const locale of ['el-GR', 'ru-RU']) {
  context.window.energyI18n.locale = locale;
  render({ pv: 1580, battery: -500, battery_voltage: 52.6 });
  assert.equal(reading('pv').textContent, '1,58');
  assert.equal(reading('battery_voltage').textContent, '52,6');
  assert.equal(reading('battery').textContent, '-500');
  assert.equal(reading('battery').nextElementSibling.textContent, 'W');
}
// Reuse the loopback preview's widest readings, including two-digit kW and signed current.
vm.runInContext(source.slice(source.indexOf('const samples = ['),
  source.indexOf('const pvChannelViews =')), context);
const highPower = vm.runInContext('samples[samples.length - 1]', context);
for (const locale of ['en-GB', 'el-GR', 'ru-RU']) {
  context.window.energyI18n.locale = locale;
  render(highPower);
  const separator = locale === 'en-GB' ? '.' : ',';
  assert.equal(reading('load').textContent, `19${separator}87`);
  assert.equal(reading('load').nextElementSibling.textContent, 'kW');
  assert.equal(reading('pv').textContent, `12${separator}34`);
  assert.equal(reading('battery').textContent, `-7${separator}53`);
  assert.equal(reading('battery_current').textContent, `-142${separator}3`);
  assert.equal(reading('soc').textContent, '100');
}
readings.forEach((value, index) => {
  assert.equal(value.parentElement.children[0], iconReferences[index], 'Updates preserve the original icon node');
  assert.equal(value.parentElement.children.filter(node => node.tagName === 'svg').length, 1);
});
const html = read('index.html');
for (const id of ['battery-state-description', 'battery-detail-state']) {
  assert.ok(html.includes(`aria-describedby="${id}"`));
  assert.ok(html.includes(`class="battery-status" id="${id}" hidden`));
}
assert.match(source, /element.textContent = t\(state.batteryLabel\)/, 'Accessible battery state remains translated');
assert.match(read('power-history.js'), /const description = card.getAttribute\('aria-describedby'\);\s*if \(description\) button.setAttribute\('aria-describedby', description\)/,
  'Keyboard users receive the same battery state when focusing the card button');
assert.match(read('interface.css'), /\.overview \.battery-status \{ display: none; \}/);
assert.match(read('interface.css'), /\.pv-channel > span\.pv-channel-details \{ display: flex; flex-wrap: wrap;/);
const narrowStyles = read('interface.css').split('@media (max-width: 360px) {')[1];
assert.ok(narrowStyles, 'Small-screen layout has an explicit regression boundary');
assert.match(narrowStyles, /width: calc\(50% - 18px\); padding: 10px 8px/);
assert.match(narrowStyles, /flex-wrap: wrap;.*white-space: normal/);
assert.match(narrowStyles, /\.pv-channel \{ grid-template-columns: minmax\(0, 1fr\)/);
assert.match(narrowStyles, /\.scene-heading \{ top: 47px; left: 16px; right: auto/);
assert.match(narrowStyles, /@media \(max-width: 300px\) \{\s*html \.overview \.energy-scene \{ height: 650px/);
assert.doesNotMatch(narrowStyles, /overflow:\s*hidden|text-overflow|display:\s*none/,
  'Narrow layout must not conceal quantities or units');
console.log('Measurement icons: persistent SVGs, units, zero/missing/signed and high-kW readings, three locales, narrow-screen rules and accessible battery state passed.');
