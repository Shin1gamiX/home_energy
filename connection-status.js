/* Overview connection feedback. Transport facts and telemetry freshness are separate. */
(() => {
  'use strict';
  const copy = {
    en: {
      checking: 'Checking the connection', checkingBody: 'Waiting for the first readings from your home.',
      viewer_offline: 'You appear to be offline', viewer_offlineBody: 'This device has lost its network connection. We’ll reconnect automatically.',
      service_unavailable: 'Unable to reach the website’s data service', service_unavailableBody: 'The cause is not yet known. This can be a network or server issue.',
      source_unavailable: 'The reading service is unavailable', source_unavailableBody: 'The website is reachable, but its monitoring source is not providing readings.',
      vpn_unreachable: 'The home VPN is unreachable', vpn_unreachableBody: 'Repeated checks could not reach the home router through the VPN. Downstream checks are unavailable.',
      dongle_unreachable: 'The dongle connection is unavailable', dongle_unreachableBody: 'The home VPN is reachable, but no dongle connection is detected.',
      collector_unavailable: 'The monitoring listener is unavailable', collector_unavailableBody: 'The home VPN is reachable. The server is not accepting the dongle’s monitoring connection.',
      inverter_stale: 'Waiting for fresh inverter readings', inverter_staleBody: 'The VPN is reachable and a dongle connection is present, but recent readings are missing.',
      unknown: 'Live readings are delayed', unknownBody: 'Fresh inverter readings are unavailable. There isn’t enough recent diagnostic information to identify the cause.',
      partial: 'Some readings are unavailable', partialBody: 'The inverter is reporting. Missing measurements are shown as dashes, never as zero.',
      retry: 'Check again', retrying: 'Checking…', automatic: 'Updates automatically', details: 'Connection details',
      website: 'Website', vpn: 'Home VPN', dongle: 'Dongle', inverter: 'Inverter data',
      up: 'Connected', reachable: 'Reachable', fresh: 'Fresh', down: 'Unavailable', stale: 'Delayed',
      unknownState: 'Unknown', checkingState: 'Checking',
      explanation: 'These checks describe the monitoring connection, not whether the inverter is supplying power. A dongle connection alone does not prove that readings are arriving.',
      checked: 'Checked {time}', notChecked: 'No recent check',
      lastReading: 'Last reading · {time} · Athens', secondsAgo: '{value}s ago', minutesAgo: '{value} min ago', hoursAgo: '{value} h ago',
      lastKnown: 'Last known readings · not live', none: 'No saved readings available',
      flowHint: 'Select a box to explore its last 12 hours.', lastBattery: 'Last known battery reading; current activity is unknown.',
    },
    el: {
      checking: 'Έλεγχος σύνδεσης', checkingBody: 'Αναμονή για τις πρώτες μετρήσεις από το σπίτι.',
      viewer_offline: 'Φαίνεται ότι είστε εκτός σύνδεσης', viewer_offlineBody: 'Αυτή η συσκευή έχασε τη σύνδεση δικτύου. Η επανασύνδεση θα γίνει αυτόματα.',
      service_unavailable: 'Η υπηρεσία δεδομένων δεν είναι προσβάσιμη', service_unavailableBody: 'Η αιτία δεν έχει εντοπιστεί ακόμη. Μπορεί να υπάρχει πρόβλημα δικτύου ή διακομιστή.',
      source_unavailable: 'Η υπηρεσία μετρήσεων δεν είναι διαθέσιμη', source_unavailableBody: 'Ο ιστότοπος είναι προσβάσιμος, αλλά η πηγή παρακολούθησης δεν παρέχει μετρήσεις.',
      vpn_unreachable: 'Το οικιακό VPN δεν είναι προσβάσιμο', vpn_unreachableBody: 'Οι επαναλαμβανόμενοι έλεγχοι δεν έφτασαν στον οικιακό δρομολογητή μέσω VPN. Οι επόμενοι έλεγχοι δεν είναι διαθέσιμοι.',
      dongle_unreachable: 'Η σύνδεση του προσαρμογέα δεν είναι διαθέσιμη', dongle_unreachableBody: 'Το οικιακό VPN είναι προσβάσιμο, αλλά δεν ανιχνεύεται σύνδεση με τον προσαρμογέα.',
      collector_unavailable: 'Η υπηρεσία λήψης δεν είναι διαθέσιμη', collector_unavailableBody: 'Το οικιακό VPN είναι προσβάσιμο. Ο διακομιστής δεν δέχεται τη σύνδεση παρακολούθησης του προσαρμογέα.',
      inverter_stale: 'Αναμονή για νέες μετρήσεις μετατροπέα', inverter_staleBody: 'Το VPN είναι προσβάσιμο και υπάρχει σύνδεση με τον προσαρμογέα, αλλά λείπουν πρόσφατες μετρήσεις.',
      unknown: 'Οι ζωντανές μετρήσεις καθυστερούν', unknownBody: 'Δεν υπάρχουν διαθέσιμες νέες μετρήσεις μετατροπέα. Δεν υπάρχουν αρκετά πρόσφατα διαγνωστικά στοιχεία για να εντοπιστεί η αιτία.',
      partial: 'Ορισμένες μετρήσεις δεν είναι διαθέσιμες', partialBody: 'Ο μετατροπέας στέλνει δεδομένα. Οι μετρήσεις που λείπουν εμφανίζονται ως παύλες, ποτέ ως μηδέν.',
      retry: 'Νέος έλεγχος', retrying: 'Έλεγχος…', automatic: 'Αυτόματη ενημέρωση', details: 'Λεπτομέρειες σύνδεσης',
      website: 'Ιστότοπος', vpn: 'Οικιακό VPN', dongle: 'Προσαρμογέας', inverter: 'Δεδομένα μετατροπέα',
      up: 'Συνδεδεμένο', reachable: 'Προσβάσιμο', fresh: 'Πρόσφατα', down: 'Μη διαθέσιμο', stale: 'Καθυστέρηση',
      unknownState: 'Άγνωστο', checkingState: 'Έλεγχος',
      explanation: 'Οι έλεγχοι αφορούν τη σύνδεση παρακολούθησης, όχι το αν ο μετατροπέας τροφοδοτεί το σπίτι. Η σύνδεση του προσαρμογέα από μόνη της δεν αποδεικνύει ότι λαμβάνονται μετρήσεις.',
      checked: 'Έλεγχος {time}', notChecked: 'Χωρίς πρόσφατο έλεγχο',
      lastReading: 'Τελευταία μέτρηση · {time} · Αθήνα', secondsAgo: 'πριν από {value} δευτ.', minutesAgo: 'πριν από {value} λεπ.', hoursAgo: 'πριν από {value} ώρ.',
      lastKnown: 'Τελευταίες γνωστές μετρήσεις · όχι ζωντανά', none: 'Δεν υπάρχουν αποθηκευμένες μετρήσεις',
      flowHint: 'Επιλέξτε ένα πλαίσιο για τις τελευταίες 12 ώρες.', lastBattery: 'Τελευταία γνωστή μέτρηση μπαταρίας· η τρέχουσα λειτουργία είναι άγνωστη.',
    },
    ru: {
      checking: 'Проверка соединения', checkingBody: 'Ожидание первых показаний из дома.',
      viewer_offline: 'Похоже, вы не в сети', viewer_offlineBody: 'Это устройство потеряло подключение к сети. Соединение восстановится автоматически.',
      service_unavailable: 'Сервис данных сайта недоступен', service_unavailableBody: 'Причина пока неизвестна. Возможно, возникла проблема с сетью или сервером.',
      source_unavailable: 'Сервис показаний недоступен', source_unavailableBody: 'Сайт доступен, но источник мониторинга не передаёт показания.',
      vpn_unreachable: 'Домашний VPN недоступен', vpn_unreachableBody: 'Повторные проверки не смогли связаться с домашним роутером через VPN. Проверки следующих этапов недоступны.',
      dongle_unreachable: 'Нет соединения с адаптером', dongle_unreachableBody: 'Домашний VPN доступен, но соединение с адаптером не обнаружено.',
      collector_unavailable: 'Сервис приёма данных недоступен', collector_unavailableBody: 'Домашний VPN доступен. Сервер не принимает соединение мониторинга от адаптера.',
      inverter_stale: 'Ожидание новых показаний инвертора', inverter_staleBody: 'VPN доступен и соединение с адаптером установлено, но свежие показания не поступают.',
      unknown: 'Задержка текущих показаний', unknownBody: 'Свежие показания инвертора недоступны. Для определения причины недостаточно актуальной диагностики.',
      partial: 'Часть показаний недоступна', partialBody: 'Инвертор передаёт данные. Отсутствующие измерения обозначены прочерками, а не нулями.',
      retry: 'Проверить снова', retrying: 'Проверка…', automatic: 'Обновляется автоматически', details: 'Сведения о соединении',
      website: 'Сайт', vpn: 'Домашний VPN', dongle: 'Адаптер', inverter: 'Данные инвертора',
      up: 'Подключено', reachable: 'Доступно', fresh: 'Актуальны', down: 'Недоступно', stale: 'Задержка',
      unknownState: 'Неизвестно', checkingState: 'Проверка',
      explanation: 'Проверки описывают соединение мониторинга, а не подачу питания инвертором. Само соединение с адаптером не подтверждает получение показаний.',
      checked: 'Проверено {time}', notChecked: 'Нет свежей проверки',
      lastReading: 'Последнее показание · {time} · Афины', secondsAgo: '{value} с назад', minutesAgo: '{value} мин. назад', hoursAgo: '{value} ч назад',
      lastKnown: 'Последние известные показания · не текущие', none: 'Нет сохранённых показаний',
      flowHint: 'Выберите блок, чтобы увидеть последние 12 часов.', lastBattery: 'Последнее известное показание батареи; её текущая активность неизвестна.',
    },
  };
  const states = new Set(['up', 'down', 'checking', 'unknown', 'stale']);
  const isRecent = (timestamp, now, age) => Number.isFinite(timestamp) && timestamp > 0 && now - timestamp >= -30 && now - timestamp <= age;
  const hasReadings = payload => isRecent(payload?.updated_at, payload?.updated_at, 0) &&
    ['grid', 'pv', 'soc', 'battery', 'load'].some(key => Number.isFinite(payload?.values?.[key]));
  function selectReadings(previous, incoming, now) {
    const freshPartial = incoming?.status === 'partial' && isRecent(incoming.updated_at, now, 90) && isRecent(incoming.generated_at, now, 20);
    if ((!hasReadings(incoming) && !freshPartial) || !isRecent(incoming.updated_at, now, Infinity) ||
        (previous && incoming.updated_at < previous.updated_at)) return previous;
    return incoming;
  }
  function assess({ latest, requestFailed = false, online = true, now }) {
    const served = !requestFailed && isRecent(latest?.generated_at, now, 20);
    const fresh = served && isRecent(latest?.updated_at, now, 90) && ['live', 'partial'].includes(latest?.status);
    const checks = { website: { state: requestFailed ? 'down' : latest ? 'up' : 'checking', checked_at: null } };
    for (const key of ['vpn', 'dongle', 'inverter']) {
      const check = latest?.health?.version === 1 ? latest.health.checks?.[key] : null;
      checks[key] = served && check && states.has(check.state) && isRecent(check.checked_at, now, 90)
        ? { state: check.state, checked_at: check.checked_at } : { state: 'unknown', checked_at: null };
    }
    if (served && latest.status !== 'offline') {
      checks.inverter = { state: fresh ? 'up' : 'stale', checked_at: latest.generated_at };
    }
    if (checks.vpn.state === 'down' && checks.dongle.state === 'up') {
      checks.vpn = { state: 'unknown', checked_at: null };
      checks.dongle = { state: 'unknown', checked_at: null };
    }
    let kind;
    if (requestFailed) kind = online === false ? 'viewer_offline' : 'service_unavailable';
    else if (!latest) kind = 'checking';
    else if (fresh) kind = latest.status;
    else if (!served) kind = 'unknown';
    else if (latest.status === 'offline') kind = 'source_unavailable';
    else {
      const collector = latest?.health?.version === 1 ? latest.health.checks?.collector : null;
      if (checks.vpn.state === 'down' && checks.dongle.state !== 'up') kind = 'vpn_unreachable';
      else if (checks.vpn.state === 'up' && collector?.state === 'down' && isRecent(collector.checked_at, now, 90)) kind = 'collector_unavailable';
      else if (checks.vpn.state === 'up' && checks.dongle.state === 'down') kind = 'dongle_unreachable';
      else if (checks.vpn.state === 'up' && checks.dongle.state === 'up') kind = 'inverter_stale';
      else kind = 'unknown';
    }
    if (checks.vpn.state === 'down') {
      checks.dongle = { state: 'unknown', checked_at: null };
      checks.inverter = { state: 'unknown', checked_at: null };
    }
    if (fresh) checks.inverter = { state: 'up', checked_at: latest.updated_at };
    return { kind, checks, fresh, lost: !fresh && kind !== 'checking',
      status: fresh ? latest.status : requestFailed ? 'offline' : 'stale' };
  }
  function translate(language, key, values = {}) {
    return (copy[language]?.[key] ?? copy.en[key] ?? key).replace(/\{(\w+)\}/g, (_, name) => values[name] ?? '');
  }
  function create({ scene, retry }) {
    const panel = document.createElement('section');
    panel.className = 'connection-alert connection-panel';
    panel.hidden = true;
    panel.setAttribute('aria-labelledby', 'connection-title');
    panel.innerHTML = '<div class="connection-main"><svg class="connection-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true"><path d="M8.5 16a5 5 0 0 1 0-8L11 5.5a5 5 0 0 1 7 7L16.5 14M7.5 10 6 11.5a5 5 0 0 0 7 7l2.5-2.5M9 15l6-6" stroke-linecap="round"/></svg><div class="connection-copy" role="status" aria-live="polite"><h2 id="connection-title"></h2><p class="connection-description"></p></div></div><div class="connection-meta"><div class="connection-stamp"><span class="connection-last"></span><span class="connection-auto"></span></div><button type="button" class="connection-retry"></button></div><details class="connection-details"><summary></summary><ol class="connection-steps"></ol><p class="connection-explanation"></p></details>';
    const rows = new Map();
    for (const key of ['website', 'vpn', 'dongle', 'inverter']) {
      const row = document.createElement('li');
      row.innerHTML = '<span class="connection-step-name"></span><strong><i aria-hidden="true"></i><span></span></strong><small></small>';
      panel.querySelector('.connection-steps').append(row);
      rows.set(key, row);
    }
    scene.before(panel);
    const button = panel.querySelector('.connection-retry');
    button.addEventListener('click', retry);
    const text = (selector, value, root = panel) => {
      const element = root.querySelector(selector);
      if (element.textContent !== value) element.textContent = value;
    };
    return {
      update(model, { now, readings, pending }) {
        const language = document.documentElement.lang;
        const t = (key, values) => translate(language, key, values);
        const locale = { en: 'en-GB', el: 'el-GR', ru: 'ru-RU' }[language] || 'en-GB';
        const time = stamp => new Intl.DateTimeFormat(locale, { timeZone: 'Europe/Athens', day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).format(stamp * 1000);
        if (model.kind === 'live' && panel.contains(document.activeElement)) {
          const heading = scene.querySelector('h2'); heading.tabIndex = -1; heading.focus({ preventScroll: true });
        }
        panel.hidden = model.kind === 'live';
        panel.dataset.kind = model.kind;
        text('#connection-title', t(model.kind));
        text('.connection-description', t(model.kind + 'Body'));
        text('.connection-retry', t(pending ? 'retrying' : 'retry'));
        button.disabled = pending;
        text('.connection-auto', t('automatic'));
        text('.connection-details > summary', t('details'));
        text('.connection-explanation', t('explanation'));
        text('.connection-last', hasReadings(readings) ? t('lastReading', { time: time(readings.updated_at) }) : t('none'));
        for (const [key, row] of rows) {
          const check = model.checks[key];
          const stateKey = check.state === 'up' ? key === 'inverter' ? 'fresh' : key === 'vpn' ? 'reachable' : 'up'
            : { unknown: 'unknownState', checking: 'checkingState' }[check.state] || check.state;
          row.dataset.state = check.state;
          text('.connection-step-name', t(key), row);
          text('strong > span', t(stateKey), row);
          text('small', check.checked_at ? t('checked', { time: time(check.checked_at) }) : key === 'website' ? t('automatic') : t('notChecked'), row);
        }
        scene.querySelector('.scene-caption').textContent = t(model.lost ? hasReadings(readings) ? 'lastKnown' : 'none' : 'flowHint');
      },
    };
  }
  window.energyConnectionStatus = { assess, hasReadings, selectReadings, isRecent, translate, create, languages: copy };
})();
