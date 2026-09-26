"""Publish allowlisted energy readings from Home Assistant Recorder, read-only."""
import json
import logging
import math
import os
from pathlib import Path
import sqlite3
import time
from history_store import record

DATABASE = Path(os.environ.get('HA_DATABASE', '/srv/homeassistant/config/home-assistant_v2.db'))
OUTPUT = Path(os.environ.get('ENERGY_OUTPUT', '/var/www/homeenergy/runtime/energy.json'))
PREFIX = os.environ.get('ENERGY_ENTITY_PREFIX', 'sensor.anenji_anj_11kw_48v_wifi_p_')
FIELDS = {'grid': ('grid_to_home_power', 'W'), 'pv': ('pv_power', 'W'),
          'battery': ('battery_power', 'W'), 'soc': ('battery_percent', '%'), 'load': ('load_power', 'W'),
          'pv_voltage': ('pv_voltage', 'V'), 'grid_voltage': ('grid_voltage', 'V'),
          'pv_current': ('pv_current', 'A'), 'battery_current': ('battery_average_current', 'A'),
          'load_current': ('output_current', 'A')}


def latest(connection, suffix):
    return connection.execute(
        'SELECT s.state,s.last_updated_ts,a.shared_attrs FROM states s '
        'LEFT JOIN state_attributes a ON a.attributes_id=s.attributes_id '
        'WHERE s.metadata_id=(SELECT metadata_id FROM states_meta WHERE entity_id=?) '
        'ORDER BY s.last_updated_ts DESC LIMIT 1', (PREFIX + suffix,),
    ).fetchone()


def snapshot(connection, now):
    values = {}
    for key, (suffix, unit) in FIELDS.items():
        row = latest(connection, suffix)
        try:
            value = float(row[0])
            attributes = json.loads(row[2] or '{}')
            if not math.isfinite(value) or attributes.get('unit_of_measurement') != unit:
                raise ValueError('Invalid measurement')
            if key == 'soc' and not 0 <= value <= 100:
                raise ValueError('Invalid percentage')
        except (TypeError, ValueError, IndexError):
            value = None
        values[key] = value
    # Total import estimate includes power sent to the house and battery.
    row = latest(connection, 'grid_to_battery_power')
    try:
        charging = float(row[0])
        if not math.isfinite(charging) or charging < 0 or json.loads(row[2] or '{}').get('unit_of_measurement') != 'W':
            raise ValueError('Invalid grid charging measurement')
        values['grid'] = values['grid'] + charging if values['grid'] is not None and values['grid'] >= 0 else None
    except (TypeError, ValueError, IndexError):
        values['grid'] = None
    # Constant SOC/grid states can be old. The changing inverter clock is the heartbeat.
    heartbeat = latest(connection, 'inverter_time')
    updated = heartbeat[1] if heartbeat and heartbeat[0] not in ('unknown', 'unavailable') else None
    fresh = updated is not None and -30 <= now - updated <= 90
    mode = latest(connection, 'operating_mode')
    mode_value = mode[0] if mode and mode[0] in ('Power On', 'Standby', 'Mains', 'Off-Grid', 'Bypass', 'Charging', 'Fault') else None
    return {'values': values, 'mode': mode_value, 'updated_at': updated, 'generated_at': now,
            'status': 'stale' if not fresh else 'partial' if None in values.values() else 'live'}


def publish(payload):
    temporary = OUTPUT.with_suffix('.tmp')
    with temporary.open('w', encoding='utf-8') as stream:
        json.dump(payload, stream, allow_nan=False, separators=(',', ':'))
    temporary.chmod(0o644)
    temporary.replace(OUTPUT)


def main():
    logging.basicConfig(level=logging.INFO)
    previous_error = None
    while True:
        try:
            connection = sqlite3.connect(DATABASE.as_uri() + '?mode=ro', uri=True, timeout=2)
            try:
                connection.execute('PRAGMA query_only=ON')
                connection.execute('BEGIN')
                payload = snapshot(connection, time.time())
            finally:
                connection.close()
            previous_error = None
        except (sqlite3.Error, OSError, ValueError) as error:
            if type(error).__name__ != previous_error:
                logging.error('Energy source unavailable: %s', type(error).__name__)
            previous_error = type(error).__name__
            payload = {'values': dict.fromkeys(FIELDS), 'updated_at': None,
                       'generated_at': time.time(), 'status': 'offline'}
        publish(payload)
        try:
            record(payload, OUTPUT.parent)
        except (sqlite3.Error, OSError, ValueError):
            logging.exception('History recording failed')
        time.sleep(5)


if __name__ == '__main__':
    main()
