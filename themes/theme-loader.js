import { themeForDate } from './schedule.js?v=20261114-christmas-1';

async function loadThemeAssets(theme) {
  const stylesheet = document.createElement('link');
  stylesheet.rel = 'stylesheet';
  stylesheet.href = new URL(theme.stylesheet, import.meta.url).href;
  let timeout;
  const ready = new Promise((resolve, reject) => {
    stylesheet.onload = resolve;
    stylesheet.onerror = () => reject(new Error('Seasonal stylesheet unavailable'));
    timeout = window.setTimeout(() => reject(new Error('Seasonal stylesheet timed out')), 15000);
    document.head.append(stylesheet);
  });
  try {
    const [module] = await Promise.all([import(new URL(theme.module, import.meta.url).href), ready]);
    if (typeof module.mount !== 'function') throw new Error('Seasonal theme has no mount function');
    return { mount: module.mount, remove: () => stylesheet.remove() };
  } catch (error) {
    stylesheet.remove();
    throw error;
  } finally {
    window.clearTimeout(timeout);
    stylesheet.onload = stylesheet.onerror = null;
  }
}

// Handles slow asset loads and date changes without mounting an expired theme.
export function createThemeController({
  choose = themeForDate,
  load = loadThemeAssets,
  reportError = error => console.warn('Seasonal theme unavailable; using the normal Overview.', error),
} = {}) {
  let active = null;
  let pending = null;
  let revision = 0;
  let stopped = false;

  function clearActive() {
    const previous = active;
    active = null;
    if (!previous) return;
    try { previous.unmount(); }
    catch (error) { reportError(error); }
    finally { previous.assets.remove(); }
  }

  async function refresh() {
    if (stopped) return;
    const desired = choose();
    if (active && active.id === desired?.id) return;
    if (pending && pending.id === desired?.id) return pending.promise;
    const ticket = ++revision;
    clearActive();
    pending = null;
    if (!desired) return;

    const request = { id: desired.id, promise: null };
    pending = request;
    request.promise = (async () => {
      let assets;
      try {
        assets = await load(desired);
        if (stopped || ticket !== revision || choose()?.id !== desired.id) {
          assets.remove();
          return;
        }
        const unmount = assets.mount();
        if (typeof unmount !== 'function') throw new Error('Seasonal theme has no cleanup function');
        active = { id: desired.id, assets, unmount };
      } catch (error) {
        assets?.remove();
        if (!stopped && ticket === revision) reportError(error);
      } finally {
        if (pending === request) pending = null;
      }
    })();
    return request.promise;
  }

  return {
    refresh,
    destroy() {
      stopped = true;
      revision++;
      pending = null;
      clearActive();
    },
  };
}

export function startSeasonalThemes({ doc = document, win = window,
  controller = createThemeController() } = {}) {
  if (!doc.body.classList.contains('overview')) return () => {};
  let timer;
  function check() {
    win.clearTimeout(timer);
    void controller.refresh();
    // Athens midnight falls on a UTC minute boundary. Check just after each
    // minute, and immediately after a sleeping/background page becomes active.
    if (!doc.hidden) timer = win.setTimeout(check, 60000 - Date.now() % 60000 + 25);
  }
  doc.addEventListener('visibilitychange', check);
  win.addEventListener('pageshow', check);
  win.addEventListener('focus', check);
  check();
  return () => {
    win.clearTimeout(timer);
    doc.removeEventListener('visibilitychange', check);
    win.removeEventListener('pageshow', check);
    win.removeEventListener('focus', check);
    controller.destroy();
  };
}

if (typeof document !== 'undefined' && document.body?.classList.contains('overview')) {
  startSeasonalThemes();
}
