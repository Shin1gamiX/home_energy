"""Complete, deterministic English/Greek/Russian report text.

Every public prose field contains all three translations. Missing translations
fail closed instead of silently publishing an English fallback. Only numeric
evidence is interpolated; no generated text or external translation is used.
"""

from string import Formatter


LANGUAGES = ('en', 'el', 'ru')

TEXT = {
    'metric.pv': {
        'en': 'Solar generated',
        'el': 'Ηλιακή παραγωγή',
        'ru': 'Солнечная выработка',
    },
    'metric.grid': {
        'en': 'Grid consumed (estimated)',
        'el': 'Ενέργεια από το δίκτυο (εκτίμηση)',
        'ru': 'Потребление из сети (оценка)',
    },
    'metric.load': {
        'en': 'House usage',
        'el': 'Κατανάλωση σπιτιού',
        'ru': 'Потребление дома',
    },
    'metric.battery_supplied': {
        'en': 'Battery supplied',
        'el': 'Ενέργεια από την μπαταρία',
        'ru': 'Энергия от аккумулятора',
    },
    'metric.battery_charged': {
        'en': 'Battery charged',
        'el': 'Ενέργεια φόρτισης μπαταρίας',
        'ru': 'Энергия зарядки аккумулятора',
    },
    'metric.soc_max': {
        'en': 'Highest recorded battery charge',
        'el': 'Μέγιστη καταγεγραμμένη φόρτιση μπαταρίας',
        'ru': 'Максимальный зарегистрированный заряд аккумулятора',
    },
    'title.incomplete_coverage': {
        'en': 'Incomplete recorded history',
        'el': 'Ελλιπής καταγραφή ιστορικού',
        'ru': 'Неполная история показаний',
    },
    'observation.incomplete_coverage': {
        'en': 'The least-covered required field was recorded for {coverage:.1f}% of the review window. '
              'Totals exclude missing intervals; comparisons for fields below 90% coverage are withheld.',
        'el': 'Το απαιτούμενο πεδίο με τη μικρότερη κάλυψη καταγράφηκε για το {coverage:.1f}% της περιόδου ελέγχου. '
              'Τα σύνολα δεν περιλαμβάνουν τα διαστήματα χωρίς δεδομένα. Δεν γίνονται συγκρίσεις για πεδία με κάλυψη κάτω από 90%.',
        'ru': 'Обязательный показатель с наименьшим охватом был записан за {coverage:.1f}% периода анализа. '
              'Итоги не включают интервалы без данных; сравнения для показателей с охватом ниже 90% не выполняются.',
    },
    'title.stale_history': {
        'en': 'Recent readings are missing',
        'el': 'Λείπουν πρόσφατες μετρήσεις',
        'ru': 'Отсутствуют свежие показания',
    },
    'observation.stale_history': {
        'en': 'No valid required readings were recorded in the final {minutes:.0f} minutes of the review window. '
              'This does not establish a power outage or device fault.',
        'el': 'Δεν καταγράφηκαν έγκυρες μετρήσεις των απαιτούμενων πεδίων κατά τα τελευταία {minutes:.0f} λεπτά της περιόδου ελέγχου. '
              'Αυτό δεν τεκμηριώνει διακοπή ρεύματος ή βλάβη συσκευής.',
        'ru': 'За последние {minutes:.0f} минут периода анализа не записано действительных значений обязательных показателей. '
              'Это не подтверждает отключение электричества или неисправность оборудования.',
    },
    'title.recording_gap': {
        'en': 'A recording gap needs attention',
        'el': 'Κενό καταγραφής που χρειάζεται έλεγχο',
        'ru': 'Пробел в записи требует проверки',
    },
    'observation.recording_gap': {
        'en': 'At least one required field was missing for a continuous {minutes:.0f}-minute interval. '
              'Missing telemetry is not evidence of zero energy usage.',
        'el': 'Έλειπαν δεδομένα από τουλάχιστον ένα απαιτούμενο πεδίο για συνεχόμενο διάστημα {minutes:.0f} λεπτών. '
              'Η απουσία τηλεμετρίας δεν αποτελεί ένδειξη μηδενικής κατανάλωσης ενέργειας.',
        'ru': 'Данные хотя бы одного обязательного показателя отсутствовали непрерывно в течение {minutes:.0f} минут. '
              'Отсутствие телеметрии не означает нулевое потребление энергии.',
    },
    'title.limited_baseline': {
        'en': 'Limited comparison history',
        'el': 'Περιορισμένο ιστορικό σύγκρισης',
        'ru': 'Недостаточно истории для сравнения',
    },
    'observation.limited_baseline': {
        'en': 'At least one metric has fewer than four preceding 24-hour windows with at least 90% recording coverage. '
              'Pattern comparisons are withheld for those metrics.',
        'el': 'Για τουλάχιστον ένα μέγεθος είναι διαθέσιμες λιγότερες από τέσσερις προηγούμενες περίοδοι 24 ωρών με κάλυψη καταγραφής τουλάχιστον 90%. '
              'Δεν γίνονται συγκρίσεις με προηγούμενα μοτίβα για αυτά τα μεγέθη.',
        'ru': 'Хотя бы для одного показателя доступно менее четырёх предшествующих 24-часовых периодов с охватом записи не менее 90%. '
              'Сравнение с предыдущими закономерностями для таких показателей не выполняется.',
    },
    'title.high_load_energy': {
        'en': 'Higher recorded house consumption',
        'el': 'Αυξημένη καταγεγραμμένη κατανάλωση σπιτιού',
        'ru': 'Повышенное зарегистрированное потребление дома',
    },
    'title.high_grid_energy': {
        'en': 'Higher estimated grid consumption',
        'el': 'Αυξημένη εκτιμώμενη κατανάλωση από το δίκτυο',
        'ru': 'Повышенное расчётное потребление из сети',
    },
    'observation.high_energy': {
        'en': 'Recorded {value:.2f} kWh versus a {baseline:.2f} kWh median across '
              '{baseline_days} qualifying baseline windows. This is a usage observation, not a device fault.',
        'el': 'Καταγράφηκαν {value:.2f} kWh, έναντι διάμεσης τιμής {baseline:.2f} kWh σε '
              '{baseline_days} περιόδους αναφοράς με επαρκή κάλυψη. Πρόκειται για παρατήρηση κατανάλωσης, όχι για ένδειξη βλάβης συσκευής.',
        'ru': 'Записано {value:.2f} кВт·ч при медиане {baseline:.2f} кВт·ч по '
              '{baseline_days} опорным периодам с достаточным охватом. Это наблюдение о потреблении, а не неисправность оборудования.',
    },
    'title.low_solar_energy': {
        'en': 'Lower recorded solar generation',
        'el': 'Μειωμένη καταγεγραμμένη ηλιακή παραγωγή',
        'ru': 'Сниженная зарегистрированная солнечная выработка',
    },
    'observation.low_solar_energy': {
        'en': 'Recorded {value:.2f} kWh versus a {baseline:.2f} kWh median across '
              '{baseline_days} qualifying baseline windows. Weather, shading and equipment causes cannot be distinguished from these readings alone.',
        'el': 'Καταγράφηκαν {value:.2f} kWh, έναντι διάμεσης τιμής {baseline:.2f} kWh σε '
              '{baseline_days} περιόδους αναφοράς με επαρκή κάλυψη. Μόνο από αυτές τις μετρήσεις δεν μπορεί να διακριθεί αν η αιτία σχετίζεται με τον καιρό, τη σκίαση ή τον εξοπλισμό.',
        'ru': 'Записано {value:.2f} кВт·ч при медиане {baseline:.2f} кВт·ч по '
              '{baseline_days} опорным периодам с достаточным охватом. По одним этим показаниям нельзя различить влияние погоды, затенения и оборудования.',
    },
    'title.low_battery_maximum': {
        'en': 'Battery reached a lower recorded charge',
        'el': 'Η μπαταρία έφτασε σε χαμηλότερη καταγεγραμμένη φόρτιση',
        'ru': 'Зарегистрированный максимум заряда аккумулятора снизился',
    },
    'observation.low_battery_maximum': {
        'en': 'The highest recorded battery charge was {value:.1f}%, compared with a {baseline:.1f}% baseline median. '
              'This alone does not establish reduced battery capacity or a charging fault.',
        'el': 'Η μέγιστη καταγεγραμμένη φόρτιση της μπαταρίας ήταν {value:.1f}%, έναντι διάμεσης τιμής αναφοράς {baseline:.1f}%. '
              'Αυτό από μόνο του δεν τεκμηριώνει μειωμένη χωρητικότητα μπαταρίας ή βλάβη φόρτισης.',
        'ru': 'Максимальный зарегистрированный заряд аккумулятора составил {value:.1f}% при опорной медиане {baseline:.1f}%. '
              'Само по себе это не подтверждает снижение ёмкости аккумулятора или неисправность зарядки.',
    },
    'title.sustained_house_load': {
        'en': 'Sustained high house demand',
        'el': 'Παρατεταμένη υψηλή ζήτηση ισχύος στο σπίτι',
        'ru': 'Длительная высокая нагрузка дома',
    },
    'observation.sustained_house_load': {
        'en': 'House demand stayed at or above 3 kW for {minutes:.0f} recorded minutes, '
              'averaging {average:.2f} kW. This uses a fixed 3 kW/30-minute threshold, not a time-of-day anomaly model.',
        'el': 'Η ζήτηση ισχύος του σπιτιού παρέμεινε στα 3 kW ή υψηλότερα για {minutes:.0f} καταγεγραμμένα λεπτά, '
              'με μέση ισχύ {average:.2f} kW. Χρησιμοποιείται σταθερό όριο 3 kW για 30 λεπτά, όχι μοντέλο ανίχνευσης αποκλίσεων ανά ώρα της ημέρας.',
        'ru': 'Нагрузка дома оставалась на уровне не ниже 3 кВт в течение {minutes:.0f} минут записи, '
              'в среднем {average:.2f} кВт. Использован фиксированный порог 3 кВт в течение 30 минут, а не модель отклонений по времени суток.',
    },
    'title.no_threshold_exceeded': {
        'en': 'No configured thresholds exceeded',
        'el': 'Δεν ξεπεράστηκαν τα καθορισμένα όρια',
        'ru': 'Заданные пороги не превышены',
    },
    'observation.no_threshold_exceeded': {
        'en': 'Recorded energy and battery-charge comparisons did not cross the configured review thresholds. '
              'Minute averages and these checks cannot rule out brief events or physical equipment faults.',
        'el': 'Οι συγκρίσεις της καταγεγραμμένης ενέργειας και της φόρτισης μπαταρίας δεν ξεπέρασαν τα καθορισμένα όρια ελέγχου. '
              'Οι μέσοι όροι ανά λεπτό και αυτοί οι έλεγχοι δεν μπορούν να αποκλείσουν σύντομα συμβάντα ή βλάβες του εξοπλισμού.',
        'ru': 'Сравнения записанной энергии и заряда аккумулятора не вышли за заданные пороги проверки. '
              'Средние значения за минуту и эти проверки не позволяют исключить краткие события или физические неисправности оборудования.',
    },
    'limitation.minute_averages': {
        'en': 'Energy totals are integrated recorded minute averages, not billing-meter or inverter lifetime totals.',
        'el': 'Τα σύνολα ενέργειας υπολογίζονται από τους καταγεγραμμένους μέσους όρους ανά λεπτό, όχι από μετρητή χρέωσης ή από τα συνολικά στοιχεία διάρκειας ζωής του αντιστροφέα.',
        'ru': 'Итоги энергии рассчитаны по записанным средним значениям за минуту; это не показания расчётного счётчика и не итоги инвертора за весь срок службы.',
    },
    'limitation.coverage': {
        'en': 'Missing readings are excluded, never treated as zero. Coverage is the smallest recorded share across required fields.',
        'el': 'Οι μετρήσεις που λείπουν εξαιρούνται και δεν θεωρούνται ποτέ μηδενικές. Η κάλυψη είναι το μικρότερο ποσοστό καταγραφής μεταξύ των απαιτούμενων πεδίων.',
        'ru': 'Отсутствующие показания исключаются и никогда не считаются нулевыми. Охват равен наименьшей доле записанного времени среди обязательных показателей.',
    },
    'limitation.baseline': {
        'en': 'Comparisons use the median of preceding, non-overlapping elapsed 24-hour windows with at least 90% coverage; at least four of seven are required.',
        'el': 'Οι συγκρίσεις χρησιμοποιούν τη διάμεση τιμή προηγούμενων, μη επικαλυπτόμενων περιόδων διάρκειας 24 ωρών με κάλυψη τουλάχιστον 90%. Απαιτούνται τουλάχιστον τέσσερις από τις επτά περιόδους.',
        'ru': 'Для сравнения используется медиана предшествующих неперекрывающихся периодов длительностью 24 часа с охватом не менее 90%; нужны как минимум четыре из семи периодов.',
    },
    'limitation.daylight_saving': {
        'en': 'Elapsed 24-hour windows can shift by one local hour across Athens daylight saving changes; no seasonal or weekday adjustment is applied.',
        'el': 'Οι περίοδοι διάρκειας 24 ωρών μπορεί να μετατοπιστούν κατά μία τοπική ώρα όταν αλλάζει η θερινή ώρα στην Αθήνα. Δεν εφαρμόζεται προσαρμογή ανά εποχή ή ημέρα της εβδομάδας.',
        'ru': 'При смене летнего времени в Афинах периоды длительностью 24 часа могут смещаться на один час по местному времени; поправки на сезон и день недели не применяются.',
    },
    'limitation.estimated_sources': {
        'en': 'Grid power is estimated. Battery charged energy does not identify whether solar or grid supplied it.',
        'el': 'Η ισχύς από το δίκτυο είναι εκτιμώμενη. Η ενέργεια φόρτισης της μπαταρίας δεν προσδιορίζει αν προήλθε από τα φωτοβολταϊκά ή το δίκτυο.',
        'ru': 'Мощность сети является оценочной. Энергия зарядки аккумулятора не показывает, поступила ли она от солнечных панелей или из сети.',
    },
    'limitation.no_cause_attribution': {
        'en': 'No weather observations or physical fault logs are available to this analysis. Causes and appliance identities are not inferred.',
        'el': 'Η ανάλυση δεν διαθέτει παρατηρήσεις καιρού ή αρχεία καταγραφής βλαβών του εξοπλισμού. Δεν συνάγονται αιτίες ούτε προσδιορίζονται οικιακές συσκευές.',
        'ru': 'В этом анализе нет наблюдений за погодой или журналов физических неисправностей. Причины и конкретные бытовые приборы не определяются.',
    },
    'limitation.finding_limit': {
        'en': 'Only the six highest-priority findings are included in this report.',
        'el': 'Η αναφορά περιλαμβάνει μόνο τα έξι ευρήματα με την υψηλότερη προτεραιότητα.',
        'ru': 'В отчёт включены только шесть наблюдений с наивысшим приоритетом.',
    },
}


class _ReportFormatter(Formatter):
    def __init__(self, language):
        self.language = language

    def format_field(self, value, format_spec):
        result = super().format_field(value, format_spec)
        if self.language != 'en' and isinstance(value, (int, float)) and not isinstance(value, bool):
            return result.replace('.', ',')
        return result


def localized(key, **values):
    """Format the same raw evidence in all supported languages, without fallback."""
    translations = TEXT[key]
    if set(translations) != set(LANGUAGES):
        raise ValueError('Report text must contain exactly en, el and ru translations')
    result = {}
    for language in LANGUAGES:
        template = translations[language]
        if not isinstance(template, str) or not template.strip():
            raise ValueError('Report translations must be nonempty text')
        result[language] = _ReportFormatter(language).vformat(template, (), values)
    return result
