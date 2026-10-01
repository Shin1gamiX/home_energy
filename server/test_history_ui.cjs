// Exercise browser-independent history calculations with Node's built-in tools.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../history.js'), 'utf8');
function readFunction(name) {
  const start = source.indexOf(`function ${name}(`);
  assert.ok(start >= 0, `Missing function: ${name}`);
  const next = source.indexOf('\nfunction ', start + 1);
  const context = {};
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
