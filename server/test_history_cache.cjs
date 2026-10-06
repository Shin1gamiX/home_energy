'use strict';
const assert = require('node:assert/strict');
const { HistoryDataCache, IndexedDayStore, validRecord, evictionKeys, MAX_DAYS, MAX_BYTES, MAX_AGE } = require('../history-cache.js');

async function main() {
  const day = '2026-10-05', now = 1791288000000;
  let payload = { day, points: [{ t: 1791151200, values: { pv: 42 }, counts: { pv: 2 } }], modes: [] };
  let etag = 'W/"first"', calls = [], mode = 'normal';
  const records = new Map();
  const storage = { get: async key => structuredClone(records.get(key)), put: async record => { records.set(record.day, structuredClone(record)); } };
  const fetcher = async (url, options) => {
    calls.push({ url, ...options });
    assert.equal(options.cache, 'no-store');
    assert.ok(options.signal instanceof AbortSignal);
    if (mode === 'offline') throw new Error('Offline');
    if (mode === '404') return new Response('missing', { status: 404 });
    if (mode === 'corrupt') return new Response('{', { status: 200 });
    if (mode === 'bare304') return new Response(null, { status: 304 });
    if (etag && options.headers['If-None-Match'] === etag) return new Response(null, { status: 304 });
    return new Response(JSON.stringify(payload), { headers: etag ? { ETag: etag } : {} });
  };
  const create = () => new HistoryDataCache({ storage, fetcher, now: () => now });
  let cache = create();
  const [one, two] = await Promise.all([cache.get(day), cache.get(day)]);
  assert.deepEqual(one, payload); assert.equal(one, two); assert.equal(calls.length, 1, 'Deduplicate concurrent requests');
  assert.ok(records.has(day));
  assert.deepEqual(calls[0].headers, {});
  cache = create();
  assert.deepEqual(await cache.get(day), payload);
  assert.equal(calls.at(-1).headers['If-None-Match'], etag, 'A new page validates its persisted copy');
  const afterValidation = calls.length;
  await cache.get(day);
  assert.equal(calls.length, afterValidation, 'In-page navigation retains snapshot semantics');
  cache.invalidate();
  await cache.get(day);
  assert.equal(calls.length, afterValidation + 1, 'Refresh checks the server, not just the cache');
  etag = '"corrected"'; payload.points[0].values.pv = 99;
  cache.invalidate();
  assert.equal((await cache.get(day)).points[0].values.pv, 99, 'Old day corrections replace cached data');

  for (mode of ['offline', '404', 'corrupt']) {
    cache.invalidate();
    if (mode === 'corrupt') etag = '"another"';
    await assert.rejects(cache.get(day), 'Failed validation must never silently return saved data');
  }
  mode = 'normal';
  assert.equal((await cache.get(day)).points[0].values.pv, 99, 'A failed request remains retryable');

  const valid = records.get(day);
  for (const change of [record => { record.version = 7; }, record => { record.etag = 'bad\r\nheader'; },
    record => { record.usedAt = now - MAX_AGE - 1; }, record => { record.usedAt = now + 1; },
    record => { record.data.day = 'wrong'; }, record => { record.data.points = null; },
    record => { record.bytes = MAX_BYTES + 1; }]) {
    const corrupt = structuredClone(valid); change(corrupt); records.set(day, corrupt);
    assert.equal(validRecord(corrupt, day, now), false);
    assert.deepEqual(await create().get(day), payload);
    assert.deepEqual(calls.at(-1).headers, {}, 'Invalid stored data is replaced, not conditionally reused');
  }
  const noStorage = { get: async () => { throw new Error('Private mode'); }, put: async () => { throw new Error('Quota'); } };
  assert.deepEqual(await new HistoryDataCache({ storage: noStorage, fetcher }).get(day), payload);
  records.clear(); etag = null;
  assert.deepEqual(await create().get(day), payload);
  assert.equal(records.size, 0, 'Without validators, use normal network loading');
  mode = 'bare304';
  await assert.rejects(create().get(day), '304 without a valid saved payload is not data');
  await assert.rejects(create().get('../api/collector/status'));
  assert.ok(calls.every(call => call.url === '/history/2026-10-05.json'), 'Only the selected daily public route was fetched');

  const entries = Array.from({ length: 40 }, (_, index) => ({ day: String(index), bytes: 100, usedAt: now - index }));
  const record = { day, bytes: 100, usedAt: now };
  assert.deepEqual(evictionKeys(entries, record), entries.slice(MAX_DAYS - 1).map(item => item.day));
  assert.equal(evictionKeys(entries, { ...record, bytes: MAX_BYTES }).length, entries.length);
  assert.deepEqual(evictionKeys([{ day: 'old', bytes: 1, usedAt: now - MAX_AGE - 1 }], record), ['old']);
  assert.deepEqual(evictionKeys([{ day: 'invalid', bytes: NaN, usedAt: now }], record), ['invalid']);
  assert.deepEqual(evictionKeys([{ ...record, bytes: MAX_BYTES }], record), [], 'An update does not double-count its old payload');
  const bounded = new HistoryDataCache({ storage: noStorage, fetcher: async url => new Response(JSON.stringify({ day: url.slice(9, 19), points: [] })) });
  for (let i = 1; i <= 31; i++) await bounded.get(`2026-09-${String(i).padStart(2, '0')}`);
  for (let i = 1; i <= 10; i++) await bounded.get(`2026-10-${String(i).padStart(2, '0')}`);
  assert.equal(bounded.days.size, MAX_DAYS);
  assert.equal(bounded.days.has('2026-09-01'), false);
  assert.equal(await new IndexedDayStore(null).get(day), null);
  assert.equal(await new IndexedDayStore({ open() { throw new Error('Blocked'); } }).get(day), null);
  assert.equal(await new IndexedDayStore({ open: () => ({}) }).get(day), null, 'Blocked storage has a bounded timeout');
  console.log('History cache: validation, 304, corrections, refresh, failure recovery, storage fallback and limits passed.');
}
main().catch(error => { console.error(error); process.exitCode = 1; });
