(() => {
  'use strict';

  const storageKey = 'homeenergy-language';
  const messages = {
    en: {
      title: 'Page not found · Home Energy',
      brand: 'HOME ENERGY',
      brandLabel: 'Home energy overview',
      language: 'Language',
      heading: 'Page not found.',
      description: 'This page may have moved, or the address may be incorrect.',
      overview: 'Back to overview',
      history: 'View history'
    },
    ru: {
      title: 'Страница не найдена · Энергия дома',
      brand: 'ЭНЕРГИЯ ДОМА',
      brandLabel: 'Обзор энергии дома',
      language: 'Язык',
      heading: 'Страница не найдена.',
      description: 'Возможно, страница была перемещена или адрес указан неверно.',
      overview: 'Вернуться к обзору',
      history: 'Посмотреть историю'
    },
    el: {
      title: 'Η σελίδα δεν βρέθηκε · Ενέργεια σπιτιού',
      brand: 'ΕΝΕΡΓΕΙΑ ΣΠΙΤΙΟΥ',
      brandLabel: 'Επισκόπηση ενέργειας σπιτιού',
      language: 'Γλώσσα',
      heading: 'Η σελίδα δεν βρέθηκε.',
      description: 'Η σελίδα μπορεί να έχει μετακινηθεί ή η διεύθυνση να είναι λανθασμένη.',
      overview: 'Επιστροφή στην επισκόπηση',
      history: 'Προβολή ιστορικού'
    }
  };

  const trigger = document.querySelector('#not-found-language');
  const languageName = document.querySelector('#not-found-language-name');
  const menu = document.querySelector('#not-found-language-menu');
  const control = document.querySelector('#not-found-language-control');
  const brand = document.querySelector('#not-found-brand');
  const textElements = document.querySelectorAll('[data-not-found-text]');
  const options = Array.from(menu.querySelectorAll('[data-language]'));
  const languageNames = { en: 'English', ru: 'Русский', el: 'Ελληνικά' };
  let language = 'en';

  try {
    const saved = localStorage.getItem(storageKey);
    if (Object.hasOwn(messages, saved)) language = saved;
  } catch { /* English remains available when storage is blocked. */ }

  function applyLanguage() {
    const text = messages[language];
    document.documentElement.lang = language;
    document.title = text.title;
    languageName.textContent = languageNames[language];
    languageName.setAttribute('lang', language);
    options.forEach(option => {
      option.setAttribute('aria-checked', String(option.dataset.language === language));
    });
    brand.setAttribute('aria-label', text.brandLabel);
    textElements.forEach(element => {
      const key = element.dataset.notFoundText;
      if (Object.hasOwn(text, key)) element.textContent = text[key];
    });
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

  options.forEach(option => {
    option.addEventListener('click', () => {
      const next = option.dataset.language;
      if (!Object.hasOwn(messages, next)) return;
      language = next;
      applyLanguage();
      closeMenu(true);
      try {
        localStorage.setItem(storageKey, language);
      } catch { /* Language selection still works for the current page. */ }
    });
  });

  menu.addEventListener('keydown', event => {
    const index = options.indexOf(document.activeElement);
    if (event.key === 'Escape' || event.key === 'Tab') {
      // Restore the trigger before the browser's normal Tab movement, so focus
      // leaves the picker in the correct direction instead of getting trapped.
      if (event.key === 'Escape') event.preventDefault();
      closeMenu(true);
    } else if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) {
      event.preventDefault();
      const next = event.key === 'Home' ? 0 : event.key === 'End' ? options.length - 1
        : (index + (event.key === 'ArrowDown' ? 1 : -1) + options.length) % options.length;
      options[next].focus();
    } else if (event.key.length === 1 && !event.altKey && !event.ctrlKey && !event.metaKey && event.key !== ' ') {
      const key = event.key.toLocaleLowerCase();
      // Match either the native language name or its short code, without timers.
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

  applyLanguage();
  control.hidden = false;
})();
