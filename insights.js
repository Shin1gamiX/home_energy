(() => {
  'use strict';

  const MAX_BYTES = 2000000;
  const LOCALES = ['en', 'el', 'ru'];
  const SEVERITIES = ['routine', 'notice', 'warning', 'data_issue'];
  const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
  const text = (value, max) => typeof value === 'string' && value.length <= max;
  const number = value => typeof value === 'number' && Number.isFinite(value);
  const optionalNumber = value => value === null || number(value);
  const stamp = value => Number.isSafeInteger(value) && value >= 0 && value <= 4102444800;
  const percentage = value => number(value) && value >= 0 && value <= 100;
  const items = (value, max, check) => Array.isArray(value) && value.length <= max && value.every(check);
  const interval = value => object(value) && stamp(value.start) && stamp(value.end) && value.end > value.start;

  function localizedText(value, max) {
    return object(value) && Object.keys(value).length === LOCALES.length && LOCALES.every(language =>
      Object.hasOwn(value, language) && text(value[language], max) && value[language].trim().length > 0);
  }

  function validReport(report) {
    if (!object(report) || typeof report.id !== 'string' || !/^[a-f0-9]{64}$/.test(report.id) || !stamp(report.published_at)) return false;
    const a = report.analysis, n = report.narrative;
    if (!object(a) || a.schema_version !== 2 || !stamp(a.generated_at) || !interval(a.window) ||
        !interval(a.baseline) || !Number.isInteger(a.baseline.days) || a.baseline.days < 1 || a.baseline.days > 90 || a.baseline.end > a.window.start ||
        !(a.data_through === null || stamp(a.data_through)) || !percentage(a.coverage_pct) ||
        !SEVERITIES.includes(a.severity)) return false;
    const windows = a.baseline.windows ?? a.baseline.days;
    if (!Number.isInteger(windows) || windows < 0 || windows > 90) return false;
    if (!items(a.metrics, 32, m => object(m) && text(m.key, 80) && localizedText(m.label, 160) && text(m.unit, 24) &&
        optionalNumber(m.value) && optionalNumber(m.baseline) && optionalNumber(m.change_pct) &&
        percentage(m.coverage_pct) && Number.isInteger(m.baseline_days) && m.baseline_days >= 0 && m.baseline_days <= windows)) return false;
    if (!items(a.findings, 32, f => object(f) && text(f.id, 160) && text(f.category, 80) &&
        SEVERITIES.includes(f.severity) && localizedText(f.title, 160) && localizedText(f.observation, 2000) &&
        stamp(f.start) && stamp(f.end) && f.end >= f.start && (f.metric === null || text(f.metric, 80)))) return false;
    const findingIds = new Set(a.findings.map(f => f.id));
    if (findingIds.size !== a.findings.length || !items(a.limitations, 20, value => localizedText(value, 600))) return false;
    return object(n) && localizedText(n.summary, 1200) && items(n.notes, 32, note => object(note) &&
      findingIds.has(note.finding_id) && localizedText(note.commentary, 2000));
  }

  function validateFeed(feed) {
    if (!object(feed) || feed.schema_version !== 2 || !items(feed.reports, 100, validReport)) {
      throw new Error('Invalid report feed');
    }
    if (new Set(feed.reports.map(report => report.id)).size !== feed.reports.length) throw new Error('Duplicate report');
    return [...feed.reports].sort((a, b) => b.published_at - a.published_at || a.id.localeCompare(b.id));
  }

  function localizeReport(report, language) {
    if (!LOCALES.includes(language) || !validReport(report)) throw new Error('Incomplete report translation');
    // Translate presentation fields only. Evidence values and times remain identical.
    const a = report.analysis, n = report.narrative;
    return { ...report, analysis: { ...a,
      metrics: a.metrics.map(metric => ({ ...metric, label: metric.label[language] })),
      findings: a.findings.map(finding => ({ ...finding, title: finding.title[language], observation: finding.observation[language] })),
      limitations: a.limitations.map(limitation => limitation[language])
    }, narrative: { summary: n.summary[language],
      notes: n.notes.map(note => ({ ...note, commentary: note.commentary[language] })) } };
  }

  async function readBoundedJson(response) {
    const size = Number(response.headers.get('Content-Length'));
    if (Number.isFinite(size) && size > MAX_BYTES) throw new Error('Report feed too large');
    if (!response.body) throw new Error('Empty report response');
    const reader = response.body.getReader(), decoder = new TextDecoder('utf-8', { fatal: true });
    let total = 0, result = '';
    try {
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        total += value.byteLength;
        if (total > MAX_BYTES) throw new Error('Report feed too large');
        result += decoder.decode(value, { stream: true });
      }
      result += decoder.decode();
      return JSON.parse(result);
    } catch (error) {
      await reader.cancel().catch(() => {});
      throw error;
    } finally {
      reader.releaseLock();
    }
  }

  function dateTime(seconds) {
    const parts = new Intl.DateTimeFormat('en-GB', { timeZone: 'Europe/Athens', day: '2-digit', month: '2-digit',
      year: 'numeric', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).formatToParts(new Date(seconds * 1000));
    const values = Object.fromEntries(parts.map(part => [part.type, part.value]));
    return `${values.day}/${values.month}/${values.year} · ${values.hour}:${values.minute}`;
  }

  // Report content supplies evidence, never a URL or an executable destination.
  function findingHistoryLink(finding) {
    if (!stamp(finding.start) || !stamp(finding.end) || finding.end <= finding.start || finding.end - finding.start > 31 * 86400) return null;
    const mapping = { pv: 'pv', solar_generated: 'pv', grid: 'grid', load: 'load', house_usage: 'load',
      battery: 'battery', battery_supplied: 'battery', battery_charged: 'battery', soc_max: 'soc', battery_max_soc: 'soc' };
    const metric = finding.metric === null ? 'mode' : Object.hasOwn(mapping, finding.metric) ? mapping[finding.metric] : null;
    if (!metric) return null;
    return `history.html?from=${finding.start}&to=${finding.end}&metric=${metric}#history-context`;
  }

  if (typeof module !== 'undefined' && module.exports) module.exports = { validReport, validateFeed, localizedText, localizeReport, readBoundedJson, dateTime, findingHistoryLink, MAX_BYTES };
  if (typeof document === 'undefined' || !document.getElementById('energy-insights')) return;

  const translations = {
    el: {
      'View this period in History': 'Προβολή περιόδου στο ιστορικό', 'Review summary': 'Σύνοψη αναφοράς',
      'Recorded energy review': 'Ανασκόπηση καταγεγραμμένης ενέργειας', 'findings': 'ευρήματα',
      'THE BIGGER PICTURE': 'Η ΣΥΝΟΛΙΚΗ ΕΙΚΟΝΑ', 'Energy insights': 'Ενεργειακές αναφορές',
      'On-demand reviews of recorded energy and recent patterns.': 'Ανασκοπήσεις κατόπιν αιτήματος της καταγεγραμμένης ενέργειας και πρόσφατων μοτίβων.',
      'Explore history ↗': 'Εξερεύνηση ιστορικού ↗', 'Loading reports…': 'Φόρτωση αναφορών…',
      'No reports published yet. Completed reviews will appear here.': 'Δεν έχουν δημοσιευτεί αναφορές ακόμη. Οι ολοκληρωμένες ανασκοπήσεις θα εμφανίζονται εδώ.',
      'Reports could not be refreshed. Any reports below are from the previous successful load.': 'Η ανανέωση απέτυχε. Οι αναφορές παρακάτω προέρχονται από την προηγούμενη επιτυχημένη φόρτωση.',
      'Reports are unavailable right now.': 'Οι αναφορές δεν είναι διαθέσιμες αυτή τη στιγμή.',
      'Synthetic preview reports — not real observations.': 'Δοκιμαστικές αναφορές — όχι πραγματικές παρατηρήσεις.',
      'Latest review is over 24 hours old; it does not describe current conditions.': 'Η τελευταία ανασκόπηση είναι παλαιότερη των 24 ωρών· δεν περιγράφει την τρέχουσα κατάσταση.',
      'Saved reviews, not live alerts.': 'Αποθηκευμένες ανασκοπήσεις, όχι ζωντανές ειδοποιήσεις.',
      'Routine': 'Τακτική αναφορά', 'Unusual activity': 'Ασυνήθιστη δραστηριότητα', 'Needs attention': 'Χρειάζεται προσοχή', 'Data issue': 'Πρόβλημα δεδομένων',
      'Latest': 'Τελευταία', 'Published': 'Δημοσιεύτηκε', 'Reviewed': 'Περίοδος', 'recorded': 'καταγεγραμμένο',
      'Athens time': 'Ώρα Αθήνας', 'Comparison period': 'Περίοδος σύγκρισης', 'Recorded figures': 'Καταγεγραμμένα στοιχεία',
      'Baseline': 'Βάση σύγκρισης', 'days compared': 'ημέρες σύγκρισης', 'No baseline': 'Χωρίς βάση σύγκρισης',
      'windows compared': 'περίοδοι σύγκρισης',
      'On-demand AI review · observations are not a diagnosis.': 'Ανασκόπηση AI κατόπιν αιτήματος · οι παρατηρήσεις δεν αποτελούν διάγνωση.',
      'Findings': 'Ευρήματα', 'Observed': 'Παρατηρήθηκε', 'AI interpretation': 'Ερμηνεία AI', 'Limitations': 'Περιορισμοί',
      'Latest included reading': 'Τελευταία συμπεριλαμβανόμενη μέτρηση', 'No readings included': 'Δεν περιλαμβάνονται μετρήσεις',
      'Show older reports': 'Παλαιότερες αναφορές', 'Show latest only': 'Μόνο οι νεότερες', 'Try again': 'Δοκιμή ξανά',
      'Automated review · observations are not a diagnosis.': 'Αυτόματη ανασκόπηση · οι παρατηρήσεις δεν αποτελούν διάγνωση.'
    },
    ru: {
      'View this period in History': 'Открыть этот период в истории', 'Review summary': 'Сводка отчёта',
      'Recorded energy review': 'Обзор записанной энергии', 'findings': 'наблюдений',
      'THE BIGGER PICTURE': 'ОБЩАЯ КАРТИНА', 'Energy insights': 'Энергетические отчёты',
      'On-demand reviews of recorded energy and recent patterns.': 'Обзоры записанной энергии и недавних закономерностей по запросу.',
      'Explore history ↗': 'Открыть историю ↗', 'Loading reports…': 'Загрузка отчётов…',
      'No reports published yet. Completed reviews will appear here.': 'Отчётов пока нет. Завершённые обзоры появятся здесь.',
      'Reports could not be refreshed. Any reports below are from the previous successful load.': 'Обновить отчёты не удалось. Ниже показаны ранее загруженные отчёты.',
      'Reports are unavailable right now.': 'Отчёты сейчас недоступны.',
      'Synthetic preview reports — not real observations.': 'Демонстрационные отчёты — не реальные наблюдения.',
      'Latest review is over 24 hours old; it does not describe current conditions.': 'Последнему обзору более 24 часов; он не описывает текущую ситуацию.',
      'Saved reviews, not live alerts.': 'Сохранённые обзоры, не оповещения в реальном времени.',
      'Routine': 'Плановый обзор', 'Unusual activity': 'Необычная активность', 'Needs attention': 'Требует внимания', 'Data issue': 'Проблема данных',
      'Latest': 'Последний', 'Published': 'Опубликовано', 'Reviewed': 'Период', 'recorded': 'записано',
      'Athens time': 'Афинское время', 'Comparison period': 'Период сравнения', 'Recorded figures': 'Записанные показатели',
      'Baseline': 'База сравнения', 'days compared': 'дней сравнения', 'No baseline': 'Нет базы сравнения',
      'windows compared': 'периодов сравнения',
      'On-demand AI review · observations are not a diagnosis.': 'Обзор ИИ по запросу · наблюдения не являются диагнозом.',
      'Findings': 'Наблюдения', 'Observed': 'Наблюдается', 'AI interpretation': 'Интерпретация ИИ', 'Limitations': 'Ограничения',
      'Latest included reading': 'Последнее учтённое показание', 'No readings included': 'Показания отсутствуют',
      'Show older reports': 'Предыдущие отчёты', 'Show latest only': 'Только последние', 'Try again': 'Повторить',
      'Automated review · observations are not a diagnosis.': 'Автоматический обзор · наблюдения не являются диагнозом.'
    }
  };
  const locale = () => document.documentElement.lang || 'en';
  const t = value => translations[locale()]?.[value] || value;
  const format = value => value === null ? '—' : new Intl.NumberFormat(locale(), { maximumFractionDigits: 1 }).format(value);
  const severityName = value => t(({ routine: 'Routine', notice: 'Unusual activity', warning: 'Needs attention', data_issue: 'Data issue' })[value]);
  const preview = location.hostname === '127.0.0.1' && location.port === '8766';
  const list = document.getElementById('insights-reports');
  const status = document.getElementById('insights-state');
  const older = document.getElementById('insights-older');
  const retry = document.getElementById('insights-retry');
  let reports = [], expanded = false, loading = true, failed = false, activeRequest = null;

  function element(tag, className, content) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (content !== undefined) node.textContent = content;
    return node;
  }

  function timeElement(seconds, prefix = '') {
    const node = element('time', '', prefix + dateTime(seconds));
    node.dateTime = new Date(seconds * 1000).toISOString();
    return node;
  }

  function renderReport(report, newest, openIds) {
    report = localizeReport(report, locale());
    const a = report.analysis, details = element('details', 'insights-report');
    details.lang = locale();
    details.dataset.severity = a.severity;
    details.dataset.reportId = report.id;
    details.open = openIds.has(report.id);
    const summary = element('summary');
    const meta = element('div', 'insights-report-meta');
    meta.append(element('span', 'insights-badge', severityName(a.severity)));
    if (newest) meta.append(element('span', 'insights-newest', t('Latest')));
    meta.append(timeElement(report.published_at, t('Published') + ' '));
    const lead = a.findings.find(finding => finding.severity === a.severity) || a.findings[0];
    const description = element('p', 'insights-summary', lead?.title || t('Recorded energy review'));
    if (a.findings.length > 1) meta.append(element('span', 'insights-count', `${a.findings.length} ${t('findings')}`));
    const chevron = element('span', 'insights-chevron'); chevron.setAttribute('aria-hidden', 'true');
    summary.append(meta, description,
      element('p', 'insights-window', `${t('Reviewed')} ${dateTime(a.window.start)} – ${dateTime(a.window.end)} · ${t('Athens time')} · ${format(a.coverage_pct)}% ${t('recorded')}`), chevron);
    const body = element('div', 'insights-body');
    body.append(element('h3', '', t('Review summary')), element('p', 'insights-narrative', report.narrative.summary));
    body.append(element('p', 'insights-baseline', `${t('Comparison period')}: ${dateTime(a.baseline.start)} – ${dateTime(a.baseline.end)}`));
    if (a.metrics.length) {
      body.append(element('h3', '', t('Recorded figures')));
      const metrics = element('div', 'insights-metrics');
      for (const m of a.metrics) {
        const card = element('div', 'insights-metric');
        const label = element('span', '', m.label);
        const change = m.change_pct === null ? '' : ` · ${m.change_pct > 0 ? '+' : ''}${format(m.change_pct)}%`;
        card.append(label, element('strong', '', `${format(m.value)}${m.value === null ? '' : ' ' + m.unit}`),
          element('small', '', m.baseline === null ? t('No baseline') : `${t('Baseline')}: ${format(m.baseline)} ${m.unit}${change}`),
          element('small', '', `${format(m.coverage_pct)}% ${t('recorded')} · ${m.baseline_days}/${a.baseline.windows ?? a.baseline.days} ${t(a.baseline.windows === undefined ? 'days compared' : 'windows compared')}`));
        metrics.append(card);
      }
      body.append(metrics);
    }
    if (a.findings.length) {
      body.append(element('h3', '', t('Findings')));
      const findings = element('div', 'insights-findings');
      for (const finding of a.findings) {
        const item = element('article', 'insights-finding');
        const title = element('h4', '', finding.title);
        item.append(title, element('p', 'insights-window', `${dateTime(finding.start)} – ${dateTime(finding.end)}`), element('p', '', `${t('Observed')}: ${finding.observation}`));
        const note = report.narrative.notes.find(note => note.finding_id === finding.id);
        if (note) {
          const commentary = element('p', 'insights-commentary', `${t('AI interpretation')}: ${note.commentary}`);
          item.append(commentary);
        }
        const href = findingHistoryLink(finding);
        if (href) {
          const link = element('a', 'insights-evidence-link', t('View this period in History'));
          link.href = href;
          item.append(link);
        }
        findings.append(item);
      }
      body.append(findings);
    }
    if (a.limitations.length) {
      body.append(element('h3', '', t('Limitations')));
      const limits = element('ul', 'insights-limitations');
      for (const limitation of a.limitations) limits.append(element('li', '', limitation));
      body.append(limits);
    }
    body.append(element('p', 'insights-provenance', `${t(a.baseline.windows === undefined ? 'Automated review · observations are not a diagnosis.' : 'On-demand AI review · observations are not a diagnosis.')} ${a.data_through === null ? t('No readings included') : t('Latest included reading') + ': ' + dateTime(a.data_through)}.`));
    details.append(summary, body);
    return details;
  }

  function render() {
    for (const [id, label] of Object.entries({ 'insights-eyebrow': 'THE BIGGER PICTURE', 'insights-title': 'Energy insights',
      'insights-description': 'On-demand reviews of recorded energy and recent patterns.', 'insights-history-link': 'Explore history ↗' })) {
      document.getElementById(id).textContent = t(label);
    }
    const focusedReport = list.contains(document.activeElement) ? document.activeElement.closest('details')?.dataset.reportId : null;
    const openIds = new Set([...list.querySelectorAll('details[open]')].map(node => node.dataset.reportId));
    list.replaceChildren(...reports.slice(0, expanded ? 100 : 3).map((report, index) => renderReport(report, index === 0, openIds)));
    if (focusedReport) list.querySelector(`[data-report-id="${focusedReport}"] > summary`)?.focus({ preventScroll: true });
    older.hidden = reports.length <= 3;
    older.textContent = t(expanded ? 'Show latest only' : 'Show older reports') + (!expanded && reports.length > 3 ? ` (${reports.length - 3})` : '');
    older.setAttribute('aria-expanded', String(expanded));
    older.setAttribute('aria-controls', 'insights-reports');
    retry.hidden = !failed; retry.disabled = loading; retry.textContent = t('Try again');
    document.getElementById('insights-language').textContent = '';
    const stale = reports.length && Date.now() / 1000 - reports[0].analysis.window.end > 86400;
    status.dataset.state = failed ? 'error' : preview ? 'preview' : stale ? 'stale' : 'ready';
    status.textContent = t(failed ? reports.length ? 'Reports could not be refreshed. Any reports below are from the previous successful load.' : 'Reports are unavailable right now.'
      : loading && !reports.length ? 'Loading reports…' : !reports.length ? 'No reports published yet. Completed reviews will appear here.'
      : preview ? 'Synthetic preview reports — not real observations.' : stale ? 'Latest review is over 24 hours old; it does not describe current conditions.' : 'Saved reviews, not live alerts.');
  }

  async function load() {
    if (activeRequest) return;
    const controller = new AbortController(); activeRequest = controller;
    const timer = setTimeout(() => controller.abort(), 10000);
    loading = true; render();
    try {
      const response = await fetch('/reports/index.json', { cache: 'no-store', signal: controller.signal, credentials: 'omit' });
      if (!response.ok) throw new Error('Reports unavailable');
      reports = validateFeed(await readBoundedJson(response));
      failed = false;
    } catch (_) {
      // A broken feed must never be presented as a clean bill of health.
      failed = true;
    } finally {
      clearTimeout(timer); activeRequest = null; loading = false; render();
    }
  }

  older.addEventListener('click', () => { expanded = !expanded; render(); });
  retry.addEventListener('click', load);
  window.addEventListener('languagechange', render);
  // A long-lived dashboard sees published reports without putting AI in the page load path.
  setInterval(() => { if (!document.hidden) load(); }, 300000);
  load();
})();
