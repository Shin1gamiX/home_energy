// Run in <head> before paint. This module stores appearance only, never credentials.
(() => {
  'use strict';
  const key = 'homeenergy-theme';
  const root = document.documentElement;
  const system = window.matchMedia('(prefers-color-scheme: dark)');
  const valid = value => value === 'light' || value === 'dark';
  let preference = null;
  try {
    const saved = localStorage.getItem(key);
    if (valid(saved)) preference = saved;
  } catch { /* Theme selection still works when storage is unavailable. */ }
  const labels = {
    en: { dark: 'Switch to dark mode', light: 'Switch to light mode' },
    ru: { dark: 'Включить тёмную тему', light: 'Включить светлую тему' },
    el: { dark: 'Ενεργοποίηση σκοτεινής λειτουργίας', light: 'Ενεργοποίηση φωτεινής λειτουργίας' },
  };
  let buttons = [];
  function updateButtons() {
    const dark = root.dataset.theme === 'dark';
    const label = (labels[root.lang] || labels.en)[dark ? 'light' : 'dark'];
    for (const button of buttons) {
      button.setAttribute('aria-label', label);
      button.title = label;
      button.hidden = false;
    }
  }
  function apply() {
    const next = preference || (system.matches ? 'dark' : 'light');
    const changed = root.dataset.theme !== next;
    root.dataset.theme = next;
    const meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.setAttribute('content', next === 'dark' ? '#0f1918' : '#eff7f6');
    updateButtons();
    if (changed) window.dispatchEvent(new Event('themechange'));
  }
  apply();
  function init() {
    buttons = [...document.querySelectorAll('[data-theme-toggle]')];
    buttons.forEach(button => button.addEventListener('click', () => {
      preference = root.dataset.theme === 'dark' ? 'light' : 'dark';
      try { localStorage.setItem(key, preference); } catch { /* Keep the current-page choice. */ }
      apply();
    }));
    updateButtons();
    // Includes the standalone 404 language picker without coupling its code.
    new MutationObserver(updateButtons).observe(root, { attributes: true, attributeFilter: ['lang'] });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init, { once: true });
  else init();
  system.addEventListener('change', () => { if (preference === null) apply(); });
  window.addEventListener('storage', event => {
    if (event.key !== key && event.key !== null) return;
    preference = valid(event.newValue) ? event.newValue : null;
    apply();
  });
})();
