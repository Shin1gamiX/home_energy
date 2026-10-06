// Pure presentation logic. Routing is estimated, not independently metered.
window.energyFlowState = (values, mode) => {
  const positive = value => Number.isFinite(value) && value >= 0.5;
  const valid = key => Number.isFinite(values[key]);
  const charging = positive(values.battery);
  const discharging = valid('battery') && values.battery <= -0.5;
  const waiting = valid('battery') && !charging && !discharging && mode === 'Mains' && valid('soc') && values.soc < 40;
  const batteryState = charging ? 'charging' : discharging ? 'discharging' : waiting ? 'waiting' : 'idle';
  // Grid charging is disabled in this installation. Avoid inventing missing flows.
  const solarToHouse = ['pv', 'load', 'grid', 'battery'].every(valid)
    ? Math.min(Math.max(0, values.pv), Math.max(0, values.load - Math.max(0, values.grid) - Math.max(0, -values.battery))) : 0;
  return { batteryState, batteryLabel: !valid('battery') ? 'No report' : charging ? 'Charging' : discharging ? 'Discharging' : waiting ? 'Waiting to charge' : 'Standby',
    routeWatts: { gridHouse: valid('grid') && valid('load') ? Math.min(Math.max(0, values.grid), Math.max(0, values.load)) : 0,
      solarHouse: solarToHouse,
      batteryHouse: valid('battery') && valid('load') ? Math.min(Math.max(0, -values.battery), Math.max(0, values.load)) : 0,
      solarBattery: valid('pv') && valid('battery') ? Math.min(Math.max(0, values.pv), Math.max(0, values.battery)) : 0 },
    routes: { gridHouse: positive(values.grid) && positive(values.load), solarHouse: positive(solarToHouse),
      batteryHouse: discharging && positive(values.load), solarBattery: positive(values.pv) && charging } };
};

window.energyModeLabel = mode => mode === 'Mains' ? 'Grid' : mode || '—';
window.energySupplyLabel = values => {
  if (!['grid', 'pv', 'battery', 'load'].every(key => Number.isFinite(values[key]))) return 'Unknown supply';
  if (values.load <= 0) return 'Standby';
  const flows = window.energyFlowState(values).routeWatts;
  const sources = { Grid: flows.gridHouse, Solar: flows.solarHouse, Battery: flows.batteryHouse };
  const active = Object.keys(sources).filter(key => sources[key] > Math.max(20, values.load * .02));
  if (active.length > 1) return 'Mixed';
  if (active.length) return active[0];
  const largest = Object.keys(sources).sort((a, b) => sources[b] - sources[a])[0];
  return sources[largest] > 0 ? largest : 'Unknown supply';
};

// Self-contained SVG presentation engine: no extra public route or dependency.
window.energyComets = (() => {
  const clamp = (value, low, high) => Math.max(low, Math.min(high, value));
  const wrap = (value, length) => ((value % length) + length) % length;
  const mix = (a, b, t) => ({ x: a.x + (b.x - a.x) * t, y: a.y + (b.y - a.y) * t });

  function roundedOutline({ x, y, width, height, radius }) {
    const r = clamp(radius, 0, Math.min(width, height) / 2);
    const segments = [];
    const line = (a, b) => segments.push({ length: Math.hypot(b.x - a.x, b.y - a.y), point: t => mix(a, b, t) });
    const arc = (cx, cy, angle) => segments.push({ length: Math.PI * r / 2,
      point: t => ({ x: cx + r * Math.cos(angle + t * Math.PI / 2), y: cy + r * Math.sin(angle + t * Math.PI / 2) }) });
    line({ x: x + r, y }, { x: x + width - r, y });
    arc(x + width - r, y + r, -Math.PI / 2);
    line({ x: x + width, y: y + r }, { x: x + width, y: y + height - r });
    arc(x + width - r, y + height - r, 0);
    line({ x: x + width - r, y: y + height }, { x: x + r, y: y + height });
    arc(x + r, y + height - r, Math.PI / 2);
    line({ x, y: y + height - r }, { x, y: y + r });
    arc(x + r, y + r, Math.PI);
    const length = segments.reduce((sum, segment) => sum + segment.length, 0);
    function point(distance) {
      let rest = wrap(distance, length);
      for (const segment of segments) {
        if (rest <= segment.length) return segment.point(segment.length ? rest / segment.length : 0);
        rest -= segment.length;
      }
      return segments[0].point(0);
    }
    function port(side, fraction = .5) {
      const horizontal = width - 2 * r, vertical = height - 2 * r, corner = Math.PI * r / 2;
      const px = clamp(width * fraction - r, 0, horizontal);
      const py = clamp(height * fraction - r, 0, vertical);
      const distance = { top: px, right: horizontal + corner + py,
        bottom: horizontal + 2 * corner + vertical + horizontal - px,
        left: 2 * horizontal + 3 * corner + vertical + vertical - py }[side];
      if (!Number.isFinite(distance)) throw new Error('Unknown connection side');
      return { ...point(distance), distance };
    }
    return { length, point, port };
  }

  function borderBranches(outline, port, isSource) {
    const half = outline.length / 2;
    const start = isSource ? port.distance + half : port.distance;
    return [1, -1].map(direction => ({ length: half,
      point: distance => outline.point(start + direction * clamp(distance, 0, half)) }));
  }

  // Arc-length sampling keeps the head speed even through curves and rounded corners.
  function sampleCurve(pointAt, count = 160) {
    const points = Array.from({ length: count + 1 }, (_, i) => pointAt(i / count));
    const distances = [0];
    for (let i = 1; i < points.length; i++) distances.push(distances[i - 1] + Math.hypot(points[i].x - points[i - 1].x, points[i].y - points[i - 1].y));
    const length = distances.at(-1);
    function point(distance) {
      const target = clamp(distance, 0, length);
      let lo = 1, hi = distances.length - 1;
      while (lo < hi) { const mid = (lo + hi) >> 1; if (distances[mid] < target) lo = mid + 1; else hi = mid; }
      const span = distances[lo] - distances[lo - 1];
      return mix(points[lo - 1], points[lo], span ? (target - distances[lo - 1]) / span : 0);
    }
    return { length, point };
  }

  function cubic(a, b, c, d) {
    return sampleCurve(t => {
      const u = 1 - t;
      return { x: u ** 3 * a.x + 3 * u ** 2 * t * b.x + 3 * u * t ** 2 * c.x + t ** 3 * d.x,
        y: u ** 3 * a.y + 3 * u ** 2 * t * b.y + 3 * u * t ** 2 * c.y + t ** 3 * d.y };
    });
  }

  function pathData(curve, from = 0, to = curve.length, count = 80) {
    return Array.from({ length: count + 1 }, (_, index) => {
      const p = curve.point(from + (to - from) * index / count);
      return `${index ? 'L' : 'M'}${p.x.toFixed(2)} ${p.y.toFixed(2)}`;
    }).join(' ');
  }

  function timings(linkLength) {
    // Power changes launch frequency, not an individual comet's speed.
    // Cross long connections quickly while keeping short transfers visible.
    const gather = .8;
    const travel = clamp(linkLength / 450, .4, 1.2);
    const spread = gather;
    const fade = .35, rest = .45;
    return { gather, travel, spread, fade, rest, total: gather + travel + spread + fade + rest };
  }

  function cadence(watts, timing) {
    const beams = !Number.isFinite(watts) || watts <= 0 ? 0 : watts < 300 ? 1 : watts <= 1000 ? 2 : 3;
    return { beams, tier: ['off', 'slow', 'medium', 'fast'][beams], interval: beams ? timing.total / beams : 0 };
  }

  function beamStateAt(time, timing, index, beams) {
    if (!Number.isInteger(beams) || beams < 1 || beams > 3 || index < 0 || index >= beams) {
      return { phase: 'waiting', progress: 0 };
    }
    // Each reusable slot starts one interval later and repeats only after its
    // complete cycle. This bounds concurrency without accumulating particles.
    const elapsed = time - index * timing.total / beams;
    return elapsed < 0 ? { phase: 'waiting', progress: 0 }
      : { ...phaseAt(elapsed, timing), cycle: Math.floor(elapsed / timing.total) };
  }

  // One uninterrupted border pass per card. Overlapping requests join that pass;
  // they are not queued or replayed after it, which avoids a permanently busy rim.
  class BorderPassCoalescer {
    constructor() { this.reset(); }
    reset() { this.owners = new Map(); this.seen = new Set(); }
    select(requests) {
      const current = new Map(requests.map(request => [request.id, request]));
      for (const [box, id] of this.owners) if (!current.has(id)) this.owners.delete(box);
      for (const request of requests) {
        if (!this.seen.has(request.id) && !this.owners.has(request.box)) this.owners.set(request.box, request.id);
      }
      this.seen = new Set(current.keys());
      return new Map([...this.owners].map(([box,id]) => [box,current.get(id)]));
    }
  }

  function phaseAt(time, timing) {
    let local = wrap(time, timing.total);
    for (const phase of ['gather', 'travel', 'spread', 'fade', 'rest']) {
      if (local < timing[phase]) return { phase, progress: local / timing[phase] };
      local -= timing[phase];
    }
    return { phase: 'rest', progress: 0 };
  }

  function create(scene) {
    const element = (tag, attributes = {}) => {
      const node = document.createElementNS('http://www.w3.org/2000/svg', tag);
      for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, value);
      return node;
    };
    const media = matchMedia('(prefers-reduced-motion: reduce)');
    const svg = element('svg', { class: 'energy-connections comet-layer', 'aria-hidden': 'true' });
    const definitions = element('defs');
    const glow = element('filter', { id: 'comet-soft-glow', x: '-50%', y: '-50%', width: '200%', height: '200%' });
    glow.append(element('feGaussianBlur', { stdDeviation: '1.6' }));
    definitions.append(glow); svg.append(definitions); scene.append(svg);
    const routes = {
      gridHouse: { from: 'grid', to: 'load', color: '#8861ba', exit: 'bottom', entry: 'top' },
      batteryHouse: { from: 'battery', to: 'load', color: '#169779', exit: 'left', entry: 'right' },
      solarBattery: { from: 'solar', to: 'battery', color: '#bc8116', exit: 'bottom', entry: 'top' },
      solarHouse: { from: 'solar', to: 'load', color: '#bc8116', exit: 'left', entry: 'right' },
    };
    const cards = Object.fromEntries(['grid', 'solar', 'battery', 'load'].map(name => [name, scene.querySelector(`.${name}-label`)]));
    const borderEffects = new Map(), borderPasses = new BorderPassCoalescer();
    let active = [], wattsByRoute = {}, enabled = false, disposed = false;
    let seconds = 0, previous = null, frame = null, layoutFrame = null, layoutSignature = '';

    class Comet {
      constructor(parent, color, small = false) {
        this.small = small;
        this.intensity = small ? .45 : 1;
        this.group = element('g', { class: `comet-particle ${small ? 'comet-border' : 'comet-transfer'}` });
        this.halo = element('path', { class:'comet-trail', stroke:color, 'stroke-width':small ? 4 : 9, opacity:small ? '.12' : '.3', filter:'url(#comet-soft-glow)' });
        this.parts = Array.from({length:18}, (_, i) => element('path', { class:'comet-trail', stroke:color,
          'stroke-width':(.35 + (small ? 1.3 : 2.8) * (i + 1) / 18).toFixed(2), opacity:(.08 + .88 * (i + 1) / 18).toFixed(2) }));
        this.head = element('circle', { r:small ? 1.3 : 3, fill:small ? color : '#f5fffb', stroke:color, 'stroke-width':small ? '.8' : '1.5' });
        this.color = color;
        this.group.append(this.halo, ...this.parts, this.head); parent.append(this.group);
      }
      hide() { this.group.style.display = 'none'; }
      setColor(color) {
        if (this.color === color) return;
        this.color = color;
        [this.halo,...this.parts,this.head].forEach(node=>node.setAttribute('stroke',color));
        if (this.small) this.head.setAttribute('fill',color);
      }
      draw(curve, progress, tail = 46, opacity = 1) {
        this.group.style.display = ''; this.group.style.opacity = opacity * this.intensity;
        const end = Math.min(curve.length, Math.max(0, progress) * curve.length);
        const start = Math.max(0, end - Math.min(tail, curve.length * .75));
        this.halo.setAttribute('d', pathData(curve, start, end, 20));
        this.parts.forEach((part, i) => part.setAttribute('d', pathData(curve, start + (end-start) * i/18, start + (end-start) * (i+1)/18, 3)));
        const p = curve.point(end); this.head.setAttribute('cx', p.x); this.head.setAttribute('cy', p.y);
      }
    }


    function card(name, bounds) {
      const node = cards[name], box = node.getBoundingClientRect();
      const inset = 2.3;
      return { x:box.left-bounds.left-inset, y:box.top-bounds.top-inset,
        width:box.width+2*inset, height:box.height+2*inset,
        radius:parseFloat(getComputedStyle(node).borderTopLeftRadius)+inset };
    }

    function buildRoute(key, watts, index, bounds) {
      const definition = routes[key];
      const route = { ...definition, color: routeColor(key) };
      const fromBox = card(route.from, bounds), toBox = card(route.to, bounds);
      const from = roundedOutline(fromBox), to = roundedOutline(toBox);
      const narrowBattery = key === 'batteryHouse' && window.innerWidth <= 700;
      // On phones use the shared vertical overlap: a short, level connection
      // instead of sagging below two almost-touching cards.
      const sharedY = (Math.max(fromBox.y, toBox.y) + Math.min(fromBox.y + fromBox.height, toBox.y + toBox.height)) / 2;
      const a = from.port(route.exit, narrowBattery ? (sharedY - fromBox.y) / fromBox.height : key === 'solarHouse' ? .8 : .5);
      const b = to.port(route.entry, narrowBattery ? (sharedY - toBox.y) / toBox.height : key === 'solarHouse' ? .25 : .5);
      let link;
      if (narrowBattery) {
        link = cubic(a, mix(a, b, 1 / 3), mix(a, b, 2 / 3), b);
      } else if (route.exit === 'bottom') {
        const gap = Math.max(0, b.y-a.y);
        link = cubic(a, {x:a.x,y:a.y+gap*.45}, {x:b.x,y:b.y-gap*.45}, b);
      } else if (key === 'solarHouse') {
        // Follow the gap between the two rows, instead of cutting through the roof.
        const aisle = Math.max(a.y+20, Math.min(toBox.y-16, fromBox.y+fromBox.height+22));
        link = cubic(a, {x:a.x-60,y:aisle}, {x:b.x+45,y:aisle}, b);
      } else {
        const width = a.x-b.x;
        const low = Math.min(bounds.height-43, Math.max(a.y,b.y)+35);
        link = cubic(a, {x:a.x-width*.3,y:low}, {x:b.x+width*.3,y:low}, b);
      }
      const group = element('g', { 'data-comet-route':key, 'data-watts':watts });
      group.style.setProperty('--comet-color', route.color);
      const track = element('path', { class:'comet-track', stroke:route.color, d:pathData(link) });
      group.append(track);
      for (const p of [a,b]) group.append(element('circle', { class:'comet-port', cx:p.x, cy:p.y, r:'2' }));
      const source = borderBranches(from,a,true), target = borderBranches(to,b,false);
      const timing = timings(link.length), schedule = cadence(watts, timing);
      const beams = Array.from({length:schedule.beams}, (_, index) => {
        const node = element('g', { 'data-beam':index });
        group.append(node);
        return { node, comet:new Comet(node,route.color) };
      });
      svg.append(group);
      group.dataset.cycle = timing.total.toFixed(3);
      group.dataset.tier = schedule.tier;
      group.dataset.beamLimit = schedule.beams;
      group.dataset.launchInterval = schedule.interval.toFixed(3);
      group.dataset.linkLength = link.length.toFixed(2);
      group.dataset.gatherSeconds = timing.gather;
      group.dataset.travelSeconds = timing.travel.toFixed(3);
      group.dataset.spreadSeconds = timing.spread;
      return { key, from:route.from, to:route.to, color:route.color, group, track, link, source, target, beams, timing, schedule, delay:index*.7 };
    }

    function routeColor(key) {
      const token = key === 'gridHouse' ? '--theme-grid' : key === 'batteryHouse' ? '--theme-house' : '--theme-solar';
      return getComputedStyle(scene).getPropertyValue?.(token).trim() || routes[key].color;
    }
    function themeChanged() {
      for (const route of active) {
        route.color = routeColor(route.key);
        route.group.style.setProperty('--comet-color', route.color);
        route.track.setAttribute('stroke', route.color);
        route.beams.forEach(beam => beam.comet.setColor(route.color));
      }
      // Recolour the current frame; a theme switch must not restart the cycle.
      draw();
    }

    function clearParticles() {
      for (const route of active) {
        for (const beam of route.beams) { beam.comet.hide(); beam.node.dataset.phase = 'stopped'; }
        route.group.dataset.activeBeams = '0';
      }
      for (const effect of borderEffects.values()) {
        effect.movers.forEach(mover => mover.hide());
        effect.node.dataset.active = 'false';
      }
      borderPasses.reset();
    }

    function draw() {
      if (!enabled || media.matches) { clearParticles(); return; }
      const requests = [];
      for (const route of active) {
        const phases = [];
        route.beams.forEach(({ comet, node }, index) => {
          comet.hide();
          const state = beamStateAt(seconds - route.delay, route.timing, index, route.schedule.beams);
          node.dataset.phase = state.phase;
          phases.push(state.phase);
          const event = `${route.key}:${index}:${state.cycle}`;
          if (state.phase === 'gather') requests.push({
            id: `${event}:source`, box: route.from, color: route.color,
            paths: route.source, phase: 'gather', progress: state.progress, opacity: Math.min(1, state.progress * 8),
          });
          if (state.phase === 'travel') comet.draw(route.link, state.progress, 60);
          if (state.phase === 'spread' || state.phase === 'fade') requests.push({
            id: `${event}:target`, box: route.to, color: route.color, paths: route.target, phase: state.phase,
            progress: state.phase === 'fade' ? 1 : state.progress, opacity: state.phase === 'fade' ? 1 - state.progress : 1,
          });
        });
        route.group.dataset.phase = phases.join(',');
        route.group.dataset.activeBeams = phases.filter(phase => ['gather', 'travel', 'spread', 'fade'].includes(phase)).length;
      }
      const selected = borderPasses.select(requests);
      for (const [box, effect] of borderEffects) {
        effect.movers.forEach(mover => mover.hide());
        const request = selected.get(box);
        effect.node.dataset.active = String(Boolean(request));
        effect.node.dataset.phase = request?.phase || 'idle';
        if (!request) continue;
        effect.movers.forEach((mover, index) => {
          mover.setColor(request.color);
          mover.draw(request.paths[index], request.progress, 30, request.opacity);
        });
      }
    }

    function cancelFrame() {
      if (frame !== null) cancelAnimationFrame(frame);
      frame = null; previous = null;
    }
    function requestFrame() {
      const running = enabled && !media.matches && !document.hidden && active.length > 0 && !disposed;
      svg.dataset.motion = !enabled ? 'stopped' : media.matches ? 'reduced' : document.hidden ? 'hidden' : running ? 'running' : 'idle';
      if (running && frame === null) frame = requestAnimationFrame(tick);
    }
    function tick(timestamp) {
      frame = null;
      if (previous !== null) seconds += Math.min(.1, Math.max(0, (timestamp - previous) / 1000));
      previous = timestamp;
      draw(); requestFrame();
    }
    function updateMotion() {
      cancelFrame();
      clearParticles();
      // Resume from a clean gather, never halfway through a stale transfer.
      seconds = 0;
      svg.style.display = enabled ? '' : 'none';
      draw(); requestFrame();
    }

    function layout() {
      layoutFrame = null;
      if (disposed) return;
      const bounds = scene.getBoundingClientRect();
      if (!bounds.width || !bounds.height) return;
      const keys = Object.keys(routes).filter(key => wattsByRoute[key] >= .5);
      const signature = JSON.stringify([bounds.width, bounds.height,
        Object.keys(cards).map(name => card(name, bounds)),
        keys.map(key => [key, cadence(wattsByRoute[key], timings(0)).beams])]);
      // Polling/translation updates must not restart a cycle unless geometry or
      // routing actually changed. Watt changes within a tier only update metadata.
      if (signature === layoutSignature) {
        active.forEach(route => { route.group.dataset.watts = wattsByRoute[route.key]; });
        return;
      }
      layoutSignature = signature;
      cancelFrame(); clearParticles();
      active.forEach(route => route.group.remove());
      borderEffects.forEach(effect => effect.node.remove()); borderEffects.clear();
      svg.setAttribute('viewBox', `0 0 ${bounds.width} ${bounds.height}`);
      active = keys.map((key, index) => buildRoute(key, wattsByRoute[key], index, bounds));
      for (const box of new Set(active.flatMap(route => [route.from, route.to]))) {
        const node = element('g', { 'data-border-box': box, 'data-active': 'false' });
        svg.append(node);
        borderEffects.set(box, { node, movers: [new Comet(node, '#169779', true), new Comet(node, '#169779', true)] });
      }
      seconds = 0;
      draw(); requestFrame();
    }
    function scheduleLayout() {
      if (!disposed && layoutFrame === null) layoutFrame = requestAnimationFrame(layout);
    }
    function visibilityChanged() {
      cancelFrame();
      requestFrame();
    }
    const observer = new ResizeObserver(scheduleLayout);
    observer.observe(scene);
    Object.values(cards).forEach(node => observer.observe(node));
    media.addEventListener('change', updateMotion);
    document.addEventListener('visibilitychange', visibilityChanged);
    window.addEventListener('themechange', themeChanged);
    updateMotion();

    return {
      setState(state) {
        wattsByRoute = Object.fromEntries(Object.keys(routes).map(key => [
          key, state.routes[key] && Number.isFinite(state.routeWatts[key]) ? Math.max(0, state.routeWatts[key]) : 0,
        ]));
        scheduleLayout();
      },
      setEnabled(value) {
        const next = Boolean(value);
        if (next === enabled || disposed) return;
        enabled = next;
        updateMotion();
      },
      destroy() {
        disposed = true;
        cancelFrame();
        if (layoutFrame !== null) cancelAnimationFrame(layoutFrame);
        observer.disconnect();
        media.removeEventListener('change', updateMotion);
        document.removeEventListener('visibilitychange', visibilityChanged);
        window.removeEventListener('themechange', themeChanged);
        svg.remove();
      },
    };
  }
  return { roundedOutline, borderBranches, cubic, pathData, timings, cadence, beamStateAt, phaseAt, BorderPassCoalescer, create };
})();

// Shared localization is also used by the history page.
window.energyI18n = (() => {
  const translations = {
    ru: {
      'Overview': 'Обзор', 'History': 'История', 'Main navigation': 'Основная навигация',
      'Live · updated {seconds}s ago': 'Онлайн · обновлено {seconds} с назад',
      'Displayed statistics': 'Отображаемые показатели', 'Legacy readings': 'Старые показатели',
      'Energy summaries. Scroll to see more.': 'Сводки энергии. Прокрутите для просмотра остальных.',
      'Battery: + charging · − discharging': 'Батарея: + заряд · − разряд',
      'Fit recorded time': 'По времени записей', 'Rapid changes': 'Частые смены', '{n} changes': '{n} смен',
      'Electrical details': 'Электрические параметры', 'PV channels, voltage and current': 'Каналы PV, напряжение и ток',
      '{n} selected': 'Выбрано: {n}',
      'Rapid changes are grouped. Select a block to zoom into individual intervals.': 'Частые смены сгруппированы. Выберите блок, чтобы увидеть отдельные интервалы.',
      'Select an interval for exact times. Drag across the timeline to zoom.': 'Выберите интервал для точного времени. Выделите участок шкалы для увеличения.',
      'Inverter · ': 'Инвертор · ', 'Off-Grid': 'Автономный',
      'House supply · {source}': 'Питание дома · {source}',
      'Not refreshed yet': 'Ещё не обновлено',
      'Snapshot loaded {time} · refresh to update': 'Данные загружены в {time} · обновите для новых показаний',
      'House supply': 'Питание дома', 'Zoom in': 'Приблизить', 'Upcoming': 'Впереди',
      'Start': 'Начало', 'End': 'Конец', 'Previous interval': 'Предыдущий интервал', 'Next interval': 'Следующий интервал',
      'Select an interval to see its times.': 'Выберите интервал, чтобы увидеть время.',
      '{n} changes · select to zoom': 'Переходов: {n} · нажмите для увеличения',
      'Striped blocks contain multiple changes. Select to zoom, or drag across the timeline.': 'Полосатые блоки содержат несколько переходов. Нажмите для увеличения или выделите участок шкалы.',
      'Today': 'Сегодня',
      'Previous month': 'Предыдущий месяц', 'Next month': 'Следующий месяц', 'Close calendar': 'Закрыть календарь',
      'Recorded data available': 'Есть записанные данные', 'No recorded data': 'Нет записанных данных', 'Future date': 'Будущая дата',
      'Loading recorded dates…': 'Загрузка дат с записями…', 'Recorded dates unavailable': 'Даты с записями недоступны',
      'A highlight means some readings exist, not a complete day.': 'Выделение означает наличие показаний, но не обязательно за весь день.',
      'Use arrow keys for days, Page Up or Page Down for months, and Escape to close.': 'Стрелки — выбор дня, Page Up и Page Down — выбор месяца, Escape — закрыть.',
      'All time': 'Всё время', 'Total recorded': 'Всего за время записи',
      'All available recorded history': 'Вся сохранённая история',
      'Monthly breakdown': 'По месяцам', 'Grand total': 'Общий итог',
      'Recorded energy · kWh': 'Записанная энергия · кВт·ч',
      'Month in progress': 'Текущий месяц', 'No recorded energy yet.': 'Данных об энергии пока нет.',
      '{percent} recorded · {duration}': 'Записано {percent} · {duration}',
      '{days} d - {hours} h': '{days} д - {hours} ч', '{hours} h': '{hours} ч',
      '{percent} recorded': 'Записано {percent}',
      'Select a month to open its graphs. All values in kWh.': 'Выберите месяц, чтобы открыть графики. Все значения в кВт·ч.',
      'Monthly energy totals. Scroll to see all columns.': 'Итоги энергии по месяцам. Прокрутите, чтобы увидеть все столбцы.',
      'Monthly energy totals in kWh': 'Итоги энергии по месяцам в кВт·ч',
      'Open {month} history': 'Открыть историю: {month}',
      'Totals since recording began, not inverter lifetime totals. Missing periods are excluded; coverage is shown for each reading.': 'Итоги с начала записи, а не за весь срок работы инвертора. Пропуски не учитываются; полнота данных указана для каждого показателя.',
      'All-time totals are not available yet. Day, week and month still work.': 'Общие итоги пока недоступны. Просмотр по дням, неделям и месяцам работает.',
      '{n}d': '{n}д', '{n}h': '{n}ч', '{n}m': '{n}мин', '{n}s': '{n}с',
      'Enter a valid date as DD/MM/YYYY, not in the future.': 'Введите корректную дату ДД/ММ/ГГГГ, не позднее сегодняшней.',
      'Mode': 'Режим', 'Mixed': 'Смешанный', 'No data': 'Нет данных', 'Unknown supply': 'Источник неизвестен', 'Mode not recorded': 'Режим не записан', 'Duration': 'Длительность',
      'Estimated source supplying the house': 'Расчётный источник питания дома',
      'Observed transitions; timing depends on polling. Gaps start 90 seconds after the last report.': 'Зафиксированные переходы; точность зависит от опроса. Пробелы начинаются через 90 секунд после последней отчётной записи.',
      'PV voltage (legacy)': 'Напряжение PV (архив)',
      'Solar total': 'Солнце · всего', 'PV1 power': 'Мощность PV1', 'PV2 power': 'Мощность PV2',
      'PV1 voltage': 'Напряжение PV1', 'PV2 voltage': 'Напряжение PV2',
      'PV1 current': 'Ток PV1', 'PV2 current': 'Ток PV2',
      'Waiting to charge': 'Ожидание зарядки', 'Discharging': 'Разрядка',
      'Estimated flow': 'Расчётный поток', 'Estimated': 'Расчёт',
      'Waiting for 40% charge before battery use resumes.': 'Ожидание заряда 40% для возобновления работы от батареи.',
      'Current': 'Ток', 'Solar current': 'Ток солнечных панелей', 'House current': 'Ток нагрузки дома',
      'Battery current (avg.)': 'Ток батареи (средний)', 'A (avg.)': 'A (сред.)',
      'Communication lost': 'Связь потеряна',
      'Live inverter data is unavailable. Check the dongle, home Wi-Fi, or VPN connection.': 'Текущие данные инвертора недоступны. Проверьте адаптер, домашнюю сеть Wi-Fi или VPN-соединение.',
      'Unable to reach the data service. Check your internet connection or try again shortly.': 'Не удаётся связаться с сервисом данных. Проверьте подключение к интернету или повторите попытку позже.',
      'Showing last known readings — not live data.': 'Показаны последние полученные показания — это не текущие данные.',
      'No readings are available yet.': 'Показания пока недоступны.',
      'Daily summary': 'Итоги дня', 'Weekly summary': 'Итоги недели', 'Monthly summary': 'Итоги месяца',
      'Solar generated': 'Солнечная генерация', 'Grid consumed': 'Энергия из сети', 'House usage': 'Потребление дома', 'Battery supplied': 'Энергия от батареи', 'Solar to house': 'Солнечная энергия для дома',
      'Battery charged': 'Энергия в батарею', 'Not available': 'Недоступно',
      'Battery charged: energy into the battery (solar or grid), not remaining capacity.': 'Энергия в батарею — поступившая от солнца или сети энергия, а не оставшийся заряд.',
      '{duration} recorded': 'Записано {duration}', 'Estimated from recorded readings only; missing periods are excluded.': 'Оценка по записанным показаниям; периоды без данных не учитываются.',
      'Grid voltage': 'Напряжение сети', 'Voltage': 'Напряжение',
      'Language': 'Язык', 'Home · Energy': 'Дом · Энергия', 'Energy history · Home': 'История энергии · Дом',
      'Home energy overview': 'Обзор энергии дома', 'HOME ENERGY': 'ЭНЕРГИЯ ДОМА', 'Mode · ': 'Режим · ',
      'Inverter operating mode': 'Режим работы инвертора', 'History ↗': 'История ↗',
      'House power information.': 'Энергоснабжение дома.', 'POWER OVERVIEW': 'ОБЗОР МОЩНОСТИ',
      'Solar': 'Солнце', 'Grid': 'Сеть', 'Grid (est.)': 'Сеть (расч.)', 'Battery': 'Батарея', 'House': 'Дом',
      'A connected view of your home.': 'Энергопотоки вашего дома.', 'Grid power': 'Мощность сети',
      'Solar production': 'Солнечная генерация', 'House consumption': 'Потребление дома', 'Total house load': 'Общая нагрузка дома',
      'Estimate unavailable': 'Расчёт недоступен', 'Estimated · house + battery': 'Расчёт · дом + батарея',
      'Some readings unavailable': 'Часть показаний недоступна', 'Waiting for fresh data': 'Ожидание новых данных',
      'Connection unavailable': 'Нет соединения', 'Last inverter report': 'Последние данные инвертора', 'Athens': 'Афины',
      'Waiting for inverter readings': 'Ожидание данных инвертора', 'Power On': 'Включение', 'Standby': 'Ожидание',
      'Bypass': 'Байпас', 'Charging': 'Зарядка', 'Fault': 'Ошибка', 'Connecting': 'Подключение', 'Waiting for readings': 'Ожидание показаний',
      '← Overview': '← Обзор', 'Energy history': 'История энергии', 'Your home, over time.': 'Энергия вашего дома во времени.',
      'Day': 'День', 'Week': 'Неделя', 'Month': 'Месяц', 'Athens time': 'Время Афин', 'Select all': 'Выбрать всё', 'Clear all': 'Снять выбор',
      'Average over': 'Усреднение', 'Auto': 'Авто', '1 minute': '1 минута', '5 minutes': '5 минут', '15 minutes': '15 минут', '1 hour': '1 час',
      'Loading history…': 'Загрузка истории…', 'History is unavailable. Please try again shortly.': 'История недоступна. Попробуйте позже.',
      'Refreshing…': 'Обновление…', 'Refresh': 'Обновить', 'No report': 'Нет данных', 'Hourly averages': 'Средние за час',
      '1-minute averages': 'Средние за 1 мин', '{n}-minute averages': 'Средние за {n} мин', 'No readings for this period.': 'За этот период нет показаний.',
      'Select one or more statistics to display their graphs.': 'Выберите показатели для отображения графиков.',
      'Power': 'Мощность', 'Battery charge': 'Заряд батареи', 'PV voltage': 'Напряжение PV', 'Battery power': 'Мощность батареи', 'Battery %': 'Батарея %',
      'Show peaks': 'Показать экстремумы', 'Max': 'Макс.', 'Min': 'Мин.',
      'Peaks of displayed averages · visible range': 'Экстремумы средних значений · видимый интервал',
      'Reset zoom': 'Сброс масштаба', 'Loading…': 'Загрузка…', 'History unavailable.': 'История недоступна.',
      'Hover or touch the chart to inspect an interval.': 'Наведите курсор или коснитесь графика для просмотра данных.',
      'No data collected in this period.': 'За этот период данные не собраны.', 'Choose date': 'Выбрать дату',
      'Previous period': 'Предыдущий период', 'Next period': 'Следующий период', 'Statistics': 'Показатели',
      'History period': 'Период истории', 'History charts': 'Графики истории', 'Energy readings': 'Показания энергии',
      'Illustrated home energy flow': 'Схема энергопотоков дома', 'Solar panels on a house connected to the grid and a home battery': 'Дом с солнечными панелями, сетью и батареей',
      '{title} chart. Use left and right arrows to inspect values.': 'График: {title}. Используйте стрелки влево и вправо для просмотра значений.',
      '{title} over time': '{title} во времени'
    },
    el: {
      'Overview': 'Επισκόπηση', 'History': 'Ιστορικό', 'Main navigation': 'Κύρια πλοήγηση',
      'Live · updated {seconds}s ago': 'Ζωντανά · ενημέρωση πριν από {seconds} δ',
      'Displayed statistics': 'Εμφανιζόμενα στοιχεία', 'Legacy readings': 'Παλαιές μετρήσεις',
      'Energy summaries. Scroll to see more.': 'Σύνοψη ενέργειας. Κάντε κύλιση για περισσότερα.',
      'Battery: + charging · − discharging': 'Μπαταρία: + φόρτιση · − εκφόρτιση',
      'Fit recorded time': 'Εστίαση στις καταγραφές', 'Rapid changes': 'Συχνές αλλαγές', '{n} changes': '{n} αλλαγές',
      'Electrical details': 'Ηλεκτρικά στοιχεία', 'PV channels, voltage and current': 'Κανάλια PV, τάση και ρεύμα',
      '{n} selected': '{n} επιλεγμένα',
      'Rapid changes are grouped. Select a block to zoom into individual intervals.': 'Οι συχνές αλλαγές ομαδοποιούνται. Επιλέξτε ένα τμήμα για να δείτε τα επιμέρους διαστήματα.',
      'Select an interval for exact times. Drag across the timeline to zoom.': 'Επιλέξτε διάστημα για ακριβείς ώρες. Σύρετε στη χρονογραμμή για μεγέθυνση.',
      'Inverter · ': 'Μετατροπέας · ', 'Off-Grid': 'Εκτός δικτύου',
      'House supply · {source}': 'Τροφοδοσία σπιτιού · {source}',
      'Not refreshed yet': 'Δεν ανανεώθηκε ακόμη',
      'Snapshot loaded {time} · refresh to update': 'Φόρτωση στις {time} · ανανεώστε για νέες μετρήσεις',
      'House supply': 'Τροφοδοσία σπιτιού', 'Zoom in': 'Μεγέθυνση', 'Upcoming': 'Αργότερα',
      'Start': 'Έναρξη', 'End': 'Λήξη', 'Previous interval': 'Προηγούμενο διάστημα', 'Next interval': 'Επόμενο διάστημα',
      'Select an interval to see its times.': 'Επιλέξτε ένα διάστημα για να δείτε τις ώρες του.',
      '{n} changes · select to zoom': '{n} αλλαγές · επιλέξτε για μεγέθυνση',
      'Striped blocks contain multiple changes. Select to zoom, or drag across the timeline.': 'Τα ριγέ τμήματα περιέχουν πολλές αλλαγές. Επιλέξτε για μεγέθυνση ή σύρετε πάνω στη χρονογραμμή.',
      'Today': 'Σήμερα',
      'Previous month': 'Προηγούμενος μήνας', 'Next month': 'Επόμενος μήνας', 'Close calendar': 'Κλείσιμο ημερολογίου',
      'Recorded data available': 'Υπάρχουν καταγεγραμμένα δεδομένα', 'No recorded data': 'Χωρίς καταγεγραμμένα δεδομένα', 'Future date': 'Μελλοντική ημερομηνία',
      'Loading recorded dates…': 'Φόρτωση ημερομηνιών με καταγραφές…', 'Recorded dates unavailable': 'Οι ημερομηνίες με καταγραφές δεν είναι διαθέσιμες',
      'A highlight means some readings exist, not a complete day.': 'Η επισήμανση δείχνει ότι υπάρχουν μετρήσεις, όχι απαραίτητα για ολόκληρη την ημέρα.',
      'Use arrow keys for days, Page Up or Page Down for months, and Escape to close.': 'Βέλη για ημέρες, Page Up ή Page Down για μήνες και Escape για κλείσιμο.',
      'All time': 'Σύνολο', 'Total recorded': 'Συνολική καταγεγραμμένη ενέργεια',
      'All available recorded history': 'Όλο το διαθέσιμο ιστορικό',
      'Monthly breakdown': 'Ανάλυση ανά μήνα', 'Grand total': 'Γενικό σύνολο',
      'Recorded energy · kWh': 'Καταγεγραμμένη ενέργεια · kWh',
      'Month in progress': 'Τρέχων μήνας', 'No recorded energy yet.': 'Δεν έχει καταγραφεί ακόμη ενέργεια.',
      '{percent} recorded · {duration}': '{percent} καταγραφή · {duration}',
      '{days} d - {hours} h': '{days} ημ. - {hours} ώρ.', '{hours} h': '{hours} ώρ.',
      '{percent} recorded': '{percent} καταγραφή',
      'Select a month to open its graphs. All values in kWh.': 'Επιλέξτε μήνα για τα γραφήματά του. Όλες οι τιμές σε kWh.',
      'Monthly energy totals. Scroll to see all columns.': 'Σύνολα ενέργειας ανά μήνα. Κάντε κύλιση για όλες τις στήλες.',
      'Monthly energy totals in kWh': 'Σύνολα ενέργειας ανά μήνα σε kWh',
      'Open {month} history': 'Άνοιγμα ιστορικού: {month}',
      'Totals since recording began, not inverter lifetime totals. Missing periods are excluded; coverage is shown for each reading.': 'Σύνολα από την έναρξη της καταγραφής, όχι από την πρώτη λειτουργία του μετατροπέα. Τα κενά εξαιρούνται· εμφανίζεται η κάλυψη κάθε μέτρησης.',
      'All-time totals are not available yet. Day, week and month still work.': 'Τα συνολικά δεδομένα δεν είναι ακόμη διαθέσιμα. Η ημέρα, η εβδομάδα και ο μήνας λειτουργούν κανονικά.',
      '{n}d': '{n}ημ', '{n}h': '{n}ω', '{n}m': '{n}λ', '{n}s': '{n}δ',
      'Enter a valid date as DD/MM/YYYY, not in the future.': 'Εισαγάγετε έγκυρη ημερομηνία ΗΗ/ΜΜ/ΕΕΕΕ, όχι στο μέλλον.',
      'Mode': 'Λειτουργία', 'Mixed': 'Μικτή', 'No data': 'Χωρίς δεδομένα', 'Unknown supply': 'Άγνωστη πηγή', 'Mode not recorded': 'Δεν καταγράφηκε', 'Duration': 'Διάρκεια',
      'Estimated source supplying the house': 'Εκτιμώμενη πηγή τροφοδοσίας σπιτιού',
      'Observed transitions; timing depends on polling. Gaps start 90 seconds after the last report.': 'Καταγεγραμμένες μεταβάσεις· η ακρίβεια εξαρτάται από τη συχνότητα λήψης. Τα κενά ξεκινούν 90 δευτερόλεπτα μετά την τελευταία αναφορά.',
      'PV voltage (legacy)': 'Τάση PV (παλαιά δεδομένα)',
      'Solar total': 'Ηλιακή · σύνολο', 'PV1 power': 'Ισχύς PV1', 'PV2 power': 'Ισχύς PV2',
      'PV1 voltage': 'Τάση PV1', 'PV2 voltage': 'Τάση PV2',
      'PV1 current': 'Ρεύμα PV1', 'PV2 current': 'Ρεύμα PV2',
      'Waiting to charge': 'Αναμονή φόρτισης', 'Discharging': 'Εκφόρτιση',
      'Estimated flow': 'Εκτιμώμενη ροή', 'Estimated': 'Εκτίμηση',
      'Waiting for 40% charge before battery use resumes.': 'Αναμονή φόρτισης στο 40% για επαναφορά της χρήσης μπαταρίας.',
      'Current': 'Ένταση ρεύματος', 'Solar current': 'Ρεύμα φωτοβολταϊκών', 'House current': 'Ρεύμα σπιτιού',
      'Battery current (avg.)': 'Ρεύμα μπαταρίας (μέσο)', 'A (avg.)': 'A (μέσο)',
      'Communication lost': 'Η επικοινωνία χάθηκε',
      'Live inverter data is unavailable. Check the dongle, home Wi-Fi, or VPN connection.': 'Τα ζωντανά δεδομένα του μετατροπέα δεν είναι διαθέσιμα. Ελέγξτε τον προσαρμογέα, το οικιακό Wi-Fi ή τη σύνδεση VPN.',
      'Unable to reach the data service. Check your internet connection or try again shortly.': 'Δεν είναι δυνατή η σύνδεση με την υπηρεσία δεδομένων. Ελέγξτε τη σύνδεσή σας στο διαδίκτυο ή δοκιμάστε ξανά σε λίγο.',
      'Showing last known readings — not live data.': 'Εμφανίζονται οι τελευταίες γνωστές μετρήσεις — όχι ζωντανά δεδομένα.',
      'No readings are available yet.': 'Δεν υπάρχουν ακόμη διαθέσιμες μετρήσεις.',
      'Daily summary': 'Ημερήσια σύνοψη', 'Weekly summary': 'Εβδομαδιαία σύνοψη', 'Monthly summary': 'Μηνιαία σύνοψη',
      'Solar generated': 'Ηλιακή παραγωγή', 'Grid consumed': 'Ενέργεια από το δίκτυο', 'House usage': 'Κατανάλωση σπιτιού', 'Battery supplied': 'Ενέργεια από μπαταρία', 'Solar to house': 'Ηλιακή ενέργεια στο σπίτι',
      'Battery charged': 'Ενέργεια προς μπαταρία', 'Not available': 'Μη διαθέσιμο',
      'Battery charged: energy into the battery (solar or grid), not remaining capacity.': 'Ενέργεια προς μπαταρία: ενέργεια φόρτισης από τον ήλιο ή το δίκτυο, όχι η υπολειπόμενη χωρητικότητα.',
      '{duration} recorded': '{duration} καταγραφής', 'Estimated from recorded readings only; missing periods are excluded.': 'Εκτίμηση από τις καταγεγραμμένες μετρήσεις· τα διαστήματα χωρίς δεδομένα εξαιρούνται.',
      'Grid voltage': 'Τάση δικτύου', 'Voltage': 'Τάση',
      'Language': 'Γλώσσα', 'Home · Energy': 'Σπίτι · Ενέργεια', 'Energy history · Home': 'Ιστορικό ενέργειας · Σπίτι',
      'Home energy overview': 'Επισκόπηση ενέργειας σπιτιού', 'HOME ENERGY': 'ΕΝΕΡΓΕΙΑ ΣΠΙΤΙΟΥ', 'Mode · ': 'Λειτουργία · ',
      'Inverter operating mode': 'Λειτουργία μετατροπέα', 'History ↗': 'Ιστορικό ↗',
      'House power information.': 'Ενέργεια του σπιτιού.', 'POWER OVERVIEW': 'ΕΠΙΣΚΟΠΗΣΗ ΙΣΧΥΟΣ',
      'Solar': 'Ηλιακή', 'Grid': 'Δίκτυο', 'Grid (est.)': 'Δίκτυο (εκτ.)', 'Battery': 'Μπαταρία', 'House': 'Σπίτι',
      'A connected view of your home.': 'Οι ροές ενέργειας του σπιτιού σας.', 'Grid power': 'Ισχύς δικτύου',
      'Solar production': 'Ηλιακή παραγωγή', 'House consumption': 'Κατανάλωση σπιτιού', 'Total house load': 'Συνολικό φορτίο σπιτιού',
      'Estimate unavailable': 'Μη διαθέσιμη εκτίμηση', 'Estimated · house + battery': 'Εκτίμηση · σπίτι + μπαταρία',
      'Some readings unavailable': 'Ορισμένες μετρήσεις δεν είναι διαθέσιμες', 'Waiting for fresh data': 'Αναμονή νέων δεδομένων',
      'Connection unavailable': 'Δεν υπάρχει σύνδεση', 'Last inverter report': 'Τελευταία αναφορά μετατροπέα', 'Athens': 'Αθήνα',
      'Waiting for inverter readings': 'Αναμονή μετρήσεων μετατροπέα', 'Power On': 'Εκκίνηση', 'Standby': 'Αναμονή',
      'Bypass': 'Παράκαμψη', 'Charging': 'Φόρτιση', 'Fault': 'Σφάλμα', 'Connecting': 'Σύνδεση', 'Waiting for readings': 'Αναμονή μετρήσεων',
      '← Overview': '← Επισκόπηση', 'Energy history': 'Ιστορικό ενέργειας', 'Your home, over time.': 'Η ενέργεια του σπιτιού σας διαχρονικά.',
      'Day': 'Ημέρα', 'Week': 'Εβδομάδα', 'Month': 'Μήνας', 'Athens time': 'Ώρα Αθήνας', 'Select all': 'Επιλογή όλων', 'Clear all': 'Αποεπιλογή όλων',
      'Average over': 'Μέσος όρος ανά', 'Auto': 'Αυτόματα', '1 minute': '1 λεπτό', '5 minutes': '5 λεπτά', '15 minutes': '15 λεπτά', '1 hour': '1 ώρα',
      'Loading history…': 'Φόρτωση ιστορικού…', 'History is unavailable. Please try again shortly.': 'Το ιστορικό δεν είναι διαθέσιμο. Δοκιμάστε ξανά σε λίγο.',
      'Refreshing…': 'Ανανέωση…', 'Refresh': 'Ανανέωση', 'No report': 'Χωρίς δεδομένα', 'Hourly averages': 'Ωριαίοι μέσοι όροι',
      '1-minute averages': 'Μέσοι όροι 1 λεπτού', '{n}-minute averages': 'Μέσοι όροι {n} λεπτών', 'No readings for this period.': 'Δεν υπάρχουν μετρήσεις για αυτή την περίοδο.',
      'Select one or more statistics to display their graphs.': 'Επιλέξτε μετρήσεις για να εμφανιστούν τα γραφήματα.',
      'Power': 'Ισχύς', 'Battery charge': 'Φόρτιση μπαταρίας', 'PV voltage': 'Τάση PV', 'Battery power': 'Ισχύς μπαταρίας', 'Battery %': 'Μπαταρία %',
      'Show peaks': 'Εμφάνιση ακρότατων', 'Max': 'Μέγ.', 'Min': 'Ελάχ.',
      'Peaks of displayed averages · visible range': 'Ακρότατα μέσων τιμών · ορατό διάστημα',
      'Reset zoom': 'Επαναφορά ζουμ', 'Loading…': 'Φόρτωση…', 'History unavailable.': 'Το ιστορικό δεν είναι διαθέσιμο.',
      'Hover or touch the chart to inspect an interval.': 'Τοποθετήστε τον δείκτη ή αγγίξτε το γράφημα για λεπτομέρειες.',
      'No data collected in this period.': 'Δεν συλλέχθηκαν δεδομένα σε αυτή την περίοδο.', 'Choose date': 'Επιλογή ημερομηνίας',
      'Previous period': 'Προηγούμενη περίοδος', 'Next period': 'Επόμενη περίοδος', 'Statistics': 'Μετρήσεις',
      'History period': 'Περίοδος ιστορικού', 'History charts': 'Γραφήματα ιστορικού', 'Energy readings': 'Ενεργειακές μετρήσεις',
      'Illustrated home energy flow': 'Διάγραμμα ροής ενέργειας σπιτιού', 'Solar panels on a house connected to the grid and a home battery': 'Σπίτι με φωτοβολταϊκά, δίκτυο και μπαταρία',
      '{title} chart. Use left and right arrows to inspect values.': 'Γράφημα: {title}. Χρησιμοποιήστε τα βέλη για προβολή τιμών.',
      '{title} over time': '{title} διαχρονικά'
    }
  };
  let language = 'en';
  try { const saved = localStorage.getItem('homeenergy-language'); if (['en', 'ru', 'el'].includes(saved)) language = saved; } catch { /* Storage may be blocked. */ }
  const t = (key, values = {}) => (translations[language]?.[key] ?? key).replace(/\{(\w+)\}/g, (match, name) => values[name] ?? match);
  // Only translate explicit text nodes so live readings and SVGs stay intact.
  const selectors = ['.brand-sub', '.mode-badge', '.history-nav a', '.intro h1', '.intro p', '.scene-heading>span:last-child', '.scene-label>div', '.scene-caption', '.metric-top>span:first-child', '.metric.load p', '.back', '[data-period]', '.date-controls>span', '#today', '#select-all', '#clear-all', 'label[for="averaging"]', '#averaging option'];
  const texts = selectors.flatMap(selector => [...document.querySelectorAll(selector)].map(element => {
    const node = [...element.childNodes].find(child => child.nodeType === Node.TEXT_NODE && child.textContent.trim());
    return node ? { node, key: node.textContent } : null;
  }).filter(Boolean));
  const attributes = [...document.querySelectorAll('[aria-label], [title]')].flatMap(element => ['aria-label', 'title'].filter(name => element.hasAttribute(name)).map(name => ({ element, name, key: element.getAttribute(name) })));
  const pageTitle = document.title;
  const languageNames = { en: 'English', ru: 'Русский', el: 'Ελληνικά' };
  const control = document.createElement('div');
  control.className = 'language-control';
  function icon(shapes, className = '') {
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    const attributes = {
      viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': '1.6',
      'stroke-linecap': 'round', 'stroke-linejoin': 'round', 'aria-hidden': 'true',
      focusable: 'false', class: className
    };
    for (const [name, value] of Object.entries(attributes)) svg.setAttribute(name, value);
    for (const [tag, attributes] of shapes) {
      const shape = document.createElementNS('http://www.w3.org/2000/svg', tag);
      for (const [name, value] of Object.entries(attributes)) shape.setAttribute(name, value);
      svg.append(shape);
    }
    return svg;
  }
  const trigger = document.createElement('button');
  trigger.type = 'button';
  trigger.id = 'language';
  trigger.className = 'language-trigger';
  trigger.setAttribute('aria-haspopup', 'menu');
  trigger.setAttribute('aria-expanded', 'false');
  trigger.setAttribute('aria-controls', 'language-menu');
  const languageName = document.createElement('span');
  trigger.append(icon([
    ['circle', { cx: '12', cy: '12', r: '9' }],
    ['ellipse', { cx: '12', cy: '12', rx: '4', ry: '9' }],
    ['path', { d: 'M3 12h18' }]
  ]), languageName, icon([['path', { d: 'm7 10 5 5 5-5' }]], 'language-chevron'));
  const menu = document.createElement('div');
  menu.id = 'language-menu';
  menu.className = 'language-menu';
  menu.setAttribute('role', 'menu');
  menu.hidden = true;
  const options = Object.entries(languageNames).map(([value, name]) => {
    const option = document.createElement('button');
    option.type = 'button';
    option.dataset.language = value;
    option.tabIndex = -1;
    option.setAttribute('role', 'menuitemradio');
    const code = document.createElement('span');
    code.className = 'language-code';
    code.textContent = value.toUpperCase();
    code.setAttribute('aria-hidden', 'true');
    const label = document.createElement('span');
    label.textContent = name;
    label.setAttribute('lang', value);
    option.append(code, label, icon([['path', { d: 'm5 12 4 4L19 6' }]], 'language-check'));
    menu.append(option);
    return option;
  });
  control.append(trigger, menu);
  document.querySelector('header').append(control);
  function apply() {
    document.documentElement.lang = language;
    document.title = t(pageTitle);
    languageName.textContent = languageNames[language];
    languageName.setAttribute('lang', language);
    trigger.setAttribute('aria-label', `${t('Language')}: ${languageNames[language]}`);
    menu.setAttribute('aria-label', t('Language'));
    options.forEach(option => option.setAttribute('aria-checked', String(option.dataset.language === language)));
    texts.forEach(({ node, key }) => { node.textContent = t(key); });
    attributes.forEach(({ element, name, key }) => element.setAttribute(name, t(key)));
    document.querySelectorAll('[data-i18n]').forEach(element => { element.textContent = t(element.dataset.i18n); });
  }
  function closeMenu(restoreFocus = false) {
    menu.hidden = true;
    trigger.setAttribute('aria-expanded', 'false');
    if (restoreFocus) trigger.focus();
  }
  function openMenu(index = 0) {
    menu.hidden = false;
    trigger.setAttribute('aria-expanded', 'true');
    options[index].focus();
  }
  trigger.addEventListener('click', () => {
    if (menu.hidden) openMenu();
    else closeMenu(true);
  });
  trigger.addEventListener('keydown', event => {
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      openMenu(event.key === 'ArrowUp' ? options.length - 1 : 0);
    }
  });
  options.forEach(option => option.addEventListener('click', () => {
    const next = option.dataset.language;
    if (!Object.hasOwn(languageNames, next)) return;
    language = next;
    try { localStorage.setItem('homeenergy-language', language); } catch { /* Still works for this page. */ }
    apply();
    closeMenu(true);
    window.dispatchEvent(new Event('languagechange'));
  }));
  menu.addEventListener('keydown', event => {
    const index = options.indexOf(document.activeElement);
    if (event.key === 'Escape' || event.key === 'Tab') {
      // Let normal Tab navigation continue from the trigger, not a hidden item.
      if (event.key === 'Escape') event.preventDefault();
      closeMenu(true);
    } else if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) {
      event.preventDefault();
      const next = event.key === 'Home' ? 0 : event.key === 'End' ? options.length - 1
        : (index + (event.key === 'ArrowDown' ? 1 : -1) + options.length) % options.length;
      options[next].focus();
    } else if (event.key.length === 1 && !event.altKey && !event.ctrlKey && !event.metaKey && event.key !== ' ') {
      const key = event.key.toLocaleLowerCase();
      for (let offset = 1; offset <= options.length; offset += 1) {
        const option = options[(index + offset) % options.length];
        const code = option.dataset.language;
        if (code.startsWith(key) || languageNames[code].toLocaleLowerCase().startsWith(key)) {
          event.preventDefault();
          option.focus();
          break;
        }
      }
    }
  });
  document.addEventListener('pointerdown', event => {
    if (!menu.hidden && !control.contains(event.target)) closeMenu();
  });
  control.addEventListener('focusout', event => {
    if (!control.contains(event.relatedTarget)) closeMenu();
  });
  apply();
  return { t, get locale() { return { en: 'en-GB', ru: 'ru-RU', el: 'el-GR' }[language]; } };
})();

if (document.querySelector('.energy-scene')) {
const { t } = window.energyI18n;
// Simulation is limited to the local preview. Production reads the allowlisted endpoint.
const demo = location.hostname === '127.0.0.1' && location.port === '8766';
const samples = [
  { grid: 535, pv: 0, load: 563, battery: 0, soc: 20, pv_voltage: 34.7, grid_voltage: 234.5, pv_current: 0, battery_current: 0, load_current: 3.1, mode: 'Mains' },
  { grid: 0, pv: 1580, load: 910, battery: 670, soc: 84, pv_voltage: 382.9, grid_voltage: 232, pv_current: 4.1, battery_current: 12.7, load_current: 4.5, mode: 'Off-Grid' },
  { grid: 0, pv: 410, load: 910, battery: -500, soc: 84, pv_voltage: 360, grid_voltage: 232, pv_current: 1.1, battery_current: -9.5, load_current: 4.5, mode: 'Off-Grid' },
  { grid: 0, pv: 0, load: 940, battery: -940, soc: 64, pv_voltage: 31, grid_voltage: 232, pv_current: 0, battery_current: -18, load_current: 4.8, mode: 'Off-Grid' },
];
// Explicitly synthetic channel values for the loopback-only preview.
samples.forEach(sample => {
  for (const channel of [1, 2]) {
    const power = sample.pv * (channel === 1 ? .6 : .4);
    const voltage = sample.pv ? (channel === 1 ? 382.9 : 237.2) : 30;
    Object.assign(sample, { [`pv${channel}_power`]: power, [`pv${channel}_voltage`]: voltage,
      [`pv${channel}_current`]: power / voltage });
  }
});
const pvChannelViews = [];
for (const selector of ['.solar-label>div', '.metric.solar']) {
  const parent = document.querySelector(selector);
  parent.querySelector('[data-value="pv_voltage"]').parentElement.remove();
  const channels = document.createElement('div'); channels.className = 'pv-channels';
  for (const channel of [1, 2]) {
    const row = document.createElement('div'); row.className = 'pv-channel';
    const name = document.createElement('strong'); name.textContent = `PV${channel}`;
    const power = document.createElement('span'); power.className = 'pv-channel-power';
    const details = document.createElement('span'); details.className = 'pv-channel-details';
    row.append(name, power, details); channels.append(row);
    pvChannelViews.push({ channel, power, details });
  }
  parent.append(channels);
}
let sampleIndex = 0;
const currentReadings = [];
for (const [name, key, label] of [
  ['battery', 'battery_current', 'Battery current (avg.)'],
  ['load', 'load_current', 'House current'],
]) {
  for (const [selector, tag] of [[`.${name}-label>div`, 'span'], [`.metric.${name}`, 'p']]) {
    if (name === 'battery' && tag === 'p') continue;
    const line = document.createElement(tag);
    line.className = 'current-reading';
    const value = document.createElement('b'); value.dataset.value = key;
    const unit = document.createElement('small');
    line.append(value, document.createTextNode(' '), unit);
    const parent = document.querySelector(selector);
    const meter = parent.querySelector('.meter');
    parent.insertBefore(line, meter);
    currentReadings.push({ line, unit, label, key, battery: name === 'battery' });
  }
}
// Existing illustration stays decorative; paths attach to the actual card edges.
const scene = document.querySelector('.energy-scene');
const svgNS = 'http://www.w3.org/2000/svg';
function svgElement(tag, attributes = {}) {
  const element = document.createElementNS(svgNS, tag);
  for (const [name, value] of Object.entries(attributes)) element.setAttribute(name, value);
  return element;
}
const icons = {
  grid: 'M13 2 4 14h7l-1 8 10-13h-7l1-7Z',
  solar: 'M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1.5 1.5m11 11L19 19M5 19l1.5-1.5m11-11L19 5M16 12a4 4 0 1 1-8 0 4 4 0 0 1 8 0',
  battery: 'M3 7h16v10H3V7Zm16 3h2v4h-2M6 10v4m3-4v4m3-4v4',
  load: 'm3 11 9-8 9 8M5 10v11h14V10M10 21v-7h4v7',
};
for (const [name, d] of Object.entries(icons)) {
  document.querySelectorAll(`.${name}-label .mini-icon, .metric.${name} .symbol`).forEach(holder => {
    const icon = svgElement('svg', { viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': '1.7', 'stroke-linecap': 'round', 'stroke-linejoin': 'round', 'aria-hidden': 'true' });
    icon.append(svgElement('path', { d })); holder.replaceChildren(icon);
  });
}
const flowAnimation = window.energyComets.create(scene);
let paused = false;
const pauseButton = document.querySelector('#pause');
pauseButton.hidden = !demo;
document.querySelector('.disclaimer').hidden = !demo;
function updateDate() { document.querySelector('#date').textContent = new Intl.DateTimeFormat('en-GB', { day: '2-digit', month: '2-digit', year: 'numeric', timeZone: 'Europe/Athens' }).format(new Date()); }
updateDate();
function formatPower(watts) {
  if (!Number.isFinite(watts)) return { value: '—', unit: 'W' };
  const kilo = Math.abs(watts) >= 1000;
  return { value: new Intl.NumberFormat(window.energyI18n.locale, { maximumFractionDigits: kilo ? 2 : 0, useGrouping: false }).format(kilo ? watts / 1000 : watts), unit: kilo ? 'kW' : 'W' };
}
function render(sample) {
  currentReadings.forEach(({ line, unit, label, key, battery }) => {
    line.title = t(label);
    unit.textContent = battery ? t('A (avg.)') : 'A';
    line.hidden = Number.isFinite(sample[key]) && Math.abs(sample[key]) < 0.05;
    if (battery && Number.isFinite(sample.battery) && Math.abs(sample.battery) < 0.5) line.hidden = true;
  });
  // Dashboard convention: positive = charging, negative = discharging.
  const mode = demo ? sample.mode : latest?.mode;
  const state = window.energyFlowState(sample, mode);
  const supply = window.energySupplyLabel(sample);
  document.querySelector('#supply-status').textContent = t('House supply · {source}', { source: t(supply) });
  document.body.dataset.activeSource = { Grid: 'grid', Solar: 'solar', Battery: 'battery' }[supply] || '';
  document.body.dataset.batteryState = state.batteryState;
  document.querySelectorAll('.battery-status').forEach(element => {
    element.textContent = t(state.batteryLabel);
    element.title = state.batteryState === 'waiting' ? t('Waiting for 40% charge before battery use resumes.') : '';
  });
  document.querySelectorAll('.battery-watts').forEach(element => { element.hidden = Number.isFinite(sample.battery) && Math.abs(sample.battery) < 0.5; });
  const values = sample;
  document.querySelectorAll('[data-value]').forEach(element => {
    const key = element.dataset.value;
    if (key === 'soc') {
      element.textContent = values[key] ?? '—';
    } else if (key.endsWith('_voltage') || key.endsWith('_current')) {
      element.textContent = Number.isFinite(values[key]) ? new Intl.NumberFormat(window.energyI18n.locale, { maximumFractionDigits: 1 }).format(values[key]) : '—';
    } else {
      const power = formatPower(values[key]);
      element.textContent = power.value;
      element.nextElementSibling.textContent = power.unit;
    }
  });
  for (const { channel, power, details } of pvChannelViews) {
    const prefix = `pv${channel}_`;
    const watts = formatPower(values[prefix + 'power']);
    power.textContent = `${watts.value} ${watts.unit}`;
    power.title = t(`PV${channel} power`);
    const format = value => Number.isFinite(value)
      ? new Intl.NumberFormat(window.energyI18n.locale, { maximumFractionDigits: 1 }).format(value) : '—';
    const current = values[prefix + 'current'];
    details.textContent = `${format(values[prefix + 'voltage'])} V` +
      (Number.isFinite(current) && Math.abs(current) < .05 ? '' : ` · ${format(current)} A`);
    details.title = `${t(`PV${channel} voltage`)} · ${t(`PV${channel} current`)}`;
  }
  document.querySelectorAll('#charge-bar, .scene-charge-bar').forEach(element => { element.style.width = `${Math.max(0, Math.min(100, sample.soc ?? 0))}%`; });
  document.querySelector('#grid-state').textContent = t(Number.isFinite(values.grid) ? 'Estimated' : 'Estimate unavailable');
  document.querySelectorAll('.metric:not(.battery) .meter').forEach(element => { element.hidden = true; });
  flowAnimation.setState(state);
  if (demo) {
    document.querySelector('#operating-mode').textContent = t(window.energyModeLabel(mode));
    document.body.dataset.connection = 'live';
    flowAnimation.setEnabled(!document.body.classList.contains('paused'));
  }
  if (demo) document.querySelector('#freshness').textContent = `Sample updated ${new Date().toLocaleTimeString()}`;
}
pauseButton.addEventListener('click', () => {
  paused = !paused;
  document.body.classList.toggle('paused', paused);
  flowAnimation.setEnabled(!paused);
  pauseButton.textContent = paused ? 'Resume demo' : 'Pause demo';
  document.querySelector('#scene-status').textContent = paused ? 'Sample flow paused' : 'Sample energy flow';
  if (paused) document.querySelector('#freshness').textContent = 'Sample updates paused';
  else render(samples[sampleIndex]);
});
let latest = null;
let requestFailed = false;
let pending = false;
const connectionAlert = document.createElement('section');
connectionAlert.className = 'connection-alert';
connectionAlert.hidden = true;
connectionAlert.setAttribute('role', 'alert');
connectionAlert.innerHTML = '<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><path d="M12 3 2 21h20L12 3Z" stroke-linejoin="round"/><path d="M12 9v5m0 3v1"/></svg><div><strong></strong><p></p><small></small></div>';
document.querySelector('.energy-scene').before(connectionAlert);
function setAlertText(selector, text) {
  const element = connectionAlert.querySelector(selector);
  // Avoid repeating screen-reader announcements on every status timer tick.
  if (element.textContent !== text) element.textContent = text;
}
function updateStatus() {
  const now = Date.now() / 1000;
  const stale = !latest || !latest.updated_at || now - latest.updated_at > 90 || now - latest.generated_at > 20;
  const status = requestFailed ? 'offline' : stale ? 'stale' : latest.status;
  const labels = { live: '', partial: 'Some readings unavailable', stale: 'Waiting for fresh data', offline: 'Connection unavailable' };
  const communicationLost = ['stale', 'offline'].includes(status) && Boolean(latest || requestFailed);
  document.body.classList.toggle('communication-lost', communicationLost);
  if (communicationLost) {
    setAlertText('strong', t('Communication lost'));
    setAlertText('p', t(requestFailed
      ? 'Unable to reach the data service. Check your internet connection or try again shortly.'
      : 'Live inverter data is unavailable. Check the dongle, home Wi-Fi, or VPN connection.'));
    setAlertText('small', t(latest ? 'Showing last known readings — not live data.' : 'No readings are available yet.'));
  }
  connectionAlert.hidden = !communicationLost;
  document.body.dataset.connection = status;
  document.body.classList.toggle('paused', status !== 'live');
  flowAnimation.setEnabled(status === 'live');
  document.querySelector('.demo').lastChild.textContent = ` ${t(labels[status])}`;
  document.querySelector('#scene-status').textContent = t(labels[status]);
  document.querySelector('.demo').style.display = status === 'live' || communicationLost ? 'none' : '';
  document.querySelector('#scene-status').parentElement.style.visibility = status === 'live' || communicationLost ? 'hidden' : '';
  const mode = ['live', 'partial'].includes(status) ? latest?.mode : null;
  if (!['live', 'partial'].includes(status)) {
    document.body.dataset.activeSource = '';
    document.querySelector('#supply-status').textContent = t(labels[status]);
  }
  document.querySelector('#operating-mode').textContent = t(window.energyModeLabel(mode));
  document.querySelector('#freshness').textContent = latest?.updated_at
    ? `${t('Last inverter report')} ${new Date(latest.updated_at * 1000).toLocaleTimeString('en-GB', { timeZone: 'Europe/Athens' })} (${new Date(latest.updated_at * 1000).toLocaleDateString('en-GB', { timeZone: 'Europe/Athens' })}) · ${t('Athens')}`
    : t('Waiting for inverter readings');
  const liveStatus = document.querySelector('#live-status');
  if (liveStatus) {
    liveStatus.textContent = status === 'live'
      ? t('Live · updated {seconds}s ago', { seconds: Math.max(0, Math.floor(now - latest.updated_at)) })
      : t(labels[status] || 'Connection unavailable');
    liveStatus.dataset.state = status;
  }
}
async function refresh() {
  if (pending || document.hidden) return;
  pending = true;
  try {
    const response = await fetch('/api/energy', { cache: 'no-store', signal: AbortSignal.timeout(8000) });
    if (!response.ok) throw new Error('Unavailable');
    const data = await response.json();
    if (!data.values || !Number.isFinite(data.generated_at) ||
        !['live', 'partial', 'stale', 'offline'].includes(data.status) ||
        !['grid', 'pv', 'soc', 'battery', 'load'].every(key => data.values[key] === null || Number.isFinite(data.values[key]))) {
      throw new Error('Invalid response');
    }
    latest = data;
    requestFailed = false;
    render(data.values);
  } catch {
    requestFailed = true;
  } finally {
    pending = false;
    updateStatus();
  }
}
if (demo) {
  document.querySelector('.demo').lastChild.textContent = ' Demo preview';
  document.querySelector('#scene-status').textContent = 'Sample energy flow';
  const previewChoice = document.createElement('select');
  previewChoice.setAttribute('aria-label', 'Preview scenario');
  ['Grid / waiting', 'Solar / charging', 'Solar + battery', 'Battery only'].forEach((label, index) => {
    const option = document.createElement('option'); option.value = index; option.textContent = label; previewChoice.append(option);
  });
  previewChoice.addEventListener('change', () => { sampleIndex = Number(previewChoice.value); paused = true; pauseButton.textContent = 'Resume demo'; document.body.classList.remove('paused'); render(samples[sampleIndex]); });
  document.querySelector('footer').append(previewChoice);
  render(samples[0]);
  setInterval(() => {
    if (paused) return;
    sampleIndex = (sampleIndex + 1) % samples.length;
    previewChoice.value = sampleIndex;
    render(samples[sampleIndex]);
  }, 5000);
} else {
  render({ grid: null, pv: null, soc: null, battery: null, load: null });
  updateStatus();
  refresh();
  setInterval(refresh, 5000);
  setInterval(updateStatus, 1000);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
}
window.addEventListener('languagechange', () => {
  updateDate();
  if (latest) render(latest.values);
  else if (demo) render(samples[sampleIndex]);
  if (!demo) updateStatus();
});
}
