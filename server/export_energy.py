"""Publish allowlisted energy readings from Home Assistant Recorder, read-only."""
import json
import logging
import math
import os
from pathlib import Path
import sqlite3
import time
from history_store import record
from connection_health import HealthMonitor

DATABASE = Path(os.environ.get('HA_DATABASE', '/srv/homeassistant/config/home-assistant_v2.db'))
OUTPUT = Path(os.environ.get('ENERGY_OUTPUT', '/var/www/homeenergy/runtime/energy.json'))
PREFIX = os.environ.get('ENERGY_ENTITY_PREFIX', 'sensor.anenji_anj_11kw_48v_wifi_p_')
PV_PREFIX = os.environ.get('ENERGY_PV_ENTITY_PREFIX', 'sensor.living_room_anenji_anj_11kw_48v_wifi_p_')
FIELDS = {'grid_house': ('grid_to_home_power', 'W'), 'grid_battery': ('grid_to_battery_power', 'W'),
          'pv': ('pv_power', 'W'),
          'battery': ('battery_power', 'W'), 'soc': ('battery_percent', '%'), 'load': ('load_power', 'W'),
          'pv_voltage': ('pv_voltage', 'V'), 'grid_voltage': ('grid_voltage', 'V'),
          'battery_voltage': ('battery_voltage', 'V'),
          'pv_current': ('pv_current', 'A'), 'battery_current': ('battery_average_current', 'A'),
          'load_current': ('output_current', 'A')}
CHANNEL_FIELDS = {f'pv{channel}_{metric}': (f'pv{channel}_{metric}', unit)
                  for channel in (1, 2)
                  for metric, unit in [('power', 'W'), ('voltage', 'V'), ('current', 'A')]}
FIELDS.update(CHANNEL_FIELDS)
SNAPSHOT_KEYS = (*FIELDS, 'grid')


def latest(connection, suffix, prefix=PREFIX):
    return connection.execute(
        'SELECT s.state,s.last_updated_ts,a.shared_attrs FROM states s '
        'LEFT JOIN state_attributes a ON a.attributes_id=s.attributes_id '
        'WHERE s.metadata_id=(SELECT metadata_id FROM states_meta WHERE entity_id=?) '
        'ORDER BY s.last_updated_ts DESC LIMIT 1', (prefix + suffix,),
    ).fetchone()


def snapshot(connection, now):
    values = {}
    for key, (suffix, unit) in FIELDS.items():
        row = latest(connection, suffix, PV_PREFIX if key in CHANNEL_FIELDS else PREFIX)
        try:
            value = float(row[0])
            attributes = json.loads(row[2] or '{}')
            if not math.isfinite(value) or attributes.get('unit_of_measurement') != unit:
                raise ValueError('Invalid measurement')
            if key == 'soc' and not 0 <= value <= 100:
                raise ValueError('Invalid percentage')
            if key in ('grid_house', 'grid_battery') and value < 0:
                raise ValueError('Negative grid import')
        except (TypeError, ValueError, IndexError):
            value = None
        values[key] = value
    # History and the Grid card use total import; Overview also needs each destination.
    house, charging = values['grid_house'], values['grid_battery']
    values['grid'] = house + charging if house is not None and charging is not None else None
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
    try:
        health = HealthMonitor.from_environment()
    except (ValueError, OSError):
        # Optional diagnostics must never stop the existing recording service.
        logging.warning('Connection health disabled: invalid local probe configuration')
        health = HealthMonitor()
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
            payload = {'values': dict.fromkeys(SNAPSHOT_KEYS), 'updated_at': None,
                       'generated_at': time.time(), 'status': 'offline'}
        payload['health'] = health.snapshot(time.time(), payload)
        publish(payload)
        try:
            record(payload, OUTPUT.parent)
        except (sqlite3.Error, OSError, ValueError):
            logging.exception('History recording failed')
        time.sleep(5)


if __name__ == '__main__':
    main()
