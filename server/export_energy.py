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
FLOW_OBSERVATIONS_ENTITY = 'sensor.home_energy_flow_observations'
FRESH_SECONDS = 90
MAINS_CHARGING_BIT = 1 << 8
PV_CHARGING_BIT = 1 << 9
DISCREPANCY_SECONDS = 120
MAX_EVIDENCE_GAP = 45  # The observation helper samples every 30 seconds.


def latest(connection, suffix, prefix=PREFIX, *, reported_time=False):
    stamp = 'COALESCE(NULLIF(s.last_reported_ts,0),s.last_updated_ts)' if reported_time else 's.last_updated_ts'
    return connection.execute(
        f'SELECT s.state,{stamp},a.shared_attrs,s.last_updated_ts FROM states s '
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


def flow_observations(connection, now):
    row = latest(connection, FLOW_OBSERVATIONS_ENTITY, prefix='')
    attributes = recorded_attributes(row, now)
    if attributes is None or row[0] != '1' or now - row[1] > FRESH_SECONDS:
        return {}, None
    reports = attributes.get('reports')
    observations = dict(reports) if isinstance(reports, dict) else {}
    charging = attributes.get('charging_reports')
    for suffix in ('power_flow_status', 'inverter_charging_power'):
        entity = PREFIX + suffix
        observations[entity] = charging.get(entity) if isinstance(charging, dict) else None
    return observations, row[1]


def flow_report_time(row, observation, sampled_at, now):
    """Latest fresh report of this specific Recorder state and revision."""
    direct = row[1] if now - row[1] <= FRESH_SECONDS else None
    # Recorder may leave the open row untouched when EyeBond repeats a value.
    # Only reuse it with a live report for the same entity, state and revision.
    if sampled_at is None or not isinstance(observation, list) or len(observation) != 3:
        return direct
    state, updated, reported = observation
    if state != row[0] or type(updated) not in (int, float) or type(reported) not in (int, float):
        return direct
    if (0 <= updated <= reported <= sampled_at + 0.000001
            and now - reported <= FRESH_SECONDS
            and isinstance(row[3], (int, float)) and abs(updated - row[3]) <= 0.000001):
        return reported if direct is None else max(direct, reported)
    return direct


def charging_observation(connection, suffix, unit, reported_time, observations, sampled_at, now):
    row = latest(connection, suffix, reported_time=reported_time)
    attrs = recorded_attributes(row, now)
    if attrs is None or attrs.get('unit_of_measurement') != unit:
        return None, None
    stamp = flow_report_time(row, observations.get(PREFIX + suffix), sampled_at, now)
    if stamp is None:
        return None, None
    try:
        value = float(row[0])
    except (TypeError, ValueError):
        return None, None
    if not math.isfinite(value) or value < 0:
        return None, None
    if suffix == 'power_flow_status':
        if not value.is_integer() or value > 0xffff:
            return None, None
        value = int(value)
    return value, stamp


def estimate_grid_house(grid_import, bridge_w, load):
    """Bound grid-to-house independently of battery charging and converter losses."""
    if grid_import == 0 or load == 0:
        return 0
    if bridge_w is None or load is None or load < 0:
        return None
    if load <= max(0, bridge_w):
        return 0
    if grid_import is None:
        return None
    return min(max(0, grid_import - max(0, -bridge_w)), load - max(0, bridge_w))


def estimate_charging(grid, bridge, battery, pv, mains, solar, ac_charger_w=None):
    """Return grid/PV charge estimates and battery watts unexplained by the active sources."""
    pv_limit = 0 if solar is False else pv if pv is not None and pv >= 0 else None
    ac_limit = None
    if mains is False or grid == 0 or (bridge is not None and bridge >= 0):
        ac_limit = 0
    elif grid is not None and bridge is not None:
        ac_limit = min(grid, -bridge)
    if mains and solar and ac_charger_w is not None and ac_limit is not None:
        # The charger reads converter power. Losses can reduce what reaches the battery.
        ac_limit = min(ac_limit, ac_charger_w)
    flags_known = mains is not None and solar is not None
    if battery is not None and battery <= 0:
        return 0, 0, 0 if flags_known else None
    grid_charge = 0 if ac_limit == 0 else None
    solar_charge = 0 if pv_limit == 0 else None
    if battery is None:
        return grid_charge, solar_charge, None
    if mains and ac_limit is not None and ac_limit > 0:
        if solar is False or pv_limit == 0 or ac_charger_w is not None:
            grid_charge = min(ac_limit, battery)
        elif pv_limit is not None and battery > pv_limit:
            grid_charge = min(ac_limit, battery - pv_limit)
        # With both sources active and enough PV, watts alone cannot establish their share.
    if solar is not False and pv_limit is not None and grid_charge is not None:
        solar_charge = min(pv_limit, battery - grid_charge)
    discrepancy = None
    if flags_known and ac_limit is not None and pv_limit is not None:
        discrepancy = max(0, battery - pv_limit - ac_limit)
    return grid_charge, solar_charge, discrepancy


def estimate_house_sources(grid_house, solar_charging, bridge_w, load, battery, pv):
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
    if pv is not None and pv >= 0 and solar_charging is not None:
        solar = min(max(0, pv - solar_charging), budget - discharge)
    return solar, discharge


class ChargingDiscrepancy:
    """Track consecutive credible source conflicts, independently of the exporter polling rate."""
    def __init__(self):
        self.reset()

    def reset(self):
        self.since = self.last_report = self.flags = None

    def observe(self, *, stamp, now, flags, watts, battery):
        if (stamp is None or now - stamp > MAX_EVIDENCE_GAP or stamp > now
                or watts is None or battery is None or watts < max(150, 0.1 * battery)):
            self.reset()
            return False
        if (flags != self.flags or self.last_report is None
                or not 0 <= stamp - self.last_report <= MAX_EVIDENCE_GAP):
            self.since = stamp
        self.flags, self.last_report = flags, stamp
        return stamp - self.since >= DISCREPANCY_SECONDS


def snapshot(connection, now, tracker=None):
    values = {}
    route_inputs = {}
    stamps = {}
    observations, sampled_at = flow_observations(connection, now)
    has_reported_time = connection.execute(
        "SELECT 1 FROM pragma_table_info('states') WHERE name='last_reported_ts'"
    ).fetchone() is not None
    for key, (suffix, unit) in FIELDS.items():
        prefix = PV_PREFIX if key in CHANNEL_FIELDS else PREFIX
        row = latest(connection, suffix, prefix, reported_time=has_reported_time)
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
            stamps[key] = (flow_report_time(row, observations.get(prefix + suffix), sampled_at, now)
                           if value is not None else None)
            route_inputs[key] = value if stamps[key] is not None else None
            if key == 'bridge':
                value = route_inputs[key]
        values[key] = value
    # Constant SOC/grid states can be old. The changing inverter clock is the heartbeat.
    heartbeat = latest(connection, 'inverter_time')
    updated = (heartbeat[1] if recorded_attributes(heartbeat, now) is not None
               and heartbeat[0] not in ('unknown', 'unavailable') else None)
    fresh = updated is not None and -30 <= now - updated <= FRESH_SECONDS
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
        if stamps['pv'] is None:
            stamps['pv'] = min(stamps['pv1_power'], stamps['pv2_power'])
    status, status_report = charging_observation(connection, 'power_flow_status', None, has_reported_time,
                                                  observations, sampled_at, now)
    mains = bool(status & MAINS_CHARGING_BIT) if fresh and status is not None else None
    solar = bool(status & PV_CHARGING_BIT) if fresh and status is not None else None
    charger = charger_report = None
    if mains and solar:
        charger, charger_report = charging_observation(connection, 'inverter_charging_power', 'W', has_reported_time,
                                                       observations, sampled_at, now)
    grid, bridge, load, battery, pv = (route_inputs[key] for key in ROUTING_INPUTS)
    values['grid'] = grid
    house = estimate_grid_house(grid, bridge, load)
    charging, pv_charging, discrepancy = estimate_charging(grid, bridge, battery, pv, mains, solar, charger)
    values.update(grid_house=house, grid_battery=charging, solar_battery=pv_charging)
    values['solar_house'], values['battery_house'] = estimate_house_sources(house, pv_charging, bridge, load, battery, pv)
    evidence = [updated, status_report, stamps['battery']]
    if solar:
        evidence.append(stamps['pv'])
    if mains:
        evidence.extend((stamps['grid'], stamps['bridge']))
    if charger_report is not None:
        evidence.append(charger_report)
    stamp = min(evidence) if None not in evidence else None
    persistent = tracker.observe(stamp=stamp, now=now, flags=(mains, solar),
                                 watts=discrepancy, battery=battery) if tracker is not None else False
    routing = dict(mains_active=mains, solar_active=solar, discrepancy_w=discrepancy, persistent=persistent)
    return {'values': values, 'charging_routing': routing, 'mode': mode_value, 'updated_at': updated, 'generated_at': now,
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
    tracker = ChargingDiscrepancy()
    while True:
        try:
            connection = sqlite3.connect(DATABASE.as_uri() + '?mode=ro', uri=True, timeout=2)
            try:
                connection.execute('PRAGMA query_only=ON')
                connection.execute('BEGIN')
                payload = snapshot(connection, time.time(), tracker)
            finally:
                connection.close()
            previous_error = None
        except (sqlite3.Error, OSError, ValueError) as error:
            if type(error).__name__ != previous_error:
                logging.error('Energy source unavailable: %s', type(error).__name__)
            previous_error = type(error).__name__
            tracker.reset()
            payload = {'values': dict.fromkeys(SNAPSHOT_KEYS), 'updated_at': None,
                       'generated_at': time.time(), 'status': 'offline',
                       'charging_routing': dict(mains_active=None, solar_active=None,
                                                discrepancy_w=None, persistent=False)}
        payload['health'] = health.snapshot(time.time(), payload)
        publish(payload)
        try:
            record(payload, OUTPUT.parent)
        except (sqlite3.Error, OSError, ValueError):
            logging.exception('History recording failed')
        time.sleep(5)


if __name__ == '__main__':
    main()
