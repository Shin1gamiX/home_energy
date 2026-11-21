"""Synthetic Home Assistant Recorder for local previews and browser regressions."""
from contextlib import closing
import json
import sqlite3

from export_energy import (CHANNEL_FIELDS, FIELDS, FLOW_OBSERVATIONS_ENTITY, PREFIX, PV_PREFIX,
                           ROUTING_INPUTS, ChargingDiscrepancy, snapshot)


class SyntheticRecorder:
    """Keep unchanged states on their original row, as Recorder does."""

    def __init__(self):
        self.db = sqlite3.connect(':memory:')
        self.tracker = ChargingDiscrepancy()
        self.db.executescript("""
            CREATE TABLE states_meta(metadata_id INTEGER PRIMARY KEY, entity_id TEXT UNIQUE);
            CREATE TABLE state_attributes(attributes_id INTEGER PRIMARY KEY, shared_attrs TEXT);
            CREATE TABLE states(metadata_id INTEGER PRIMARY KEY, state TEXT, last_updated_ts REAL,
                                attributes_id INTEGER, last_reported_ts REAL);
        """)
        entities = {key: ((PV_PREFIX if key in CHANNEL_FIELDS else PREFIX) + suffix, unit)
                    for key, (suffix, unit) in FIELDS.items()}
        entities.update(power_flow_status=(PREFIX + 'power_flow_status', None),
                        inverter_charging_power=(PREFIX + 'inverter_charging_power', 'W'),
                        heartbeat=(PREFIX + 'inverter_time', None), mode=(PREFIX + 'operating_mode', None),
                        helper=(FLOW_OBSERVATIONS_ENTITY, None))
        self.ids = {}
        self.names = {}
        self.reports = {}
        for index, (key, (entity, unit)) in enumerate(entities.items(), 1):
            self.ids[key], self.names[key] = index, entity
            self.db.execute('INSERT INTO states_meta VALUES(?,?)', (index, entity))
            attrs = {'unit_of_measurement': unit} if unit else {}
            self.db.execute('INSERT INTO state_attributes VALUES(?,?)', (index, json.dumps(attrs)))
            state = {'soc': '84', 'mode': 'Mains', 'helper': '1', 'heartbeat': '00:00:00'}.get(key, '0')
            self.db.execute('INSERT INTO states VALUES(?,?,0,?,NULL)', (index, state, index))

    def close(self):
        self.db.close()

    def read(self, stamp, changes=None, *, silent=(), connected=True):
        for key, value in (changes or {}).items():
            state = str(value)
            if key not in self.reports or self.reports[key][0] != state:
                self.db.execute('UPDATE states SET state=?,last_updated_ts=? WHERE metadata_id=?',
                                (state, stamp, self.ids[key]))
                self.reports[key] = [state, stamp, stamp]
        if connected:
            attributes = {'reports': {}, 'charging_reports': {}}
            for key, observation in self.reports.items():
                if key not in silent:
                    observation[2] = stamp
                if observation[0] in ('unknown', 'unavailable'):
                    continue
                if key in (*ROUTING_INPUTS, 'pv1_power', 'pv2_power'):
                    attributes['reports'][self.names[key]] = observation
                elif key in ('power_flow_status', 'inverter_charging_power'):
                    attributes['charging_reports'][self.names[key]] = observation
            self.db.execute('UPDATE state_attributes SET shared_attrs=? WHERE attributes_id=?',
                            (json.dumps(attributes), self.ids['helper']))
            self.db.execute('UPDATE states SET last_updated_ts=? WHERE metadata_id IN (?,?)',
                            (stamp, self.ids['helper'], self.ids['heartbeat']))
        return snapshot(self.db, stamp, self.tracker)


def packets():
    start = 1_800_000_000
    day = dict(grid=0, bridge=569, load=657, battery=-37, pv=482, pv1_power=426, pv2_power=16,
               power_flow_status=85, inverter_charging_power=0)
    night = dict(grid=900, bridge=-300, load=580, battery=400, pv=0, pv1_power=0, pv2_power=0,
                 power_flow_status=341, inverter_charging_power=300)
    with closing(SyntheticRecorder()) as recorder:
        return {
            'initial': recorder.read(start, day),
            'steady': recorder.read(start + 600),
            'silentBattery': recorder.read(start + 696, silent=('battery',)),
            'unavailableBattery': recorder.read(start + 701, {'battery': 'unavailable'}),
            'independentGrid': recorder.read(start + 706, {'grid': 900, 'bridge': -300, 'load': 580,
                                                           'power_flow_status': 341}),
            'night': recorder.read(start + 711, night),
            'heldZero': recorder.read(start + 1311),
            'linkLost': recorder.read(start + 1411, connected=False),
            'recovered': recorder.read(start + 1416, day),
        }


if __name__ == '__main__':
    print(json.dumps(packets(), allow_nan=False))
