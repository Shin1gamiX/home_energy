'use strict';

// Only public daily history belongs here. No index, live readings, credentials
// or control requests are persisted. A cached day is never used without HTTP
// validation after opening the page or explicitly refreshing the snapshot.
(function (root) {
  const MAX_DAYS = 32;
  const MAX_BYTES = 32 * 1024 * 1024; // JSON payload budget; browser storage adds overhead.
  const MAX_AGE = 60 * 86400000;
  const STORAGE_TIMEOUT = 1000;

  function validDay(data, day) {
    return data?.day === day && Array.isArray(data.points)
      && data.points.every(row => Number.isFinite(row?.t) && row.values && typeof row.values === 'object')
      && (data.modes === undefined || (Array.isArray(data.modes) && data.modes.every(row =>
        Number.isFinite(row?.start) && Number.isFinite(row.end) && row.end >= row.start && typeof row.state === 'string')));
  }

  function validRecord(record, day, now) {
    return record?.version === 1 && typeof record.etag === 'string' && record.etag.length > 0
      && record.etag.length <= 256 && !/[\r\n]/.test(record.etag)
      && Number.isFinite(record.usedAt) && record.usedAt <= now && now - record.usedAt <= MAX_AGE
      && Number.isFinite(record.bytes) && record.bytes > 0 && record.bytes <= MAX_BYTES
      && validDay(record.data, day);
  }

  function evictionKeys(entries, record) {
    let bytes = record.bytes, count = 1;
    return entries.filter(item => item.day !== record.day).sort((a, b) => b.usedAt - a.usedAt)
      .filter(item => {
        if (!Number.isFinite(item.usedAt) || record.usedAt - item.usedAt > MAX_AGE || item.usedAt > record.usedAt
            || !Number.isFinite(item.bytes) || item.bytes <= 0 || count >= MAX_DAYS || bytes + item.bytes > MAX_BYTES) return true;
        count++; bytes += item.bytes;
        return false;
      }).map(item => item.day);
  }

  class IndexedDayStore {
    constructor(indexedDB) {
      try { this.indexedDB = indexedDB === undefined ? root.indexedDB : indexedDB; } catch { this.indexedDB = null; }
      this.opening = null;
      this.disabled = false;
    }

    open() {
      if (this.disabled || !this.indexedDB) return Promise.resolve(null);
      if (this.opening) return this.opening;
      this.opening = new Promise(resolve => {
        let finished = false;
        const finish = db => {
          if (finished) { db?.close(); return; }
          finished = true;
          clearTimeout(timer);
          if (!db) this.disabled = true;
          resolve(db);
        };
        const timer = setTimeout(() => finish(null), STORAGE_TIMEOUT);
        try {
          const request = this.indexedDB.open('homeenergy-history-v1', 1);
          request.onupgradeneeded = () => {
            const db = request.result;
            db.createObjectStore('days', { keyPath: 'day' });
            db.createObjectStore('metadata', { keyPath: 'day' });
          };
          request.onerror = request.onblocked = () => finish(null);
          request.onsuccess = () => {
            const db = request.result;
            db.onversionchange = () => { db.close(); this.disabled = true; };
            finish(db);
          };
        } catch { finish(null); }
      });
      return this.opening;
    }

    async transaction(mode, action) {
      const db = await this.open();
      if (!db || this.disabled) return null;
      return new Promise(resolve => {
        let tx, result = null, finished = false;
        const finish = value => {
          if (finished) return;
          finished = true;
          clearTimeout(timer);
          resolve(value);
        };
        const timer = setTimeout(() => {
          this.disabled = true;
          try { tx?.abort(); } catch {}
          finish(null);
        }, STORAGE_TIMEOUT);
        try {
          tx = db.transaction(['days', 'metadata'], mode);
          tx.oncomplete = () => finish(result);
          tx.onerror = tx.onabort = () => { this.disabled = true; finish(null); };
          action(tx, value => { result = value; });
        } catch { this.disabled = true; finish(null); }
      });
    }

    get(day) {
      return this.transaction('readonly', (tx, done) => {
        tx.objectStore('days').get(day).onsuccess = event => done(event.target.result);
      });
    }

    put(record) {
      // Read only tiny metadata for eviction, not every stored day's readings.
      // Eviction and insertion share a transaction, including across open tabs.
      return this.transaction('readwrite', tx => {
        const days = tx.objectStore('days'), metadata = tx.objectStore('metadata');
        metadata.getAll().onsuccess = event => {
          for (const day of evictionKeys(event.target.result, record)) { days.delete(day); metadata.delete(day); }
          days.put(record);
          metadata.put({ day: record.day, bytes: record.bytes, usedAt: record.usedAt });
        };
      });
    }
  }

  class HistoryDataCache {
    constructor({ storage = new IndexedDayStore(), fetcher = (...args) => root.fetch(...args), now = Date.now, concurrency = 4 } = {}) {
      this.storage = storage;
      this.fetcher = fetcher;
      this.now = now;
      this.days = new Map();
      this.concurrency = Math.max(1, Math.min(8, Math.floor(concurrency) || 4));
      this.queue = [];
      this.active = 0;
    }

    invalidate() {
      // Keep persisted payloads for conditional requests, never trust the old
      // in-page snapshot after Refresh. In-flight loads cannot reinsert entries.
      this.days.clear();
    }

    get(day, { signal } = {}) {
      if (!/^\d{4}-\d{2}-\d{2}$/.test(day)) return Promise.reject(new Error('Invalid history day'));
      if (signal?.aborted) return Promise.reject(signal.reason);
      let entry = this.days.get(day);
      if (!entry) {
        entry = { day, controller: new AbortController(), consumers: 0, keepAlive: false, settled: false, started: false };
        entry.promise = new Promise((resolve, reject) => { entry.resolve = resolve; entry.reject = reject; });
        this.queue.push(entry);
      }
      this.days.delete(day); this.days.set(day, entry);
      while (this.days.size > MAX_DAYS) this.days.delete(this.days.keys().next().value);
      const result = this.subscribe(entry, signal);
      this.pump();
      return result;
    }

    subscribe(entry, signal) {
      if (!signal) { entry.keepAlive = true; return entry.promise; }
      entry.consumers++;
      return new Promise((resolve, reject) => {
        let finished = false;
        const finish = (callback, value) => {
          if (finished) return;
          finished = true; signal.removeEventListener('abort', abort); entry.consumers--;
          callback(value);
        };
        const abort = () => {
          finish(reject, signal.reason);
          // Cancelling one view must not cancel another view's shared day.
          if (!entry.settled && !entry.consumers && !entry.keepAlive) {
            if (this.days.get(entry.day) === entry) this.days.delete(entry.day);
            entry.controller.abort();
            if (!entry.started) {
              this.queue = this.queue.filter(job => job !== entry);
              entry.settled = true; entry.reject(entry.controller.signal.reason);
            }
          }
        };
        signal.addEventListener('abort', abort, { once: true });
        entry.promise.then(value => finish(resolve, value), error => finish(reject, error));
      });
    }

    pump() {
      while (this.active < this.concurrency && this.queue.length) {
        const entry = this.queue.shift();
        entry.started = true; this.active++;
        void this.run(entry);
      }
    }

    async run(entry) {
      const timeout = setTimeout(() => entry.controller.abort(new DOMException('History request timed out', 'TimeoutError')), 10000);
      try { entry.resolve(await this.load(entry.day, entry.controller.signal)); }
      catch (error) {
        if (this.days.get(entry.day) === entry) this.days.delete(entry.day);
        entry.reject(error);
      } finally {
        clearTimeout(timeout); entry.settled = true; this.active--; this.pump();
      }
    }

    async load(day, signal) {
      const stored = await this.storage.get(day).catch(() => null);
      signal.throwIfAborted();
      const cached = validRecord(stored, day, this.now()) ? stored : null;
      const response = await this.fetcher(`/history/${day}.json`, {
        cache: 'no-store', signal,
        headers: cached ? { 'If-None-Match': cached.etag } : {},
      });
      if (response.status === 304 && cached) {
        void this.storage.put({ ...cached, usedAt: this.now() }).catch(() => {});
        return cached.data;
      }
      if (!response.ok) throw new Error('History unavailable');
      const text = await response.text();
      const data = JSON.parse(text);
      if (!validDay(data, day)) throw new Error('Invalid history');
      const record = { version: 1, day, data, etag: response.headers.get('ETag'),
        usedAt: this.now(), bytes: new TextEncoder().encode(text).byteLength };
      if (validRecord(record, day, this.now())) void this.storage.put(record).catch(() => {});
      return data;
    }
  }

  root.HistoryDataCache = HistoryDataCache;
  if (typeof module !== 'undefined' && module.exports) module.exports = { HistoryDataCache, IndexedDayStore, validRecord, evictionKeys, MAX_DAYS, MAX_BYTES, MAX_AGE };
})(typeof window !== 'undefined' ? window : globalThis);
