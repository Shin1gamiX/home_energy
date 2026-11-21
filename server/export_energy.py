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
FIELDS = {'grid': ('grid_power', 'W'), 'bridge': ('inverter_power', 'W'),
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
SNAPSHOT_KEYS = (*FIELDS, 'grid_house', 'grid_battery', 'solar_house', 'battery_house', 'solar_battery')
ROUTING_INPUTS = ('grid', 'bridge', 'load', 'battery', 'pv')


def latest(connection, suffix, prefix=PREFIX, *, reported_time=False):
    stamp = 'COALESCE(NULLIF(s.last_reported_ts,0),s.last_updated_ts)' if reported_time else 's.last_updated_ts'
    return connection.execute(
        f'SELECT s.state,{stamp},a.shared_attrs FROM states s '
        'LEFT JOIN state_attributes a ON a.attributes_id=s.attributes_id '
        'WHERE s.metadata_id=(SELECT metadata_id FROM states_meta WHERE entity_id=?) '
        'ORDER BY s.last_updated_ts DESC LIMIT 1', (prefix + suffix,),
    ).fetchone()


def recorded_attributes(row, now):
    """Accept an actual Recorder observation, including legacy rows without attributes."""
    if row is None:
        return None
    try:
        attributes = json.loads(row[2] or '{}')
        if not math.isfinite(row[1]) or row[1] > now + 30:
            return None
    except (TypeError, ValueError):
        return None
    if not isinstance(attributes, dict) or attributes.get('restored'):
        return None
    return attributes


def estimate_grid_split(grid_import, bridge_w, load, battery, pv):
    """Estimate destinations of imported watts from net AC/DC flow, leaving losses unassigned."""
    if grid_import is not None and grid_import <= 0:
        return 0, 0
    house = 0 if load == 0 else None
    charge = 0 if battery is not None and battery <= 0 else None
    if bridge_w is None:
        return house, charge
    to_dc = max(0, -bridge_w)
    to_ac = max(0, bridge_w)
    if to_dc == 0:
        charge = 0
    if load is not None and 0 <= load <= to_ac:
        house = 0
    if grid_import is None:
        return house, charge
    if load is not None and load >= 0:
        house = min(max(0, grid_import - to_dc), max(0, load - to_ac))
    if to_dc and battery is not None and battery > 0 and pv is not None and pv >= 0:
        # Only the charging demand that PV cannot cover supports a grid contribution.
        charge = min(to_dc, max(0, battery - pv), grid_import - (house or 0))
    return house, charge


def estimate_solar_charging(grid_battery, battery, pv):
    if pv == 0 or (battery is not None and battery <= 0):
        return 0
    if grid_battery is None or battery is None or pv is None or pv < 0:
        return None
    return min(pv, max(0, battery - grid_battery))


def estimate_house_sources(grid_house, grid_battery, bridge_w, load, battery, pv):
    """Allocate the remaining house load within the measured DC-to-AC output."""
    if load == 0 or (bridge_w is not None and bridge_w <= 0):
        return 0, 0
    solar = 0 if pv == 0 else None
    discharge = 0 if battery is not None and battery >= 0 else None
    if grid_house is None or load is None or load < 0:
        return solar, discharge
    budget = max(0, load - grid_house)
    if bridge_w is not None:
        budget = min(budget, bridge_w)
    elif grid_house != 0:
        return solar, discharge
    # With positive load and a missing bridge, the split above only returns zero
    # grid-to-house when import is known to be zero. PV/battery can still serve it.
    if not budget:
        return 0, 0
    if battery is None:
        return solar, discharge
    discharge = min(max(0, -battery), budget)
    if discharge == budget:
        return 0, discharge
    solar_charging = estimate_solar_charging(grid_battery, battery, pv)
    if pv is not None and pv >= 0 and solar_charging is not None:
        solar = min(max(0, pv - solar_charging), budget - discharge)
    return solar, discharge


def snapshot(connection, now):
    values = {}
    route_inputs = {}
    # Older Recorder schemas only track changes. Do not reuse an old bridge
    # reading when its entity has silently stopped reporting or was disabled.
    has_reported_time = connection.execute(
        "SELECT 1 FROM pragma_table_info('states') WHERE name='last_reported_ts'"
    ).fetchone() is not None
    for key, (suffix, unit) in FIELDS.items():
        row = latest(connection, suffix, PV_PREFIX if key in CHANNEL_FIELDS else PREFIX,
                     reported_time=has_reported_time)
        attributes = recorded_attributes(row, now)
        try:
            value = float(row[0])
            if not math.isfinite(value) or attributes is None or attributes.get('unit_of_measurement') != unit:
                raise ValueError('Invalid measurement')
            if key == 'soc' and not 0 <= value <= 100:
                raise ValueError('Invalid percentage')
            if key == 'grid':
                value = max(0, value)  # Signed active power; export does not count as import.
        except (TypeError, ValueError, IndexError):
            value = None
        if key in ROUTING_INPUTS or key in ('pv1_power', 'pv2_power'):
            route_inputs[key] = value if value is not None and now - row[1] <= 90 else None
            if key == 'bridge':
                value = route_inputs[key]
        values[key] = value
    # Constant SOC/grid states can be old. The changing inverter clock is the heartbeat.
    heartbeat = latest(connection, 'inverter_time')
    updated = (heartbeat[1] if recorded_attributes(heartbeat, now) is not None
               and heartbeat[0] not in ('unknown', 'unavailable') else None)
    fresh = updated is not None and -30 <= now - updated <= 90
    mode = latest(connection, 'operating_mode')
    known_modes = ('Power On', 'Standby', 'Mains', 'Off-Grid', 'Bypass', 'Charging', 'Fault')
    mode_value = mode[0] if recorded_attributes(mode, now) is not None and mode[0] in known_modes else None
    # A held zero import agrees with the inverter's Off-Grid operating mode.
    if fresh and values['grid'] == 0 and mode_value == 'Off-Grid':
        route_inputs['grid'] = 0
    # A held aggregate zero alone cannot prove an absence of PV. Both channels
    # must independently confirm zero in the current observation window.
    if fresh and values['pv'] == route_inputs['pv1_power'] == route_inputs['pv2_power'] == 0:
        route_inputs['pv'] = 0
    grid, bridge, load, battery, pv = (route_inputs[key] for key in ROUTING_INPUTS)
    values['grid'] = grid
    house, charging = estimate_grid_split(grid, bridge, load, battery, pv)
    values['grid_house'], values['grid_battery'] = house, charging
    values['solar_house'], values['battery_house'] = estimate_house_sources(house, charging, bridge, load, battery, pv)
    values['solar_battery'] = estimate_solar_charging(charging, battery, pv)
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
