// Calendar-aligned comparisons of recorded data. No estimation of missing usage.
(function (root) {
  'use strict';
  const METRICS = ['house', 'solar', 'grid', 'soc'];
  const KEYS = {house: 'load', solar: 'pv', grid: 'grid', charged: 'battery_charged', supplied: 'battery'};
  const valid = Number.isFinite;
  function shiftDate(iso, offset) {
    const d = new Date(`${iso}T12:00:00Z`);
    d.setUTCDate(d.getUTCDate() + offset);
    return d.toISOString().slice(0, 10);
  }
  function athensDay(now = new Date()) {
    const parts = new Intl.DateTimeFormat('en-GB', {timeZone: 'Europe/Athens', year: 'numeric', month: '2-digit', day: '2-digit'}).formatToParts(now);
    const field = key => parts.find(item => item.type === key).value;
    return `${field('year')}-${field('month')}-${field('day')}`;
  }
  function normalize(payload, today = athensDay()) {
    if (payload?.version !== 1 || payload.completed_before !== today || !Array.isArray(payload.days) || payload.days.length !== 60) throw new Error('Trends snapshot unavailable');
    return payload.days.map((entry, index) => {
      const date = shiftDate(today, index - 60);
      if (entry?.day !== date || ![82800, 86400, 90000].includes(entry.seconds)) throw new Error('Invalid daily trend');
      const row = {date, seconds: entry.seconds, coverage: {}};
      for (const [key, name] of Object.entries(KEYS)) {
        const metric = entry.totals?.[name];
        const ok = valid(metric?.kwh) && metric.kwh >= 0 && valid(metric.seconds) && metric.seconds > 0 && metric.seconds <= entry.seconds;
        row[key] = ok ? metric.kwh : null;
        row.coverage[key] = ok ? metric.seconds / entry.seconds * 100 : 0;
      }
      const soc = entry.soc;
      const ok = valid(soc?.minimum) && valid(soc.maximum) && soc.minimum >= 0 && soc.maximum <= 100 && soc.maximum >= soc.minimum && valid(soc.seconds) && soc.seconds > 0 && soc.seconds <= entry.seconds;
      row.minSoc = ok ? soc.minimum : null;
      row.maxSoc = row.soc = ok ? soc.maximum : null;
      row.coverage.soc = ok ? soc.seconds / entry.seconds * 100 : 0;
      return row;
    });
  }
  function compare(current, previous) {
    if (!valid(current) || !valid(previous)) return {delta: null, percent: null};
    return {delta: current - previous, percent: previous === 0 ? null : (current - previous) / previous * 100};
  }
  function changeTone(key, metric) {
    if (!['house', 'solar', 'grid'].includes(key) || metric.comparable === false || !valid(metric.percent) || Math.abs(metric.percent) < .05) return 'neutral';
    if (Math.abs(metric.percent) <= 5) return 'small';
    return (key === 'solar' ? metric.percent > 0 : metric.percent < 0) ? 'favorable' : 'unfavorable';
  }
  function roundedDelta(current, previous) {
    return valid(current) && valid(previous) ? Math.round((current - previous) * 10) / 10 || 0 : null;
  }
  function summarize(rows, key) {
    const known = rows.filter(row => valid(row[key]));
    const seconds = rows.reduce((sum, row) => sum + row.seconds, 0);
    return {total: known.length ? known.reduce((sum, row) => sum + row[key], 0) / (key === 'soc' ? known.length : 1) : null,
      coverage: seconds ? rows.reduce((sum, row) => sum + row.seconds * row.coverage[key], 0) / seconds : 0,
      complete: known.length === rows.length && rows.every(row => row.coverage[key] >= 95)};
  }
  function buildComparison(days, rows) {
    if (![1, 7, 14, 30].includes(days) || rows.length !== 60) throw new RangeError('Invalid comparison period');
    const current = rows.slice(-days), previous = rows.slice(-days * 2, -days);
    const metrics = Object.fromEntries([...METRICS, 'charged', 'supplied'].map(key => {
      const now = summarize(current, key), before = summarize(previous, key);
      const comparable = now.complete && before.complete;
      return [key, {now, before, comparable, ...compare(comparable ? now.total : null, comparable ? before.total : null)}];
    }));
    return {days, current, previous, metrics};
  }
  function dailyDelta(now, before, key) {
    return now.coverage[key] >= 95 && before.coverage[key] >= 95 ? roundedDelta(now[key], before[key]) : null;
  }
  function extreme(rows, key, maximum) {
    if (!rows.every(row => valid(row[key]) && row.coverage[key] >= 95)) return null;
    return rows.reduce((a, b) => (maximum ? a[key] > b[key] : a[key] < b[key]) ? a : b);
  }
  function ratio(data, which) {
    const a = data.metrics.grid[which], b = data.metrics.house[which];
    return a.complete && b.complete && b.total > 0 ? a.total / b.total * 100 : null;
  }
  const api = {METRICS, shiftDate, athensDay, normalize, compare, changeTone, roundedDelta, summarize, buildComparison, dailyDelta, extreme, ratio};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.EnergyTrendsData = api;
})(globalThis);
