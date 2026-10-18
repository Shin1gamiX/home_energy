// Served only by the loopback preview helper with ?compare=1. No saved preferences.
(() => {
  if (location.hostname !== '127.0.0.1' || location.port !== '8766') return;
  const text = {
    en: ['History starting view · local comparison', 'A · Current selection', 'B · Focused selection',
      'Synthetic data. Compare the same day and graphs; no production defaults are changed.',
      'A shows the existing 15 statistics. B starts with house, solar, grid, battery power, battery charge and supply mode.'],
    el: ['Αρχική προβολή ιστορικού · τοπική σύγκριση', 'A · Τρέχουσα επιλογή', 'B · Εστιασμένη επιλογή',
      'Υποθετικά δεδομένα. Συγκρίνετε την ίδια ημέρα και γραφήματα· οι προεπιλογές παραγωγής δεν αλλάζουν.',
      'Η A δείχνει τις υπάρχουσες 15 μετρήσεις. Η B ξεκινά με σπίτι, ηλιακή ισχύ, δίκτυο, ισχύ και φόρτιση μπαταρίας και πηγή τροφοδοσίας.'],
    ru: ['Начальный вид истории · локальное сравнение', 'A · Текущий выбор', 'B · Основные показатели',
      'Искусственные данные. Сравните один день и графики; настройки рабочего сайта не изменяются.',
      'A показывает текущие 15 показателей. B начинает с дома, солнца, сети, мощности и заряда батареи и источника питания.']
  };
  const panel = document.createElement('section'); panel.className = 'preview-comparison';
  const title = document.createElement('h2'), notice = document.createElement('p'), description = document.createElement('p');
  const controls = document.createElement('div'); controls.className = 'preview-options';
  let choice = 'focused';
  const buttons = ['current', 'focused'].map(value => {
    const button = document.createElement('button'); button.type = 'button';
    button.addEventListener('click', () => {
      choice = value;
      selected.clear();
      for (const key of value === 'focused' ? ['load', 'pv', 'grid', 'battery', 'soc']
        : Object.keys(metrics).filter(key => !['pv_voltage', 'pv_current'].includes(key))) selected.add(key);
      showMode = true; updateFilters(); labels();
    });
    controls.append(button); return button;
  });
  function labels() {
    const strings = text[document.documentElement.lang] || text.en;
    title.textContent = strings[0]; notice.textContent = strings[3]; description.textContent = strings[4];
    buttons.forEach((button, i) => { button.textContent = strings[i + 1]; button.setAttribute('aria-pressed', String((i === 0) === (choice === 'current'))); });
  }
  panel.append(title, notice, controls, description);
  document.querySelector('.history-navigation').before(panel);
  window.addEventListener('languagechange', labels);
  labels();
})();
