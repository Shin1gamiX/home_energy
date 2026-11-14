// Decorative Overview theme. No telemetry, control, storage or network access.
export function mount() {
  const body = document.body;
  const intro = document.querySelector('.intro');
  const footerControls = document.querySelector('.dashboard > footer');
  if (!body.classList.contains('overview') || !intro || !footerControls) return () => {};
  const copy = {
    en: { effects: 'Christmas effects', reduced: 'Reduced motion', footer: 'A little light. A little warmth. A place to call home.' },
    el: { effects: 'Χριστουγεννιάτικα εφέ', reduced: 'Περιορισμένη κίνηση', footer: 'Λίγο φως. Λίγη ζεστασιά. Το δικό μας σπίτι.' },
    ru: { effects: 'Рождественские эффекты', reduced: 'Без анимации', footer: 'Немного света. Немного тепла. И уют родного дома.' },
  };
  const snowIcon = '<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M12 2v20M3.3 7l17.4 10M3.3 17 20.7 7M9 4l3 3 3-3M9 20l3-3 3 3M4 10l4-1-1-4M17 19l-1-4 4-1M4 14l4 1-1 4M17 5l-1 4 4 1"/></svg>';
  // Small vector fir sprig. Decorative details never cover interactive content.
  let needles = '';
  for (let i = 0; i < 16; i++) {
    const x = 18 + i * 7, y = 43 - i * .9;
    needles += `<path d="M${x} ${y}l-12 -15m12 15l-17 9" opacity="${.45 + i % 3 * .2}"/>`;
  }
  intro.insertAdjacentHTML('beforeend', `<svg class="season-branch" aria-hidden="true" viewBox="0 0 160 75" fill="none" stroke="currentColor" stroke-linecap="round" stroke-width="1.5"><path d="M7 49Q84 30 145 31"/>${needles}<circle cx="65" cy="41" r="4" fill="var(--winter-red)" stroke="none"/><circle cx="75" cy="46" r="3.5" fill="var(--winter-red)" stroke="none"/><path d="m103 20 8 5-3 10-7-5Z" fill="var(--winter-soft-gold)" stroke="none"/></svg>`);
  const scene = document.querySelector('.energy-scene');
  const bulbColors = ['gold', 'rose', 'sage', 'blue'];
  let bulbs = '', wire = '';
  for (let i = 0; i <= 24; i++) {
    const x = i * 1000 / 24, y = 5 + Math.sin(Math.PI * i / 8) ** 2 * 18;
    wire += `${i ? 'L' : 'M'}${x} ${y} `;
    if (i > 0 && i < 24) bulbs += `<g class="light-bulb season-animated" style="--bulb-color:var(--winter-bulb-${bulbColors[i % 4]});--bulb-delay:-${i % 4 * 4}s;--twinkle-delay:-${i % 5 * .9}s"><path d="M${x} ${y}v5" stroke="var(--winter-pine)"/><circle class="light-halo season-animated" cx="${x}" cy="${y + 8}" r="10"/><ellipse class="light-core" cx="${x}" cy="${y + 8}" rx="2.3" ry="3.8"/><ellipse class="light-shine" cx="${x - .5}" cy="${y + 6.7}" rx=".7" ry="1.3"/></g>`;
  }
  scene.insertAdjacentHTML('beforeend', `<svg class="season-decoration season-lights" aria-hidden="true" viewBox="0 0 1000 44" preserveAspectRatio="none"><path class="light-wire" d="${wire}"/>${bulbs}</svg>`);
  // The original house asset is untouched. This tree occupies the same viewBox.
  scene.insertAdjacentHTML('beforeend', `<div class="season-decoration season-tree" aria-hidden="true"><svg viewBox="0 0 560 560" focusable="false">
    <defs><linearGradient id="winter-pine-fill"><stop stop-color="#4a7559"/><stop offset=".5" stop-color="#2c563e"/><stop offset="1" stop-color="#163a2d"/></linearGradient><radialGradient id="winter-star-glow"><stop stop-color="#fbd492" stop-opacity=".45"/><stop offset="1" stop-color="#fbd492" stop-opacity="0"/></radialGradient></defs>
    <g transform="translate(92 211)">
      <ellipse cx="42" cy="195" rx="51" ry="13" fill="#102a21" opacity=".2"/>
      <ellipse cx="42" cy="191" rx="48" ry="9" fill="#e5ede4" opacity=".85"/>
      <path d="M38 152h9v38h-9Z" fill="#776148"/>
      <path d="M42 18 15 70h12L5 111h14L-6 161q48 19 96 0l-25-50h14L57 70h12Z" fill="url(#winter-pine-fill)" stroke="#75927a" stroke-width=".7"/>
      <path d="m22 62 20-38 20 38-20-7Zm-12 44 10-20 22 8 22-8 10 20-32-7Zm-9 49 6-13 35 9 35-9 6 13-41 10Z" fill="#d9e5d5" opacity=".5"/>
      <path d="M26 61q25 12 35 0M18 95q28 15 48 0M7 130q34 17 72 0M0 157q42 14 84 0" fill="none" stroke="#c7ad72" stroke-width="1.6"/>
      <g fill="#ffe2a0"><circle cx="30" cy="64" r="2.4"/><circle cx="54" cy="66" r="2.4"/><circle cx="23" cy="100" r="2.5"/><circle cx="46" cy="103" r="2.5"/><circle cx="64" cy="97" r="2.4"/><circle cx="15" cy="133" r="2.6"/><circle cx="37" cy="139" r="2.6"/><circle cx="66" cy="135" r="2.6"/><circle cx="7" cy="159" r="2.4"/><circle cx="51" cy="166" r="2.4"/><circle cx="77" cy="161" r="2.5"/></g>
      <g fill="#ad5252" stroke="#de8f7a" stroke-width=".7"><circle cx="37" cy="82" r="3.8"/><circle cx="52" cy="122" r="4"/><circle cx="22" cy="151" r="4.5"/></g>
      <circle cx="42" cy="14" r="28" fill="url(#winter-star-glow)"/><path d="m42 2 3.3 8 8.7.8-6.7 5.7 2 8.5-7.3-4.4-7.3 4.4 2-8.5-6.7-5.7 8.7-.8Z" fill="#f4d38f" stroke="#fff0cb" stroke-width=".8"/>
      <g><path d="m58 177 20-5 12 8-20 6Z" fill="#cc7971"/><path d="m58 177 12 9v17l-12-9Z" fill="#984848"/><path d="m70 186 20-6v17l-20 6Z" fill="#b75b55"/><path d="m68 175 13 8v17M58 187l12 8 20-7" fill="none" stroke="#e2c994" stroke-width="2"/><path d="m14 181 18-4 11 6-18 5Z" fill="#dfcca3"/><path d="m14 181 11 7v14l-11-7Z" fill="#bba176"/><path d="m25 188 18-5v15l-18 4Z" fill="#cdb586"/><path d="m24 179 10 7v15" stroke="#476d53" stroke-width="2.5"/></g>
    </g>
  </svg></div>`);
  const snow = document.createElement('div');
  snow.className = 'season-decoration season-snow';
  snow.setAttribute('aria-hidden', 'true');
  // Eight small crystals among sixteen dots: clearer without a dense overlay.
  for (let i = 0; i < 24; i++) {
    const flake = document.createElement('i');
    const crystal = i % 3 === 0;
    const duration = 12 + i % 7;
    flake.className = crystal ? 'season-animated snow-flake' : 'season-animated';
    flake.style.cssText = `--x:${(i * 37) % 98}%;--size:${crystal ? 7 + i % 4 : 2 + i % 3 * .6}px;--duration:${duration}s;--delay:-${((i * .618) % 1 * duration).toFixed(2)}s;--drift:${i % 2 ? -22 : 28}px;--turn:${i % 2 ? -60 : 70}deg;`;
    if (crystal) flake.innerHTML = snowIcon;
    snow.append(flake);
  }
  scene.prepend(snow);
  const footer = document.createElement('p');
  footer.className = 'season-footer'; footer.dataset.season = 'footer';
  document.querySelector('.dashboard').append(footer);
  const motion = document.createElement('button');
  motion.className = 'season-effects-toggle';
  motion.type = 'button';
  motion.innerHTML = snowIcon + '<span></span>';
  footerControls.append(motion);
  body.classList.add('christmas');
  const ownedNodes = [...document.querySelectorAll('.season-decoration, .season-branch'), footer, motion];
  const events = new AbortController();
  const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)');
  let effects = !reducedMotion.matches, inView = true;
  function updateMotion() {
    body.dataset.winterMotion = effects && !reducedMotion.matches ? 'on' : 'off';
    body.dataset.winterVisible = String(inView && !document.hidden);
    motion.setAttribute('aria-pressed', String(effects && !reducedMotion.matches));
    motion.disabled = reducedMotion.matches;
  }
  function translate() {
    const t = copy[document.documentElement.lang] || copy.en;
    footer.textContent = t.footer;
    motion.querySelector('span').textContent = t[reducedMotion.matches ? 'reduced' : 'effects'];
    updateMotion();
  }
  motion.addEventListener('click', () => { effects = !effects; updateMotion(); }, { signal: events.signal });
  window.addEventListener('languagechange', translate, { signal: events.signal });
  reducedMotion.addEventListener('change', () => { if (reducedMotion.matches) effects = false; translate(); }, { signal: events.signal });
  document.addEventListener('visibilitychange', updateMotion, { signal: events.signal });
  const observer = new IntersectionObserver(entries => { inView = entries[0].isIntersecting; updateMotion(); });
  observer.observe(scene);
  translate();
  return () => {
    events.abort();
    observer.disconnect();
    ownedNodes.forEach(node => node.remove());
    body.classList.remove('christmas');
    delete body.dataset.winterMotion;
    delete body.dataset.winterVisible;
  };
}
