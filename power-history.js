'use strict';

// A small overview drill-down using the same recorded minute averages as History.
(() => {
  const sources = { grid: 'Grid (est.)', pv: 'Solar total', load: 'House', battery: 'Battery power' };
  const timezone = 'Europe/Athens';
  const date = new Intl.DateTimeFormat('en-GB', { timeZone: timezone, day: '2-digit', month: '2-digit', year: 'numeric' });
  const time = new Intl.DateTimeFormat('en-GB', { timeZone: timezone, hour: '2-digit', minute: '2-digit' });
  function dayAt(seconds) {
    const parts = new Intl.DateTimeFormat('en-CA', { timeZone: timezone, year: 'numeric', month: '2-digit', day: '2-digit' }).formatToParts(seconds * 1000);
    const part = key => parts.find(item => item.type === key).value;
    return `${part('year')}-${part('month')}-${part('day')}`;
  }
  function windowAt(now) {
    const to = Math.floor(now), from = to - 12 * 3600;
    return { from, to, days: [...new Set([dayAt(from), dayAt(to)])] };
  }
  function powerPoints(rows, key, range) {
    if (!Object.hasOwn(sources, key)) throw new Error('Unknown power source');
    const buckets = new Map();
    for (const row of rows) {
      const value = row.values?.[key], count = row.counts?.[key];
      if (!Number.isFinite(row.t) || row.t < range.from || row.t >= range.to
          || !Number.isFinite(value) || !Number.isFinite(count) || count <= 0) continue;
      const stamp = Math.floor(row.t / 60) * 60;
      const bucket = buckets.get(stamp) || { t: stamp, sum: 0, count: 0 };
      bucket.sum += value * count; bucket.count += count; buckets.set(stamp, bucket);
    }
    return [...buckets.values()].sort((a, b) => a.t - b.t).map(item => ({ t: item.t, value: item.sum / item.count }));
  }
  // Matches History's rounded axis, including zero and negative battery power.
  function powerScale(minimum, maximum) {
    const low = Math.min(0, minimum), high = Math.max(0, maximum), extent = high - low || 1;
    const paddedLow = low < 0 ? low - extent * .05 : 0;
    const paddedHigh = high > 0 ? high + extent * .05 : low < 0 ? 0 : 1;
    const roughStep = (paddedHigh - paddedLow) / 5;
    const magnitude = 10 ** Math.floor(Math.log10(roughStep));
    const step = [1, 2, 2.5, 5, 10].find(n => n * magnitude >= roughStep) * magnitude;
    const first = Math.floor(paddedLow / step), last = Math.ceil(paddedHigh / step);
    const ticks = Array.from({ length: last - first + 1 }, (_, i) => Number(((first + i) * step).toPrecision(12)));
    return { low: ticks[0], high: ticks[ticks.length - 1], ticks };
  }
  if (typeof module !== 'undefined' && module.exports) module.exports = { windowAt, powerPoints, powerScale };
  if (typeof document === 'undefined' || !document.querySelector('.energy-scene')) return;

  const translations = {
    el: {
      'Last 12 hours': 'Τελευταίες 12 ώρες', 'Close': 'Κλείσιμο', 'Open full history': 'Άνοιγμα πλήρους ιστορικού',
      'Last reading': 'Τελευταία μέτρηση', 'Try again': 'Δοκιμή ξανά',
      'Select a box to explore its last 12 hours.': 'Επιλέξτε ένα πλαίσιο για τις τελευταίες 12 ώρες.',
      'View {source} power for the last 12 hours': 'Προβολή ισχύος: {source}, τελευταίες 12 ώρες',
      'Drag to zoom · tap or use arrow keys to inspect.': 'Σύρετε για μεγέθυνση · πατήστε ή χρησιμοποιήστε τα βέλη για τιμές.',
      'Missing readings appear as gaps.': 'Οι μετρήσεις που λείπουν εμφανίζονται ως κενά.',
    },
    ru: {
      'Last 12 hours': 'Последние 12 часов', 'Close': 'Закрыть', 'Open full history': 'Открыть всю историю',
      'Last reading': 'Последнее показание', 'Try again': 'Повторить',
      'Select a box to explore its last 12 hours.': 'Выберите блок, чтобы увидеть последние 12 часов.',
      'View {source} power for the last 12 hours': 'Мощность: {source}, последние 12 часов',
      'Drag to zoom · tap or use arrow keys to inspect.': 'Потяните для увеличения · нажмите или используйте стрелки для просмотра.',
      'Missing readings appear as gaps.': 'Пропущенные показания отображаются как разрывы.',
    },
  };
  const t = (key, values = {}) => {
    const language = document.documentElement.lang;
    const translated = translations[language]?.[key];
    return translated ? translated.replace(/\{(\w+)\}/g, (_, name) => values[name] ?? '') : window.energyI18n.t(key, values);
  };
  const dialog = document.createElement('dialog');
  dialog.id = 'power-history-dialog';
  dialog.setAttribute('aria-labelledby', 'power-history-title');
  dialog.setAttribute('aria-describedby', 'power-history-range');
  // Static structure only; all readings and translated text use textContent.
  dialog.innerHTML = `<div class="power-history-heading"><div><p class="power-history-eyebrow"></p><h2 id="power-history-title"></h2></div><button class="power-history-close" type="button" autofocus>×</button></div>
    <p id="power-history-range"></p><p class="power-history-direction"></p>
    <div class="power-history-tools"><span class="power-history-status" role="status"></span><div><button class="power-history-zoom" type="button"></button><button class="power-history-reset" type="button"></button><button class="power-history-retry" type="button" hidden></button></div></div>
    <div class="power-history-chart" role="group" tabindex="0"></div>
    <div class="power-history-tip" aria-live="polite"></div>
    <div class="power-history-peaks"></div>
    <div class="power-history-footer"><p></p><a href="history.html"></a></div>`;
  document.body.append(dialog);
  const $ = selector => dialog.querySelector(selector);
  const chart = $('.power-history-chart'), tip = $('.power-history-tip');
  const cache = new window.HistoryDataCache();
  let source = 'load', range, zoom = null, points = [], active = null, loading = false, error = false;
  let sequence = 0, opener, request, resizeFrame = 0;
  const triggers = Object.entries({ grid: '.grid-label', pv: '.solar-label', load: '.load-label', battery: '.battery-label' }).map(([key, selector]) => {
    const button = document.createElement('button');
    button.type = 'button'; button.className = 'power-history-trigger';
    button.setAttribute('aria-haspopup', 'dialog'); button.setAttribute('aria-controls', dialog.id);
    button.addEventListener('click', () => open(key, button));
    document.querySelector(selector).append(button);
    return { key, button };
  });
  function format(value) {
    if (!Number.isFinite(value)) return t('No report');
    const kilo = Math.abs(value) >= 1000;
    return `${new Intl.NumberFormat(window.energyI18n.locale, { maximumFractionDigits: kilo ? 2 : 1 }).format(kilo ? value / 1000 : value)} ${kilo ? 'kW' : 'W'}`;
  }
  function stamp(seconds) { return `${date.format(seconds * 1000)} · ${time.format(seconds * 1000)}`; }
  function svgNode(tag, attrs = {}, text) {
    const node = document.createElementNS('http://www.w3.org/2000/svg', tag);
    for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
    if (text !== undefined) node.textContent = text;
    return node;
  }
  function labels() {
    for (const { key, button } of triggers) button.setAttribute('aria-label', t('View {source} power for the last 12 hours', { source: t(sources[key]) }));
    document.querySelector('.scene-caption').textContent = t('Select a box to explore its last 12 hours.');
    $('.power-history-eyebrow').textContent = t('Last 12 hours');
    $('#power-history-title').textContent = t(sources[source]);
    $('.power-history-close').setAttribute('aria-label', t('Close'));
    $('.power-history-zoom').textContent = t('Zoom in');
    $('.power-history-reset').textContent = t('Reset zoom');
    $('.power-history-retry').textContent = t('Try again');
    $('.power-history-footer a').textContent = t('Open full history') + ' ↗';
    $('.power-history-footer p').textContent = t('Missing readings appear as gaps.');
    $('.power-history-direction').textContent = source === 'battery' ? t('Battery: + charging · − discharging') : '';
    $('.power-history-direction').hidden = source !== 'battery';
    chart.setAttribute('aria-label', t('{title} chart. Use left and right arrows to inspect values.', { title: t(sources[source]) }));
  }
  async function open(key, button) {
    source = key; opener = button;
    if (!dialog.open) dialog.showModal();
    document.body.classList.add('power-history-open');
    await load();
  }
  async function load() {
    const id = ++sequence;
    request?.abort(); request = new AbortController();
    const controller = request;
    range = windowAt(Date.now() / 1000); zoom = null; active = null; points = [];
    const requestedRange = range, requestedSource = source;
    loading = true; error = false; cache.invalidate(); render();
    const timeout = setTimeout(() => controller.abort(), 10000);
    try {
      const response = await fetch('/history/index.json', { cache: 'no-store', signal: controller.signal });
      if (!response.ok) throw new Error('History unavailable');
      const index = await response.json();
      if (!Array.isArray(index.days)) throw new Error('Invalid history index');
      const days = requestedRange.days.filter(day => index.days.includes(day));
      const files = await Promise.all(days.map(day => cache.get(day)));
      if (id !== sequence || !dialog.open) return;
      points = powerPoints(files.flatMap(file => file.points), requestedSource, requestedRange);
    } catch {
      if (id !== sequence || !dialog.open) return;
      error = true;
    } finally {
      clearTimeout(timeout);
      if (id === sequence && dialog.open) { loading = false; render(); }
    }
  }
  function setZoom(from, to) {
    zoom = { from: Math.max(range.from, from), to: Math.min(range.to, to) };
    if (zoom.to - zoom.from < 120) zoom = null;
    active = null; render(); chart.focus({ preventScroll: true });
  }
  function render() {
    labels();
    const view = zoom || range;
    dialog.dataset.source = source;
    $('#power-history-range').textContent = `${stamp(view.from)} – ${stamp(view.to)} · ${t('Athens time')}`;
    $('.power-history-status').textContent = t(loading ? 'Loading history…' : error ? 'History is unavailable. Please try again shortly.' : points.length ? '1-minute averages' : 'No readings for this period.');
    chart.setAttribute('aria-busy', String(loading));
    $('.power-history-retry').hidden = !error;
    $('.power-history-zoom').disabled = loading || error || !points.length || view.to - view.from <= 120;
    $('.power-history-reset').disabled = !zoom;
    chart.replaceChildren(); tip.replaceChildren(); $('.power-history-peaks').replaceChildren();
    tip.textContent = t('Drag to zoom · tap or use arrow keys to inspect.');
    const visible = points.filter(point => point.t >= view.from && point.t < view.to);
    const width = Math.max(280, chart.clientWidth), height = 250, left = 47, right = width - 12, top = 18, bottom = 220;
    const scale = powerScale(Math.min(0, ...visible.map(p => p.value)), Math.max(0, ...visible.map(p => p.value)));
    const unit = Math.max(Math.abs(scale.low), scale.high) >= 1000 ? 1000 : 1;
    const x = value => left + (value - view.from) / (view.to - view.from) * (right - left);
    const y = value => bottom - (value - scale.low) / (scale.high - scale.low) * (bottom - top);
    const svg = svgNode('svg', { viewBox: `0 0 ${width} ${height}`, role: 'img', 'aria-label': t('{title} over time', { title: t(sources[source]) }) });
    for (const value of scale.ticks) {
      svg.append(svgNode('line', { x1: left, x2: right, y1: y(value), y2: y(value), class: value === 0 ? 'ph-zero' : 'ph-grid' }));
      svg.append(svgNode('text', { x: left - 8, y: y(value) + 4, 'text-anchor': 'end' }, new Intl.NumberFormat(window.energyI18n.locale, { maximumFractionDigits: 6 }).format(value / unit)));
    }
    svg.append(svgNode('text', { x: 0, y: 11 }, unit === 1000 ? 'kW' : 'W'));
    for (let i = 0; i <= 4; i++) {
      const timestamp = view.from + (view.to - view.from) * i / 4;
      svg.append(svgNode('text', { x: x(timestamp), y: 244, 'text-anchor': i === 0 ? 'start' : i === 4 ? 'end' : 'middle' }, time.format(timestamp * 1000)));
    }
    let path = '';
    visible.forEach((point, i) => {
      path += `${i && point.t - visible[i - 1].t <= 60 ? 'L' : 'M'}${x(point.t)},${y(point.value)} `;
      if (visible.length < 10 || ((!visible[i - 1] || point.t - visible[i - 1].t > 60) && (!visible[i + 1] || visible[i + 1].t - point.t > 60))) svg.append(svgNode('circle', { cx: x(point.t), cy: y(point.value), r: 2.5, class: 'ph-point' }));
    });
    svg.append(svgNode('path', { d: path, class: 'ph-line' }));
    const cursor = svgNode('line', { y1: top, y2: bottom, class: 'ph-cursor', visibility: 'hidden' });
    const dot = svgNode('circle', { r: 4, class: 'ph-point', visibility: 'hidden' });
    const selection = svgNode('rect', { y: top, height: bottom - top, class: 'ph-selection', visibility: 'hidden' });
    svg.append(cursor, dot, selection); chart.append(svg);
    const byTime = new Map(visible.map(point => [point.t, point]));
    function inspect(timestamp) {
      active = Math.max(Math.ceil(view.from / 60) * 60, Math.min(Math.ceil(view.to / 60) * 60 - 60, Math.floor(timestamp / 60) * 60));
      const point = byTime.get(active);
      cursor.setAttribute('x1', x(active)); cursor.setAttribute('x2', x(active)); cursor.setAttribute('visibility', 'visible');
      dot.setAttribute('visibility', point ? 'visible' : 'hidden');
      if (point) { dot.setAttribute('cx', x(active)); dot.setAttribute('cy', y(point.value)); }
      const label = document.createElement('span'); label.textContent = `${stamp(active)} – ${time.format(Math.min(active + 60, view.to) * 1000)}`;
      const value = document.createElement('strong'); value.textContent = format(point?.value);
      tip.replaceChildren(label, value);
    }
    if (visible.length) {
      const min = visible.reduce((a, b) => b.value < a.value ? b : a), max = visible.reduce((a, b) => b.value > a.value ? b : a);
      for (const [label, point] of [['Min', min], ['Max', max], ['Last reading', visible.at(-1)]]) {
        const button = document.createElement('button'); button.type = 'button';
        const caption = document.createElement('span'); caption.textContent = t(label);
        const value = document.createElement('strong'); value.textContent = format(point.value);
        const timestamp = document.createElement('small'); timestamp.textContent = time.format(point.t * 1000);
        button.append(caption, value, timestamp); button.title = stamp(point.t);
        button.addEventListener('click', () => inspect(point.t)); $('.power-history-peaks').append(button);
      }
    }
    const pointerTime = event => {
      const rect = svg.getBoundingClientRect();
      return view.from + Math.max(0, Math.min(1, ((event.clientX - rect.left) / rect.width * width - left) / (right - left))) * (view.to - view.from);
    };
    let drag = null;
    chart.onpointerdown = event => {
      if (event.button !== 0 || loading || error || !points.length) return;
      drag = { id: event.pointerId, t: pointerTime(event), x: event.clientX };
      chart.setPointerCapture(event.pointerId); inspect(drag.t);
    };
    chart.onpointermove = event => {
      if (loading || error) return;
      const timestamp = pointerTime(event);
      if (drag && drag.id === event.pointerId) {
        selection.setAttribute('x', x(Math.min(drag.t, timestamp))); selection.setAttribute('width', Math.abs(x(timestamp) - x(drag.t))); selection.setAttribute('visibility', 'visible');
      } else inspect(timestamp);
    };
    chart.onpointerup = event => {
      if (!drag || drag.id !== event.pointerId) return;
      const start = drag; drag = null;
      if (chart.hasPointerCapture(event.pointerId)) chart.releasePointerCapture(event.pointerId);
      selection.setAttribute('visibility', 'hidden');
      const end = pointerTime(event);
      if (Math.abs(event.clientX - start.x) < 12 || Math.abs(end - start.t) < 120) { inspect(end); return; }
      setZoom(Math.floor(Math.min(start.t, end) / 60) * 60, Math.ceil(Math.max(start.t, end) / 60) * 60);
    };
    chart.onpointercancel = () => { drag = null; selection.setAttribute('visibility', 'hidden'); };
    chart.onkeydown = event => {
      if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key) || loading || error) return;
      event.preventDefault();
      inspect(event.key === 'Home' ? view.from : event.key === 'End' ? view.to - 1 : (active ?? visible.at(-1)?.t ?? view.from) + (event.key === 'ArrowLeft' ? -60 : 60));
    };
    if (active !== null && active >= view.from && active < view.to) inspect(active);
  }
  $('.power-history-close').addEventListener('click', () => dialog.close());
  dialog.addEventListener('click', event => {
    const bounds = dialog.getBoundingClientRect();
    if (event.target === dialog && (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom)) dialog.close();
  });
  dialog.addEventListener('close', () => {
    sequence++; request?.abort(); document.body.classList.remove('power-history-open'); opener?.focus({ preventScroll: true });
  });
  $('.power-history-retry').addEventListener('click', load);
  $('.power-history-reset').addEventListener('click', () => { zoom = null; active = null; render(); });
  $('.power-history-zoom').addEventListener('click', () => {
    const view = zoom || range, span = Math.max(120, (view.to - view.from) / 2);
    const center = active ?? (view.from + view.to) / 2;
    const from = Math.max(view.from, Math.min(view.to - span, center - span / 2));
    setZoom(from, from + span);
  });
  window.addEventListener('languagechange', () => { labels(); if (dialog.open) render(); });
  window.addEventListener('resize', () => {
    cancelAnimationFrame(resizeFrame);
    resizeFrame = requestAnimationFrame(() => { if (dialog.open) render(); });
  });
  labels();
})();
