'use strict';

// History-only controls: draft dates never change the loaded snapshot until Apply.
window.HistoryCustomRange = class HistoryCustomRange {
  static messages = {
    el: {
      'Custom': 'Προσαρμογή', 'Custom date range': 'Προσαρμοσμένο διάστημα',
      'DD/MM/YYYY': 'ΗΗ/ΜΜ/ΕΕΕΕ',
      'Start date': 'Από', 'End date': 'Έως', 'Apply range': 'Εφαρμογή',
      'Choose start date': 'Επιλογή ημερομηνίας έναρξης', 'Choose end date': 'Επιλογή ημερομηνίας λήξης',
      'Enter a date as DD/MM/YYYY.': 'Εισαγάγετε ημερομηνία ως ΗΗ/ΜΜ/ΕΕΕΕ.',
      'Choose today or an earlier date.': 'Επιλέξτε τη σημερινή ή προγενέστερη ημερομηνία.',
      'End date must be on or after the start date.': 'Η λήξη πρέπει να είναι ίδια ή μεταγενέστερη της έναρξης.',
      'Both dates included · Athens time.': 'Περιλαμβάνονται και οι δύο ημερομηνίες · Ώρα Αθήνας.',
      'Today includes only the readings recorded so far.': 'Για σήμερα εμφανίζονται μόνο οι έως τώρα καταγραφές.',
      'Select dates, then apply to update the charts.': 'Επιλέξτε ημερομηνίες και πατήστε Εφαρμογή για ενημέρωση των γραφημάτων.',
      'Changes not applied yet.': 'Οι αλλαγές δεν έχουν εφαρμοστεί ακόμη.',
      'Showing the selected range.': 'Εμφανίζεται το επιλεγμένο διάστημα.',
      '1 day': '1 ημέρα', '{n} days': '{n} ημέρες',
      'Selected range': 'Επιλεγμένο διάστημα', 'Before start date': 'Πριν από την ημερομηνία έναρξης',
      '6-hour averages': 'Μέσοι όροι 6 ωρών',
      'Dots mark recorded data; shading marks the selected range. Days may have gaps.': 'Οι τελείες δείχνουν καταγραφές, η σκίαση το επιλεγμένο διάστημα. Ενδέχεται να υπάρχουν κενά.',
    },
    ru: {
      'Custom': 'Свой период', 'Custom date range': 'Произвольный период',
      'DD/MM/YYYY': 'ДД/ММ/ГГГГ',
      'Start date': 'Начальная дата', 'End date': 'Конечная дата', 'Apply range': 'Применить',
      'Choose start date': 'Выбрать начальную дату', 'Choose end date': 'Выбрать конечную дату',
      'Enter a date as DD/MM/YYYY.': 'Введите дату в формате ДД/ММ/ГГГГ.',
      'Choose today or an earlier date.': 'Выберите сегодняшнюю или более раннюю дату.',
      'End date must be on or after the start date.': 'Конечная дата не может быть раньше начальной.',
      'Both dates included · Athens time.': 'Обе даты включены · Афинское время.',
      'Today includes only the readings recorded so far.': 'За сегодня показаны только уже записанные данные.',
      'Select dates, then apply to update the charts.': 'Выберите даты и нажмите «Применить», чтобы обновить графики.',
      'Changes not applied yet.': 'Изменения ещё не применены.',
      'Showing the selected range.': 'Показан выбранный период.',
      '1 day': '1 день', '{n} days': 'Дней: {n}',
      'Selected range': 'Выбранный период', 'Before start date': 'Раньше начальной даты',
      '6-hour averages': 'Средние за 6 часов',
      'Dots mark recorded data; shading marks the selected range. Days may have gaps.': 'Точки — записанные данные, заливка — выбранный период. В данных могут быть пропуски.',
    },
  };

  static t(key, values = {}) {
    const language = document.documentElement.lang;
    const message = HistoryCustomRange.messages[language]?.[key] ?? window.energyI18n.t(key);
    return message.replace(/\{(\w+)\}/g, (_, name) => values[name] ?? `{${name}}`);
  }

  static parseDate(value) {
    const match = /^(\d{2})\/(\d{2})\/(\d{4})$/.exec(value.trim());
    const day = match ? `${match[3]}-${match[2]}-${match[1]}` : '';
    return window.HistoryCalendar.validDay(day) ? day : '';
  }

  static validate(startText, endText, today) {
    const start = HistoryCustomRange.parseDate(startText), end = HistoryCustomRange.parseDate(endText);
    const errors = {};
    for (const [key, day] of Object.entries({ start, end })) {
      if (!day) errors[key] = 'Enter a date as DD/MM/YYYY.';
      else if (day > today) errors[key] = 'Choose today or an earlier date.';
    }
    if (!errors.start && !errors.end && end < start) errors.end = 'End date must be on or after the start date.';
    return { start, end, errors, valid: Object.keys(errors).length === 0 };
  }

  static dayCount(start, end) {
    return Math.round((new Date(`${end}T12:00:00Z`) - new Date(`${start}T12:00:00Z`)) / 86400000) + 1;
  }

  static bounds({ start, end }, midnight) {
    const exclusiveEnd = window.HistoryCalendar.shiftDay(end, 1);
    const days = HistoryCustomRange.dayCount(start, end);
    return { start, end: exclusiveEnd, from: midnight(start), to: midnight(exclusiveEnd),
      step: days <= 2 ? 60 : days <= 10 ? 900 : days <= 90 ? 3600 : 21600 };
  }

  constructor({ form, today, onApply }) {
    Object.assign(this, { form, today, onApply });
    this.opened = false;
    this.applied = null;
    this.active = false;
    this.touched = new Set();
    this.button = document.querySelector('[data-period="custom"]');
    this.inputs = Object.fromEntries(['start', 'end'].map(key => [key, form.querySelector(`#custom-${key}`)]));
    this.applyButton = form.querySelector('[type="submit"]');
    this.status = form.querySelector('.custom-range-status');
    this.calendars = Object.fromEntries(['start', 'end'].map(key => {
      const input = this.inputs[key];
      input.addEventListener('input', () => this.update());
      input.addEventListener('blur', () => { this.touched.add(key); this.update(); });
      const calendar = new window.HistoryCalendar({
        trigger: form.querySelector(`#custom-${key}-calendar-button`),
        dialog: document.querySelector(`#custom-${key}-calendar`),
        input: { get value() { return HistoryCustomRange.parseDate(input.value); } },
        t: HistoryCustomRange.t, locale: () => window.energyI18n.locale, today,
        minimum: () => {
          const start = HistoryCustomRange.parseDate(this.inputs.start.value);
          return key === 'end' && start <= today() ? start : '';
        },
        selection: () => { const draft = this.validate(); return draft.valid ? draft : null; },
        onSelect: day => {
          input.value = day.split('-').reverse().join('/');
          this.touched.add(key); this.update();
        },
      });
      return [key, calendar];
    }));
    form.addEventListener('submit', event => {
      event.preventDefault();
      this.touched = new Set(['start', 'end']);
      const draft = this.update();
      if (!draft.valid) { this.inputs[Object.keys(draft.errors)[0]].focus(); return; }
      this.applied = { start: draft.start, end: draft.end };
      this.active = true;
      this.update();
      this.onApply();
    });
    window.addEventListener('languagechange', () => this.update());
    this.update();
  }

  validate() {
    return HistoryCustomRange.validate(this.inputs.start.value, this.inputs.end.value, this.today());
  }

  show(seed) {
    if (!this.inputs.start.value && !this.inputs.end.value) {
      for (const key of ['start', 'end']) this.inputs[key].value = seed[key].split('-').reverse().join('/');
    }
    this.form.hidden = false;
    this.opened = true;
    this.button.setAttribute('aria-expanded', 'true');
    this.update();
  }

  hide() {
    this.closeCalendars();
    this.form.hidden = true;
    this.opened = false;
    this.active = false;
    this.button.setAttribute('aria-expanded', 'false');
  }

  closeCalendars() { Object.values(this.calendars).forEach(calendar => calendar.close()); }
  setAvailability(days, state) { Object.values(this.calendars).forEach(calendar => calendar.setAvailability(days, state)); }

  update() {
    const t = HistoryCustomRange.t, draft = this.validate();
    this.button.textContent = t('Custom');
    this.form.setAttribute('aria-label', t('Custom date range'));
    this.form.querySelectorAll('[data-custom-i18n]').forEach(node => { node.textContent = t(node.dataset.customI18n); });
    for (const key of ['start', 'end']) {
      const error = this.form.querySelector(`#custom-${key}-error`);
      const relationshipError = key === 'end' && draft.errors.end === 'End date must be on or after the start date.' && this.touched.size > 0;
      const message = this.touched.has(key) || relationshipError ? draft.errors[key] : '';
      error.textContent = message ? t(message) : '';
      error.hidden = !message;
      this.inputs[key].setAttribute('aria-invalid', String(Boolean(message)));
      this.inputs[key].placeholder = t('DD/MM/YYYY');
      this.form.querySelector(`#custom-${key}-calendar-button`).setAttribute('aria-label', t(key === 'start' ? 'Choose start date' : 'Choose end date'));
    }
    this.applyButton.disabled = !draft.valid;
    const matching = this.applied && draft.start === this.applied.start && draft.end === this.applied.end;
    const state = this.active ? matching ? 'Showing the selected range.' : 'Changes not applied yet.' : 'Select dates, then apply to update the charts.';
    const days = draft.valid ? HistoryCustomRange.dayCount(draft.start, draft.end) : 0;
    const count = days ? (days === 1 ? t('1 day') : t('{n} days', { n: new Intl.NumberFormat(window.energyI18n.locale).format(days) })) + ' · ' : '';
    this.status.textContent = count + t(state);
    this.form.querySelector('.custom-today-note').hidden = !draft.valid || draft.end !== this.today();
    return draft;
  }
};
