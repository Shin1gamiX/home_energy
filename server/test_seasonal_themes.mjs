import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import { themeForDate, isInAnnualWindow } from '../themes/schedule.js';
import { createThemeController, startSeasonalThemes } from '../themes/theme-loader.js';

test('December begins and ends at Athens midnight, independently of visitor timezone', () => {
  for (const [instant, expected] of [
    ['2026-11-30T21:59:59.999Z', null],
    ['2026-11-30T22:00:00.000Z', 'christmas'],
    ['2026-12-31T21:59:59.999Z', 'christmas'],
    ['2026-12-31T22:00:00.000Z', null],
    ['2027-01-01T12:00:00Z', null],
    ['2027-11-30T22:00:00Z', 'christmas'],
    ['2030-12-31T22:00:00Z', null],
    ['2026-06-15T12:00:00Z', null],
  ]) assert.equal(themeForDate(new Date(instant))?.id ?? null, expected, instant);
  assert.equal(themeForDate(new Date('invalid')), null);
  assert.equal(isInAnnualWindow('01-05', '12-01', '01-07'), true);
  assert.equal(isInAnnualWindow('01-07', '12-01', '01-07'), false);
  assert.equal(isInAnnualWindow('04-05', '04-01', '04-10'), true);
  assert.equal(isInAnnualWindow('04-10', '04-01', '04-10'), false);
  assert.equal(isInAnnualWindow('12-15', '12-01', '12-01'), false);
});

function fixture() {
  let date = new Date('2026-11-30T21:59:59Z');
  const calls = [];
  const assets = { mount() { calls.push('mount'); return () => calls.push('unmount'); },
    remove() { calls.push('remove CSS'); } };
  const controller = createThemeController({ choose: () => themeForDate(date),
    load: async () => { calls.push('load'); return assets; },
    reportError: error => calls.push(error.message) });
  return { controller, calls, assets, choose: () => themeForDate(date),
    setDate(value) { date = new Date(value); } };
}

test('an open page activates once, removes everything in January, and works next December', async () => {
  const f = fixture();
  await f.controller.refresh();
  assert.deepEqual(f.calls, [], 'no seasonal downloads outside the window');
  f.setDate('2026-11-30T22:00:00Z');
  await f.controller.refresh(); await f.controller.refresh();
  assert.deepEqual(f.calls, ['load', 'mount']);
  f.setDate('2026-12-31T22:00:00Z');
  await f.controller.refresh();
  assert.deepEqual(f.calls.slice(-2), ['unmount', 'remove CSS']);
  f.setDate('2027-12-01T00:00:00Z');
  await f.controller.refresh();
  assert.deepEqual(f.calls.slice(-2), ['load', 'mount']);
  f.controller.destroy();
  assert.deepEqual(f.calls.slice(-2), ['unmount', 'remove CSS']);
});

test('slow December assets cannot activate after the window ends', async () => {
  const f = fixture();
  f.setDate('2026-12-31T21:59:59Z');
  let resolve;
  let loads = 0;
  const controller = createThemeController({ choose: f.choose,
    load: () => { loads++; return new Promise(done => { resolve = done; }); } });
  const pending = controller.refresh();
  const duplicate = controller.refresh();
  assert.equal(loads, 1);
  f.setDate('2026-12-31T22:00:00Z');
  await controller.refresh();
  resolve(f.assets);
  await Promise.all([pending, duplicate]);
  assert.deepEqual(f.calls, ['remove CSS']);
});

test('failed assets leave normal Overview usable and can recover on a later check', async () => {
  const f = fixture();
  f.setDate('2026-12-15T00:00:00Z');
  let attempts = 0;
  const errors = [];
  const controller = createThemeController({ choose: f.choose,
    load: async () => { if (++attempts === 1) throw new Error('offline'); return f.assets; },
    reportError: error => errors.push(error.message) });
  await controller.refresh();
  assert.deepEqual(errors, ['offline']);
  assert.deepEqual(f.calls, []);
  await controller.refresh();
  assert.deepEqual(f.calls, ['mount']);
});

test('destroying during an asset load discards the late result', async () => {
  const f = fixture(); f.setDate('2026-12-15T00:00:00Z');
  let resolve;
  const controller = createThemeController({ choose: f.choose,
    load: () => new Promise(done => { resolve = done; }) });
  const pending = controller.refresh(); controller.destroy(); resolve(f.assets);
  await pending;
  assert.deepEqual(f.calls, ['remove CSS']);
});

test('page resume checks immediately; background pages do not keep a polling timer', () => {
  const events = new Map(); const timers = new Map(); let checks = 0; let destroyed = false;
  const doc = { hidden: false, body: { classList: { contains: name => name === 'overview' } },
    addEventListener: (name, fn) => events.set(name, fn),
    removeEventListener: name => events.delete(name) };
  const win = { addEventListener: doc.addEventListener, removeEventListener: doc.removeEventListener,
    setTimeout(fn, delay) { assert.ok(delay > 0 && delay <= 60025); timers.set(1, fn); return 1; },
    clearTimeout: id => timers.delete(id) };
  const stop = startSeasonalThemes({ doc, win,
    controller: { refresh() { checks++; }, destroy() { destroyed = true; } } });
  assert.equal(checks, 1); assert.equal(timers.size, 1);
  doc.hidden = true; events.get('visibilitychange')(); assert.equal(timers.size, 0);
  doc.hidden = false; events.get('visibilitychange')(); assert.equal(checks, 3);
  events.get('pageshow')(); events.get('focus')(); assert.equal(checks, 5);
  stop(); assert.equal(events.size, 0); assert.equal(timers.size, 0); assert.equal(destroyed, true);
});

test('only Overview includes the loader; main data/control scripts stay separate', () => {
  const read = name => readFileSync(new URL('../' + name, import.meta.url), 'utf8');
  assert.match(read('index.html'), /type="module" src="themes\/theme-loader\.js\?v=/);
  for (const name of ['history.html', '404.html']) assert.doesNotMatch(read(name), /themes\/theme-loader/);
  for (const name of ['themes/theme-loader.js', 'themes/christmas/theme.js']) {
    assert.doesNotMatch(read(name), /localStorage|sessionStorage|document\.cookie|\/api\/|\/reports\/|\/history\//);
  }
});
