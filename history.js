'use strict';
const { t } = window.energyI18n;
if (location.hostname === '127.0.0.1' && location.port === '8766') {
  const notice = document.createElement('p');
  notice.textContent = 'Design preview · simulated history, not live inverter data.';
  document.querySelector('.intro').append(notice);
}
const metrics = {
  pv: { get label() { return t('Solar total'); }, color: '#c39232', unit: 'W', group: 'power' },
  pv1_power: { get label() { return t('PV1 power'); }, color: '#b56610', unit: 'W', group: 'power' },
  pv2_power: { get label() { return t('PV2 power'); }, color: '#365dc1', unit: 'W', group: 'power' },
  pv1_voltage: { get label() { return t('PV1 voltage'); }, color: '#337eb9', unit: 'V', group: 'voltage' },
  pv2_voltage: { get label() { return t('PV2 voltage'); }, color: '#c27019', unit: 'V', group: 'voltage' },
  pv1_current: { get label() { return t('PV1 current'); }, color: '#b56610', unit: 'A', group: 'current' },
  pv2_current: { get label() { return t('PV2 current'); }, color: '#365dc1', unit: 'A', group: 'current' },
  grid: { get label() { return t('Grid (est.)'); }, color: '#8861ba', unit: 'W', group: 'power' },
  load: { get label() { return t('House'); }, color: '#168b8a', unit: 'W', group: 'power' },
  battery: { get label() { return t('Battery power'); }, color: '#d65c66', unit: 'W', group: 'power' },
  soc: { get label() { return t('Battery %'); }, color: '#80a57c', unit: '%', group: 'soc' },
  pv_voltage: { get label() { return t('PV voltage (legacy)'); }, color: '#337eb9', unit: 'V', group: 'voltage', legacy: true },
  grid_voltage: { get label() { return t('Grid voltage'); }, color: '#aa4e91', unit: 'V', group: 'voltage' },
  pv_current: { get label() { return t('Solar current'); }, color: '#c39232', unit: 'A', group: 'current', legacy: true },
  battery_current: { get label() { return t('Battery current (avg.)'); }, color: '#d65c66', unit: 'A', group: 'current' },
  load_current: { get label() { return t('House current'); }, color: '#168b8a', unit: 'A', group: 'current' },
};
// Generic voltage/current remain selectable for older recorded history.
const selected = new Set(Object.keys(metrics).filter(key => !['pv_voltage', 'pv_current'].includes(key)));
const timezone = 'Europe/Athens';
const dateInput = document.querySelector('#history-date');
const dateDisplay = document.querySelector('#date-display');
const statusElement = document.querySelector('#history-status');
const dateFormat = new Intl.DateTimeFormat('en-GB', { timeZone: timezone, day: '2-digit', month: '2-digit', year: 'numeric' });
const timeFormat = new Intl.DateTimeFormat('en-GB', { timeZone: timezone, hour: '2-digit', minute: '2-digit' });
const refreshButton = document.querySelector('#refresh-history');
const refreshCooldown = 5 * 60 * 1000;
let nextRefreshAt = 0;
let indexPromise;
const dayCache = new Map();
let rawRows = [];
let modeRows = [];
let modeRecordedFrom = null;
let showMode = true;
let modeSelectedTime = null;
let snapshotTime = 0;
let snapshotObservedAt = 0;
let zoom = null;
let inspectedTime = null;
let period = 'day';
let points = [];
let range;
let requestId = 0;
let loading = false;
let hasError = false;
const peaksToggle = document.querySelector('#show-peaks');
try { peaksToggle.checked = localStorage.getItem('homeenergy-show-peaks') !== 'false'; } catch {}

// Stable tie handling: retain the earliest interval with an equal value.
function findPeaks(rows, key) {
  let min = null, max = null;
  for (const point of rows) {
    const value = point.values[key];
    if (!Number.isFinite(value)) continue;
    if (!min || value < min.values[key]) min = point;
    if (!max || value > max.values[key]) max = point;
  }
  return min ? { min, max } : null;
}

function renderPeaks(card, area, svg, keys, x, y, width, inspect) {
  if (!peaksToggle.checked || loading || hasError) return;
  let highlightTimer;
  function selectPeak(point, key) {
    inspect(point.t);
    clearTimeout(highlightTimer);
    const series = [...svg.querySelectorAll('[data-series]')];
    for (const line of series) line.style.opacity = line.dataset.series === key ? '1' : '.18';
    // Raise the selected series so coincident lines cannot cover it.
    const chosen = series.find(line => line.dataset.series === key);
    if (chosen) svg.insertBefore(chosen, svg.querySelector('.cursor'));
    highlightTimer = setTimeout(() => {
      for (const line of series) { line.style.opacity = ''; svg.insertBefore(line, svg.querySelector('.cursor')); }
    }, 2500);
  }
  const summary = document.createElement('div');
  summary.className = 'peak-summary';
  const note = document.createElement('p');
  note.textContent = t('Peaks of displayed averages · visible range');
  summary.append(note);
  for (const key of keys) {
    const peaks = findPeaks(points, key);
    if (!peaks) continue;
    const row = document.createElement('div');
    row.className = 'peak-row';
    row.style.setProperty('--peak-color', metrics[key].color);
    const name = document.createElement('strong');
    name.textContent = metrics[key].label;
    row.append(name);
    for (const [kind, point] of Object.entries(peaks)) {
      const label = `${kind === 'max' ? '↑' : '↓'} ${t(kind === 'max' ? 'Max' : 'Min')} ${display(point.values[key], key)}`;
      const timestamp = `${dateFormat.format(point.t * 1000)} · ${timeFormat.format(point.t * 1000)}`;
      const button = document.createElement('button');
      button.type = 'button';
      button.textContent = label;
      button.title = timestamp;
      button.setAttribute('aria-label', `${metrics[key].label}: ${label} · ${timestamp}`);
      button.addEventListener('click', () => selectPeak(point, key));
      row.append(button);
      // Multi-series charts use the summary, avoiding ambiguous overlapping dots.
      if (keys.length !== 1 || (kind === 'max' && peaks.min.t === peaks.max.t)) continue;
      const position = { x: x(point.t), y: y(point.values[key]) };
      const marker = svgElement('g', { 'data-peak-time': point.t, class: 'peak-marker' });
      marker.append(svgElement('title', {}, `${metrics[key].label}: ${label} · ${timestamp}`));
      marker.append(svgElement('circle', { cx: position.x, cy: position.y, r: 10, fill: 'transparent' }));
      marker.append(svgElement('circle', { cx: position.x, cy: position.y, r: 4, fill: metrics[key].color, stroke: 'white', 'stroke-width': 2 }));
      marker.addEventListener('click', () => selectPeak(point, key));
      svg.append(marker);
      if (keys.length === 1) {
        const badge = button.cloneNode(true);
        badge.className = `peak-label peak-${kind}`;
        badge.style.setProperty('--peak-color', metrics[key].color);
        badge.style.left = `clamp(70px, ${position.x / width * 100}%, calc(100% - 70px))`;
        badge.style.top = `${position.y / 240 * 100}%`;
        badge.dataset.peakTime = point.t;
        badge.addEventListener('click', () => selectPeak(point, key));
        area.append(badge);
      }
    }
    summary.append(row);
  }
  if (summary.children.length > 1) card.append(summary);
}

function today() {
  const parts = new Intl.DateTimeFormat('en-CA', { timeZone: timezone, year: 'numeric', month: '2-digit', day: '2-digit' }).formatToParts(new Date());
  const part = name => parts.find(p => p.type === name).value;
  return `${part('year')}-${part('month')}-${part('day')}`;
}
function shift(day, count) {
  const date = new Date(day + 'T12:00:00Z');
  date.setUTCDate(date.getUTCDate() + count);
  return date.toISOString().slice(0, 10);
}
function midnight(day) {
  const utc = new Date(day + 'T00:00:00Z');
  const offset = new Intl.DateTimeFormat('en', { timeZone: timezone, timeZoneName: 'shortOffset' }).formatToParts(utc).find(p => p.type === 'timeZoneName').value;
  return utc.getTime() / 1000 - Number(offset.replace('GMT', '')) * 3600;
}
function getRange() {
  let start = dateInput.value;
  let end = shift(start, 1);
  if (period === 'week') {
    const weekday = new Date(start + 'T12:00:00Z').getUTCDay();
    start = shift(start, -((weekday + 6) % 7));
    end = shift(start, 7);
  } else if (period === 'month') {
    start = start.slice(0, 7) + '-01';
    const next = new Date(start + 'T12:00:00Z');
    next.setUTCMonth(next.getUTCMonth() + 1);
    end = next.toISOString().slice(0, 10);
  }
  return { start, end, from: midnight(start), to: midnight(end), step: period === 'day' ? 60 : period === 'week' ? 900 : 3600 };
}
function aggregate(rows, step) {
  const buckets = new Map();
  for (const row of rows) {
    if (!Number.isFinite(row.t) || row.t < range.from || row.t >= range.to) continue;
    const t = Math.floor(row.t / step) * step;
    const bucket = buckets.get(t) || { t, sums: {}, counts: {} };
    for (const key of Object.keys(metrics)) {
      const value = row.values?.[key];
      const count = row.counts?.[key];
      if (!Number.isFinite(value) || !Number.isFinite(count) || count <= 0) continue;
      bucket.sums[key] = (bucket.sums[key] || 0) + value * count;
      bucket.counts[key] = (bucket.counts[key] || 0) + count;
    }
    buckets.set(t, bucket);
  }
  return [...buckets.values()].sort((a, b) => a.t - b.t).map(b => ({ t: b.t, values: Object.fromEntries(Object.keys(b.sums).map(k => [k, b.sums[k] / b.counts[k]])) }));
}
async function json(url) {
  const response = await fetch(url, { cache: 'no-store', signal: AbortSignal.timeout(10000) });
  if (!response.ok) throw new Error('History unavailable');
  return response.json();
}
async function load({ refresh = false } = {}) {
  const id = ++requestId;
  if (refresh) { indexPromise = undefined; dayCache.clear(); }
  const previousRange = range;
  range = getRange();
  dateDisplay.value = dateInput.value.split('-').reverse().join('/');
  dateDisplay.setCustomValidity('');
  if (previousRange?.from !== range.from || previousRange?.to !== range.to || previousRange?.step !== range.step) {
    zoom = null;
    rawRows = [];
    modeRows = [];
    inspectedTime = null;
  }
  const current = range;
  document.querySelector('#range-label').textContent = `${dateFormat.format(current.from * 1000)} – ${dateFormat.format((current.to - 1) * 1000)}`;
  document.querySelector('#next').disabled = current.end > today();
  statusElement.textContent = t('Loading history…');
  loading = true;
  hasError = false;
  updateRefreshButton();
  if (!rawRows.length) render();
  try {
    indexPromise ??= json('/history/index.json').then(data => ({ data, observedAt: Date.now() / 1000 }))
      .catch(error => { indexPromise = undefined; throw error; });
    const { data: index, observedAt } = await indexPromise;
    if (!Array.isArray(index.days)) throw new Error('Invalid history');
    const days = [...new Set(index.days)].filter(d => /^\d{4}-\d{2}-\d{2}$/.test(d) && d >= current.start && d < current.end);
    const files = await Promise.all(days.map(day => {
      if (!dayCache.has(day)) dayCache.set(day, json(`/history/${day}.json`).catch(error => { dayCache.delete(day); throw error; }));
      return dayCache.get(day);
    }));
    if (id !== requestId) return;
    if (files.some(file => !Array.isArray(file.points))) throw new Error('Invalid history');
    rawRows = files.flatMap(file => file.points);
    modeRows = files.flatMap(file => Array.isArray(file.modes) ? file.modes : []);
    modeRecordedFrom = Number.isFinite(index.mode_recorded_from) ? index.mode_recorded_from : null;
    snapshotTime = Number.isFinite(index.updated_at) ? index.updated_at : Date.now() / 1000;
    snapshotObservedAt = observedAt;
    loading = false;
    if (refresh || !nextRefreshAt) nextRefreshAt = Date.now() + refreshCooldown;
    render();
  } catch {
    if (id !== requestId) return;
    loading = false;
    hasError = true;
    statusElement.textContent = t('History is unavailable. Please try again shortly.');
    render();
  } finally {
    if (id === requestId) updateRefreshButton();
  }
}
function updateRefreshButton() {
  const seconds = Math.max(0, Math.ceil((nextRefreshAt - Date.now()) / 1000));
  refreshButton.disabled = loading || seconds > 0;
  refreshButton.textContent = loading ? t('Refreshing…') : seconds ? `↻ ${t('Refresh')} ${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}` : `↻ ${t('Refresh')}`;
  const updated = document.querySelector('#history-updated');
  if (updated) updated.textContent = hasError ? t('History unavailable.') : snapshotObservedAt
    ? t('Snapshot loaded {time} · refresh to update', { time: timeFormat.format(snapshotObservedAt * 1000) }) : '';
}
function svgElement(name, attributes = {}, text) {
  const element = document.createElementNS('http://www.w3.org/2000/svg', name);
  for (const [key, value] of Object.entries(attributes)) element.setAttribute(key, value);
  if (text !== undefined) element.textContent = text;
  return element;
}
function display(value, key) {
  if (!Number.isFinite(value)) return t('No report');
  const kilo = metrics[key].unit === 'W' && Math.abs(value) >= 1000;
  return `${new Intl.NumberFormat(window.energyI18n.locale, { maximumFractionDigits: kilo ? 2 : 1 }).format(kilo ? value / 1000 : value)} ${kilo ? 'kW' : metrics[key].unit}`;
}
// Rounded, outward-facing bounds with zero on an actual labelled tick.
function chartScale(minimum, maximum) {
  const low = Math.min(0, minimum), high = Math.max(0, maximum);
  const extent = high - low || 1;
  const paddedLow = low < 0 ? low - extent * .05 : 0;
  const paddedHigh = high > 0 ? high + extent * .05 : low < 0 ? 0 : 1;
  const roughStep = (paddedHigh - paddedLow) / 5;
  const magnitude = 10 ** Math.floor(Math.log10(roughStep));
  const step = [1, 2, 2.5, 5, 10].find(n => n * magnitude >= roughStep) * magnitude;
  const first = Math.floor(paddedLow / step), last = Math.ceil(paddedHigh / step);
  const ticks = Array.from({ length: last - first + 1 }, (_, i) => Number(((first + i) * step).toPrecision(12)));
  return { low: ticks[0], high: ticks[ticks.length - 1], ticks };
}
function render() {
  peaksToggle.nextElementSibling.textContent = t('Show peaks');
  renderSummary();
  const scrollPosition = window.scrollY;
  const requestedView = zoom || range;
  const requestedSpan = requestedView.to - requestedView.from;
  const averaging = document.querySelector('#averaging').value;
  const automaticStep = zoom ? requestedSpan <= 2 * 86400 ? 60 : requestedSpan <= 10 * 86400 ? 900 : 3600 : range.step;
  const step = averaging === 'auto' ? automaticStep : Number(averaging);
  // Align the visible range to complete averaging intervals, including after zoom.
  const view = { from: Math.max(range.from, Math.floor(requestedView.from / step) * step), to: Math.min(range.to, Math.ceil(requestedView.to / step) * step) };
  const span = view.to - view.from;
  points = aggregate(rawRows, step).filter(p => p.t >= view.from && p.t < view.to);
  if (loading) statusElement.textContent = t('Loading history…');
  else if (hasError) statusElement.textContent = t('History is unavailable. Please try again shortly.');
  else statusElement.textContent = points.length ? step === 3600 ? t('Hourly averages') : step === 60 ? t('1-minute averages') : t('{n}-minute averages', { n: step / 60 }) : t('No readings for this period.');
  const container = document.querySelector('#charts');
  container.replaceChildren();
  const jumps = document.querySelector('#chart-jumps');
  jumps.replaceChildren();
  for (const [group, title] of Object.entries({ supply: 'House supply', power: 'Power', soc: 'Battery charge', voltage: 'Voltage', current: 'Current' })) {
    if (group === 'supply' ? !showMode : ![...selected].some(key => metrics[key].group === group)) continue;
    const link = document.createElement('a'); link.href = `#chart-${group}`; link.textContent = t(title); jumps.append(link);
  }
  if (showMode) renderModeTimeline(container, view);
  const inspectors = [];
  function inspectTogether(timestamp) {
    inspectedTime = Math.max(view.from, Math.min(view.to - step, Math.floor(timestamp / step) * step));
    for (const update of inspectors) update(inspectedTime);
  }
  if (!selected.size && !showMode) {
    container.textContent = t('Select one or more statistics to display their graphs.');
    return;
  }
  for (const [group, title] of Object.entries({ power: 'Power', soc: 'Battery charge', voltage: 'Voltage', current: 'Current' })) {
    const keys = Object.keys(metrics).filter(k => selected.has(k) && metrics[k].group === group);
    if (!keys.length) continue;
    const card = document.createElement('article');
    card.className = 'chart-card';
    card.id = `chart-${group}`;
    const heading = document.createElement('h2');
    heading.textContent = t(title);
    const cardHeader = document.createElement('div');
    cardHeader.className = 'chart-header';
    cardHeader.append(heading);
    const reset = document.createElement('button');
    reset.textContent = t('Reset zoom');
    reset.className = 'reset-zoom';
    reset.disabled = !zoom;
    reset.addEventListener('click', () => { zoom = null; render(); });
    cardHeader.append(reset);
    const description = document.createElement('p');
    description.textContent = keys.map(k => metrics[k].label).join(' · ');
    card.append(cardHeader, description);
    if (keys.includes('battery') || keys.includes('battery_current')) {
      const direction = document.createElement('p'); direction.className = 'battery-direction';
      direction.textContent = t('Battery: + charging · − discharging'); card.append(direction);
    }
    const area = document.createElement('div');
    area.className = 'chart-area';
    area.tabIndex = 0;
    area.setAttribute('role', 'group');
    area.setAttribute('aria-label', t('{title} chart. Use left and right arrows to inspect values.', { title: t(title) }));
    const width = Math.max(300, Math.min(900, document.documentElement.clientWidth - 90));
    const height = 240, left = 48, right = width - 12, top = 12, bottom = 210;
    const svg = svgElement('svg', { viewBox: `0 0 ${width} ${height}`, preserveAspectRatio: 'none', role: 'img', 'aria-label': t('{title} over time', { title: t(title) }) });
    let low = 0, high = group === 'soc' ? 100 : 0;
    for (const p of points) for (const k of keys) if (Number.isFinite(p.values[k])) { low = Math.min(low, p.values[k]); high = Math.max(high, p.values[k]); }
    const scale = group === 'soc' ? { low: 0, high: 100, ticks: [0, 25, 50, 75, 100] } : chartScale(low, high);
    ({ low, high } = scale);
    const x = t => left + (t - view.from) / span * (right - left);
    const y = v => bottom - (v - low) / (high - low) * (bottom - top);
    for (const value of scale.ticks) {
      svg.append(svgElement('line', { x1: left, x2: right, y1: y(value), y2: y(value), class: 'chart-grid' }));
      const unitScale = group === 'power' && Math.max(high, Math.abs(low)) >= 1000 ? 1000 : 1;
      const label = new Intl.NumberFormat(window.energyI18n.locale, { maximumFractionDigits: 6 }).format(value / unitScale);
      svg.append(svgElement('text', { x: left - 7, y: y(value) + 4, 'text-anchor': 'end', class: 'axis-label' }, label));
    }
    const unit = group === 'soc' ? '%' : group === 'voltage' ? 'V' : group === 'current' ? 'A' : Math.max(high, Math.abs(low)) >= 1000 ? 'kW' : 'W';
    svg.append(svgElement('text', { x: 0, y: 10, class: 'axis-label' }, unit));
    if (low < 0) svg.append(svgElement('line', { x1: left, x2: right, y1: y(0), y2: y(0), stroke: '#a8b9af', 'stroke-dasharray': '4 4' }));
    for (let i = 0; i <= 4; i++) {
      const t = view.from + span * i / 4;
      const label = !zoom && period === 'day' && i === 4 ? '24:00' : new Intl.DateTimeFormat('en-GB', span <= 86400 ? { timeZone: timezone, hour: '2-digit', minute: '2-digit' } : { timeZone: timezone, day: '2-digit', month: '2-digit' }).format(t * 1000);
      svg.append(svgElement('text', { x: x(t), y: 234, 'text-anchor': i === 0 ? 'start' : i === 4 ? 'end' : 'middle', class: 'axis-label' }, label));
    }
    for (const key of keys) {
      const series = svgElement('g', { 'data-series': key });
      let path = '', previous = null;
      for (const p of points) {
        if (!Number.isFinite(p.values[key])) { previous = null; continue; }
        path += `${previous !== null && p.t - previous <= step ? 'L' : 'M'}${x(p.t)},${y(p.values[key])} `;
        series.append(svgElement('circle', { cx: x(p.t), cy: y(p.values[key]), r: points.length < 10 ? 2.5 : 0.8, fill: metrics[key].color }));
        previous = p.t;
      }
      series.append(svgElement('path', { d: path, fill: 'none', stroke: metrics[key].color, 'stroke-width': 2, 'stroke-linejoin': 'round' }));
      svg.append(series);
    }
    const cursor = svgElement('line', { x1: left, x2: left, y1: top, y2: bottom, class: 'cursor', visibility: 'hidden' });
    svg.append(cursor);
    area.append(svg);
    const tip = document.createElement('div');
    tip.className = 'chart-tip';
    tip.setAttribute('aria-live', 'polite');
    tip.textContent = t(loading ? 'Loading…' : hasError ? 'History unavailable.' : points.length ? 'Hover or touch the chart to inspect an interval.' : 'No data collected in this period.');
    const byTime = new Map(points.map(p => [p.t, p]));
    let active = points.length ? points[points.length - 1].t : view.from;
    function updateInspection(t) {
      active = Math.max(view.from, Math.min(view.to - step, Math.floor(t / step) * step));
      cursor.setAttribute('x1', x(active)); cursor.setAttribute('x2', x(active)); cursor.setAttribute('visibility', 'visible');
      tip.replaceChildren();
      const time = document.createElement('strong');
      time.textContent = `${dateFormat.format(active * 1000)} · ${timeFormat.format(active * 1000)} – ${timeFormat.format((active + step) * 1000)}`;
      tip.append(time);
      for (const key of keys) {
        const item = document.createElement('span'); item.className = 'tip-value'; item.style.setProperty('--series', metrics[key].color);
        item.textContent = `${metrics[key].label}: ${display(byTime.get(active)?.values[key], key)}`;
        tip.append(item);
      }
    }
    inspectors.push(updateInspection);
    const inspect = inspectTogether;
    const selection = svgElement('rect', { y: top, height: bottom - top, class: 'zoom-selection', visibility: 'hidden' });
    svg.append(selection);
    let drag = null;
    const pointerTime = event => {
      const rect = svg.getBoundingClientRect();
      const px = (event.clientX - rect.left) / rect.width * width;
      return view.from + Math.max(0, Math.min(1, (px - left) / (right - left))) * span;
    };
    area.addEventListener('pointermove', event => {
      const peak = event.target.closest('[data-peak-time]');
      if (peak && !drag) { inspect(Number(peak.dataset.peakTime)); return; }
      const t = pointerTime(event);
      if (drag && event.pointerId === drag.id) {
        selection.setAttribute('x', x(Math.min(drag.time, t)));
        selection.setAttribute('width', Math.abs(x(t) - x(drag.time)));
        selection.setAttribute('visibility', 'visible');
      } else if (!drag) inspect(t);
    });
    area.addEventListener('pointerdown', event => {
      if (event.target.closest('[data-peak-time]')) return;
      if (event.button !== 0 || loading || hasError) return;
      drag = { id: event.pointerId, time: pointerTime(event), clientX: event.clientX };
      area.setPointerCapture(event.pointerId);
      inspect(drag.time);
    });
    area.addEventListener('pointerup', event => {
      if (!drag || event.pointerId !== drag.id) return;
      const start = drag;
      drag = null;
      if (area.hasPointerCapture(event.pointerId)) area.releasePointerCapture(event.pointerId);
      selection.setAttribute('visibility', 'hidden');
      const end = pointerTime(event);
      if (Math.abs(event.clientX - start.clientX) < 12) { inspect(end); return; }
      const from = Math.max(view.from, Math.floor(Math.min(start.time, end) / 60) * 60);
      const to = Math.min(view.to, Math.ceil(Math.max(start.time, end) / 60) * 60);
      if (to - from < 120) return;
      zoom = { from, to };
      render();
    });
    area.addEventListener('pointercancel', () => { drag = null; selection.setAttribute('visibility', 'hidden'); });
    area.addEventListener('keydown', event => {
      if (event.target.closest('[data-peak-time]')) return;
      if (['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) {
        event.preventDefault();
        inspect(event.key === 'Home' ? view.from : event.key === 'End' ? view.to - step : active + (event.key === 'ArrowLeft' ? -step : step));
      }
    });
    card.append(area, tip);
    renderPeaks(card, area, svg, keys, x, y, width, inspect);
    container.append(card);
  }
  if (inspectedTime !== null && inspectedTime >= view.from && inspectedTime < view.to) inspectTogether(inspectedTime);
  window.scrollTo({ top: scrollPosition, behavior: 'instant' });
}
// Integrate the original minute averages, never the zoomed or smoothed graph.
// A recorded minute represents up to 60 seconds; missing minutes contribute nothing.
function energyTotals(rows, from, to, cutoff) {
  const totals = Object.fromEntries(['pv', 'grid', 'load', 'battery'].map(key => [key, { kwh: 0, seconds: 0 }]));
  const solarToHouse = { kwh: 0, seconds: 0 };
  const minutes = new Map(rows.filter(row => Number.isFinite(row.t)).map(row => [row.t, row]));
  for (const row of minutes.values()) {
    const seconds = Math.max(0, Math.min(row.t + 60, to, cutoff) - Math.max(row.t, from));
    if (!seconds) continue;
    for (const key of Object.keys(totals)) {
      const reading = row.values?.[key];
      if (!Number.isFinite(reading)) continue;
      // Battery power is negative during discharge; charging must not offset usage.
      const watts = key === 'battery' ? Math.max(0, -reading) : reading;
      if (!Number.isFinite(watts) || watts < 0) continue;
      totals[key].kwh += watts * seconds / 3600000;
      totals[key].seconds += seconds;
    }
    const { load, grid, battery, pv } = row.values || {};
    // Compare matching intervals only. This assumes grid charging is disabled.
    if ([load, grid, battery, pv].every(Number.isFinite) && load >= 0 && grid >= 0 && pv >= 0) {
      // A residual is not independent solar metering. Bound every matching
      // interval by reported PV so estimator noise cannot invent night energy.
      const watts = Math.min(pv, Math.max(0, load - grid - Math.max(0, -battery)));
      solarToHouse.kwh += watts * seconds / 3600000;
      solarToHouse.seconds += seconds;
    }
  }
  totals.solar_to_house = solarToHouse;
  return totals;
}
function renderSummary() {
  const section = document.querySelector('#energy-summary');
  section.replaceChildren();
  const heading = document.createElement('h2');
  heading.textContent = t(period === 'day' ? 'Daily summary' : period === 'week' ? 'Weekly summary' : 'Monthly summary');
  section.append(heading);
  const cards = document.createElement('div'); cards.className = 'summary-cards';
  cards.tabIndex = 0;
  cards.setAttribute('aria-label', t('Energy summaries. Scroll to see more.'));
  const totals = energyTotals(rawRows, range.from, range.to, snapshotTime);
  const format = value => new Intl.NumberFormat(window.energyI18n.locale, { maximumFractionDigits: 2 }).format(value);
  for (const [key, name] of Object.entries({ pv: 'Solar generated', grid: 'Grid consumed', load: 'House usage', battery: 'Battery supplied', solar_to_house: 'Solar to house' })) {
    const card = document.createElement('article'); card.className = 'summary-card'; card.style.setProperty('--summary-color', metrics[key === 'solar_to_house' ? 'pv' : key].color);
    const label = document.createElement('h3'); label.textContent = t(name);
    const value = document.createElement('div'); value.className = 'summary-value';
    const number = document.createElement('strong'); number.textContent = !loading && !hasError && totals[key].seconds ? format(totals[key].kwh) : '—';
    const unit = document.createElement('span'); unit.textContent = ' kWh'; value.append(number, unit);
    const coverage = document.createElement('p');
    coverage.textContent = loading ? t('Loading…') : hasError ? t('History unavailable.') : totals[key].seconds ? t('{hours} h recorded', { hours: format(totals[key].seconds / 3600) }) : t('No report');
    card.append(label, value, coverage); cards.append(card);
  }
  const note = document.createElement('p'); note.className = 'summary-note';
  note.textContent = t('Estimated from recorded readings only; missing periods are excluded.');
  section.append(cards, note);
}
function historyCutoffs(view, observedAt, now) {
  const present = Math.min(view.to, now);
  return { observed: Math.max(view.from, Math.min(present, observedAt || view.from)), present };
}
function renderModeTimeline(container, view) {
  // Missing reports can only be inferred up to when this snapshot was fetched.
  // Re-rendering cached data must not manufacture an outage after that time.
  const bounds = historyCutoffs(view, snapshotObservedAt, Date.now() / 1000);
  const cutoff = bounds.observed;
  const states = { grid: ['Grid', '#8861ba'], mixed: ['Mixed', '#596cb0'], solar: ['Solar', '#c39232'], battery: ['Battery', '#d65c66'], standby: ['Standby', '#80a57c'], unknown: ['Unknown supply', '#869b98'], missing: ['No data', '#adb8b4'], unrecorded: ['Mode not recorded', '#c3ccc8'] };
  const segments = [];
  let cursor = view.from;
  function gap(end) {
    if (end <= cursor) return;
    const before = Math.min(end, modeRecordedFrom ?? end);
    if (before > cursor) segments.push({ start: cursor, end: before, state: 'unrecorded' });
    if (end > Math.max(cursor, before)) segments.push({ start: Math.max(cursor, before), end, state: 'missing' });
    cursor = end;
  }
  for (const row of [...modeRows].sort((a, b) => a.start - b.start)) {
    if (!Number.isFinite(row.start) || !Number.isFinite(row.end) || !states[row.state] || row.end <= cursor || row.start >= cutoff) continue;
    const start = Math.max(cursor, view.from, row.start), end = Math.min(cutoff, row.end);
    gap(start);
    if (end > start) segments.push({ start, end, state: row.state, observedStart: row.start, observedEnd: Math.min(cutoff, row.end) });
    cursor = Math.max(cursor, end);
  }
  gap(cutoff);
  const card = document.createElement('article'); card.className = 'chart-card mode-card'; card.id = 'chart-supply';
  const heading = document.createElement('h2'); heading.textContent = t('House supply');
  const note = document.createElement('p'); note.textContent = t('Estimated source supplying the house');
  const header = document.createElement('div'); header.className = 'chart-header'; header.append(heading);
  const controls = document.createElement('div'); controls.className = 'mode-controls';
  function setModeZoom(from, to) {
    zoom = { from: Math.max(range.from, from), to: Math.min(range.to, to) };
    render();
  }
  for (const [label, action] of [
    ['Zoom in', () => { const span = Math.max(120, (view.to - view.from) / 2); const center = modeSelectedTime !== null && modeSelectedTime >= view.from && modeSelectedTime < view.to ? modeSelectedTime : (view.from + Math.min(view.to, cutoff)) / 2; const from = Math.max(range.from, Math.min(range.to - span, center - span / 2)); setModeZoom(from, from + span); }],
    ['Reset zoom', () => { zoom = null; render(); }]
  ]) {
    const button = document.createElement('button'); button.type = 'button'; button.textContent = t(label);
    button.disabled = label === 'Reset zoom' ? !zoom : view.to - view.from <= 120;
    button.addEventListener('click', action); controls.append(button);
  }
  header.append(controls); card.append(header, note);
  if (loading || hasError || cutoff <= view.from) { const message = document.createElement('p'); message.textContent = t(loading ? 'Loading history…' : hasError ? 'History is unavailable. Please try again shortly.' : 'No readings for this period.'); card.append(message); container.append(card); return; }
  const keys = ['grid', 'mixed', 'solar', 'battery', ...['standby', 'unknown', 'unrecorded'].filter(key => segments.some(s => s.state === key)), 'missing'];
  const plot = document.createElement('div'); plot.className = 'mode-plot';
  const labels = document.createElement('div'); labels.className = 'mode-labels';
  const tracks = document.createElement('div'); tracks.className = 'mode-tracks'; tracks.style.height = `${keys.length * 44}px`;
  const guide = document.createElement('div'); guide.className = 'mode-guide'; guide.hidden = true;
  for (const key of keys) { const label = document.createElement('span'); label.textContent = t(states[key][0]); labels.append(label); }
  const detail = document.createElement('div'); detail.className = 'chart-tip mode-detail'; detail.setAttribute('aria-live', 'polite'); detail.textContent = t('Select an interval to see its times.');
  const secondsFormat = new Intl.DateTimeFormat('en-GB', { timeZone: timezone, day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
  function duration(seconds) {
    let remaining = Math.max(0, Math.round(seconds));
    const parts = [];
    for (const [size, label] of [[86400, '{n}d'], [3600, '{n}h'], [60, '{n}m'], [1, '{n}s']]) {
      const count = Math.floor(remaining / size);
      remaining %= size;
      if (count) parts.push(t(label, { n: count }));
    }
    return parts.join(' ') || t('{n}s', { n: 0 });
  }
  function inspect(segment, time = segment.start) {
    modeSelectedTime = time;
    guide.hidden = false; guide.style.left = `${(time - view.from) / (view.to - view.from) * 100}%`;
    for (const block of tracks.querySelectorAll('.mode-segment')) block.classList.toggle('is-selected', Number(block.dataset.start) <= time && time < Number(block.dataset.end));
    const title = document.createElement('strong'); title.textContent = t(states[segment.state][0]);
    title.style.color = states[segment.state][1];
    const fields = document.createElement('div'); fields.className = 'mode-detail-fields';
    const observedStart = segment.observedStart ?? segment.start, observedEnd = segment.observedEnd ?? segment.end;
    for (const [label, value] of [['Start', secondsFormat.format(observedStart * 1000)], ['End', secondsFormat.format(observedEnd * 1000)], ['Duration', duration(observedEnd - observedStart)]]) {
      const field = document.createElement('div'), caption = document.createElement('span'), valueElement = document.createElement('b');
      caption.textContent = t(label); valueElement.textContent = value; field.append(caption, valueElement); fields.append(field);
    }
    const navigation = document.createElement('div'); navigation.className = 'mode-controls';
    const index = segments.indexOf(segment);
    for (const [label, next] of [['Previous interval', index - 1], ['Next interval', index + 1]]) {
      const button = document.createElement('button'); button.type = 'button'; button.textContent = t(label); button.disabled = next < 0 || next >= segments.length;
      button.addEventListener('click', () => inspect(segments[next])); navigation.append(button);
    }
    detail.replaceChildren(title, fields, navigation);
  }
  plot.append(labels, tracks); card.append(plot); container.append(card);
  const span = view.to - view.from;
  const pixelSeconds = span / Math.max(1, tracks.clientWidth);
  // Group only visually indistinguishable consecutive intervals. Raw intervals
  // still drive totals and inspection, and are revealed by zooming the group.
  const groups = [];
  for (let i = 0; i < segments.length;) {
    const first = segments[i];
    const group = { start: first.start, end: first.end, items: [first] };
    i++;
    if (!['missing', 'unrecorded', 'unknown'].includes(first.state) && first.end - first.start < pixelSeconds * 6) {
      while (i < segments.length && !['missing', 'unrecorded', 'unknown'].includes(segments[i].state) && segments[i].end - segments[i].start < pixelSeconds * 6 && segments[i].start <= group.end + 0.001) {
        group.items.push(segments[i]); group.end = segments[i++].end;
      }
    }
    groups.push(group);
  }
  for (const group of groups) for (const state of new Set(group.items.map(item => item.state))) {
    const dense = group.items.length > 1;
    const block = document.createElement('button'); block.type = 'button'; block.className = 'mode-segment';
    block.dataset.start = group.start; block.dataset.end = group.end;
    block.style.left = `${(group.start - view.from) / span * 100}%`; block.style.width = `${(group.end - group.start) / span * 100}%`;
    block.style.top = `${keys.indexOf(state) * 44 + 11}px`; block.style.setProperty('--mode-color', states[state][1]);
    const description = dense ? `${t(states[state][0])} · ${t('{n} changes · select to zoom', { n: group.items.length - 1 })}` : t(states[state][0]);
    block.setAttribute('aria-label', `${description}: ${secondsFormat.format(group.start * 1000)} – ${secondsFormat.format(group.end * 1000)}`);
    block.title = description;
    if (dense) block.classList.add('is-dense');
    block.addEventListener('focus', () => inspect(group.items[0]));
    block.addEventListener('click', event => {
      event.stopPropagation();
      if (suppressModeClick) { suppressModeClick = false; return; }
      if (dense && span > 120) {
        const width = Math.max(120, Math.min(span / 2, (group.end - group.start) * 1.2));
        const center = (group.start + group.end) / 2;
        setModeZoom(center - width / 2, center + width / 2);
      }
      else inspect(group.items[0]);
    }); tracks.append(block);
  }
  function shade(start, end, label, className) {
    if (end <= start) return;
    const area = document.createElement('div'); area.className = `mode-future ${className}`;
    area.style.left = `${Math.max(0, (start - view.from) / span * 100)}%`;
    area.style.width = `${(end - start) / span * 100}%`; area.style.right = 'auto';
    area.title = t(label);
    if ((end - start) / span * tracks.clientWidth >= 80) area.textContent = t(label);
    tracks.append(area);
  }
  shade(cutoff, bounds.present, 'Not refreshed yet', 'mode-pending');
  shade(bounds.present, view.to, 'Upcoming', '');
  tracks.append(guide);
  const pointerTime = event => { const bounds = tracks.getBoundingClientRect(); return view.from + Math.max(0, Math.min(1, (event.clientX - bounds.left) / bounds.width)) * span; };
  let drag = null, suppressModeClick = false;
  const selection = document.createElement('div'); selection.className = 'mode-selection'; selection.hidden = true; tracks.append(selection);
  tracks.addEventListener('pointerdown', event => { if (event.button === 0) { suppressModeClick = false; drag = { x: event.clientX, time: pointerTime(event) }; } });
  tracks.addEventListener('pointermove', event => {
    const time = pointerTime(event);
    if (drag) { selection.hidden = false; selection.style.left = `${(Math.min(time, drag.time) - view.from) / span * 100}%`; selection.style.width = `${Math.abs(time - drag.time) / span * 100}%`; }
    else if (event.pointerType !== 'touch') { const segment = segments.find(s => s.start <= time && time < s.end); if (segment) inspect(segment, time); }
  });
  tracks.addEventListener('pointerup', event => {
    if (!drag) return;
    const start = drag; drag = null; selection.hidden = true;
    if (Math.abs(event.clientX - start.x) < 12) return;
    const end = pointerTime(event);
    if (Math.abs(end - start.time) < 120) return;
    suppressModeClick = true; setModeZoom(Math.min(start.time, end), Math.max(start.time, end));
  });
  for (const name of ['pointercancel', 'pointerleave']) tracks.addEventListener(name, () => { drag = null; selection.hidden = true; });
  tracks.addEventListener('click', event => { const time = pointerTime(event); const segment = segments.find(s => s.start <= time && time < s.end); if (segment) inspect(segment, time); });
  const axis = document.createElement('div'); axis.className = 'mode-axis';
  for (let i = 0; i <= 4; i++) { const tick = document.createElement('span'); const time = (view.from + span * i / 4) * 1000; tick.textContent = !zoom && period === 'day' && i === 4 ? '24:00' : span <= 86400 ? timeFormat.format(time) : new Intl.DateTimeFormat('en-GB', { timeZone: timezone, day: '2-digit', month: '2-digit' }).format(time); axis.append(tick); }
  const help = document.createElement('p'); help.className = 'mode-help'; help.textContent = t('Striped blocks contain multiple changes. Select to zoom, or drag across the timeline.');
  card.append(axis, help, detail);
  const totals = document.createElement('div'); totals.className = 'mode-totals';
  for (const key of keys) { const sum = segments.filter(s => s.state === key).reduce((a, s) => a + s.end - s.start, 0); if (!sum) continue; const item = document.createElement('span'); item.style.setProperty('--mode-color', states[key][1]); item.textContent = `${t(states[key][0])} · ${duration(sum)}`; totals.append(item); }
  const foot = document.createElement('p'); foot.className = 'mode-note'; foot.textContent = t('Observed transitions; timing depends on polling. Gaps start 90 seconds after the last report.');
  card.append(totals, foot); container.append(card);
  if (modeSelectedTime !== null) { const active = segments.find(s => s.start <= modeSelectedTime && modeSelectedTime < s.end); if (active) inspect(active, modeSelectedTime); }
}

function updateFilters() {
  const modeButton = document.querySelector('#mode-filter');
  if (modeButton) { modeButton.setAttribute('aria-pressed', String(showMode)); modeButton.textContent = `◷ ${t('House supply')}`; }
  document.querySelectorAll('[data-metric]').forEach(button => {
    const enabled = selected.has(button.dataset.metric);
    button.setAttribute('aria-pressed', String(enabled));
    button.querySelector('span').textContent = metrics[button.dataset.metric].label;
  });
  document.querySelectorAll('.filter-category').forEach(category => {
    const buttons = [...category.querySelectorAll('button')];
    const count = buttons.filter(button => button.getAttribute('aria-pressed') === 'true').length;
    category.querySelector('.filter-count').textContent = `${count}/${buttons.length}`;
  });
  render();
}
const filterContainers = {};
for (const [key, label] of Object.entries({ power: 'Power', battery: 'Battery', voltage: 'Voltage', current: 'Current', supply: 'House supply', legacy: 'Legacy readings' })) {
  const category = document.createElement('details'); category.className = 'filter-category';
  const summary = document.createElement('summary');
  const name = document.createElement('span'); name.dataset.i18n = label; name.textContent = t(label);
  const count = document.createElement('span'); count.className = 'filter-count';
  summary.append(name, count);
  const filters = document.createElement('div'); filters.className = 'filters';
  category.append(summary, filters); document.querySelector('.filter-groups').append(category);
  filterContainers[key] = filters;
}
for (const [key, metric] of Object.entries(metrics)) {
  const button = document.createElement('button');
  button.dataset.metric = key; button.style.setProperty('--series', metric.color);
  const icon = svgElement('svg', { viewBox: '0 0 24 24', width: 20, height: 20, fill: 'none', stroke: 'currentColor', 'stroke-width': 1.8, 'stroke-linecap': 'round', 'stroke-linejoin': 'round', 'aria-hidden': 'true' });
  const paths = {
    pv: 'M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1.5 1.5m11 11L19 19M5 19l1.5-1.5m11-11L19 5M16 12a4 4 0 1 1-8 0 4 4 0 0 1 8 0',
    grid: 'M8 22l4-20 4 20M6 8h12M5 13h14M9 18h6M9 8l6 5m-6 0 6 5',
    load: 'M3 11l9-8 9 8M5 10v11h14V10M9 21v-8h6v8',
    battery: 'M3 6h16v12H3zM21 10v4M6 9v6m4-6v6m4-6v6',
    soc: 'M3 6h16v12H3zM21 10v4M6 9v6m4-6v6m4-6v6',
    pv_voltage: 'M3 6l5 12 5-12M17 6v12m-3-6h6',
    grid_voltage: 'M8 22l4-20 4 20M6 8h12M5 13h14M9 18h6M9 8l6 5m-6 0 6 5',
  };
  const iconKey = /^pv[12]_/.test(key) ? (key.endsWith('_voltage') ? 'pv_voltage' : 'pv')
    : { pv_current: 'pv', battery_current: 'battery', load_current: 'load' }[key] || key;
  icon.append(svgElement('path', { d: paths[iconKey] }));
  const label = document.createElement('span'); label.textContent = metric.label;
  button.append(icon, label);
  button.addEventListener('click', () => { selected.has(key) ? selected.delete(key) : selected.add(key); updateFilters(); });
  const category = metric.legacy ? 'legacy' : ['battery', 'soc', 'battery_current'].includes(key) ? 'battery' : metric.group;
  filterContainers[category].append(button);
}
const modeButton = document.createElement('button');
modeButton.id = 'mode-filter'; modeButton.type = 'button'; modeButton.style.setProperty('--series', '#284e43');
modeButton.addEventListener('click', () => { showMode = !showMode; updateFilters(); });
filterContainers.supply.append(modeButton);
document.querySelector('#select-all').addEventListener('click', () => {
  showMode = true;
  selected.clear();
  Object.keys(metrics).filter(k => !metrics[k].legacy).forEach(k => selected.add(k));
  updateFilters();
});
document.querySelector('#clear-all').addEventListener('click', () => { showMode = false; selected.clear(); updateFilters(); });
document.querySelectorAll('[data-period]').forEach(button => button.addEventListener('click', () => {
  period = button.dataset.period;
  document.querySelectorAll('[data-period]').forEach(b => b.setAttribute('aria-pressed', String(b === button)));
  load();
}));
function move(direction) {
  if (period === 'month') {
    const date = new Date(range.start + 'T12:00:00Z'); date.setUTCMonth(date.getUTCMonth() + direction);
    dateInput.value = date.toISOString().slice(0, 10);
  } else dateInput.value = shift(dateInput.value, direction * (period === 'week' ? 7 : 1));
  if (dateInput.value > today()) dateInput.value = today();
  load();
}
document.querySelector('#previous').addEventListener('click', () => move(-1));
document.querySelector('#next').addEventListener('click', () => move(1));
document.querySelector('#today').addEventListener('click', () => {
  period = 'day';
  dateInput.max = today();
  dateInput.value = dateInput.max;
  dateDisplay.setCustomValidity('');
  zoom = null;
  inspectedTime = null;
  document.querySelectorAll('[data-period]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.period === period)));
  load();
});
dateInput.max = today(); dateInput.value = today();
dateInput.addEventListener('change', () => { if (dateInput.validity.valid && dateInput.value) load(); });
dateInput.addEventListener('click', () => { if (dateInput.showPicker) dateInput.showPicker(); });
function applyDisplayedDate() {
  const match = /^(\d{2})\/(\d{2})\/(\d{4})$/.exec(dateDisplay.value);
  const iso = match ? `${match[3]}-${match[2]}-${match[1]}` : '';
  const parsed = iso ? new Date(`${iso}T12:00:00Z`) : null;
  if (!parsed || !Number.isFinite(parsed.getTime()) || parsed.toISOString().slice(0, 10) !== iso || iso > today()) {
    dateDisplay.setCustomValidity(t('Enter a valid date as DD/MM/YYYY, not in the future.'));
    dateDisplay.reportValidity();
    return;
  }
  dateDisplay.setCustomValidity('');
  if (dateInput.value !== iso) { dateInput.value = iso; load(); }
}
dateDisplay.addEventListener('blur', applyDisplayedDate);
dateDisplay.addEventListener('keydown', event => { if (event.key === 'Enter') applyDisplayedDate(); });
dateDisplay.addEventListener('input', () => dateDisplay.setCustomValidity(''));
range = getRange(); updateFilters(); load();
document.querySelector('#averaging').addEventListener('change', render);
peaksToggle.addEventListener('change', () => {
  try { localStorage.setItem('homeenergy-show-peaks', String(peaksToggle.checked)); } catch {}
  render();
});
window.addEventListener('languagechange', () => { updateFilters(); updateRefreshButton(); });
refreshButton.addEventListener('click', () => {
  if (loading || Date.now() < nextRefreshAt) return;
  dateInput.max = today();
  load({ refresh: true });
});
// This timer only updates the button label; history never refreshes automatically.
setInterval(updateRefreshButton, 1000);
let resizeTimer;
let previousWidth = document.documentElement.clientWidth;
window.addEventListener('resize', () => {
  if (document.documentElement.clientWidth === previousWidth) return;
  previousWidth = document.documentElement.clientWidth;
  clearTimeout(resizeTimer); resizeTimer = setTimeout(render, 150);
});
