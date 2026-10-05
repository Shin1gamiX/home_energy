// Exercise browser-independent history calculations with Node's built-in tools.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../history.js'), 'utf8');
function readFunction(name, context = {}) {
  const start = source.indexOf(`function ${name}(`);
  assert.ok(start >= 0, `Missing function: ${name}`);
  const next = source.indexOf('\nfunction ', start + 1);
  vm.runInNewContext(source.slice(start, next < 0 ? undefined : next) + `\nresult = ${name};`, context);
  return context.result;
}
const energyTotals = readFunction('energyTotals');
const row = (t, values) => ({t, values});
const base = {load: 500, grid: 420, battery: 0, pv: 0};
const total = values => energyTotals([row(0, values)], 0, 60, 60).solar_to_house;
assert.equal(total(base).kwh, 0, 'Nighttime grid residual must not become solar');
assert.equal(total({...base, pv: 20}).kwh, 20 / 60000, 'Estimate cannot exceed PV');
assert.equal(total({...base, pv: 100, grid: 200, battery: -250}).kwh, 50 / 60000);
assert.equal(total({...base, pv: 100, grid: 600}).kwh, 0);
assert.equal(total({...base, pv: null}).seconds, 0, 'Missing PV is unknown, not zero');
assert.equal(total({...base, battery: null}).seconds, 0);
assert.equal(total({...base, pv: -1}).seconds, 0);
assert.equal(energyTotals([row(0, {...base, pv: 100})], 0, 60, 30).solar_to_house.kwh, 80 / 120000);
assert.equal(energyTotals([row(0, base), row(120, base)], 0, 180, 180).load.seconds, 120);
console.log('History energy regression checks passed.');
const historyCutoffs = readFunction('historyCutoffs');
const view = {from: 0, to: 86400};
assert.equal(historyCutoffs(view, 300, 300).observed, 300);
assert.equal(historyCutoffs(view, 300, 900).observed, 300, 'Cached redraw must not extend inferred missing data');
assert.equal(historyCutoffs(view, 300, 900).present, 900);
assert.equal(historyCutoffs(view, 900, 900).observed, 900, 'A new fetch can observe a real outage');
assert.equal(historyCutoffs(view, 90000, 91000).observed, 86400);
console.log('History snapshot boundary regression checks passed.');
const chartScale = readFunction('chartScale');
for (const [low, high] of [[0, 0], [-88, 76], [0, 5245], [-57, 0], [0, .005], [0, 1000000]]) {
  const scale = chartScale(low, high);
  assert.ok(scale.low <= low && scale.high >= high);
  assert.ok(scale.high > scale.low);
  assert.ok(scale.ticks.includes(0), 'Zero must be a labelled tick');
  assert.ok(scale.ticks.length >= 3 && scale.ticks.length <= 8);
  assert.ok(scale.ticks.every(Number.isFinite));
}
assert.deepEqual(Array.from(chartScale(-88, 76).ticks), [-100, -50, 0, 50, 100]);
console.log('Rounded chart scale regression checks passed.');
const groupModeSegments = readFunction('groupModeSegments');
const shortSegments = [{start: 0, end: 15, state: 'solar'}, {start: 15, end: 30, state: 'grid'}];
assert.equal(groupModeSegments(shortSegments, 100).length, 1);
assert.equal(groupModeSegments(shortSegments, 1).length, 2, 'Zoom exposes original intervals');
for (const state of ['missing', 'unrecorded', 'unknown']) {
  const groups = groupModeSegments([shortSegments[0], {...shortSegments[1], state}], 100);
  assert.equal(groups.length, 2, 'Unknown/gap states must not be hidden in a rapid-changes block');
}
assert.equal(groupModeSegments([shortSegments[0], {...shortSegments[1], start: 20}], 100).length, 2);
const recordedRange = readFunction('recordedRange');
assert.equal(recordedRange([], [], view, 1000), null);
assert.equal(recordedRange([row(100, {pv: null})], [], view, 1000), null);
assert.equal(recordedRange([row(100, {pv: 0}), row(700, {pv: 20})], [], view, 730).to, 730);
assert.equal(recordedRange([], [{start: 200, end: 800}], view, 900).from, 200);
assert.equal(recordedRange([row(100, {pv: 0})], [], view, 120).to, 220, 'Very short data keeps a usable two-minute view');
console.log('Supply timeline grouping and recorded-range regression checks passed.');

const validateEnergySummary = readFunction('validateEnergySummary');
const energyKeys = ['pv', 'grid', 'load', 'battery', 'solar_to_house'];
const energyFields = (kwh, seconds) => Object.fromEntries(energyKeys.map(key => [key, {kwh, seconds}]));
const cumulative = {
  version: 1, from: 0, to: 120, totals: energyFields(2, 120),
  months: [{month: '2026-08', from: 0, to: 60, totals: energyFields(1, 60)},
    {month: '2026-09', from: 60, to: 120, totals: energyFields(1, 60)}]
};
assert.equal(validateEnergySummary(cumulative, 120), cumulative);
const emptyCumulative = {version: 1, from: null, to: null, totals: energyFields(0, 0), months: []};
assert.equal(validateEnergySummary(emptyCumulative, 0), emptyCumulative);
assert.throws(() => validateEnergySummary(undefined, 120), 'Old exporters have no summary');
assert.throws(() => validateEnergySummary(cumulative, 121), 'Snapshot boundaries must agree');
for (const corrupt of [
  data => { data.version = 2; },
  data => { data.totals.pv.kwh = 3; },
  data => { data.months[0].totals.pv.seconds = 61; },
  data => { data.months[0].totals.pv.kwh = -1; },
  data => { data.months[1].month = '2026-08'; },
  data => { data.months[1].from = 59; },
  data => { delete data.totals.grid; },
  data => { data.months[0].totals.pv.kwh = Infinity; }
]) {
  const invalid = structuredClone(cumulative);
  corrupt(invalid);
  assert.throws(() => validateEnergySummary(invalid, 120));
}
console.log('All-time energy response validation checks passed.');

// Small DOM stand-in: empty/error states must never leave old totals visible.
function renderSummaryState(state) {
  const elements = [];
  function element(tag) {
    const node = {tag, children: [], textContent: '', style: {setProperty() {}},
      append(...children) { this.children.push(...children); },
      replaceChildren() { this.children = []; }};
    elements.push(node);
    return node;
  }
  const sections = Object.fromEntries(['#energy-summary', '#monthly-breakdown', '#range-label'].map(id => [id, element('section')]));
  const statusElement = element('status');
  const context = {
    loading: false, hasError: false, summaryUnavailable: false,
    allTimeSummary: emptyCumulative, ...state, statusElement,
    summaryLabels: Object.fromEntries(energyKeys.map(key => [key, key])),
    metrics: Object.fromEntries(energyKeys.map(key => [key, {color: '#000'}])),
    window: {energyI18n: {locale: 'en-GB'}}, t: key => key,
    document: {querySelector: id => sections[id], createElement: element}
  };
  readFunction('renderAllTime', context)();
  assert.equal(sections['#monthly-breakdown'].children.length, 0);
  assert.equal(elements.filter(node => node.tag === 'strong' && node.textContent === '—').length, 5);
  return statusElement.textContent;
}
assert.equal(renderSummaryState({}), 'No recorded energy yet.');
assert.equal(renderSummaryState({loading: true}), 'Loading history…');
assert.equal(renderSummaryState({hasError: true, summaryUnavailable: true}), 'All-time totals are not available yet. Day, week and month still work.');
assert.equal(renderSummaryState({hasError: true, allTimeSummary: cumulative}), 'History is unavailable. Please try again shortly.');
console.log('All-time loading, empty, old-exporter and failed-refresh UI checks passed.');
