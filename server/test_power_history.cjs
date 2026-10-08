'use strict';
const assert = require('node:assert/strict');
const { windowAt, powerPoints, powerScale } = require('../power-history.js');
for (const instant of ['2026-10-09T00:00:00+03:00', '2026-10-25T08:00:00+02:00', '2026-03-29T08:00:00+03:00']) {
  const range = windowAt(Date.parse(instant) / 1000);
  assert.equal(range.to - range.from, 43200);
  assert.equal(range.days.length, 2);
}
const rows = [
  { t: 60, values: { battery: -100, pv: 0 }, counts: { battery: 1, pv: 1 } },
  { t: 61, values: { battery: -200 }, counts: { battery: 3 } },
  { t: 180, values: { battery: 200 }, counts: { battery: 1 } },
  { t: 240, values: { battery: 0 }, counts: { battery: 0 } },
];
assert.deepEqual(powerPoints(rows, 'battery', { from: 0, to: 240 }), [{ t: 60, value: -175 }, { t: 180, value: 200 }]);
assert.deepEqual(powerPoints(rows, 'pv', { from: 0, to: 240 }), [{ t: 60, value: 0 }]);
assert.deepEqual(powerPoints(rows, 'grid', { from: 0, to: 240 }), []);
assert.throws(() => powerPoints(rows, 'soc', { from: 0, to: 240 }));
for (const [min, max] of [[0, 0], [-500, 100], [0, 5000], [-1000, -100]]) {
  const scale = powerScale(min, max);
  assert.ok(scale.low <= min && scale.high >= max && scale.high > scale.low);
  assert.ok(scale.ticks.includes(0));
}
console.log('Power history range, aggregation, source isolation and axes passed.');
