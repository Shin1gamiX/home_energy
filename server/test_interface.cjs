'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const read = file => fs.readFileSync(path.join(__dirname, '..', file), 'utf8');
const source = read('history.js');
const start = source.indexOf('function chooseChartView(');
const end = source.indexOf('\nfunction ', start + 1);
const context = {
  activeChartView: 'power', showMode: false, updates: 0,
  selected: new Set(['load', 'pv']),
  metrics: {load: {group: 'power'}, pv: {group: 'power'}, soc: {group: 'soc'}, pv1_voltage: {group: 'voltage'}},
  chartViewDefaults: {power: ['load', 'pv'], soc: ['soc'], voltage: ['pv1_voltage']},
  updateFilters() { context.updates++; },
};
vm.runInNewContext(source.slice(start, end) + '\nchoose = chooseChartView;', context);
context.choose('voltage');
assert.equal(context.activeChartView, 'voltage');
assert.deepEqual([...context.selected], ['load', 'pv', 'pv1_voltage']);
context.choose('soc');
assert.ok(context.selected.has('soc'));
context.choose('supply');
assert.equal(context.showMode, true);
context.choose('untrusted');
assert.equal(context.activeChartView, 'supply');
assert.equal(context.updates, 3);
context.choose('all');
assert.equal(context.activeChartView, 'all');
assert.doesNotMatch(source.slice(start, end), /fetch\(|load\(/, 'View switching must reuse loaded history');
assert.match(source, /activeChartView = showMode \? 'supply' : metrics\[linkedContext.metric\].group/, 'Report links reveal their exact metric');
assert.ok(source.indexOf('if (showMode) renderModeTimeline(container, view);') > source.indexOf('card.append(area, tip);'), 'DOM reading order matches chart-first visual order');
assert.match(source, /detail.hidden = true;/);
assert.match(source, /block.dataset.mode = state;/, 'Mode label contrast can follow the exact mode');
assert.match(source, /function inspect\(segment[^]*?detail.hidden = false;/);
assert.match(source, /historyCutoffs\(view, snapshotObservedAt, Date.now\(\) \/ 1000\).present/, 'Future shade is not a data gap');
const app = read('app.js');
for (const [quantity, unit] of [['power', 'W'], ['voltage', 'V'], ['current', 'A']]) {
  assert.ok(app.includes(`createChannelReading(\`pv\${channel}_${quantity}\`, '${unit}')`),
    `Both PV ${quantity} readings use the shared zero/missing-aware formatter`);
}
assert.match(app, /line.hidden = false;/, 'Zero current is a reading, not absent data');
const html = read('index.html');
for (const key of ['grid', 'grid_voltage', 'pv', 'load', 'soc', 'battery']) assert.ok(html.includes(`data-value="${key}"`));
for (const file of ['index.html', 'history.html']) assert.ok(read(file).includes('history-cache.js?v=20261018-queue-8'), 'Preserve request tuning');
const insights = read('insights.js');
assert.match(insights, /expanded \? 100 : 1/, 'Only latest review leads, older feed is retained');
assert.match(insights, /reports.length - 1/);
assert.match(insights, /insights-lead', lead.observation/);
assert.doesNotMatch(read('interface.css'), /https?:|@import/, 'No external assets or dependencies');
function luminance(hex) {
  const rgb = hex.match(/[a-f\d]{2}/gi).map(x => parseInt(x, 16) / 255).map(x => x <= .04045 ? x / 12.92 : ((x + .055) / 1.055) ** 2.4);
  return rgb[0] * .2126 + rgb[1] * .7152 + rgb[2] * .0722;
}
function contrast(a, b) { const pair = [luminance(a), luminance(b)].sort((x, y) => y - x); return (pair[0] + .05) / (pair[1] + .05); }
assert.ok(contrast('ffffff', '8861ba') >= 4.5, 'Light Grid label');
assert.ok(contrast('ffffff', '596cb0') >= 4.5, 'Light Mixed label');
assert.ok(contrast('875809', 'daf0e2') >= 4.5, 'Solar value on the active card');
assert.ok(contrast('875809', 'ffffff') >= 3, 'Solar track on its surface underlay');
console.log('Approved interface: view state, cached switching, deep links, chronological DOM, zero A/V/W, latest/older reports and request-tuning preservation passed.');
