"""Derived energy summaries; raw readings remain the source of truth.

Keep the integration rules aligned with energyTotals in history.js. Summaries
represent recorded minute averages, not billing meters or inverter lifetime totals.
"""
import json
import math
from datetime import datetime
from zoneinfo import ZoneInfo

ATHENS = ZoneInfo('Europe/Athens')
SUMMARY_VERSION = 1
ENERGY_KEYS = ('pv', 'grid', 'load', 'battery', 'solar_to_house')


def empty_totals():
    return {key: {'kwh': 0.0, 'seconds': 0.0} for key in ENERGY_KEYS}


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def summarize_points(points, cutoff):
    """Integrate each distinct minute once, clipping the newest minute at cutoff."""
    totals = empty_totals()
    minutes = {row['t']: row for row in points if finite(row.get('t'))}
    first = min(minutes, default=None)
    for stamp, row in minutes.items():
        seconds = max(0, min(stamp + 60, cutoff) - stamp)
        if not seconds:
            continue
        values = row.get('values', {})
        for key in ENERGY_KEYS[:-1]:
            reading = values.get(key)
            if not finite(reading):
                continue
            watts = max(0, -reading) if key == 'battery' else reading
            if watts < 0:
                continue
            totals[key]['kwh'] += watts * seconds / 3_600_000
            totals[key]['seconds'] += seconds
        load, grid, battery, pv = (values.get(key) for key in ('load', 'grid', 'battery', 'pv'))
        if all(finite(value) for value in (load, grid, battery, pv)) and min(load, grid, pv) >= 0:
            watts = min(pv, max(0, load - grid - max(0, -battery)))
            totals['solar_to_house']['kwh'] += watts * seconds / 3_600_000
            totals['solar_to_house']['seconds'] += seconds
    return {'from': first, 'totals': totals}


def combine_days(days, cutoff):
    """Return compact monthly rows, including completely unrecorded months."""
    if not days:
        return {'version': SUMMARY_VERSION, 'from': None, 'to': None, 'totals': empty_totals(), 'months': []}
    first = min(day['from'] for day in days)
    monthly = {}
    for day in days:
        totals = monthly.setdefault(day['day'][:7], empty_totals())
        for key in ENERGY_KEYS:
            for field in ('kwh', 'seconds'):
                totals[key][field] += day['totals'][key][field]
    cursor = datetime.fromtimestamp(first, ATHENS).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    rows = []
    totals = empty_totals()
    # Include the current month even when its first observation is at midnight.
    while cursor.timestamp() <= cutoff:
        end = cursor.replace(year=cursor.year + 1, month=1) if cursor.month == 12 else cursor.replace(month=cursor.month + 1)
        month = cursor.strftime('%Y-%m')
        values = monthly.get(month, empty_totals())
        rows.append({'month': month, 'from': max(first, cursor.timestamp()),
                     'to': min(cutoff, end.timestamp()), 'totals': values})
        for key in ENERGY_KEYS:
            for field in ('kwh', 'seconds'):
                totals[key][field] += values[key][field]
        cursor = end
    return {'version': SUMMARY_VERSION, 'from': first, 'to': cutoff, 'totals': totals, 'months': rows}


def update_summary(db, days, day, cutoff, previous_stamp=None):
    """Backfill derived caches once; subsequently recalculate only affected days.

    On a day rollover the last observed day's final minute becomes complete,
    matching the cutoff used by the existing browser summaries. Cache versioning
    permits rebuilding these derived values without editing any raw history.
    """
    db.execute('CREATE TABLE IF NOT EXISTS energy_daily_summary '
               '(day TEXT PRIMARY KEY, version INTEGER NOT NULL, first_minute REAL NOT NULL, totals TEXT NOT NULL)')
    cached = {row[0]: row[1] for row in db.execute('SELECT day,version FROM energy_daily_summary')}
    affected = {value for value in days if cached.get(value) != SUMMARY_VERSION}
    affected.add(day)
    if previous_stamp is not None:
        affected.add(datetime.fromtimestamp(previous_stamp, ATHENS).strftime('%Y-%m-%d'))
    for value in sorted(affected.intersection(days)):
        points = {}
        for minute, metric, total, count in db.execute(
                'SELECT minute,metric,total,count FROM readings WHERE day=? ORDER BY minute', (value,)):
            point = points.setdefault(minute, {'t': minute, 'values': {}})
            if finite(total) and finite(count) and count > 0:
                point['values'][metric] = total / count
        summary = summarize_points(points.values(), cutoff)
        db.execute('INSERT OR REPLACE INTO energy_daily_summary VALUES (?,?,?,?)',
                   (value, SUMMARY_VERSION, summary['from'], json.dumps(summary['totals'], allow_nan=False)))
    available = set(days)
    summaries = [{'day': value, 'from': first, 'totals': json.loads(totals)}
                 for value, first, totals in db.execute('SELECT day,first_minute,totals FROM energy_daily_summary ORDER BY day')
                 if value in available]
    return combine_days(summaries, cutoff)
