'use strict';

// A dependency-free date picker. Availability comes from the existing history index;
// choosing a day never fetches data or changes the current history period here.
window.HistoryCalendar = class HistoryCalendar {
  static validDay(day) {
    if (typeof day !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(day) || day < '0001-01-01') return false;
    const date = new Date(`${day}T12:00:00Z`);
    return Number.isFinite(date.getTime()) && date.toISOString().slice(0, 10) === day;
  }

  static shiftDay(day, offset) {
    const date = new Date(`${day}T12:00:00Z`);
    date.setUTCDate(date.getUTCDate() + offset);
    const result = date.toISOString().slice(0, 10);
    return HistoryCalendar.validDay(result) ? result : null;
  }

  static shiftMonth(day, offset) {
    const date = new Date(`${day}T12:00:00Z`);
    const originalDay = date.getUTCDate();
    date.setUTCDate(1);
    date.setUTCMonth(date.getUTCMonth() + offset);
    const last = new Date(date);
    last.setUTCMonth(last.getUTCMonth() + 1, 0);
    date.setUTCDate(Math.min(originalDay, last.getUTCDate()));
    const result = date.toISOString().slice(0, 10);
    return HistoryCalendar.validDay(result) ? result : null;
  }

  static monthDays(month) {
    const first = `${month}-01`;
    const offset = (new Date(`${first}T12:00:00Z`).getUTCDay() + 6) % 7;
    return Array.from({ length: 42 }, (_, index) => HistoryCalendar.shiftDay(first, index - offset));
  }

  constructor({ trigger, dialog, input, t, locale, today, onSelect }) {
    Object.assign(this, { trigger, dialog, input, t, locale, today, onSelect });
    this.recordedDays = null;
    this.availability = 'loading';
    this.month = today().slice(0, 7);
    this.focusedDay = today();
    const element = (tag, className) => {
      const node = document.createElement(tag);
      node.className = className;
      return node;
    };
    const button = (className, text, action) => {
      const node = element('button', className);
      node.type = 'button'; node.textContent = text;
      node.addEventListener('click', action);
      return node;
    };
    const header = element('div', 'calendar-header');
    this.title = element('h2', 'calendar-title'); this.title.id = 'calendar-title';
    this.title.setAttribute('aria-live', 'polite');
    this.previous = button('calendar-nav', '‹', () => this.changeMonth(-1));
    this.next = button('calendar-nav', '›', () => this.changeMonth(1));
    this.dismiss = button('calendar-close', '×', () => this.close());
    header.append(this.previous, this.title, this.next, this.dismiss);
    this.table = element('table', 'calendar-grid');
    this.table.setAttribute('role', 'grid');
    this.table.setAttribute('aria-labelledby', 'calendar-title');
    this.table.setAttribute('aria-describedby', 'calendar-keys');
    this.weekdays = document.createElement('thead');
    this.body = document.createElement('tbody');
    this.table.append(this.weekdays, this.body);
    this.legend = element('p', 'calendar-legend');
    this.legend.setAttribute('role', 'status');
    this.help = element('p', 'calendar-help'); this.help.id = 'calendar-help';
    this.keys = element('p', 'calendar-sr-only'); this.keys.id = 'calendar-keys';
    const footer = element('div', 'calendar-footer');
    this.todayButton = button('calendar-today', '', () => this.select(this.today()));
    this.timezoneLabel = element('span', 'calendar-timezone');
    footer.append(this.timezoneLabel, this.todayButton);
    dialog.append(header, this.table, this.legend, this.help, this.keys, footer);

    trigger.addEventListener('click', () => this.open());
    trigger.addEventListener('keydown', event => {
      if (event.key === 'ArrowDown') { event.preventDefault(); this.open(); }
    });
    dialog.addEventListener('close', () => trigger.setAttribute('aria-expanded', 'false'));
    dialog.addEventListener('keydown', event => this.trapFocus(event));
    dialog.addEventListener('click', event => {
      if (event.target !== dialog) return;
      const box = dialog.getBoundingClientRect();
      if (event.clientX < box.left || event.clientX > box.right || event.clientY < box.top || event.clientY > box.bottom) this.close();
    });
    this.body.addEventListener('click', event => {
      const target = event.target.closest('button[data-date]');
      if (target && !target.disabled) this.select(target.dataset.date);
    });
    this.body.addEventListener('keydown', event => this.navigate(event));
    window.addEventListener('resize', () => { if (dialog.open) this.position(); });
    window.addEventListener('languagechange', () => { if (dialog.open) this.render(); });
  }

  setAvailability(days, state = 'ready') {
    this.availability = Array.isArray(days) ? 'ready' : state;
    this.recordedDays = Array.isArray(days) ? new Set(days.filter(HistoryCalendar.validDay)) : null;
    if (this.dialog.open) this.render();
  }

  open() {
    if (this.dialog.open) return;
    this.focusedDay = HistoryCalendar.validDay(this.input.value) && this.input.value <= this.today() ? this.input.value : this.today();
    this.month = this.focusedDay.slice(0, 7);
    this.render();
    this.dialog.showModal();
    this.trigger.setAttribute('aria-expanded', 'true');
    this.position();
    this.focusDay();
  }

  close() {
    if (this.dialog.open) this.dialog.close();
  }

  select(day) {
    if (!HistoryCalendar.validDay(day) || day > this.today()) return;
    this.close();
    this.onSelect(day);
  }

  changeMonth(offset) {
    const next = HistoryCalendar.shiftMonth(this.focusedDay, offset);
    if (!next || next.slice(0, 7) > this.today().slice(0, 7)) return;
    this.focusedDay = next > this.today() ? this.today() : next;
    this.month = this.focusedDay.slice(0, 7);
    this.render();
    if (document.activeElement?.disabled) this.focusDay();
  }

  navigate(event) {
    const target = event.target.closest('button[data-date]');
    if (!target || event.altKey || event.ctrlKey || event.metaKey) return;
    const day = target.dataset.date;
    const weekday = (new Date(`${day}T12:00:00Z`).getUTCDay() + 6) % 7;
    const offsets = { ArrowLeft: -1, ArrowRight: 1, ArrowUp: -7, ArrowDown: 7, Home: -weekday, End: 6 - weekday };
    let next;
    if (Object.hasOwn(offsets, event.key)) next = HistoryCalendar.shiftDay(day, offsets[event.key]);
    else if (event.key === 'PageUp' || event.key === 'PageDown') next = HistoryCalendar.shiftMonth(day, (event.key === 'PageUp' ? -1 : 1) * (event.shiftKey ? 12 : 1));
    else return;
    event.preventDefault();
    if (!next) return;
    this.focusedDay = next > this.today() ? this.today() : next;
    this.month = this.focusedDay.slice(0, 7);
    this.render();
    this.focusDay();
  }

  focusDay() {
    this.body.querySelector(`button[data-date="${this.focusedDay}"]`)?.focus({ preventScroll: true });
  }

  trapFocus(event) {
    if (event.key !== 'Tab') return;
    const day = this.body.querySelector(`button[data-date="${this.focusedDay}"]`);
    const targets = [this.previous, this.next, this.dismiss, day, this.todayButton].filter(button => button && !button.disabled);
    const first = targets[0], last = targets[targets.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault(); last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault(); first.focus();
    }
  }

  render() {
    const restoreDayFocus = this.body.contains(document.activeElement);
    const { t } = this;
    const currentDay = this.today();
    const formatter = options => new Intl.DateTimeFormat(this.locale(), { timeZone: 'UTC', ...options });
    this.title.textContent = formatter({ month: 'long', year: 'numeric' }).format(new Date(`${this.month}-01T12:00:00Z`));
    this.previous.setAttribute('aria-label', t('Previous month'));
    this.next.setAttribute('aria-label', t('Next month'));
    this.dismiss.setAttribute('aria-label', t('Close calendar'));
    this.previous.disabled = this.month === '0001-01';
    this.next.disabled = this.month >= currentDay.slice(0, 7);
    this.todayButton.textContent = t('Today');
    this.timezoneLabel.textContent = t('Athens time');
    this.help.textContent = t('A highlight means some readings exist, not a complete day.');
    this.keys.textContent = t('Use arrow keys for days, Page Up or Page Down for months, and Escape to close.');
    this.legend.classList.toggle('is-ready', this.recordedDays !== null);
    this.legend.textContent = t(this.recordedDays !== null ? 'Recorded data available' : this.availability === 'loading' ? 'Loading recorded dates…' : 'Recorded dates unavailable');

    const headings = document.createElement('tr');
    for (let index = 0; index < 7; index += 1) {
      const day = new Date(`2024-01-0${index + 1}T12:00:00Z`); // Monday first in every language.
      const heading = document.createElement('th');
      heading.scope = 'col';
      heading.textContent = formatter({ weekday: 'short' }).format(day);
      heading.setAttribute('aria-label', formatter({ weekday: 'long' }).format(day));
      headings.append(heading);
    }
    this.weekdays.replaceChildren(headings);
    const rows = [];
    const dates = HistoryCalendar.monthDays(this.month);
    for (let week = 0; week < 6; week += 1) {
      const weekDays = dates.slice(week * 7, week * 7 + 7);
      if (!weekDays.some(day => day?.startsWith(this.month))) continue;
      const row = document.createElement('tr');
      for (const day of weekDays) {
        const cell = document.createElement('td'); cell.setAttribute('role', 'gridcell');
        // Adjacent-month blanks keep this month clear; keyboard navigation still crosses months.
        if (day?.startsWith(this.month)) {
          const recorded = this.recordedDays?.has(day) ?? false;
          const selected = day === this.input.value;
          const future = day > currentDay;
          const button = document.createElement('button');
          button.type = 'button'; button.dataset.date = day;
          button.textContent = String(Number(day.slice(-2)));
          button.className = `calendar-day${recorded && !future ? ' has-readings' : ''}${selected ? ' is-selected' : ''}${day === currentDay ? ' is-today' : ''}`;
          button.disabled = future;
          button.tabIndex = day === this.focusedDay ? 0 : -1;
          cell.setAttribute('aria-selected', String(selected));
          if (day === currentDay) button.setAttribute('aria-current', 'date');
          const dateLabel = day.split('-').reverse().join('/');
          const availability = future ? 'Future date' : this.recordedDays === null ? this.availability === 'loading' ? 'Loading recorded dates…' : 'Recorded dates unavailable' : recorded ? 'Recorded data available' : 'No recorded data';
          button.setAttribute('aria-label', `${dateLabel} · ${t(availability)}${day === currentDay ? ` · ${t('Today')}` : ''}`);
          button.title = button.getAttribute('aria-label');
          cell.append(button);
        }
        row.append(cell);
      }
      rows.push(row);
    }
    this.body.replaceChildren(...rows);
    if (this.dialog.open) this.position();
    if (restoreDayFocus) this.focusDay();
  }

  position() {
    const anchor = this.trigger.getBoundingClientRect();
    const box = this.dialog.getBoundingClientRect();
    const gap = 12;
    const width = document.documentElement.clientWidth;
    const height = window.innerHeight;
    const left = Math.max(gap, Math.min(anchor.right - box.width, width - box.width - gap));
    const top = anchor.bottom + gap + box.height <= height - gap ? anchor.bottom + gap
      : anchor.top - gap - box.height >= gap ? anchor.top - gap - box.height
        : Math.max(gap, (height - box.height) / 2);
    this.dialog.style.left = `${left}px`;
    this.dialog.style.top = `${top}px`;
  }
};
