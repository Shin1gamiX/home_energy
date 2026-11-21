"""Derived energy summaries; raw readings remain the source of truth.

Keep the integration rules aligned with energyTotals in history.js. Summaries
represent recorded minute averages, not billing meters or inverter lifetime totals.
"""
import json
import math
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

ATHENS = ZoneInfo('Europe/Athens')
SUMMARY_VERSION = 2
GRID_BRANCHES = ('grid_house', 'grid_battery')
METER_KEYS = ('pv', 'grid', 'load', 'battery', 'battery_charged', *GRID_BRANCHES)
ENERGY_KEYS = (*METER_KEYS, 'solar_to_house')


def empty_totals():
    return {key: {'kwh': 0.0, 'seconds': 0.0} for key in ENERGY_KEYS}


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def split_house_sources(values):
    """House-side estimates when the exporter provides directional grid readings."""
    grid_house, pv, load, battery = (values.get(key) for key in ('grid_house', 'pv', 'load', 'battery'))
    if not all(finite(value) for value in (grid_house, pv, load, battery)) or min(grid_house, pv, load) < 0:
        return None
    grid = min(grid_house, load)
    discharge = min(-battery, load - grid) if battery <= -0.5 else 0
    grid_battery = values.get('grid_battery')
    # The house-side residual remains available if the charging branch is missing.
    # A known charging split also bounds the solar power available to the house.
    solar_charging = 0
    if battery >= 0.5 and finite(grid_battery) and grid_battery >= 0:
        solar_charging = min(pv, max(0, battery - grid_battery))
    return {'grid': grid, 'solar': min(pv - solar_charging, load - grid - discharge), 'battery': discharge}


def summarize_points(points, cutoff, split_from=None, bridge_from=None):
    """Integrate each distinct minute once, clipping the newest minute at cutoff."""
    totals = empty_totals()
    minutes = {row['t']: row for row in points if finite(row.get('t'))}
    first = min(minutes, default=None)
    for stamp, row in minutes.items():
        seconds = max(0, min(stamp + 60, cutoff) - stamp)
        if not seconds:
            continue
        values = row.get('values', {})
        for key in METER_KEYS:
            reading = values.get('battery' if key == 'battery_charged' else key)
            if not finite(reading):
                continue
            # Split the signed minute average into separate energy directions.
            if key == 'battery':
                watts = max(0, -reading)
            elif key == 'battery_charged':
                watts = max(0, reading)
            else:
                watts = reading
            if watts < 0:
                continue
            totals[key]['kwh'] += watts * seconds / 3_600_000
            totals[key]['seconds'] += seconds
        if 'solar_house' in values or (bridge_from is not None and stamp >= bridge_from):
            # The exporter calculated this from simultaneous source readings.
            # Reconstructing it from independently averaged fields can invent a flow.
            watts = values.get('solar_house')
            if not finite(watts) or watts < 0:
                continue
        elif (split_from is not None and stamp >= split_from) or any(key in values for key in GRID_BRANCHES):
            sources = split_house_sources(values)
            if sources is None:
                continue
            watts = sources['solar']
        else:
            # Keep the original aggregate-only calculation for pre-split minutes.
            load, grid, battery, pv = (values.get(key) for key in ('load', 'grid', 'battery', 'pv'))
            if not all(finite(value) for value in (load, grid, battery, pv)) or min(load, grid, pv) < 0:
                continue
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
            if key in GRID_BRANCHES and key not in day['totals']:
                # Version 2 days already cached before split recording have no coverage here.
                continue
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


def update_summary(db, days, day, cutoff, previous_stamp=None, split_from=None, bridge_from=None):
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
        summary = summarize_points(points.values(), cutoff, split_from, bridge_from)
        db.execute('INSERT OR REPLACE INTO energy_daily_summary VALUES (?,?,?,?)',
                   (value, SUMMARY_VERSION, summary['from'], json.dumps(summary['totals'], allow_nan=False)))
    available = set(days)
    summaries = [{'day': value, 'from': first, 'totals': json.loads(totals)}
                 for value, first, totals in db.execute('SELECT day,first_minute,totals FROM energy_daily_summary ORDER BY day')
                 if value in available]
    result = combine_days(summaries, cutoff)
    result['trends'] = daily_trends(db, summaries, cutoff)
    return result


def daily_trends(db, summaries, cutoff):
    """Small 60-completed-day index; never rewrite readings or daily exports.

    Energy reuses the existing integration cache. SOC extrema come from recorded
    minute averages, not instantaneous samples. Closed days are cached once.
    Missing calendar days are explicit and coverage uses actual Athens duration.
    """
    today = datetime.fromtimestamp(cutoff, ATHENS).replace(hour=0, minute=0, second=0, microsecond=0)
    first = today - timedelta(days=60)
    db.execute('CREATE TABLE IF NOT EXISTS energy_trends_soc '
               '(day TEXT PRIMARY KEY, minimum REAL, maximum REAL, seconds REAL NOT NULL)')
    cached = {day: dict(minimum=low, maximum=high, seconds=seconds)
              for day, low, high, seconds in db.execute(
                  'SELECT day,minimum,maximum,seconds FROM energy_trends_soc WHERE day>=? AND day<?',
                  (first.strftime('%Y-%m-%d'), today.strftime('%Y-%m-%d')))}
    energy = {row['day']: row['totals'] for row in summaries}
    rows = []
    for offset in range(60):
        start = first + timedelta(days=offset)
        end = start + timedelta(days=1)
        day = start.strftime('%Y-%m-%d')
        if day not in cached and day in energy:
            values = []
            for total, count in db.execute(
                    "SELECT total,count FROM readings WHERE day=? AND metric='soc' AND minute>=? AND minute<?",
                    (day, start.timestamp(), end.timestamp())):
                if finite(total) and finite(count) and count > 0 and 0 <= total / count <= 100:
                    values.append(total / count)
            cached[day] = dict(minimum=min(values, default=None), maximum=max(values, default=None),
                               seconds=len(values) * 60)
            db.execute('INSERT INTO energy_trends_soc VALUES (?,?,?,?)',
                       (day, cached[day]['minimum'], cached[day]['maximum'], cached[day]['seconds']))
        rows.append(dict(day=day, seconds=end.timestamp() - start.timestamp(),
                         totals=energy.get(day, empty_totals()),
                         soc=cached.get(day, dict(minimum=None, maximum=None, seconds=0))))
    return dict(version=1, completed_before=today.strftime('%Y-%m-%d'), days=rows)
