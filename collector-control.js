/* Only this module handles the password. No cookies, storage, URL parameters or logs. */
(() => {
  'use strict';
  const trigger = document.querySelector('#restart-dongle');
  const dialog = document.querySelector('#collector-dialog');
  if (!trigger || !dialog) return;
  const form = document.querySelector('#collector-form');
  const password = document.querySelector('#collector-password');
  const submit = document.querySelector('#collector-submit');
  const feedback = document.querySelector('#collector-feedback');
  const messages = {
    en: {
      title: 'Restart dongle', description: 'Restart the monitoring dongle only. Readings may briefly stop; the inverter will not be restarted.',
      password: 'Restart password', rules: 'Two wrong passwords lock your network out for 5 minutes. Every restart has a 5-minute cooldown shared by everyone.',
      cancel: 'Cancel', close: 'Close', confirm: 'Confirm restart', ready: 'Enter your password to confirm the restart.',
      unavailable: 'Restart is unavailable or has not been configured. No command was sent.', invalid_password: 'Incorrect password. One more wrong attempt will lock your network out for 5 minutes.',
      locked: 'Too many wrong passwords. Try again in {time}.', cooldown: 'A restart was recently requested. Everyone must wait {time}.',
      busy: 'Restart requests are temporarily limited. Try again in {time}.', forbidden: 'This request was blocked. Reload the dashboard on its secure address.',
      sending: 'Sending restart request…', requested: 'Restart requested. Waiting for fresh readings…', unknown: 'No confirmation received. The command may have reached the dongle. Checking readings; do not retry yet.',
      restart_failed: 'Home Assistant could not confirm the restart. The cooldown remains active to prevent repeated commands.',
      recovered: 'Fresh inverter readings received. Monitoring is updating; this alone does not confirm a reboot.',
      waiting: 'Fresh readings have not returned yet. The dongle may need further checks. The cooldown still applies.',
      network: 'The response was lost; the restart may have been sent. Please wait {time} before trying again.'
    },
    ru: {
      title: 'Перезапустить адаптер', description: 'Перезапуск только адаптера мониторинга. Данные могут ненадолго пропасть; инвертор не будет перезапущен.',
      password: 'Пароль перезапуска', rules: 'Два неверных пароля блокируют вашу сеть на 5 минут. После каждого запроса перезапуска все должны ждать 5 минут.',
      cancel: 'Отмена', close: 'Закрыть', confirm: 'Подтвердить перезапуск', ready: 'Введите пароль для подтверждения перезапуска.',
      unavailable: 'Перезапуск недоступен или ещё не настроен. Команда не отправлена.', invalid_password: 'Неверный пароль. Ещё одна ошибка заблокирует вашу сеть на 5 минут.',
      locked: 'Слишком много неверных паролей. Повторите через {time}.', cooldown: 'Недавно запрошен перезапуск. Всем нужно подождать {time}.',
      busy: 'Запросы временно ограничены. Повторите через {time}.', forbidden: 'Запрос заблокирован. Откройте панель заново по защищённому адресу.',
      sending: 'Отправка запроса…', requested: 'Перезапуск запрошен. Ожидаем свежие данные…', unknown: 'Подтверждение не получено. Команда могла дойти до адаптера. Проверяем данные; пока не повторяйте запрос.',
      restart_failed: 'Home Assistant не смог подтвердить перезапуск. Пауза сохраняется, чтобы избежать повторных команд.',
      recovered: 'Получены свежие данные инвертора. Мониторинг обновляется; это само по себе не подтверждает перезагрузку.',
      waiting: 'Свежие данные пока не поступили. Может потребоваться дополнительная проверка адаптера. Пауза остаётся в силе.',
      network: 'Ответ потерян; команда могла быть отправлена. Подождите {time} перед повторной попыткой.'
    },
    el: {
      title: 'Επανεκκίνηση αντάπτορα', description: 'Επανεκκίνηση μόνο του αντάπτορα παρακολούθησης. Οι ενδείξεις μπορεί να διακοπούν προσωρινά· ο μετατροπέας δεν θα επανεκκινηθεί.',
      password: 'Κωδικός επανεκκίνησης', rules: 'Δύο λανθασμένοι κωδικοί αποκλείουν το δίκτυό σας για 5 λεπτά. Μετά από κάθε αίτημα επανεκκίνησης ισχύει αναμονή 5 λεπτών για όλους.',
      cancel: 'Ακύρωση', close: 'Κλείσιμο', confirm: 'Επιβεβαίωση επανεκκίνησης', ready: 'Εισαγάγετε τον κωδικό σας για επιβεβαίωση.',
      unavailable: 'Η επανεκκίνηση δεν είναι διαθέσιμη ή δεν έχει ρυθμιστεί. Δεν στάλθηκε εντολή.', invalid_password: 'Λανθασμένος κωδικός. Μία ακόμη αποτυχημένη προσπάθεια θα αποκλείσει το δίκτυό σας για 5 λεπτά.',
      locked: 'Πολλοί λανθασμένοι κωδικοί. Δοκιμάστε ξανά σε {time}.', cooldown: 'Ζητήθηκε πρόσφατα επανεκκίνηση. Όλοι πρέπει να περιμένουν {time}.',
      busy: 'Τα αιτήματα περιορίζονται προσωρινά. Δοκιμάστε ξανά σε {time}.', forbidden: 'Το αίτημα αποκλείστηκε. Ανοίξτε ξανά τον πίνακα στην ασφαλή διεύθυνσή του.',
      sending: 'Αποστολή αιτήματος…', requested: 'Ζητήθηκε επανεκκίνηση. Αναμονή για νέες ενδείξεις…', unknown: 'Δεν ελήφθη επιβεβαίωση. Η εντολή μπορεί να έφτασε στον αντάπτορα. Ελέγχουμε τις ενδείξεις· μην επαναλάβετε ακόμη.',
      restart_failed: 'Το Home Assistant δεν επιβεβαίωσε την επανεκκίνηση. Η αναμονή παραμένει για αποφυγή επαναλαμβανόμενων εντολών.',
      recovered: 'Ελήφθησαν νέες ενδείξεις μετατροπέα. Η παρακολούθηση ενημερώνεται· αυτό από μόνο του δεν επιβεβαιώνει επανεκκίνηση.',
      waiting: 'Δεν έχουν φτάσει ακόμη νέες ενδείξεις. Ίσως χρειάζεται επιπλέον έλεγχος του αντάπτορα. Η αναμονή εξακολουθεί να ισχύει.',
      network: 'Η απάντηση χάθηκε· η εντολή μπορεί να στάλθηκε. Περιμένετε {time} πριν προσπαθήσετε ξανά.'
    }
  };
  let pending = false;
  let blockedUntil = 0;
  let blockKey = 'cooldown';
  let statusKey = 'ready';
  let isError = false;
  let recovery = null;
  let timer = null;
  const text = key => messages[document.documentElement.lang]?.[key] || messages.en[key];
  const setText = (node, value) => { if (node.textContent !== value) node.textContent = value; };
  const remaining = () => Math.max(0, Math.ceil((blockedUntil - Date.now()) / 1000));
  const duration = seconds => {
    const lang = document.documentElement.lang;
    const [m, s] = lang === 'ru' ? ['мин', 'с'] : lang === 'el' ? ['λ', 'δ'] : ['m', 's'];
    return `${Math.floor(seconds / 60)}${m} ${seconds % 60}${s}`;
  };
  function render() {
    setText(trigger, text('title'));
    dialog.querySelectorAll('[data-control-text]').forEach(node => { setText(node, text(node.dataset.controlText)); });
    document.querySelector('#collector-close').setAttribute('aria-label', text('close'));
    submit.disabled = pending || remaining() > 0 || Boolean(recovery);
    password.disabled = submit.disabled;
    if (['locked', 'cooldown', 'busy', 'network'].includes(statusKey) && remaining() === 0 && !pending) {
      statusKey = 'ready'; isError = false;
    }
    setText(feedback, text(statusKey).replace('{time}', duration(remaining())));
    feedback.dataset.error = String(isError);
  }
  function show(key, error = false) { statusKey = key; isError = error; render(); }
  async function checkRecovery() {
    const check = recovery;
    if (!check || check.pending || !dialog.open) return;
    if (Date.now() - check.started > 180000) { recovery = null; show('waiting', true); return; }
    check.pending = true;
    try {
      const response = await fetch('/api/energy', { cache: 'no-store', credentials: 'omit', signal: AbortSignal.timeout(8000) });
      const data = await response.json();
      if (recovery !== check) return;
      const now = Date.now() / 1000;
      if (response.ok && ['live', 'partial'].includes(data.status) && Number.isFinite(data.updated_at) &&
          data.updated_at > check.baseline && data.updated_at > check.started / 1000 && data.updated_at <= now + 5 &&
          now - data.updated_at < 90 && Number.isFinite(data.generated_at) && data.generated_at <= now + 5 && now - data.generated_at < 20) {
        recovery = null; show('recovered');
      }
    } catch { /* Ordinary while the collector is reconnecting; never auto-retry a restart. */ }
    finally { check.pending = false; }
  }
  trigger.addEventListener('click', () => {
    password.value = '';
    dialog.showModal();
    show(pending ? 'sending' : remaining() > 0 ? blockKey : 'ready');
    if (!password.disabled) password.focus();
    let ticks = 0;
    timer = setInterval(() => {
      render();
      if (++ticks % 5 === 0) checkRecovery();
    }, 1000);
  });
  function close() { dialog.close(); }
  document.querySelector('#collector-close').addEventListener('click', close);
  document.querySelector('#collector-cancel').addEventListener('click', close);
  dialog.addEventListener('close', () => {
    password.value = '';
    clearInterval(timer);
    recovery = null;
    trigger.focus();
  });
  window.addEventListener('pagehide', () => { password.value = ''; });
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (submit.disabled || !password.value) return;
    pending = true;
    const started = Date.now();
    show('sending');
    try {
      const request = fetch('/api/collector/restart', {
        method: 'POST', credentials: 'omit', cache: 'no-store',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ password: password.value }), signal: AbortSignal.timeout(45000)
      });
      password.value = '';
      const response = await request;
      const data = await response.json();
      // Keep the known deadline even if the dialog was closed while awaiting a
      // response. The server is authoritative across reloads, browsers and IPs.
      const wait = Math.max(Number(data.cooldown_seconds) || 0, Number(data.retry_after_seconds) || 0);
      blockedUntil = Math.max(blockedUntil, Date.now() + wait * 1000);
      blockKey = ['locked', 'busy'].includes(data.error) ? data.error : 'cooldown';
      if (response.ok && ['requested', 'unknown'].includes(data.state)) {
        if (dialog.open) recovery = { started, baseline: Number(data.baseline_updated_at) || 0, pending: false };
        show(data.state);
      } else {
        const key = ['invalid_password', 'locked', 'cooldown', 'busy', 'forbidden', 'restart_failed', 'unavailable'].includes(data.error) ? data.error : 'unavailable';
        show(key, true);
      }
    } catch {
      // No public status lookup or automatic retry. A lost response may still
      // represent an accepted action, so keep a conservative local wait.
      blockedUntil = Math.max(blockedUntil, Date.now() + 300000);
      blockKey = 'network';
      if (dialog.open) recovery = { started, baseline: 0, pending: false };
      show('network', true);
    } finally {
      password.value = '';
      pending = false;
      render(); if (dialog.open && !password.disabled) password.focus();
    }
  });
  window.addEventListener('languagechange', render);
  render();
})();
