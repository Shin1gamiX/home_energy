"""Record fresh reports once, retaining minute aggregates and public daily files."""
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

KEYS = ('grid', 'pv', 'battery', 'soc', 'load', 'pv_voltage', 'grid_voltage',
        'pv_current', 'battery_current', 'load_current')
ATHENS = ZoneInfo('Europe/Athens')


def record(payload, runtime):
    if payload['status'] not in ('live', 'partial') or not payload.get('updated_at'):
        return
    runtime = Path(runtime)
    stamp = payload['updated_at']
    minute = int(stamp // 60) * 60
    day = datetime.fromtimestamp(stamp, ATHENS).strftime('%Y-%m-%d')
    with sqlite3.connect(runtime / 'history.sqlite3', timeout=2) as db:
        db.execute('CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value REAL)')
        db.execute('CREATE TABLE IF NOT EXISTS readings (minute INTEGER, day TEXT, metric TEXT, total REAL, count INTEGER, PRIMARY KEY(minute, metric))')
        db.execute('CREATE INDEX IF NOT EXISTS readings_day ON readings(day)')
        previous = db.execute("SELECT value FROM metadata WHERE key='last_report'").fetchone()
        if previous and stamp <= previous[0]:
            return
        for key in KEYS:
            value = payload['values'].get(key)
            if value is not None:
                db.execute('INSERT INTO readings VALUES (?,?,?,?,1) ON CONFLICT(minute,metric) DO UPDATE SET total=total+excluded.total,count=count+1', (minute, day, key, value))
        db.execute("INSERT OR REPLACE INTO metadata VALUES ('last_report',?)", (stamp,))
        rows = db.execute('SELECT minute,metric,total,count FROM readings WHERE day=? ORDER BY minute', (day,)).fetchall()
        days = [r[0] for r in db.execute('SELECT DISTINCT day FROM readings ORDER BY day')]
    points = {}
    for timestamp, key, total, count in rows:
        point = points.setdefault(timestamp, {'t': timestamp, 'values': {}, 'counts': {}})
        point['values'][key] = total / count
        point['counts'][key] = count
    destination = runtime / 'history'
    destination.mkdir(exist_ok=True)
    atomic_json(destination / (day + '.json'), {'day': day, 'points': list(points.values())})
    atomic_json(destination / 'index.json', {'days': days, 'updated_at': stamp})


def atomic_json(path, data):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(data, separators=(',', ':'), allow_nan=False), encoding='utf-8')
    temporary.chmod(0o644)
    temporary.replace(path)
