"""Produce synthetic Recorder snapshots for the Overview browser regression."""
import json

from export_energy import CHANNEL_FIELDS, FIELDS, PREFIX, PV_PREFIX, snapshot
from test_export_energy import SnapshotTests


START = 1_800_000_000
DAY = dict(grid=0, bridge=569, load=657, battery=-37, pv=482, pv1_power=426, pv2_power=16)
NIGHT = dict(grid=900, bridge=-300, load=580, battery=400, pv=0, pv1_power=0, pv2_power=0)


def packets():
    recorder = SnapshotTests()
    recorder.setUp()
    try:
        db = recorder.db
        db.execute('ALTER TABLE states ADD COLUMN last_reported_ts REAL')
        recorder.add_flow_helper({'reports': {}}, timestamp=START)
        db.execute('INSERT INTO states_meta VALUES(102,?)', (PREFIX + 'operating_mode',))
        db.execute("INSERT INTO states(metadata_id,state,last_updated_ts) VALUES(102,'Mains',?)", (START,))
        reports = {}

        def read(seconds, changes=None, *, silent=(), connected=True):
            now = START + seconds
            for key, value in (changes or {}).items():
                state = str(value)
                if key not in reports or reports[key][0] != state:
                    recorder.set_readings(**{key: value})
                    recorder.set_time(key, now)
                    reports[key] = [state, now, now]
            if connected:
                for key, observation in reports.items():
                    if key not in silent:
                        observation[2] = now
                by_entity = {(PV_PREFIX if key in CHANNEL_FIELDS else PREFIX) + FIELDS[key][0]: report
                             for key, report in reports.items() if report[0] not in ('unknown', 'unavailable')}
                db.execute('UPDATE state_attributes SET shared_attrs=? WHERE attributes_id=201',
                           (json.dumps({'reports': by_entity}),))
                db.execute('UPDATE states SET last_updated_ts=? WHERE metadata_id IN (101,201)', (now,))
            return snapshot(db, now)

        return {
            'initial': read(0, DAY),
            'steady': read(600),
            'silentBattery': read(696, silent=('battery',)),
            'unavailableBattery': read(701, {'battery': 'unavailable'}),
            'independentGrid': read(706, {'grid': 900, 'bridge': -300, 'load': 580}),
            'night': read(711, NIGHT),
            'heldZero': read(1311),
            'linkLost': read(1411, connected=False),
            'recovered': read(1416, DAY),
        }
    finally:
        recorder.doCleanups()


if __name__ == '__main__':
    print(json.dumps(packets(), allow_nan=False))
