// No network or dependencies: exercise the production queue with controlled requests.
const assert = require('node:assert/strict');
const { HistoryDataCache } = require('../history-cache.js');
const tick = () => new Promise(resolve => setImmediate(resolve));
const day = n => `2026-10-${String(n).padStart(2, '0')}`;
function harness(concurrency) {
  const pending = [], started = [];
  let active = 0, maximum = 0;
  const cache = new HistoryDataCache({ concurrency, storage: { get: async () => null, put: async () => {} },
    fetcher: (url, { signal }) => new Promise((resolve, reject) => {
      active++; maximum = Math.max(maximum, active); started.push(url);
      let done = false;
      const finish = fn => { if (done) return; done = true; active--; signal.removeEventListener('abort', abort); fn(); };
      const abort = () => finish(() => reject(signal.reason));
      signal.addEventListener('abort', abort, { once: true });
      pending.push(() => finish(() => resolve(new Response(JSON.stringify({day: url.slice(9, 19), points: []})))));
    }) });
  return { cache, pending, started, get maximum() { return maximum; } };
}
async function main() {
  // Exercise the tuned default, supported overrides and the existing hard cap.
  for (const [configured, expected] of [[undefined, 8], [1, 1], [4, 4], [6, 6], [8, 8], [32, 8], [0, 8], [NaN, 8], [-1, 1], [2.9, 2]]) {
    const month = harness(configured);
    const requests = Array.from({length: 31}, (_, i) => month.cache.get(day(i + 1)));
    await tick();
    assert.equal(month.started.length, expected, 'Only the configured limit may begin together');
    month.pending.shift()(); await tick();
    assert.equal(month.started.length, expected + 1, 'One freed slot immediately starts the next queued day');
    assert.equal(month.cache.active, expected);
    while (month.cache.active || month.cache.queue.length) { month.pending.splice(0).forEach(resolve => resolve()); await tick(); }
    const results = await Promise.all(requests);
    assert.equal(month.maximum, expected); assert.equal(month.started.length, 31);
    assert.deepEqual(results.map(result => result.day), Array.from({length: 31}, (_, i) => day(i + 1)));
    assert.equal(month.cache.active, 0); assert.equal(month.cache.queue.length, 0);
  }

  const obsolete = harness(), range = new AbortController();
  const old = Array.from({length: 31}, (_, i) => obsolete.cache.get(day(i + 1), {signal: range.signal}));
  const outcomes = Promise.allSettled(old);
  await tick(); range.abort(); await outcomes; await tick();
  assert.equal(obsolete.started.length, 8, 'The remaining 23 obsolete days must never start');
  assert.equal(obsolete.cache.active, 0); assert.equal(obsolete.cache.queue.length, 0);
  const fresh = obsolete.cache.get(day(12)); await tick(); obsolete.pending.splice(0).forEach(resolve => resolve());
  assert.equal((await fresh).day, day(12), 'An aborted day can be requested again');

  const shared = harness(), a = new AbortController(), b = new AbortController();
  const first = shared.cache.get(day(1), {signal: a.signal});
  const second = shared.cache.get(day(1), {signal: b.signal});
  const rejected = assert.rejects(first, {name: 'AbortError'});
  await tick(); a.abort(); await rejected;
  assert.equal(shared.cache.active, 1, 'A remaining consumer keeps its request alive');
  shared.pending.shift()(); assert.equal((await second).day, day(1));
  assert.equal(shared.started.length, 1);
  b.abort(); assert.equal((await shared.cache.get(day(1))).day, day(1), 'Completed entries stay cached');
  const cancelled = new AbortController(); cancelled.abort();
  await assert.rejects(shared.cache.get(day(2), {signal: cancelled.signal}), {name: 'AbortError'});
  assert.equal(shared.started.length, 1);
  console.log('History queue: default/hard cap of 8; 23 obsolete queued reads avoided; overrides, slot refill, shared cancellation and retry passed.');
}
main().catch(error => { console.error(error); process.exitCode = 1; });
