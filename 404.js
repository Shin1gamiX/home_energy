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

  const select = document.querySelector('#not-found-language');
  const control = document.querySelector('#not-found-language-control');
  const brand = document.querySelector('#not-found-brand');
  const textElements = document.querySelectorAll('[data-not-found-text]');
  let language = 'en';

  try {
    const saved = localStorage.getItem(storageKey);
    if (Object.hasOwn(messages, saved)) language = saved;
  } catch { /* English remains available when storage is blocked. */ }

  function applyLanguage() {
    const text = messages[language];
    document.documentElement.lang = language;
    document.title = text.title;
    select.value = language;
    brand.setAttribute('aria-label', text.brandLabel);
    textElements.forEach(element => {
      const key = element.dataset.notFoundText;
      if (Object.hasOwn(text, key)) element.textContent = text[key];
    });
  }

  select.addEventListener('change', () => {
    if (!Object.hasOwn(messages, select.value)) {
      select.value = language;
      return;
    }

    language = select.value;
    applyLanguage();
    try {
      localStorage.setItem(storageKey, language);
    } catch { /* Language selection still works for the current page. */ }
  });

  applyLanguage();
  control.hidden = false;
})();
