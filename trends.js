(function () {
'use strict';
const {buildComparison, METRICS, changeTone, compare, normalize, dailyDelta, extreme, ratio, athensDay} = window.EnergyTrendsData;

const copy = {
  en: {
    loading: "Loading recorded daily comparisons…",
    loadError: "Daily comparisons could not be loaded. Try again to get the latest snapshot.",
    retry: "Try again",
    refresh: "Refresh",
    recorded: "recorded",
    title: 'Energy trends', subtitle: 'See what changed, without digging through history.', days: 'days',
    complete: 'Completed days · Athens time',
    versus: 'compared with', house: 'House usage', solar: 'Solar generated', grid: 'Grid consumed',
    soc: 'Daily peak charge', points: 'pp', socUnits: 'Highest recorded charge each day · %', socNote: 'Each bar shows the highest recorded battery charge that day. The summary averages these daily peaks, giving each day equal weight. Differences are percentage points (pp).', socSummary: 'Average of daily peaks',
    previous: 'Previous', current: 'Selected period', higher: 'higher', lower: 'lower', unchanged: 'No change',
    explore: 'Explore comparison', collapse: 'Hide comparison', coverage: 'recorded in both periods',
    daily: 'Daily comparison', help: 'Select a day for exact values.', currentLegend: 'Selected', previousLegend: 'Previous',
    battery: 'Battery activity', charged: 'Energy charged', supplied: 'Energy supplied',
    range: 'Charge level range', rangeNote: 'Lowest and highest recorded charge level; not battery capacity.',
    note: 'Green: less house/grid consumption or more solar. Red: the reverse. Amber: changes within ±5%. Battery changes stay neutral; battery energy is not added to solar generation.',
    table: 'View daily values', date: 'Date', difference: 'Difference', observations: 'At a glance',
    high: 'Highest household use', low: 'Lowest solar generation', gridShare: 'Grid / house energy',
    shareNote: 'Ratio of recorded totals; not a precise source allocation.',
    noPrevious: 'No previous usage', insufficient: 'Insufficient coverage for comparison', unavailable: 'Unavailable',
    periodLabel: 'Comparison period', dayButton: 'Select day', coverageNote: "Recorded minute averages only; gaps are not counted as zero. Differences require at least 95% coverage on every day in both periods. Battery peaks may be lower when readings are missing.",
    units: 'kWh per day', historyLink: 'Open live History ↗', insights: 'Energy insights', sparkKey: 'Lines: solid = selected · dashed = previous',
  },
  el: {
    loading: "Φόρτωση καταγεγραμμένων ημερήσιων συγκρίσεων…",
    loadError: "Δεν ήταν δυνατή η φόρτωση των συγκρίσεων. Δοκιμάστε ξανά για το πιο πρόσφατο στιγμιότυπο.",
    retry: "Δοκιμή ξανά",
    refresh: "Ανανέωση",
    recorded: "καταγεγραμμένο",
    title: 'Ενεργειακές τάσεις', subtitle: 'Δείτε τι άλλαξε, χωρίς αναζήτηση στο ιστορικό.', days: 'ημέρες',
    complete: 'Ολοκληρωμένες ημέρες · Ώρα Αθήνας',
    versus: 'σε σύγκριση με', house: 'Κατανάλωση σπιτιού', solar: 'Παραγωγή φωτοβολταϊκών', grid: 'Κατανάλωση δικτύου',
    soc: 'Ημερήσια μέγιστη φόρτιση', points: 'μ.π.', socUnits: 'Υψηλότερη καταγεγραμμένη φόρτιση κάθε ημέρας · %', socNote: 'Κάθε στήλη δείχνει την υψηλότερη καταγεγραμμένη φόρτιση μπαταρίας εκείνης της ημέρας. Η σύνοψη είναι ο μέσος όρος αυτών των ημερήσιων μέγιστων, με ίσο βάρος ανά ημέρα. Οι διαφορές είναι ποσοστιαίες μονάδες (μ.π.).', socSummary: 'Μέσος όρος ημερήσιων μέγιστων',
    previous: 'Προηγούμενη', current: 'Επιλεγμένη περίοδος', higher: 'περισσότερο', lower: 'λιγότερο', unchanged: 'Χωρίς αλλαγή',
    explore: 'Αναλυτική σύγκριση', collapse: 'Απόκρυψη σύγκρισης', coverage: 'καταγραφή και στις δύο περιόδους',
    daily: 'Ημερήσια σύγκριση', help: 'Επιλέξτε ημέρα για ακριβείς τιμές.', currentLegend: 'Επιλεγμένη', previousLegend: 'Προηγούμενη',
    battery: 'Δραστηριότητα μπαταρίας', charged: 'Ενέργεια φόρτισης', supplied: 'Ενέργεια που παρείχε',
    range: 'Εύρος στάθμης φόρτισης', rangeNote: 'Ελάχιστη και μέγιστη καταγεγραμμένη φόρτιση, όχι χωρητικότητα μπαταρίας.',
    note: 'Πράσινο: λιγότερη κατανάλωση σπιτιού/δικτύου ή περισσότερη ηλιακή παραγωγή. Κόκκινο: το αντίθετο. Πορτοκαλί: αλλαγές εντός ±5%. Οι αλλαγές μπαταρίας παραμένουν ουδέτερες· η ενέργειά της δεν προστίθεται στην ηλιακή παραγωγή.',
    table: 'Προβολή ημερήσιων τιμών', date: 'Ημερομηνία', difference: 'Διαφορά', observations: 'Με μια ματιά',
    high: 'Μέγιστη κατανάλωση σπιτιού', low: 'Ελάχιστη ηλιακή παραγωγή', gridShare: 'Ενέργεια δικτύου / σπιτιού',
    shareNote: 'Αναλογία καταγεγραμμένων συνόλων, όχι ακριβής κατανομή πηγών.',
    noPrevious: 'Χωρίς προηγούμενη κατανάλωση', insufficient: 'Ανεπαρκής κάλυψη για σύγκριση', unavailable: 'Μη διαθέσιμο',
    periodLabel: 'Περίοδος σύγκρισης', dayButton: 'Επιλογή ημέρας', coverageNote: "Μόνο καταγεγραμμένοι μέσοι όροι λεπτού· τα κενά δεν υπολογίζονται ως μηδέν. Οι διαφορές απαιτούν τουλάχιστον 95% κάλυψη κάθε ημέρας και στις δύο περιόδους. Τα μέγιστα φόρτισης μπορεί να είναι χαμηλότερα όταν λείπουν μετρήσεις.",
    units: 'kWh ανά ημέρα', historyLink: 'Άνοιγμα ζωντανού ιστορικού ↗', insights: 'Ενεργειακές αναφορές', sparkKey: 'Γραμμές: συνεχής = επιλεγμένη · διακεκομμένη = προηγούμενη',
  },
  ru: {
    loading: "Загрузка записанных сравнений по дням…",
    loadError: "Не удалось загрузить сравнения. Повторите попытку для получения актуального снимка.",
    retry: "Повторить",
    refresh: "Обновить",
    recorded: "записано",
    title: 'Энергетические тенденции', subtitle: 'Узнайте, что изменилось, без поиска в истории.', days: 'дней',
    complete: 'Полные дни · Афинское время',
    versus: 'в сравнении с', house: 'Потребление дома', solar: 'Солнечная генерация', grid: 'Потребление из сети',
    soc: 'Пиковый заряд за день', points: 'п.п.', socUnits: 'Максимальный записанный заряд за каждый день · %', socNote: 'Каждый столбец показывает максимальный записанный заряд батареи за день. Сводка — среднее этих дневных максимумов, с одинаковым весом каждого дня. Разница указана в процентных пунктах (п.п.).', socSummary: 'Среднее дневных максимумов',
    previous: 'Ранее', current: 'Выбранный период', higher: 'больше', lower: 'меньше', unchanged: 'Без изменений',
    explore: 'Подробное сравнение', collapse: 'Скрыть сравнение', coverage: 'записано в обоих периодах',
    daily: 'Сравнение по дням', help: 'Выберите день для точных значений.', currentLegend: 'Выбранный', previousLegend: 'Предыдущий',
    battery: 'Работа аккумулятора', charged: 'Энергия заряда', supplied: 'Отданная энергия',
    range: 'Диапазон уровня заряда', rangeNote: 'Минимальный и максимальный записанный заряд, а не ёмкость аккумулятора.',
    note: 'Зелёный: меньше потребления дома/из сети или больше солнечной энергии. Красный: наоборот. Янтарный: изменения в пределах ±5%. Изменения аккумулятора нейтральны; его энергия не прибавляется к солнечной генерации.',
    table: 'Показать значения по дням', date: 'Дата', difference: 'Разница', observations: 'Краткий обзор',
    high: 'Максимальное потребление дома', low: 'Минимальная солнечная генерация', gridShare: 'Энергия сети / дома',
    shareNote: 'Отношение записанных итогов, а не точное распределение источников.',
    noPrevious: 'Ранее потребления не было', insufficient: 'Недостаточно данных для сравнения', unavailable: 'Нет данных',
    periodLabel: 'Период сравнения', dayButton: 'Выбрать день', coverageNote: "Только записанные средние за минуту; пропуски не считаются нулями. Разница показывается при покрытии не менее 95% каждого дня обоих периодов. При пропусках измерений пики заряда могут быть занижены.",
    units: 'кВт·ч в день', historyLink: 'Открыть актуальную историю ↗', insights: 'Энергетические отчёты', sparkKey: 'Линии: сплошная = выбранный · пунктир = предыдущий',
  },
};
const host = document.querySelector('#energy-trends');
if (!host) return;
let rows = null, snapshotDay = null, loading = false, failed = false, controller = null;
// On phones, trends follows Power flow directly; Insights stays alongside it on desktop.
document.querySelector('.energy-scene').after(host);
let days = 7, selected = 'house', expanded = false, day = 0;
const t = key => (copy[document.documentElement.lang] || copy.en)[key];
const number = (value, precision = 1) => Number.isFinite(value) ? new Intl.NumberFormat(document.documentElement.lang, {maximumFractionDigits: precision}).format(value) : '—';
const date = value => value.split('-').reverse().join('/');
const shortDate = value => date(value).slice(0, 5);
const range = rows => `${date(rows[0].date)} – ${date(rows.at(-1).date)}`;
const unit = key => key === 'soc' ? '%' : 'kWh';
const deltaUnit = key => key === 'soc' ? t('points') : 'kWh';
function change(metric, key) {
  if (!metric.comparable) return t('insufficient');
  if (Math.abs(metric.delta) < .001) return t('unchanged');
  if (key === 'soc') return `${metric.delta > 0 ? '↗ +' : '↘ −'}${number(Math.abs(metric.delta))} ${t('points')}`;
  if (metric.percent === null) return `${metric.delta > 0 ? '+' : '−'}${number(Math.abs(metric.delta))} kWh · ${t('noPrevious')}`;
  return `${metric.delta > 0 ? '↗' : '↘'} ${number(Math.abs(metric.percent))}% ${t(metric.delta > 0 ? 'higher' : 'lower')} <span>(${metric.delta > 0 ? '+' : '−'}${number(Math.abs(metric.delta))} kWh)</span>`;
}
function coverage(row, key) { return number(row.coverage[key]) + '% ' + t('recorded'); }
function spark(data, key) {
  const max = key === 'soc' ? 100 : Math.max(...[...data.current, ...data.previous].map(row => row[key]).filter(Number.isFinite), 1);
  const lines = (items, className) => {
    let segments = [], points = [];
    items.forEach((row, i) => {
      if (!Number.isFinite(row[key])) { if (points.length) segments.push(points); points = []; return; }
      points.push(`${3 + i / (items.length - 1) * 154},${49 - row[key] / max * 42}`);
    });
    if (points.length) segments.push(points);
    return segments.map(part => `<polyline class="${className}" points="${part.join(' ')}"/>`).join('');
  };
  return `<svg class="trend-spark" viewBox="0 0 160 56" aria-hidden="true">${lines(data.previous, 'spark-before')}${lines(data.current, '')}</svg>`;
}
function battery(data) {
  const extrema = rows => {
    const known = rows.filter(row => Number.isFinite(row.minSoc) && Number.isFinite(row.maxSoc));
    return known.length ? `${number(Math.min(...known.map(row => row.minSoc)))}–${number(Math.max(...known.map(row => row.maxSoc)))}%` : '—';
  };
  return `<aside class="trend-battery"><h3>${t('battery')}</h3>${['charged', 'supplied'].map(key => `<div class="battery-stat"><span>${t(key)}</span><strong>${number(data.metrics[key].now.total)} <small>kWh</small></strong><p>${t('previous')} ${number(data.metrics[key].before.total)} kWh</p><p>${change(data.metrics[key], key)}</p><p>${number(data.metrics[key].now.coverage)}% ${t("recorded")} · ${t("previous")} ${number(data.metrics[key].before.coverage)}%</p></div>`).join('')}<div class="battery-stat"><span>${t('range')}</span><strong>${extrema(data.current)}</strong><p>${t('previous')} ${extrema(data.previous)}</p><p>${t('rangeNote')}</p></div></aside>`;
}
function dayReadout(data) {
  const before = data.previous[day], now = data.current[day];
  const delta = dailyDelta(now, before, selected);
  return `<span>${shortDate(now.date)} <strong>${number(now[selected])} ${unit(selected)}</strong>${coverage(now, selected)}</span><span>${t('previous')} · ${shortDate(before.date)} <strong>${number(before[selected])} ${unit(selected)}</strong>${coverage(before, selected)}</span><span>${t('difference')} <strong data-tone="${changeTone(selected, {...compare(now[selected], before[selected]), comparable: delta !== null})}">${delta > 0 ? '+' : delta < 0 ? '−' : ''}${number(delta === null ? null : Math.abs(delta))} ${deltaUnit(selected)}</strong></span>`;
}
function bar(row, key, ceiling, before) {
  if (!Number.isFinite(row[key])) return '<i class="bar-missing" title="' + t('unavailable') + '"></i>';
  return `<i class="${before ? 'bar-before' : ''} ${row.coverage[key] < 95 ? 'bar-partial' : ''}" style="height:${row[key] / ceiling * 100}%"></i>`;
}
function chart(data) {
  const max = Math.max(...[...data.current, ...data.previous].map(row => row[selected]).filter(Number.isFinite), 1);
  const ceiling = selected === 'soc' ? 100 : Math.ceil(max / 5) * 5;
  return `<div class="trend-chart" data-source="${selected}"><div class="chart-heading"><div><h3>${t('daily')} · ${t(selected)}</h3><p>${t('help')}</p></div><div class="trend-legend"><span><i></i>${t('currentLegend')}</span><span><i class="before-key"></i>${t('previousLegend')}</span></div></div>
    <p class="chart-unit">${t(selected === 'soc' ? 'socUnits' : 'units')}</p><div class="chart-plot"><div class="chart-axis" aria-hidden="true"><span>${number(ceiling)}</span><span>${number(ceiling / 2)}</span><span>0</span></div><div class="bars-scroll" tabindex="0" aria-label="${t('daily')}"><div class="trend-bars" style="--days:${days};min-width:${days * 34}px">${data.current.map((row, i) => `<button class="bar-pair" type="button" data-day="${i}" aria-pressed="${i === day}" aria-label="${date(row.date)}: ${number(row[selected])} ${unit(selected)}; ${date(data.previous[i].date)}: ${number(data.previous[i][selected])} ${unit(selected)}"><span class="bar-well">${bar(data.previous[i], selected, ceiling, true)}${bar(row, selected, ceiling, false)}</span><span class="bar-label">${shortDate(row.date)}</span></button>`).join('')}</div></div></div>
    <div class="day-readout" aria-live="polite" aria-atomic="true">${dayReadout(data)}</div>
    ${selected === 'soc' ? `<p>${t('socNote')}</p>` : ''}
    <details class="trend-table"><summary>${t('table')}</summary><div class="table-scroll"><table><caption>${t(selected)} · ${unit(selected)}</caption><thead><tr><th>${t('date')}</th><th>${t('currentLegend')}</th><th>${t('date')}</th><th>${t('previousLegend')}</th><th>${t('difference')} · ${deltaUnit(selected)}</th></tr></thead><tbody>${data.current.map((row, i) => `<tr><th>${date(row.date)}</th><td>${number(row[selected])}<small>${coverage(row, selected)}</small></td><th>${date(data.previous[i].date)}</th><td>${number(data.previous[i][selected])}<small>${coverage(data.previous[i], selected)}</small></td><td>${number(dailyDelta(row, data.previous[i], selected))}</td></tr>`).join('')}</tbody></table></div></details></div>`;
}
function render() {
  if (!rows) {
    host.innerHTML = '<h2 id="trends-title">' + t('title') + '</h2><p role="status">' + t(failed ? 'loadError' : 'loading') + '</p>' + (failed ? '<button type="button" id="trends-retry">' + t('retry') + '</button>' : '');
    return;
  }
  const data = buildComparison(days, rows);
  const high = extreme(data.current, 'house', true);
  const low = extreme(data.current, 'solar', false);
  host.innerHTML = `<div class="trends-heading"><div><h2 id="trends-title">${t('title')}</h2><p>${t('subtitle')}</p></div><div class="trend-periods" role="group" aria-label="${t('periodLabel')}">${[7, 14, 30].map(value => `<button type="button" data-period="${value}" aria-pressed="${value === days}">${value} ${t('days')}</button>`).join('')}</div></div>
    <div class="trend-dates"><strong>${range(data.current)}</strong><span>${t('versus')} ${range(data.previous)}</span><span>${t('complete')}</span></div>
    <div class="trend-summary">${METRICS.map(key => {const metric = data.metrics[key]; return `<button class="trend-metric" type="button" data-metric="${key}" data-source="${key}" aria-expanded="${expanded && selected === key}" aria-controls="trend-details"><span class="trend-label"><i></i>${t(key)}<svg class="metric-open" viewBox="0 0 20 20" aria-hidden="true" focusable="false"><path d="m6 8 4 4 4-4"/></svg></span><span class="metric-visual"><span class="trend-value">${number(metric.now.total)} <small>${unit(key)}</small></span>${spark(data, key)}</span><span class="trend-previous">${t('previous')} ${number(metric.before.total)} ${unit(key)}</span><span class="trend-change">${change(metric, key)}</span><span class="trend-previous">${number(metric.now.coverage)}% ${t("recorded")} · ${t("previous")} ${number(metric.before.coverage)}%</span></button>`;}).join('')}</div>
    <div class="trend-bottom"><div><span>${t("complete")} · <button id="trends-retry" type="button">${t("refresh")}</button></span><p class="spark-key">${t('sparkKey')}</p></div><button id="trend-expand" type="button" aria-expanded="${expanded}" aria-controls="trend-details">${t(expanded ? 'collapse' : 'explore')} <span aria-hidden="true">${expanded ? '−' : '+'}</span></button></div>
    <div id="trend-details" ${expanded ? '' : 'hidden'}><div class="trend-detail-grid">${expanded ? chart(data) + battery(data) : ''}</div>${expanded ? `<div class="trend-observations"><h3>${t('observations')}</h3><dl><div><dt>${t('high')}</dt><dd>${number(high?.house)} kWh <span>· ${high ? shortDate(high.date) : t("insufficient")}</span></dd></div><div><dt>${t('low')}</dt><dd>${number(low?.solar)} kWh <span>· ${low ? shortDate(low.date) : t("insufficient")}</span></dd></div><div><dt>${t('gridShare')}</dt><dd>${number(ratio(data, 'now'))}% <span>· ${t('previous')} ${number(ratio(data, 'before'))}%</span></dd></div></dl><p>${t('shareNote')}</p></div><p class="trend-footnote">${t('note')}</p><p class="trend-footnote">${t('coverageNote')}</p>` : ''}</div>`;
  for (const key of METRICS) {
    host.querySelector(`[data-metric="${key}"] .trend-change`).dataset.tone = changeTone(key, data.metrics[key]);
  }
  const peakDescription = document.createElement('span');
  peakDescription.className = 'trend-previous';
  peakDescription.textContent = t('socSummary');
  host.querySelector('[data-metric="soc"] .metric-visual').after(peakDescription);
}
host.addEventListener('click', event => {
  const button = event.target.closest('button');
  if (!button) return;
  if (button.id === 'trends-retry') { load(); return; }
  let focusSelector;
  if (button.dataset.period) { days = Number(button.dataset.period); day = 0; focusSelector = `[data-period="${days}"]`; }
  else if (button.dataset.metric) {
    const nextMetric = button.dataset.metric;
    expanded = nextMetric === selected ? !expanded : true;
    selected = nextMetric;
    focusSelector = `[data-metric="${selected}"]`;
  }
  else if (button.id === 'trend-expand') { expanded = !expanded; focusSelector = '#trend-expand'; }
  else if (button.dataset.day !== undefined) {
    day = Number(button.dataset.day);
    // Preserve the mounted live region, table disclosure and horizontal scroll.
    host.querySelectorAll('[data-day]').forEach(item => item.setAttribute('aria-pressed', String(Number(item.dataset.day) === day)));
    host.querySelector('.day-readout').innerHTML = dayReadout(buildComparison(days, rows));
    host.querySelector(`[data-day="${day}"]`).focus({preventScroll: true});
    return;
  } else return;
  render();
  host.querySelector(focusSelector).focus({preventScroll: true});
});
host.addEventListener('keydown', event => {
  const button = event.target.closest('[data-day]');
  if (!button || !['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
  event.preventDefault();
  let next = Number(button.dataset.day) + (event.key === 'ArrowLeft' ? -1 : 1);
  if (event.key === 'Home') next = 0;
  if (event.key === 'End') next = days - 1;
  host.querySelector(`[data-day="${Math.max(0, Math.min(days - 1, next))}"]`).click();
  host.querySelector(`[data-day="${day}"]`).scrollIntoView({block: 'nearest', inline: 'nearest'});
});
async function load() {
  if (loading) return;
  loading = true; failed = false; rows = null;
  render();
  controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 15000);
  try {
    const response = await fetch('/history/index.json', {cache: 'no-store', credentials: 'omit', signal: controller.signal});
    if (!response.ok) throw new Error('History unavailable');
    const text = await response.text();
    if (text.length > 1000000) throw new Error('Index too large');
    rows = normalize(JSON.parse(text).energy_summary?.trends);
    snapshotDay = athensDay();
  } catch { failed = true; }
  finally { clearTimeout(timer); loading = false; render(); }
}
window.addEventListener('languagechange', render);
window.addEventListener('pagehide', () => controller?.abort());
document.addEventListener('visibilitychange', () => {
  if (!document.hidden && snapshotDay && snapshotDay !== athensDay()) load();
});
render();
if ('IntersectionObserver' in window) {
  const observer = new IntersectionObserver(entries => {
    if (entries.some(entry => entry.isIntersecting)) { observer.disconnect(); load(); }
  }, {rootMargin: '200px'});
  observer.observe(host);
} else load();
})();
