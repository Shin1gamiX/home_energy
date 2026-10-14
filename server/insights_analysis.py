"""Read-only, deterministic observations from recorded minute-average history.

An insight is an observation, not an inverter alarm or an appliance diagnosis.
Energy integration follows energy_summary.py: each minute represents up to 60
seconds; positive battery power is charging and negative power is discharge.
Unlike the summary cache, this reader also requires a positive sample count.
Missing/invalid readings contribute neither energy nor recording coverage.

All review windows are elapsed 24-hour periods, including across Athens daylight
saving changes. The seven comparison windows precede (and never overlap) the
review window. Median comparisons need four windows with >=90% coverage each.
No files are written, network requests made, or device commands issued here.
"""

import json
import math
import os
import stat
from datetime import datetime, timedelta
from pathlib import Path
from statistics import median
from zoneinfo import ZoneInfo

from insights_i18n import localized


ATHENS = ZoneInfo('Europe/Athens')
DAY_SECONDS = 86_400
BASELINE_DAYS = 7
MIN_COVERAGE = 90.0
MIN_BASELINE_DAYS = 4
MAX_FILE_BYTES = 4 * 1024 * 1024
MAX_DAY_POINTS = 1500  # A 25-hour Athens clock-change day.
REQUIRED_KEYS = ('pv', 'grid', 'load', 'battery', 'soc')
METRICS = (
    ('pv', 'metric.pv', 'kWh', 'pv'),
    ('grid', 'metric.grid', 'kWh', 'grid'),
    ('load', 'metric.load', 'kWh', 'load'),
    ('battery_supplied', 'metric.battery_supplied', 'kWh', 'battery'),
    ('battery_charged', 'metric.battery_charged', 'kWh', 'battery'),
    ('soc_max', 'metric.soc_max', '%', 'soc'),
)
SEVERITY_ORDER = {'routine': 0, 'notice': 1, 'warning': 2, 'data_issue': 3}


def finite(value):
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _reject_constant(value):
    raise ValueError('Non-finite JSON numbers are not valid history data')


def _day_names(start, end):
    """Only locally generated filenames are used, never index-provided paths."""
    day = datetime.fromtimestamp(start, ATHENS).date()
    last = datetime.fromtimestamp(end - 1, ATHENS).date()
    names = []
    while day <= last:
        names.append(day.isoformat())
        day += timedelta(days=1)
    return names


def _read_daily(path):
    """Bound reads and reject links/non-files, including FIFOs on the server."""
    try:
        file_stat = path.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(file_stat.st_mode):
        raise ValueError('Daily history must be a regular file, not a symlink')
    # O_NOFOLLOW closes the link-swap race on Linux. Nonblocking open prevents a
    # replaced FIFO from hanging the report; fstat validates the opened object.
    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_BINARY', 0)
    descriptor = os.open(path, flags)
    with os.fdopen(descriptor, 'rb') as stream:
        opened = os.fstat(stream.fileno())
        if not stat.S_ISREG(opened.st_mode) or opened.st_size > MAX_FILE_BYTES:
            raise ValueError('Daily history is not a bounded regular file')
        body = stream.read(MAX_FILE_BYTES + 1)
    if len(body) > MAX_FILE_BYTES:
        raise ValueError('Daily history exceeds the size limit')
    return body


def _load_points(history_dir, start, end):
    root = Path(history_dir).resolve(strict=True)
    if not root.is_dir():
        raise ValueError('History location must be a directory')
    rows = {}
    for day in _day_names(start, end):
        path = root / (day + '.json')
        body = _read_daily(path)
        if body is None:
            continue  # An absent date is a gap, not a day of zero usage.
        try:
            document = json.loads(body, parse_constant=_reject_constant)
        except (ValueError, UnicodeDecodeError) as error:
            raise ValueError('Invalid JSON in daily history: ' + day) from error
        if not isinstance(document, dict) or document.get('day') != day:
            raise ValueError('Daily history date does not match its filename')
        points = document.get('points')
        if not isinstance(points, list) or len(points) > MAX_DAY_POINTS:
            raise ValueError('Invalid or oversized daily points list')
        for row in points:
            if not isinstance(row, dict):
                raise ValueError('History point must be an object')
            stamp = row.get('t')
            if not finite(stamp) or stamp % 60 or not 0 <= stamp <= 32_503_680_000:
                raise ValueError('History point must have a valid minute timestamp')
            stamp = int(stamp)
            if datetime.fromtimestamp(stamp, ATHENS).date().isoformat() != day:
                raise ValueError('History point belongs to a different local date')
            values, counts = row.get('values'), row.get('counts', {})
            if not isinstance(values, dict) or not isinstance(counts, dict):
                raise ValueError('History values and counts must be objects')
            if stamp >= end or stamp + 60 <= start:
                continue
            cleaned = {}
            for key in REQUIRED_KEYS:
                value, count = values.get(key), counts.get(key)
                if not finite(value) or not finite(count) or count <= 0:
                    continue
                # Technical sanity bounds also keep arithmetic finite. These
                # are not operating limits or inferred inverter capabilities.
                if abs(value) > 1_000_000_000 or (key != 'battery' and value < 0):
                    continue
                if key == 'soc' and value > 100:
                    continue
                cleaned[key] = value
            if stamp in rows and rows[stamp]['values'] != cleaned:
                raise ValueError('Conflicting duplicate history minute')
            rows[stamp] = {'t': stamp, 'values': cleaned}
    return [rows[stamp] for stamp in sorted(rows)]


def summarize_window(rows, start, end):
    """Integrate distinct minutes, clipping both edges of a half-open window.

    Input rows are validated and count-filtered by _load_points. Exposing this
    pure helper makes the integration and clipping rules directly testable.
    """
    seconds = dict.fromkeys(REQUIRED_KEYS, 0)
    totals = dict.fromkeys((metric[0] for metric in METRICS if metric[0] != 'soc_max'), 0.0)
    highest_soc = None
    for row in {row['t']: row for row in rows}.values():
        duration = max(0, min(row['t'] + 60, end) - max(row['t'], start))
        if not duration:
            continue
        values = row['values']
        for key in REQUIRED_KEYS:
            if key in values:
                seconds[key] += duration
        for key in ('pv', 'grid', 'load'):
            if key in values:
                totals[key] += values[key] * duration / 3_600_000
        if 'battery' in values:
            totals['battery_supplied'] += max(0, -values['battery']) * duration / 3_600_000
            totals['battery_charged'] += max(0, values['battery']) * duration / 3_600_000
        if 'soc' in values:
            highest_soc = values['soc'] if highest_soc is None else max(highest_soc, values['soc'])
    values = {key: (totals[key] if seconds[source] else None)
              for key, _, _, source in METRICS if key != 'soc_max'}
    values['soc_max'] = highest_soc
    return {'values': values, 'seconds': seconds}


def _number(value, digits=3):
    return None if value is None else round(value, digits)


def _finding(code, category, severity, start, end, metric=None, observation_key=None, **values):
    return {'id': code, 'category': category, 'severity': severity, 'title': localized('title.' + code),
            'observation': localized(observation_key or 'observation.' + code, **values),
            'start': int(start), 'end': int(end), 'metric': metric}


def _longest_gap(rows, start, end):
    """Longest interval where at least one required field lacks a reading."""
    cursor = start
    longest = (start, start)
    for row in rows:
        if row['t'] >= end or row['t'] + 60 <= start or not all(key in row['values'] for key in REQUIRED_KEYS):
            continue
        left, right = max(start, row['t']), min(end, row['t'] + 60)
        if left - cursor > longest[1] - longest[0]:
            longest = (cursor, left)
        cursor = max(cursor, right)
    if end - cursor > longest[1] - longest[0]:
        longest = (cursor, end)
    return longest


def _sustained_load(rows, start, end):
    """Longest contiguous >=3 kW run; a missing minute always ends a run."""
    run = None
    best = None
    for row in rows:
        left, right = max(start, row['t']), min(end, row['t'] + 60)
        if right <= left:
            continue
        watts = row['values'].get('load')
        if watts is None or watts < 3000:
            run = None
            continue
        if run is None or left != run[1]:
            run = [left, right, watts * (right - left)]
        else:
            run[1] = right
            run[2] += watts * (right - left)
        if best is None or run[1] - run[0] > best[1] - best[0]:
            best = list(run)
    return best if best and best[1] - best[0] >= 1800 else None


def analyze(history_dir, now):
    """Return a bounded, secret-free report derived only from history files.

    now is a UTC Unix timestamp. generated_at and window.end are floored to the
    completed minute so repeated calls within that minute produce identical
    payloads. data_through is the end of the latest recorded valid minute, not
    a promise that all fields were present in that minute.
    """
    if not finite(now) or not DAY_SECONDS * 8 <= now <= 32_503_680_000:
        raise ValueError('now must be a finite supported Unix timestamp')
    end = int(now // 60) * 60
    start = end - DAY_SECONDS
    baseline_start = start - BASELINE_DAYS * DAY_SECONDS
    rows = _load_points(history_dir, baseline_start, end)
    current = summarize_window(rows, start, end)
    prior = [summarize_window(rows, start - (index + 1) * DAY_SECONDS, start - index * DAY_SECONDS)
             for index in range(BASELINE_DAYS)]
    metrics = []
    comparisons = {}
    for key, label, unit, source in METRICS:
        eligible = [day['values'][key] for day in prior
                    if day['seconds'][source] * 100 / DAY_SECONDS >= MIN_COVERAGE
                    and day['values'][key] is not None]
        baseline = median(eligible) if len(eligible) >= MIN_BASELINE_DAYS else None
        value = current['values'][key]
        coverage = current['seconds'][source] * 100 / DAY_SECONDS
        change = ((value - baseline) * 100 / baseline
                  if baseline is not None and baseline > 0 and value is not None and coverage >= MIN_COVERAGE
                  else None)
        metrics.append({'key': key, 'label': localized(label), 'unit': unit, 'value': _number(value),
                        'baseline': _number(baseline), 'change_pct': _number(change, 1),
                        'coverage_pct': _number(coverage, 2), 'baseline_days': len(eligible)})
        # Apply thresholds before display rounding can tip an edge case across
        # a limit. Public metrics remain compact and deterministic.
        if value is not None and baseline is not None and coverage >= MIN_COVERAGE:
            comparisons[key] = {'value': value, 'baseline': baseline, 'baseline_days': len(eligible)}
    coverage = min(current['seconds'].values()) * 100 / DAY_SECONDS
    data_through = max((row['t'] + 60 for row in rows if row['values']), default=None)
    limitations = [localized('limitation.' + key) for key in (
        'minute_averages', 'coverage', 'baseline', 'daylight_saving', 'estimated_sources', 'no_cause_attribution')]
    findings = []
    gap_start, gap_end = _longest_gap(rows, start, end)
    if coverage < MIN_COVERAGE:
        findings.append(_finding('incomplete_coverage', 'monitoring', 'data_issue', start, end, coverage=coverage))
    if data_through is None or end - data_through >= 600:
        gap_from = max(start, data_through if data_through is not None else start)
        findings.append(_finding('stale_history', 'monitoring', 'data_issue', gap_from, end,
                                 minutes=(end - gap_from) / 60))
    elif gap_end - gap_start >= 600:
        findings.append(_finding('recording_gap', 'monitoring', 'data_issue', gap_start, gap_end,
                                 minutes=(gap_end - gap_start) / 60))

    if any(metric['baseline'] is None for metric in metrics):
        findings.append(_finding('limited_baseline', 'monitoring', 'notice', start, end))

    for key, category, relative in (('load', 'house', 1.3), ('grid', 'grid', 1.5)):
        metric = comparisons.get(key)
        if metric and metric['value'] >= metric['baseline'] * relative and metric['value'] - metric['baseline'] >= 2:
            severity = 'warning' if key == 'load' and metric['value'] >= metric['baseline'] * 2 and metric['value'] - metric['baseline'] >= 10 else 'notice'
            findings.append(_finding('high_' + key + '_energy', category, severity, start, end, key,
                                     observation_key='observation.high_energy', **metric))
    solar = comparisons.get('pv')
    if solar and solar['baseline'] >= 2 and solar['value'] <= solar['baseline'] * .6 and solar['baseline'] - solar['value'] >= 2:
        findings.append(_finding('low_solar_energy', 'solar', 'notice', start, end, 'pv', **solar))
    soc = comparisons.get('soc_max')
    if soc and soc['value'] < 90 and soc['baseline'] - soc['value'] >= 20:
        findings.append(_finding('low_battery_maximum', 'battery', 'notice', start, end, 'soc_max', **soc))
    sustained = _sustained_load(rows, start, end)
    if sustained:
        left, right, watt_seconds = sustained
        findings.append(_finding('sustained_house_load', 'house', 'notice', left, right, 'load',
                                 minutes=(right - left) / 60, average=watt_seconds / (right - left) / 1000))
    if not findings:
        findings.append(_finding('no_threshold_exceeded', 'monitoring', 'routine', start, end))
    findings.sort(key=lambda finding: -SEVERITY_ORDER[finding['severity']])
    if len(findings) > 6:
        limitations.append(localized('limitation.finding_limit'))
    findings = findings[:6]
    return {'schema_version': 2, 'generated_at': end, 'window': {'start': start, 'end': end},
            'baseline': {'start': baseline_start, 'end': start, 'days': BASELINE_DAYS},
            'data_through': data_through, 'coverage_pct': _number(coverage, 2),
            'severity': max((finding['severity'] for finding in findings), key=SEVERITY_ORDER.get),
            'metrics': metrics, 'findings': findings, 'limitations': limitations}
