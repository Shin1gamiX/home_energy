"""Record fresh reports once, retaining minute aggregates and public daily files."""
import json
import math
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from energy_summary import update_summary

# Generic PV voltage remains in old rows, but only channel voltages are recorded now.
KEYS = ('grid', 'pv', 'battery', 'soc', 'load', 'grid_voltage',
        'pv_current', 'battery_current', 'load_current',
        'pv1_power', 'pv1_voltage', 'pv1_current', 'pv2_power', 'pv2_voltage', 'pv2_current')
ATHENS = ZoneInfo('Europe/Athens')


def supply_mode(values):
    """Estimate house supply; grid charging is disabled in this installation."""
    if not all(isinstance(values.get(k), (int, float)) and math.isfinite(values[k])
               for k in ('grid', 'pv', 'battery', 'load')):
        return 'unknown'
    load = max(0, values['load'])
    if load <= 0:
        return 'standby'
    grid = min(load, max(0, values['grid']))
    battery = min(load, max(0, -values['battery']))
    solar = min(max(0, values['pv']), max(0, load - grid - battery))
    sources = {'grid': grid, 'solar': solar, 'battery': battery}
    threshold = max(20, load * .02)
    active = [name for name, watts in sources.items() if watts > threshold]
    if len(active) > 1:
        return 'mixed'
    if active:
        return active[0]
    return max(sources, key=sources.get) if max(sources.values()) > 0 else 'unknown'


def record(payload, runtime):
    if payload['status'] not in ('live', 'partial') or not payload.get('updated_at'):
        return
    runtime = Path(runtime)
    stamp = payload['updated_at']
    minute = int(stamp // 60) * 60
    day = datetime.fromtimestamp(stamp, ATHENS).strftime('%Y-%m-%d')
    with closing(sqlite3.connect(runtime / 'history.sqlite3', timeout=2)) as db, db:
        db.execute('CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value REAL)')
        db.execute('CREATE TABLE IF NOT EXISTS readings (minute INTEGER, day TEXT, metric TEXT, total REAL, count INTEGER, PRIMARY KEY(minute, metric))')
        db.execute('CREATE INDEX IF NOT EXISTS readings_day ON readings(day)')
        db.execute('CREATE TABLE IF NOT EXISTS supply_intervals (start REAL PRIMARY KEY, end REAL, state TEXT)')
        previous = db.execute("SELECT value FROM metadata WHERE key='last_report'").fetchone()
        if previous and stamp <= previous[0]:
            return
        state = supply_mode(payload['values'])
        last = db.execute('SELECT start,end,state FROM supply_intervals ORDER BY start DESC LIMIT 1').fetchone()
        # Each fresh observation remains valid for at most 90 seconds. A state
        # change truncates the previous interval at its observed timestamp.
        if last and stamp <= last[1] and state == last[2]:
            db.execute('UPDATE supply_intervals SET end=? WHERE start=?', (stamp + 90, last[0]))
        else:
            if last and stamp <= last[1]:
                db.execute('UPDATE supply_intervals SET end=? WHERE start=?', (stamp, last[0]))
            db.execute('INSERT INTO supply_intervals VALUES (?,?,?)', (stamp, stamp + 90, state))
        for key in KEYS:
            value = payload['values'].get(key)
            if value is not None:
                db.execute('INSERT INTO readings VALUES (?,?,?,?,1) ON CONFLICT(minute,metric) DO UPDATE SET total=total+excluded.total,count=count+1', (minute, day, key, value))
        db.execute("INSERT OR REPLACE INTO metadata VALUES ('last_report',?)", (stamp,))
        rows = db.execute('SELECT minute,metric,total,count FROM readings WHERE day=? ORDER BY minute', (day,)).fetchall()
        days = [r[0] for r in db.execute('SELECT DISTINCT day FROM readings ORDER BY day')]
        start = datetime.fromtimestamp(stamp, ATHENS).replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
        intervals = [{'start': max(a, start.timestamp()), 'end': min(b, end.timestamp()), 'state': s}
                     for a, b, s in db.execute('SELECT start,end,state FROM supply_intervals WHERE end>? AND start<? ORDER BY start', (start.timestamp(), end.timestamp()))]
        recorded_from = db.execute('SELECT MIN(start) FROM supply_intervals').fetchone()[0]
        energy_summary = update_summary(db, days, day, stamp, previous[0] if previous else None)
    points = {}
    for timestamp, key, total, count in rows:
        point = points.setdefault(timestamp, {'t': timestamp, 'values': {}, 'counts': {}})
        point['values'][key] = total / count
        point['counts'][key] = count
    destination = runtime / 'history'
    destination.mkdir(exist_ok=True)
    atomic_json(destination / (day + '.json'), {'day': day, 'points': list(points.values()), 'modes': intervals})
    atomic_json(destination / 'index.json', {'days': days, 'updated_at': stamp,
                                           'mode_recorded_from': recorded_from,
                                           'energy_summary': energy_summary})


def atomic_json(path, data):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(data, separators=(',', ':'), allow_nan=False), encoding='utf-8')
    temporary.chmod(0o644)
    temporary.replace(path)
