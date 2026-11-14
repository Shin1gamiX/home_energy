// Annual windows use Athens calendar dates. Start is inclusive; end is exclusive.
export const TIME_ZONE = 'Europe/Athens';
export const THEME_SCHEDULE = Object.freeze([
  Object.freeze({
    id: 'christmas',
    start: '12-01',
    end: '01-01',
    stylesheet: './christmas/theme.css?v=20261114-christmas-2',
    module: './christmas/theme.js?v=20261114-christmas-1',
  }),
]);

const calendar = new Intl.DateTimeFormat('en-US', {
  timeZone: TIME_ZONE, calendar: 'iso8601', numberingSystem: 'latn',
  month: '2-digit', day: '2-digit',
});

export function isInAnnualWindow(day, start, end) {
  if (start === end) return false;
  return start < end ? day >= start && day < end : day >= start || day < end;
}

export function themeForDate(date = new Date(), schedule = THEME_SCHEDULE) {
  if (!(date instanceof Date) || !Number.isFinite(date.getTime())) return null;
  const parts = Object.fromEntries(calendar.formatToParts(date).map(part => [part.type, part.value]));
  const day = `${parts.month}-${parts.day}`;
  // Put the preferred theme first if deliberately configuring overlapping windows.
  return schedule.find(theme => isInAnnualWindow(day, theme.start, theme.end)) || null;
}
