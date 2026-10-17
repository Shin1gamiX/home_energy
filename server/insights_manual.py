"""On-demand evidence, with selectable periods and no model/runtime dependency.

The existing minute reader, energy integration and immutable writer are reused.
Review is read-only; preparation adds a snapshot only when publication is wanted.
"""
from bisect import bisect_left
from datetime import datetime, timedelta
import hashlib
from statistics import median
import time

import insights_analysis as history_reader
import insights_publish as writer
from insights_i18n import localized

MAX_HOURS = 168
MAX_BASELINE_DAYS = 90
MIN_WINDOWS = 3


def translations(en, el, ru):
    return {'en': en, 'el': el, 'ru': ru}


def options(payload, now):
    """Only numbers/booleans are accepted; no paths, URLs or command arguments."""
    writer.require(type(payload) is dict and set(payload) <= {
        'hours', 'baseline_days', 'end', 'save'} and 'save' in payload,
        'Expected review options and an explicit save flag')
    hours, days = payload.get('hours', 24), payload.get('baseline_days', 7)
    end = payload.get('end', int(now // 60) * 60)
    writer.require(type(hours) is int and 1 <= hours <= MAX_HOURS, 'Review hours must be 1 to 168')
    writer.require(type(days) is int and 1 <= days <= MAX_BASELINE_DAYS, 'Comparison days must be 1 to 90')
    writer.require(type(payload['save']) is bool, 'Save must be a boolean')
    writer.require(type(end) is int and 946684800 <= end <= now and end % 60 == 0,
                   'Review end must be a completed minute in the past')
    return hours, days, end, payload['save']


def comparison_windows(start, end, days):
    """Use equal durations; never mix review readings into the baseline.

    Up to 24 hours: match the Athens ending clock time on earlier local dates.
    Longer reviews: use non-overlapping equal-length blocks, avoiding repeated
    counting of the same historical day. Incomplete oldest blocks are excluded.
    """
    duration = end - start
    baseline_start = int((datetime.fromtimestamp(start, history_reader.ATHENS)
                          - timedelta(days=days)).timestamp())
    windows = []
    if duration <= 86400:
        local_end = datetime.fromtimestamp(end, history_reader.ATHENS)
        latest_end = start
        for offset in range(1, days + 2):
            right = int((local_end - timedelta(days=offset)).timestamp())
            left = right - duration
            if left >= baseline_start and right <= latest_end:
                windows.append((left, right))
                latest_end = left
    else:
        right = start
        while right - duration >= baseline_start:
            windows.append((right - duration, right))
            right -= duration
    return baseline_start, windows


def analyze(history, now, request):
    hours, days, end, _ = options(request, now)
    duration = hours * 3600
    start = end - duration
    baseline_start, windows = comparison_windows(start, end, days)
    rows = history_reader._load_points(history, baseline_start, end)
    stamps = [row['t'] for row in rows]

    def rows_between(left, right):
        return rows[bisect_left(stamps, left):bisect_left(stamps, right)]

    def summarize(left, right):
        return history_reader.summarize_window(rows_between(left, right), left, right)

    current = summarize(start, end)
    prior = [summarize(left, right) for left, right in windows]
    metrics, comparisons = [], {}
    for key, label, unit, source in history_reader.METRICS:
        eligible = [item['values'][key] for item in prior
                    if item['seconds'][source] >= .9 * duration and item['values'][key] is not None]
        baseline = median(eligible) if len(eligible) >= MIN_WINDOWS else None
        value = current['values'][key]
        coverage = 100 * current['seconds'][source] / duration
        change = (100 * (value - baseline) / baseline
                  if baseline is not None and baseline > 0 and value is not None and coverage >= 90 else None)
        metrics.append({'key': key, 'label': localized(label), 'unit': unit,
                        'value': history_reader._number(value), 'baseline': history_reader._number(baseline),
                        'change_pct': history_reader._number(change, 1), 'coverage_pct': round(coverage, 2),
                        # Retain the schema-2 field name for old readers; it counts windows.
                        'baseline_days': len(eligible)})
        if baseline is not None and value is not None and coverage >= 90:
            comparisons[key] = dict(value=value, baseline=baseline, baseline_days=len(eligible))

    review_rows = rows_between(start, end)
    coverage = 100 * min(current['seconds'].values()) / duration
    data_through = max((row['t'] + 60 for row in rows if row['values']), default=None)
    findings = []
    add = history_reader._finding
    if coverage < 90:
        findings.append(add('incomplete_coverage', 'monitoring', 'data_issue', start, end, coverage=coverage))
    gap_start, gap_end = history_reader._longest_gap(review_rows, start, end)
    if data_through is None or end - data_through >= 600:
        left = max(start, data_through or start)
        findings.append(add('stale_history', 'monitoring', 'data_issue', left, end, minutes=(end - left) / 60))
    elif gap_end - gap_start >= 600:
        findings.append(add('recording_gap', 'monitoring', 'data_issue', gap_start, gap_end,
                            minutes=(gap_end - gap_start) / 60))
    if any(metric['baseline'] is None for metric in metrics):
        finding = add('limited_baseline', 'monitoring', 'notice', start, end)
        finding['observation'] = translations(
            'At least one metric has fewer than three comparable windows with 90% coverage. Its comparison is withheld.',
            'Για τουλάχιστον ένα μέγεθος υπάρχουν λιγότερες από τρεις συγκρίσιμες περίοδοι με κάλυψη 90%. Δεν γίνεται σύγκριση για αυτό.',
            'Хотя бы для одного показателя есть менее трёх сопоставимых периодов с охватом 90%. Сравнение для него не выполняется.')
        findings.append(finding)
    # Absolute energy thresholds scale with the review duration, not a fixed day.
    material = 2 * hours / 24
    for key, category, ratio in (('load', 'house', 1.3), ('grid', 'grid', 1.5)):
        metric = comparisons.get(key)
        if metric and metric['value'] >= metric['baseline'] * ratio and metric['value'] - metric['baseline'] >= material:
            severity = ('warning' if key == 'load' and metric['value'] >= metric['baseline'] * 2
                        and metric['value'] - metric['baseline'] >= 10 * hours / 24 else 'notice')
            findings.append(add('high_' + key + '_energy', category, severity, start, end, key,
                                observation_key='observation.high_energy', **metric))
    solar = comparisons.get('pv')
    if solar and solar['baseline'] >= material and solar['value'] <= .6 * solar['baseline'] and solar['baseline'] - solar['value'] >= material:
        findings.append(add('low_solar_energy', 'solar', 'notice', start, end, 'pv', **solar))
    soc = comparisons.get('soc_max')
    if soc and soc['value'] < 90 and soc['baseline'] - soc['value'] >= 20:
        findings.append(add('low_battery_maximum', 'battery', 'notice', start, end, 'soc_max', **soc))
    sustained = history_reader._sustained_load(review_rows, start, end)
    if sustained:
        left, right, watt_seconds = sustained
        findings.append(add('sustained_house_load', 'house', 'notice', left, right, 'load',
                            minutes=(right - left) / 60, average=watt_seconds / (right - left) / 1000))
    if not findings:
        findings.append(add('no_threshold_exceeded', 'monitoring', 'routine', start, end))
    findings.sort(key=lambda item: -history_reader.SEVERITY_ORDER[item['severity']])
    limitations = [localized('limitation.' + key) for key in (
        'minute_averages', 'coverage', 'estimated_sources', 'no_cause_attribution')]
    limitations.append(translations(
        'Baseline: median of equal-length windows with at least 90% coverage; at least three are needed. Partial totals are not extrapolated.',
        'Βάση σύγκρισης: διάμεσος περιόδων ίδιας διάρκειας με κάλυψη τουλάχιστον 90%. Απαιτούνται τουλάχιστον τρεις. Τα ελλιπή σύνολα δεν προεκτείνονται.',
        'База сравнения: медиана периодов одинаковой длительности с охватом не менее 90%; нужны минимум три. Неполные итоги не экстраполируются.'))
    limitations.append(translations(
        'Reviews up to 24 hours match the Athens ending clock time on earlier dates. Longer reviews use consecutive equal-length blocks; unused oldest hours are excluded. No weekday or seasonal adjustment is applied. Clock changes can shift a boundary by one hour.',
        'Έως 24 ώρες συγκρίνονται περίοδοι που λήγουν την ίδια ώρα Αθήνας σε προηγούμενες ημερομηνίες. Μεγαλύτερες ανασκοπήσεις χρησιμοποιούν διαδοχικά τμήματα ίδιας διάρκειας, εξαιρώντας τις παλαιότερες ώρες που περισσεύουν. Δεν γίνεται προσαρμογή ανά ημέρα ή εποχή. Η αλλαγή ώρας μπορεί να μετατοπίσει ένα όριο κατά μία ώρα.',
        'Обзоры до 24 часов сравниваются с периодами, заканчивающимися в то же афинское время в предыдущие даты. Более длинные обзоры используют последовательные равные блоки; остаток в начале исключается. Поправок на день недели и сезон нет. Смена времени может сдвинуть границу на час.'))
    if len(findings) > 6:
        limitations.append(localized('limitation.finding_limit'))
    analysis = {'schema_version': 2, 'generated_at': int(now // 60) * 60,
                'window': {'start': start, 'end': end},
                'baseline': {'start': baseline_start, 'end': start, 'days': days,
                             'windows': len(windows), 'method': 'clock_matched' if hours <= 24 else 'consecutive'},
                'data_through': data_through, 'coverage_pct': round(coverage, 2),
                'severity': findings[0]['severity'], 'metrics': metrics,
                'findings': findings[:6], 'limitations': limitations}

    # Compact private evidence for a human-requested review, not part of the feed.
    # Each bucket is relative to review start (not necessarily a clock hour).
    hourly = []
    for index in range(hours):
        left = start + index * 3600
        item = summarize(left, left + 3600)
        point = {'start': left, 'end': left + 3600,
                 'values': {key: history_reader._number(value) for key, value in item['values'].items()},
                 'coverage_pct': {key: round(value / 36, 2) for key, value in item['seconds'].items()}}
        hourly.append(point)
    return analysis, {'hourly': hourly, 'comparison_windows': [{'start': a, 'end': b} for a, b in windows]}


def prepare(raw, state=writer.STATE, history=writer.HISTORY, now=None):
    now = int(time.time() if now is None else now)
    request = writer.decode(raw)
    _, _, _, save = options(request, now)
    analysis, context = analyze(history, now, request)
    body = writer.encoded(analysis)
    writer.require(len(body) <= writer.MAX_ANALYSIS, 'Analysis too large')
    identifier = hashlib.sha256(body).hexdigest()
    if save:
        with writer.locked(state), writer.database(state) as db, db:
            writer.require(db.execute('SELECT count(*) FROM snapshots WHERE created>?',
                                      (now - 3600,)).fetchone()[0] < 12, 'Preparation rate limit reached')
            # Manual conversion never prunes earlier snapshots or other evidence.
            db.execute('INSERT OR IGNORE INTO snapshots VALUES (?,?,?)', (identifier, now, body.decode('utf-8')))
    return {'analysis_id': identifier, 'analysis': analysis, 'context': context, 'saved': save}
