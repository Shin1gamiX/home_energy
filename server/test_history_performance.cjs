'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../history.js'), 'utf8');
function functionSource(name) {
  const start = source.indexOf(`function ${name}(`);
  assert.ok(start >= 0);
  return source.slice(start, source.indexOf('\n}', start) + 2);
}
const context = { range: { from: 0, to: 86400 }, rawRows: [], snapshotTime: 86400,
  aggregationCache: { rows: null, steps: new Map() }, summaryCache: null, metrics: { pv: {}, load: {} } };
vm.createContext(context);
for (const name of ['needsSampleMarker', 'aggregate', 'energyTotals', 'aggregatedRows', 'summaryTotals']) vm.runInContext(functionSource(name), context);
const rows = Array.from({ length: 1440 }, (_, i) => ({ t: i * 60, values: { pv: i }, counts: { pv: 1 } }));
assert.equal(rows.filter((row, i) => context.needsSampleMarker(rows, i, 'pv', 60)).length, 0);
assert.ok(rows.slice(0, 8).every((row, i, sparse) => context.needsSampleMarker(sparse, i, 'pv', 60)));
const isolated = structuredClone(rows);
delete isolated[19].values.pv; delete isolated[21].values.pv;
assert.equal(context.needsSampleMarker(isolated, 20, 'pv', 60), true, 'Isolated readings must still be visible');
assert.equal(context.needsSampleMarker(isolated, 18, 'pv', 60), false, 'Connected segment endpoint is already visible');
const gap = [{ t: -120, values: { pv: 5 } }, ...rows.map(row => ({ ...row, t: row.t + 120 }))];
assert.equal(context.needsSampleMarker(gap, 0, 'pv', 60), true, 'A temporal gap is not connected');

context.rawRows = rows;
const first = context.aggregatedRows(900);
assert.equal(context.aggregatedRows(900), first, 'Appearance/filter changes reuse averages');
const minutes = context.aggregatedRows(60);
assert.notEqual(first, minutes); assert.equal(minutes.length, 1440);
assert.equal(context.aggregatedRows(900), first, 'Averaging selections reuse their own results');
assert.equal(first[0].values.pv, 7);
context.rawRows = [{ t: 0, values: { pv: 100 }, counts: { pv: 3 } }, { t: 60, values: { pv: 500 }, counts: { pv: 1 } }];
assert.equal(context.aggregatedRows(900)[0].values.pv, 200, 'Weights and raw precision are unchanged');
const summary = context.summaryTotals();
assert.equal(context.summaryTotals(), summary);
context.snapshotTime = 30;
assert.notEqual(context.summaryTotals(), summary, 'A new snapshot invalidates totals');
assert.equal(context.summaryTotals().pv.kwh, 100 / 120000);
context.range = { from: 60, to: 120 };
assert.equal(context.aggregatedRows(60).length, 1);
assert.equal(context.aggregatedRows(60)[0].values.pv, 500);
assert.equal(context.summaryTotals().pv.seconds, 0, 'Range and cutoff remain independent');
console.log('History rendering: dense/sparse/isolated markers, weighted aggregation reuse and summary invalidation passed.');

const fallback = { window: {}, json: url => url };
vm.createContext(fallback);
vm.runInContext(functionSource('createHistoryDataCache'), fallback);
assert.equal(fallback.createHistoryDataCache().get('2026-10-05'), '/history/2026-10-05.json');
assert.doesNotThrow(() => fallback.createHistoryDataCache().invalidate());
fallback.window.HistoryDataCache = class {};
assert.ok(fallback.createHistoryDataCache() instanceof fallback.window.HistoryDataCache);
console.log('History remains available when older HTML has not loaded the optional cache helper.');
