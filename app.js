// Pure presentation logic. Routing is estimated, not independently metered.
window.energyFlowDuration = watts => {
  // Speed (not duration) scales linearly from 1x at 300 W to 3x at 2 kW.
  const fraction = Number.isFinite(watts) ? Math.max(0, Math.min(1, (watts - 300) / 1700)) : 0;
  return 3 / (1 + 2 * fraction);
};
window.energyFlowState = (values, mode) => {
  const positive = value => Number.isFinite(value) && value >= 0.5;
  const valid = key => Number.isFinite(values[key]);
  const charging = positive(values.battery);
  const discharging = valid('battery') && values.battery <= -0.5;
  const waiting = valid('battery') && !charging && !discharging && mode === 'Mains' && valid('soc') && values.soc < 40;
  const batteryState = charging ? 'charging' : discharging ? 'discharging' : waiting ? 'waiting' : 'idle';
  // Grid charging is disabled in this installation. Avoid inventing missing flows.
  const solarToHouse = ['pv', 'load', 'grid', 'battery'].every(valid)
    ? Math.min(Math.max(0, values.pv), Math.max(0, values.load - Math.max(0, values.grid) - Math.max(0, -values.battery))) : 0;
  return { batteryState, batteryLabel: !valid('battery') ? 'No report' : charging ? 'Charging' : discharging ? 'Discharging' : waiting ? 'Waiting to charge' : 'Standby',
    routeWatts: { gridHouse: valid('grid') && valid('load') ? Math.min(Math.max(0, values.grid), Math.max(0, values.load)) : 0,
      solarHouse: solarToHouse,
      batteryHouse: valid('battery') && valid('load') ? Math.min(Math.max(0, -values.battery), Math.max(0, values.load)) : 0,
      solarBattery: valid('pv') && valid('battery') ? Math.min(Math.max(0, values.pv), Math.max(0, values.battery)) : 0 },
    routes: { gridHouse: positive(values.grid) && positive(values.load), solarHouse: positive(solarToHouse),
      batteryHouse: discharging && positive(values.load), solarBattery: positive(values.pv) && charging } };
};

window.energyModeLabel = mode => mode === 'Mains' ? 'Grid' : mode || '—';
window.energySupplyLabel = values => {
  if (!['grid', 'pv', 'battery', 'load'].every(key => Number.isFinite(values[key]))) return 'Unknown supply';
  if (values.load <= 0) return 'Standby';
  const flows = window.energyFlowState(values).routeWatts;
  const sources = { Grid: flows.gridHouse, Solar: flows.solarHouse, Battery: flows.batteryHouse };
  const active = Object.keys(sources).filter(key => sources[key] > Math.max(20, values.load * .02));
  if (active.length > 1) return 'Mixed';
  if (active.length) return active[0];
  const largest = Object.keys(sources).sort((a, b) => sources[b] - sources[a])[0];
  return sources[largest] > 0 ? largest : 'Unknown supply';
};

// Shared localization is also used by the history page.
window.energyI18n = (() => {
  const translations = {
    ru: {
      'Overview': 'Обзор', 'History': 'История', 'Main navigation': 'Основная навигация',
      'Live · updated {seconds}s ago': 'Онлайн · обновлено {seconds} с назад',
      'Inverter · ': 'Инвертор · ', 'Off-Grid': 'Автономный',
      'House supply · {source}': 'Питание дома · {source}',
      'Not refreshed yet': 'Ещё не обновлено',
      'Snapshot loaded {time} · refresh to update': 'Данные загружены в {time} · обновите для новых показаний',
      'House supply': 'Питание дома', 'Zoom in': 'Приблизить', 'Upcoming': 'Впереди',
      'Start': 'Начало', 'End': 'Конец', 'Previous interval': 'Предыдущий интервал', 'Next interval': 'Следующий интервал',
      'Select an interval to see its times.': 'Выберите интервал, чтобы увидеть время.',
      '{n} changes · select to zoom': 'Переходов: {n} · нажмите для увеличения',
      'Striped blocks contain multiple changes. Select to zoom, or drag across the timeline.': 'Полосатые блоки содержат несколько переходов. Нажмите для увеличения или выделите участок шкалы.',
      'Today': 'Сегодня',
      '{n}d': '{n}д', '{n}h': '{n}ч', '{n}m': '{n}мин', '{n}s': '{n}с',
      'Enter a valid date as DD/MM/YYYY, not in the future.': 'Введите корректную дату ДД/ММ/ГГГГ, не позднее сегодняшней.',
      'Mode': 'Режим', 'Mixed': 'Смешанный', 'No data': 'Нет данных', 'Unknown supply': 'Источник неизвестен', 'Mode not recorded': 'Режим не записан', 'Duration': 'Длительность',
      'Estimated source supplying the house': 'Расчётный источник питания дома',
      'Observed transitions; timing depends on polling. Gaps start 90 seconds after the last report.': 'Зафиксированные переходы; точность зависит от опроса. Пробелы начинаются через 90 секунд после последней отчётной записи.',
      'PV voltage (legacy)': 'Напряжение PV (архив)',
      'Solar total': 'Солнце · всего', 'PV1 power': 'Мощность PV1', 'PV2 power': 'Мощность PV2',
      'PV1 voltage': 'Напряжение PV1', 'PV2 voltage': 'Напряжение PV2',
      'PV1 current': 'Ток PV1', 'PV2 current': 'Ток PV2',
      'Waiting to charge': 'Ожидание зарядки', 'Discharging': 'Разрядка',
      'Estimated flow': 'Расчётный поток', 'Estimated': 'Расчёт',
      'Waiting for 40% charge before battery use resumes.': 'Ожидание заряда 40% для возобновления работы от батареи.',
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
      'Overview': 'Επισκόπηση', 'History': 'Ιστορικό', 'Main navigation': 'Κύρια πλοήγηση',
      'Live · updated {seconds}s ago': 'Ζωντανά · ενημέρωση πριν από {seconds} δ',
      'Inverter · ': 'Μετατροπέας · ', 'Off-Grid': 'Εκτός δικτύου',
      'House supply · {source}': 'Τροφοδοσία σπιτιού · {source}',
      'Not refreshed yet': 'Δεν ανανεώθηκε ακόμη',
      'Snapshot loaded {time} · refresh to update': 'Φόρτωση στις {time} · ανανεώστε για νέες μετρήσεις',
      'House supply': 'Τροφοδοσία σπιτιού', 'Zoom in': 'Μεγέθυνση', 'Upcoming': 'Αργότερα',
      'Start': 'Έναρξη', 'End': 'Λήξη', 'Previous interval': 'Προηγούμενο διάστημα', 'Next interval': 'Επόμενο διάστημα',
      'Select an interval to see its times.': 'Επιλέξτε ένα διάστημα για να δείτε τις ώρες του.',
      '{n} changes · select to zoom': '{n} αλλαγές · επιλέξτε για μεγέθυνση',
      'Striped blocks contain multiple changes. Select to zoom, or drag across the timeline.': 'Τα ριγέ τμήματα περιέχουν πολλές αλλαγές. Επιλέξτε για μεγέθυνση ή σύρετε πάνω στη χρονογραμμή.',
      'Today': 'Σήμερα',
      '{n}d': '{n}ημ', '{n}h': '{n}ω', '{n}m': '{n}λ', '{n}s': '{n}δ',
      'Enter a valid date as DD/MM/YYYY, not in the future.': 'Εισαγάγετε έγκυρη ημερομηνία ΗΗ/ΜΜ/ΕΕΕΕ, όχι στο μέλλον.',
      'Mode': 'Λειτουργία', 'Mixed': 'Μικτή', 'No data': 'Χωρίς δεδομένα', 'Unknown supply': 'Άγνωστη πηγή', 'Mode not recorded': 'Δεν καταγράφηκε', 'Duration': 'Διάρκεια',
      'Estimated source supplying the house': 'Εκτιμώμενη πηγή τροφοδοσίας σπιτιού',
      'Observed transitions; timing depends on polling. Gaps start 90 seconds after the last report.': 'Καταγεγραμμένες μεταβάσεις· η ακρίβεια εξαρτάται από τη συχνότητα λήψης. Τα κενά ξεκινούν 90 δευτερόλεπτα μετά την τελευταία αναφορά.',
      'PV voltage (legacy)': 'Τάση PV (παλαιά δεδομένα)',
      'Solar total': 'Ηλιακή · σύνολο', 'PV1 power': 'Ισχύς PV1', 'PV2 power': 'Ισχύς PV2',
      'PV1 voltage': 'Τάση PV1', 'PV2 voltage': 'Τάση PV2',
      'PV1 current': 'Ρεύμα PV1', 'PV2 current': 'Ρεύμα PV2',
      'Waiting to charge': 'Αναμονή φόρτισης', 'Discharging': 'Εκφόρτιση',
      'Estimated flow': 'Εκτιμώμενη ροή', 'Estimated': 'Εκτίμηση',
      'Waiting for 40% charge before battery use resumes.': 'Αναμονή φόρτισης στο 40% για επαναφορά της χρήσης μπαταρίας.',
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
  const selectors = ['.brand-sub', '.mode-badge', '.history-nav a', '.intro h1', '.intro p', '.scene-heading>span:last-child', '.scene-label>div', '.scene-caption', '.metric-top>span:first-child', '.metric.load p', '.back', '[data-period]', '.date-controls>span', '#today', '#select-all', '#clear-all', 'label[for="averaging"]', '#averaging option'];
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
    document.querySelectorAll('[data-i18n]').forEach(element => { element.textContent = t(element.dataset.i18n); });
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
  { grid: 535, pv: 0, load: 563, battery: 0, soc: 20, pv_voltage: 34.7, grid_voltage: 234.5, pv_current: 0, battery_current: 0, load_current: 3.1, mode: 'Mains' },
  { grid: 0, pv: 1580, load: 910, battery: 670, soc: 84, pv_voltage: 382.9, grid_voltage: 232, pv_current: 4.1, battery_current: 12.7, load_current: 4.5, mode: 'Off-Grid' },
  { grid: 0, pv: 410, load: 910, battery: -500, soc: 84, pv_voltage: 360, grid_voltage: 232, pv_current: 1.1, battery_current: -9.5, load_current: 4.5, mode: 'Off-Grid' },
  { grid: 0, pv: 0, load: 940, battery: -940, soc: 64, pv_voltage: 31, grid_voltage: 232, pv_current: 0, battery_current: -18, load_current: 4.8, mode: 'Off-Grid' },
];
// Explicitly synthetic channel values for the loopback-only preview.
samples.forEach(sample => {
  for (const channel of [1, 2]) {
    const power = sample.pv * (channel === 1 ? .6 : .4);
    const voltage = sample.pv ? (channel === 1 ? 382.9 : 237.2) : 30;
    Object.assign(sample, { [`pv${channel}_power`]: power, [`pv${channel}_voltage`]: voltage,
      [`pv${channel}_current`]: power / voltage });
  }
});
const pvChannelViews = [];
for (const selector of ['.solar-label>div', '.metric.solar']) {
  const parent = document.querySelector(selector);
  parent.querySelector('[data-value="pv_voltage"]').parentElement.remove();
  const channels = document.createElement('div'); channels.className = 'pv-channels';
  for (const channel of [1, 2]) {
    const row = document.createElement('div'); row.className = 'pv-channel';
    const name = document.createElement('strong'); name.textContent = `PV${channel}`;
    const power = document.createElement('span'); power.className = 'pv-channel-power';
    const details = document.createElement('span'); details.className = 'pv-channel-details';
    row.append(name, power, details); channels.append(row);
    pvChannelViews.push({ channel, power, details });
  }
  parent.append(channels);
}
let sampleIndex = 0;
const currentReadings = [];
for (const [name, key, label] of [
  ['battery', 'battery_current', 'Battery current (avg.)'],
  ['load', 'load_current', 'House current'],
]) {
  for (const [selector, tag] of [[`.${name}-label>div`, 'span'], [`.metric.${name}`, 'p']]) {
    if (name === 'battery' && tag === 'p') continue;
    const line = document.createElement(tag);
    line.className = 'current-reading';
    const value = document.createElement('b'); value.dataset.value = key;
    const unit = document.createElement('small');
    line.append(value, document.createTextNode(' '), unit);
    const parent = document.querySelector(selector);
    const meter = parent.querySelector('.meter');
    parent.insertBefore(line, meter);
    currentReadings.push({ line, unit, label, key, battery: name === 'battery' });
  }
}
// Existing illustration stays decorative; paths attach to the actual card edges.
const scene = document.querySelector('.energy-scene');
const svgNS = 'http://www.w3.org/2000/svg';
function svgElement(tag, attributes = {}) {
  const element = document.createElementNS(svgNS, tag);
  for (const [name, value] of Object.entries(attributes)) element.setAttribute(name, value);
  return element;
}
const icons = {
  grid: 'M13 2 4 14h7l-1 8 10-13h-7l1-7Z',
  solar: 'M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1.5 1.5m11 11L19 19M5 19l1.5-1.5m11-11L19 5M16 12a4 4 0 1 1-8 0 4 4 0 0 1 8 0',
  battery: 'M3 7h16v10H3V7Zm16 3h2v4h-2M6 10v4m3-4v4m3-4v4',
  load: 'm3 11 9-8 9 8M5 10v11h14V10M10 21v-7h4v7',
};
for (const [name, d] of Object.entries(icons)) {
  document.querySelectorAll(`.${name}-label .mini-icon, .metric.${name} .symbol`).forEach(holder => {
    const icon = svgElement('svg', { viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': '1.7', 'stroke-linecap': 'round', 'stroke-linejoin': 'round', 'aria-hidden': 'true' });
    icon.append(svgElement('path', { d })); holder.replaceChildren(icon);
  });
}
const flowSvg = svgElement('svg', { class: 'energy-connections', 'aria-hidden': 'true' });
const flowDefinitions = {
  gridHouse: ['grid', 'load', '#8861ba'],
  solarHouse: ['solar', 'load', '#c48a15'],
  batteryHouse: ['battery', 'load', '#169779'],
  solarBattery: ['solar', 'battery', '#c48a15'],
};
const flowGroups = {};
for (const [key, [, , color]] of Object.entries(flowDefinitions)) {
  const group = svgElement('g', { 'data-route': key, stroke: color, fill: 'none' });
  group.append(svgElement('path', { class: 'connection-line' }), svgElement('path', { class: 'connection-dots' }), svgElement('path', { class: 'connection-arrow', d: 'M-8 -4 0 0-8 4' }));
  group.style.display = 'none'; flowGroups[key] = group; flowSvg.append(group);
}
scene.append(flowSvg);
let layoutFrame = null;
function scheduleFlowLayout() {
  if (layoutFrame !== null) return;
  layoutFrame = requestAnimationFrame(() => {
    layoutFrame = null;
    const bounds = scene.getBoundingClientRect();
    flowSvg.setAttribute('viewBox', `0 0 ${bounds.width} ${bounds.height}`);
    function rect(name) {
      const box = document.querySelector(`.${name}-label`).getBoundingClientRect();
      return { x: box.left - bounds.left + box.width / 2, y: box.top - bounds.top + box.height / 2, w: box.width, h: box.height };
    }
    for (const [key, [source, target]] of Object.entries(flowDefinitions)) {
      const a = rect(source), b = rect(target);
      const dx = b.x - a.x, dy = b.y - a.y;
      const length = Math.hypot(dx, dy);
      if (!length) continue;
      // Intersect the center-to-center ray with each card's rectangle, plus a small gap.
      const edge = box => Math.min(dx ? box.w / 2 / Math.abs(dx) : Infinity, dy ? box.h / 2 / Math.abs(dy) : Infinity) + 5 / length;
      const start = edge(a), end = 1 - edge(b);
      const x1 = a.x + dx * start, y1 = a.y + dy * start;
      const x2 = a.x + dx * end, y2 = a.y + dy * end;
      const d = `M${x1} ${y1}L${x2} ${y2}`;
      flowGroups[key].querySelectorAll('.connection-line,.connection-dots').forEach(path => path.setAttribute('d', d));
      flowGroups[key].querySelector('.connection-arrow').setAttribute('transform', `translate(${x2} ${y2}) rotate(${Math.atan2(dy, dx) * 180 / Math.PI})`);
    }
  });
}
const flowResize = new ResizeObserver(scheduleFlowLayout);
flowResize.observe(scene);
document.querySelectorAll('.scene-label').forEach(box => flowResize.observe(box));
let paused = false;
const pauseButton = document.querySelector('#pause');
pauseButton.hidden = !demo;
document.querySelector('.disclaimer').hidden = !demo;
function updateDate() { document.querySelector('#date').textContent = new Intl.DateTimeFormat('en-GB', { day: '2-digit', month: '2-digit', year: 'numeric', timeZone: 'Europe/Athens' }).format(new Date()); }
updateDate();
function formatPower(watts) {
  if (!Number.isFinite(watts)) return { value: '—', unit: 'W' };
  const kilo = Math.abs(watts) >= 1000;
  return { value: new Intl.NumberFormat(window.energyI18n.locale, { maximumFractionDigits: kilo ? 2 : 0, useGrouping: false }).format(kilo ? watts / 1000 : watts), unit: kilo ? 'kW' : 'W' };
}
function render(sample) {
  currentReadings.forEach(({ line, unit, label, key, battery }) => {
    line.title = t(label);
    unit.textContent = battery ? t('A (avg.)') : 'A';
    line.hidden = Number.isFinite(sample[key]) && Math.abs(sample[key]) < 0.05;
    if (battery && Number.isFinite(sample.battery) && Math.abs(sample.battery) < 0.5) line.hidden = true;
  });
  // Dashboard convention: positive = charging, negative = discharging.
  const mode = demo ? sample.mode : latest?.mode;
  const state = window.energyFlowState(sample, mode);
  const supply = window.energySupplyLabel(sample);
  document.querySelector('#supply-status').textContent = t('House supply · {source}', { source: t(supply) });
  document.body.dataset.activeSource = { Grid: 'grid', Solar: 'solar', Battery: 'battery' }[supply] || '';
  document.body.dataset.batteryState = state.batteryState;
  document.querySelectorAll('.battery-status').forEach(element => {
    element.textContent = t(state.batteryLabel);
    element.title = state.batteryState === 'waiting' ? t('Waiting for 40% charge before battery use resumes.') : '';
  });
  document.querySelectorAll('.battery-watts').forEach(element => { element.hidden = Number.isFinite(sample.battery) && Math.abs(sample.battery) < 0.5; });
  const values = sample;
  document.querySelectorAll('[data-value]').forEach(element => {
    const key = element.dataset.value;
    if (key === 'soc') {
      element.textContent = values[key] ?? '—';
    } else if (key.endsWith('_voltage') || key.endsWith('_current')) {
      element.textContent = Number.isFinite(values[key]) ? new Intl.NumberFormat(window.energyI18n.locale, { maximumFractionDigits: 1 }).format(values[key]) : '—';
    } else {
      const power = formatPower(values[key]);
      element.textContent = power.value;
      element.nextElementSibling.textContent = power.unit;
    }
  });
  for (const { channel, power, details } of pvChannelViews) {
    const prefix = `pv${channel}_`;
    const watts = formatPower(values[prefix + 'power']);
    power.textContent = `${watts.value} ${watts.unit}`;
    power.title = t(`PV${channel} power`);
    const format = value => Number.isFinite(value)
      ? new Intl.NumberFormat(window.energyI18n.locale, { maximumFractionDigits: 1 }).format(value) : '—';
    const current = values[prefix + 'current'];
    details.textContent = `${format(values[prefix + 'voltage'])} V` +
      (Number.isFinite(current) && Math.abs(current) < .05 ? '' : ` · ${format(current)} A`);
    details.title = `${t(`PV${channel} voltage`)} · ${t(`PV${channel} current`)}`;
  }
  document.querySelectorAll('#charge-bar, .scene-charge-bar').forEach(element => { element.style.width = `${Math.max(0, Math.min(100, sample.soc ?? 0))}%`; });
  document.querySelector('#grid-state').textContent = t(Number.isFinite(values.grid) ? 'Estimated' : 'Estimate unavailable');
  document.querySelectorAll('.metric:not(.battery) .meter').forEach(element => { element.hidden = true; });
  for (const [key, active] of Object.entries(state.routes)) {
    flowGroups[key].style.display = active ? '' : 'none';
    flowGroups[key].style.setProperty('--flow-duration', `${window.energyFlowDuration(state.routeWatts[key])}s`);
  }
  scheduleFlowLayout();
  if (demo) {
    document.querySelector('#operating-mode').textContent = t(window.energyModeLabel(mode));
    document.body.dataset.connection = 'live';
  }
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
  if (!['live', 'partial'].includes(status)) {
    document.body.dataset.activeSource = '';
    document.querySelector('#supply-status').textContent = t(labels[status]);
  }
  document.querySelector('#operating-mode').textContent = t(window.energyModeLabel(mode));
  document.querySelector('#freshness').textContent = latest?.updated_at
    ? `${t('Last inverter report')} ${new Date(latest.updated_at * 1000).toLocaleTimeString('en-GB', { timeZone: 'Europe/Athens' })} (${new Date(latest.updated_at * 1000).toLocaleDateString('en-GB', { timeZone: 'Europe/Athens' })}) · ${t('Athens')}`
    : t('Waiting for inverter readings');
  const liveStatus = document.querySelector('#live-status');
  if (liveStatus) {
    liveStatus.textContent = status === 'live'
      ? t('Live · updated {seconds}s ago', { seconds: Math.max(0, Math.floor(now - latest.updated_at)) })
      : t(labels[status] || 'Connection unavailable');
    liveStatus.dataset.state = status;
  }
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
  const previewChoice = document.createElement('select');
  previewChoice.setAttribute('aria-label', 'Preview scenario');
  ['Grid / waiting', 'Solar / charging', 'Solar + battery', 'Battery only'].forEach((label, index) => {
    const option = document.createElement('option'); option.value = index; option.textContent = label; previewChoice.append(option);
  });
  previewChoice.addEventListener('change', () => { sampleIndex = Number(previewChoice.value); paused = true; pauseButton.textContent = 'Resume demo'; document.body.classList.remove('paused'); render(samples[sampleIndex]); });
  document.querySelector('footer').append(previewChoice);
  render(samples[0]);
  setInterval(() => {
    if (paused) return;
    sampleIndex = (sampleIndex + 1) % samples.length;
    previewChoice.value = sampleIndex;
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
