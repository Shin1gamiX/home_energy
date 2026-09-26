// Shared localization is also used by the history page.
window.energyI18n = (() => {
  const translations = {
    ru: {
      'Current': 'Ток', 'Solar current': 'Ток солнечных панелей', 'House current': 'Ток нагрузки дома',
      'Battery current (avg.)': 'Ток батареи (средний)', 'A (avg.)': 'A (сред.)',
      'Communication lost': 'Связь потеряна',
      'Live inverter data is unavailable. Check the dongle, home Wi-Fi, or VPN connection.': 'Текущие данные инвертора недоступны. Проверьте адаптер, домашнюю сеть Wi-Fi или VPN-соединение.',
      'Unable to reach the data service. Check your internet connection or try again shortly.': 'Не удаётся связаться с сервисом данных. Проверьте подключение к интернету или повторите попытку позже.',
      'Showing last known readings — not live data.': 'Показаны последние полученные показания — это не текущие данные.',
      'No readings are available yet.': 'Показания пока недоступны.',
      'Daily summary': 'Итоги дня', 'Weekly summary': 'Итоги недели', 'Monthly summary': 'Итоги месяца',
      'Solar generated': 'Солнечная генерация', 'Grid consumed': 'Энергия из сети', 'House usage': 'Потребление дома', 'Battery supplied': 'Энергия от батареи', 'Solar to house': 'Солнечная энергия для дома',
      '{hours} h recorded': 'Записано {hours} ч', 'Estimated from recorded readings only; missing periods are excluded.': 'Оценка по записанным показаниям; периоды без данных не учитываются.',
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
      'Current': 'Ένταση ρεύματος', 'Solar current': 'Ρεύμα φωτοβολταϊκών', 'House current': 'Ρεύμα σπιτιού',
      'Battery current (avg.)': 'Ρεύμα μπαταρίας (μέσο)', 'A (avg.)': 'A (μέσο)',
      'Communication lost': 'Η επικοινωνία χάθηκε',
      'Live inverter data is unavailable. Check the dongle, home Wi-Fi, or VPN connection.': 'Τα ζωντανά δεδομένα του μετατροπέα δεν είναι διαθέσιμα. Ελέγξτε τον προσαρμογέα, το οικιακό Wi-Fi ή τη σύνδεση VPN.',
      'Unable to reach the data service. Check your internet connection or try again shortly.': 'Δεν είναι δυνατή η σύνδεση με την υπηρεσία δεδομένων. Ελέγξτε τη σύνδεσή σας στο διαδίκτυο ή δοκιμάστε ξανά σε λίγο.',
      'Showing last known readings — not live data.': 'Εμφανίζονται οι τελευταίες γνωστές μετρήσεις — όχι ζωντανά δεδομένα.',
      'No readings are available yet.': 'Δεν υπάρχουν ακόμη διαθέσιμες μετρήσεις.',
      'Daily summary': 'Ημερήσια σύνοψη', 'Weekly summary': 'Εβδομαδιαία σύνοψη', 'Monthly summary': 'Μηνιαία σύνοψη',
      'Solar generated': 'Ηλιακή παραγωγή', 'Grid consumed': 'Ενέργεια από το δίκτυο', 'House usage': 'Κατανάλωση σπιτιού', 'Battery supplied': 'Ενέργεια από μπαταρία', 'Solar to house': 'Ηλιακή ενέργεια στο σπίτι',
      '{hours} h recorded': '{hours} ώρες καταγραφής', 'Estimated from recorded readings only; missing periods are excluded.': 'Εκτίμηση από τις καταγεγραμμένες μετρήσεις· τα διαστήματα χωρίς δεδομένα εξαιρούνται.',
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
  const selectors = ['.brand-sub', '.mode-badge', '.history-nav a', '.intro h1', '.intro p', '.scene-heading>span:last-child', '.scene-label>div', '.scene-caption', '.metric-top>span:first-child', '.metric.load p', '.back', '[data-period]', '.date-controls>span', '#select-all', '#clear-all', 'label[for="averaging"]', '#averaging option'];
  const texts = selectors.flatMap(selector => [...document.querySelectorAll(selector)].map(element => {
    const node = [...element.childNodes].find(child => child.nodeType === Node.TEXT_NODE && child.textContent.trim());
    return node ? { node, key: node.textContent } : null;
  }).filter(Boolean));
  const attributes = [...document.querySelectorAll('[aria-label], [title]')].flatMap(element => ['aria-label', 'title'].filter(name => element.hasAttribute(name)).map(name => ({ element, name, key: element.getAttribute(name) })));
  const pageTitle = document.title;
  const control = document.createElement('label');
  control.className = 'language-control';
  const symbol = document.createElement('span'); symbol.textContent = '◎'; symbol.setAttribute('aria-hidden', 'true');
  const select = document.createElement('select'); select.id = 'language';
  for (const [value, name] of [['en', 'English'], ['ru', 'Русский'], ['el', 'Ελληνικά']]) {
    const option = document.createElement('option'); option.value = value; option.textContent = name; select.append(option);
  }
  control.append(symbol, select);
  document.querySelector('header').append(control);
  function apply() {
    document.documentElement.lang = language;
    document.title = t(pageTitle);
    select.value = language; select.setAttribute('aria-label', t('Language'));
    texts.forEach(({ node, key }) => { node.textContent = t(key); });
    attributes.forEach(({ element, name, key }) => element.setAttribute(name, t(key)));
  }
  select.addEventListener('change', () => {
    language = select.value;
    try { localStorage.setItem('homeenergy-language', language); } catch { /* Still works for this page. */ }
    apply(); window.dispatchEvent(new Event('languagechange'));
  });
  apply();
  return { t, get locale() { return { en: 'en-GB', ru: 'ru-RU', el: 'el-GR' }[language]; } };
})();

if (document.querySelector('.energy-scene')) {
const { t } = window.energyI18n;
// Simulation is limited to the local preview. Production reads the allowlisted endpoint.
const demo = location.hostname === '127.0.0.1' && location.port === '8766';
const samples = [
  { pv: 1580, load: 910, battery: 670, soc: 84 },
  { pv: 1610, load: 930, battery: 680, soc: 84 },
  { pv: 410, load: 910, battery: -500, soc: 84 },
  { pv: 420, load: 940, battery: -520, soc: 84 },
];
let sampleIndex = 0;
const currentReadings = [];
for (const [name, key, label] of [
  ['solar', 'pv_current', 'Solar current'],
  ['battery', 'battery_current', 'Battery current (avg.)'],
  ['load', 'load_current', 'House current'],
]) {
  for (const [selector, tag] of [[`.${name}-label>div`, 'span'], [`.metric.${name}`, 'p']]) {
    const line = document.createElement(tag);
    line.className = 'current-reading';
    const value = document.createElement('b'); value.dataset.value = key;
    const unit = document.createElement('small');
    line.append(value, document.createTextNode(' '), unit);
    const parent = document.querySelector(selector);
    const meter = parent.querySelector('.meter');
    parent.insertBefore(line, meter);
    currentReadings.push({ line, unit, label, battery: name === 'battery' });
  }
}
let paused = false;
const pauseButton = document.querySelector('#pause');
pauseButton.hidden = !demo;
document.querySelector('.disclaimer').hidden = !demo;
function updateDate() { document.querySelector('#date').textContent = new Intl.DateTimeFormat(window.energyI18n.locale, { weekday: 'long', day: 'numeric', month: 'long', timeZone: 'Europe/Athens' }).format(new Date()); }
updateDate();
function formatPower(watts) {
  if (!Number.isFinite(watts)) return { value: '—', unit: 'W' };
  const kilo = Math.abs(watts) >= 1000;
  return { value: new Intl.NumberFormat(window.energyI18n.locale, { maximumFractionDigits: kilo ? 2 : 0, useGrouping: false }).format(kilo ? watts / 1000 : watts), unit: kilo ? 'kW' : 'W' };
}
function render(sample) {
  currentReadings.forEach(({ line, unit, label, battery }) => {
    line.title = t(label);
    unit.textContent = battery ? t('A (avg.)') : 'A';
  });
  // Dashboard convention: positive = charging, negative = discharging.
  const batteryState = sample.battery > 0 ? 'charging' : sample.battery < 0 ? 'discharging' : 'idle';
  document.body.dataset.batteryState = batteryState;
  const values = { grid: 0, ...sample };
  document.querySelectorAll('[data-value]').forEach(element => {
    const key = element.dataset.value;
    if (key === 'soc') {
      element.textContent = values[key] ?? '—';
    } else if (key === 'pv_voltage' || key === 'grid_voltage' || key.endsWith('_current')) {
      element.textContent = Number.isFinite(values[key]) ? new Intl.NumberFormat(window.energyI18n.locale, { maximumFractionDigits: 1 }).format(values[key]) : '—';
    } else {
      const power = formatPower(values[key]);
      element.textContent = power.value;
      element.nextElementSibling.textContent = power.unit;
    }
  });
  document.querySelector('#charge-bar').style.width = `${sample.soc ?? 0}%`;
  document.querySelector('#grid-state').textContent = t(values.grid === null ? 'Estimate unavailable' : 'Estimated · house + battery');
  document.querySelectorAll('.metric:not(.battery) .meter').forEach(element => { element.hidden = !demo; });
  document.querySelector('.solar-flow').style.animationPlayState = sample.pv > 0 ? '' : 'paused';
  document.querySelector('.load-flow').style.animationPlayState = sample.load > 0 ? '' : 'paused';
  if (demo) document.querySelector('#freshness').textContent = `Sample updated ${new Date().toLocaleTimeString()}`;
}
pauseButton.addEventListener('click', () => {
  paused = !paused;
  document.body.classList.toggle('paused', paused);
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
  document.querySelector('.demo').lastChild.textContent = ` ${t(labels[status])}`;
  document.querySelector('#scene-status').textContent = t(labels[status]);
  document.querySelector('.demo').style.display = status === 'live' || communicationLost ? 'none' : '';
  document.querySelector('#scene-status').parentElement.style.visibility = status === 'live' || communicationLost ? 'hidden' : '';
  const mode = ['live', 'partial'].includes(status) ? latest?.mode : null;
  document.body.dataset.activeSource = mode === 'Mains' ? 'grid' : mode?.toLowerCase() === 'off-grid' ? 'solar' : '';
  document.querySelector('#operating-mode').textContent = t(mode === 'Mains' ? 'Grid' : mode?.toLowerCase() === 'off-grid' ? 'Solar' : mode || '—');
  document.querySelector('#freshness').textContent = latest?.updated_at
    ? `${t('Last inverter report')} ${new Date(latest.updated_at * 1000).toLocaleTimeString('en-GB', { timeZone: 'Europe/Athens' })} (${new Date(latest.updated_at * 1000).toLocaleDateString('en-GB', { timeZone: 'Europe/Athens' })}) · ${t('Athens')}`
    : t('Waiting for inverter readings');
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
  render(samples[0]);
  setInterval(() => {
    if (paused) return;
    sampleIndex = (sampleIndex + 1) % samples.length;
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
