'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../history.js'), 'utf8').replace(/\r\n/g, '\n');
const css = fs.readFileSync(path.join(__dirname, '../history.css'), 'utf8');

function functionSource(name) {
  const start = source.indexOf(`function ${name}(`);
  assert.ok(start >= 0, `Missing ${name}`);
  return source.slice(start, source.indexOf('\n}', start) + 2);
}

const document = { documentElement: { dataset: {} } };
const context = { document, t: value => value };
vm.createContext(context);
vm.runInContext(functionSource('historyColor'), context);
const metricsStart = source.indexOf('const metrics = {');
const metricsEnd = source.indexOf('// Generic voltage/current');
vm.runInContext(source.slice(metricsStart, metricsEnd) + '\nthis.metrics = metrics;', context);
const light = Object.fromEntries(Object.entries(context.metrics).map(([key, value]) => [key, value.color]));
assert.equal(Object.keys(light).length, 16);
assert.equal(light.grid, '#8861ba');
assert.equal(light.pv, '#c39232');

function luminance(hex) {
  const channels = [1, 3, 5].map(start => {
    const value = parseInt(hex.slice(start, start + 2), 16) / 255;
    return value <= .04045 ? value / 12.92 : ((value + .055) / 1.055) ** 2.4;
  });
  return channels[0] * .2126 + channels[1] * .7152 + channels[2] * .0722;
}
function contrast(a, b) {
  const values = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (values[0] + .05) / (values[1] + .05);
}
document.documentElement.dataset.theme = 'dark';
for (const [key, metric] of Object.entries(context.metrics)) {
  assert.notEqual(metric.color, light[key], `${key} adapts for dark surfaces`);
  assert.ok(contrast(metric.color, '#172624') >= 3, `${key} line contrast`);
  assert.ok(contrast(metric.color, '#20332f') >= 3, `${key} accent contrast`);
}
for (const stateColor of ['#596cb0', '#869b98', '#adb8b4', '#c3ccc8', '#60776c']) {
  assert.ok(contrast(context.historyColor(stateColor), '#172624') >= 3, 'Mode intervals remain visible');
}
assert.equal(context.historyColor('#123456'), '#123456', 'Unknown colours do not become undefined');
document.documentElement.dataset.theme = 'light';
for (const [key, metric] of Object.entries(context.metrics)) assert.equal(metric.color, light[key]);
console.log('History dark palette contrast, fallback and light round-trip checks passed.');

// Theme changes repaint cached selections only, preserving chart interaction state.
const properties = new Map();
function button(key) {
  return {
    dataset: { metric: key },
    style: { setProperty(name, value) { properties.set(`${key}:${name}`, value); } },
    setAttribute(name, value) { this[name] = value; },
    querySelector() { return { textContent: '' }; },
  };
}
const buttons = Object.keys(context.metrics).map(button);
const mode = button('mode');
document.querySelector = selector => selector === '#mode-filter' ? mode : { textContent: '' };
document.querySelectorAll = selector => selector === '[data-metric]' ? buttons : [];
Object.assign(context, {
  selected: new Set(['pv', 'grid']), showMode: true,
  zoom: { from: 10, to: 20 }, range: { from: 0, to: 100 },
  inspectedTime: 15, modeSelectedTime: 12, period: 'week',
  renderCount: 0, fetchCount: 0,
  render() { context.renderCount += 1; },
  fetch() { context.fetchCount += 1; throw new Error('Theme changes must not fetch'); },
});
const before = JSON.stringify([context.zoom, context.range, context.inspectedTime, context.modeSelectedTime, context.period, [...context.selected]]);
vm.runInContext(functionSource('updateFilters'), context);
document.documentElement.dataset.theme = 'dark';
context.updateFilters();
assert.equal(context.renderCount, 1);
assert.equal(context.fetchCount, 0);
assert.equal(JSON.stringify([context.zoom, context.range, context.inspectedTime, context.modeSelectedTime, context.period, [...context.selected]]), before);
assert.equal(properties.get('grid:--series'), '#b893ed');
assert.equal(properties.get('mode:--series'), '#80d4b3');
assert.equal(buttons.find(item => item.dataset.metric === 'grid')['aria-pressed'], 'true');
assert.equal(buttons.find(item => item.dataset.metric === 'load')['aria-pressed'], 'false');
assert.match(source, /window\.addEventListener\('themechange', updateFilters\);/);
assert.match(source, /for \(const state of Object\.values\(states\)\) state\[1\] = historyColor\(state\[1\]\);/);
console.log('History theme repaint preserves range, zoom, inspection and filters without fetching.');

for (const selector of ['.chart-grid', '.axis-label', '.chart-zero', '.peak-dot', '.zoom-selection',
  '.chart-tip', '.filters button', '.mode-tracks', '.mode-future', '.mode-pending',
  '.mode-segment.is-selected', '.monthly-table th:first-child', '.calendar-dialog',
  '.calendar-day.has-readings', '.calendar-day.is-selected', '.calendar-day.is-today']) {
  assert.ok(css.split('\n').some(line => line.startsWith('html[data-theme="dark"]') && line.includes(selector)), `Dark rule for ${selector}`);
}
console.log('History chart, table and calendar dark-state coverage checks passed.');
