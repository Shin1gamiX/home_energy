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
const interpolate = (key, values) => key.replace(/\{(\w+)\}/g, (_, name) => values[name]);
const formatRecordedDuration = readFunction('formatRecordedDuration', {
  window: {energyI18n: {locale: 'en-GB'}}, t: interpolate
});
for (const [hours, expected] of [[0, '0 h'], [1, '1 h'], [21.2, '21.2 h'],
  [141.2, '5 d - 21.2 h'], [1503.69, '62 d - 15.7 h'], [24, '1 d - 0 h'],
  [23.94, '23.9 h'], [23.96, '1 d - 0 h'], [47.96, '2 d - 0 h']]) {
  assert.equal(formatRecordedDuration(hours * 3600), expected);
}
for (const value of [-1, NaN, Infinity, null]) assert.equal(formatRecordedDuration(value), '—');
for (const locale of ['ru-RU', 'el-GR']) {
  const localized = readFunction('formatRecordedDuration', {
    window: {energyI18n: {locale}}, t: interpolate
  });
  assert.equal(localized(141.2 * 3600), '5 d - 21,2 h');
}
console.log('Recorded days/hours formatting and rollover checks passed.');
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
const chargeRows = [row(0, {...base, battery: 600}), row(60, {...base, battery: -600}),
  row(120, {...base, battery: 0}), row(180, {...base, battery: null}),
  row(240, {...base, battery: Infinity}), row(300, {})];
const directions = energyTotals(chargeRows, 0, 360, 360);
assert.equal(directions.battery_charged.kwh, .01, 'Grid charging counts; do not infer charge from PV');
assert.equal(directions.battery.kwh, .01, 'Discharge does not cancel charging');
assert.equal(directions.battery_charged.seconds, 180, 'Real zero counts as coverage; unknown does not');
assert.equal(energyTotals(chargeRows, 30, 360, 45).battery_charged.kwh, .0025, 'Clip to both range and snapshot');
assert.equal(energyTotals([...chargeRows, chargeRows[0]], 0, 360, 360).battery_charged.kwh, .01, 'Do not count duplicate minutes');
assert.equal(energyTotals([row(0, {})], 0, 60, 60).battery_charged.seconds, 0);
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
const energyKeys = ['pv', 'grid', 'load', 'battery', 'battery_charged', 'solar_to_house'];
const energyFields = (kwh, seconds) => Object.fromEntries(energyKeys.map(key => [key, {kwh, seconds}]));
const cumulative = {
  version: 2, from: 0, to: 120, totals: energyFields(2, 120),
  months: [{month: '2026-08', from: 0, to: 60, totals: energyFields(1, 60)},
    {month: '2026-09', from: 60, to: 120, totals: energyFields(1, 60)}]
};
assert.equal(validateEnergySummary(cumulative, 120), cumulative);
const emptyCumulative = {version: 2, from: null, to: null, totals: energyFields(0, 0), months: []};
assert.equal(validateEnergySummary(emptyCumulative, 0), emptyCumulative);
assert.throws(() => validateEnergySummary(undefined, 120), 'Old exporters have no summary');
assert.throws(() => validateEnergySummary(cumulative, 121), 'Snapshot boundaries must agree');
for (const corrupt of [
  data => { data.version = 3; },
  data => { data.totals.pv.kwh = 3; },
  data => { data.months[0].totals.pv.seconds = 61; },
  data => { data.months[0].totals.pv.kwh = -1; },
  data => { data.months[1].month = '2026-08'; },
  data => { data.months[1].from = 59; },
  data => { delete data.totals.grid; },
  data => { delete data.totals.battery_charged; },
  data => { delete data.months[0].totals.battery_charged; },
  data => { data.totals.battery_charged.kwh = 3; },
  data => { data.months[0].totals.battery_charged.seconds = 61; },
  data => { data.months[0].totals.battery_charged.kwh = -1; },
  data => { data.months[0].totals.pv.kwh = Infinity; }
]) {
  const invalid = structuredClone(cumulative);
  corrupt(invalid);
  assert.throws(() => validateEnergySummary(invalid, 120));
}
const legacyInput = structuredClone(cumulative);
legacyInput.version = 1;
delete legacyInput.totals.battery_charged;
for (const month of legacyInput.months) delete month.totals.battery_charged;
const legacySummary = validateEnergySummary(legacyInput, 120);
assert.equal(legacySummary.totals.battery_charged, null);
assert.ok(legacySummary.months.every(month => month.totals.battery_charged === null));
assert.equal(legacySummary.totals.battery.kwh, cumulative.totals.battery.kwh);
assert.equal(legacyInput.totals.battery_charged, undefined, 'Do not mutate the fetched cache');
legacyInput.totals.battery_charged = {kwh: 'unvalidated', seconds: 120};
assert.equal(validateEnergySummary(legacyInput, 120).totals.battery_charged, null);
console.log('All-time energy response validation checks passed.');

// Small DOM stand-in: empty/error states must never leave old totals visible.
function renderSummaryState(state) {
  const elements = [];
  function element(tag) {
    const node = {tag, children: [], textContent: '', style: {setProperty() {}},
      setAttribute() {}, addEventListener() {},
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
    summaryMetricKeys: {solar_to_house: 'pv', battery_charged: 'soc'},
    metrics: Object.fromEntries([...energyKeys, 'soc'].map(key => [key, {color: '#000'}])),
    window: {energyI18n: {locale: 'en-GB'}}, t: interpolate,
    dateFormat: new Intl.DateTimeFormat('en-GB', {timeZone: 'Europe/Athens'}),
    timezone: 'Europe/Athens', today: () => '2026-10-05', formatRecordedDuration,
    svgElement: element,
    document: {querySelector: id => sections[id], createElement: element}
  };
  readFunction('renderAllTime', context)();
  const ready = !context.loading && !context.hasError && context.allTimeSummary.months.length > 0;
  if (!ready) {
    assert.equal(sections['#monthly-breakdown'].children.length, 0);
    assert.equal(elements.filter(node => node.tag === 'strong' && node.textContent === '—').length, 6);
  }
  return {status: statusElement.textContent, elements, sections};
}
assert.equal(renderSummaryState({}).status, 'No recorded energy yet.');
assert.equal(renderSummaryState({loading: true}).status, 'Loading history…');
assert.equal(renderSummaryState({hasError: true, summaryUnavailable: true}).status, 'All-time totals are not available yet. Day, week and month still work.');
assert.equal(renderSummaryState({hasError: true, allTimeSummary: cumulative}).status, 'History is unavailable. Please try again shortly.');
const readyState = renderSummaryState({allTimeSummary: cumulative});
assert.equal(readyState.status, 'Recorded energy · kWh');
assert.equal(readyState.elements.filter(node => node.tag === 'h3').length, 6);
assert.equal(readyState.elements.filter(node => node.tag === 'td').length, 18, 'Six values per month and grand total');
const legacyState = renderSummaryState({allTimeSummary: legacySummary});
assert.equal(legacyState.status, 'Recorded energy · kWh');
assert.equal(legacyState.elements.filter(node => node.textContent === 'Not available').length, 4, 'Legacy charge is unknown on card, months and grand total');
assert.equal(legacyState.elements.filter(node => node.tag === 'strong' && node.textContent === '—').length, 4);
console.log('All-time loading, empty, old-exporter and failed-refresh UI checks passed.');
