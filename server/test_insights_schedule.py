"""Offline checks for the approved Windows schedule; never register or run it."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import unittest
import xml.etree.ElementTree as ET


PATH = Path(__file__).resolve().parents[1] / 'deploy/homeenergy-reports-task.xml'
NS = {'t': 'http://schemas.microsoft.com/windows/2004/02/mit/task'}


class ScheduleTests(unittest.TestCase):
    def setUp(self):
        self.root = ET.fromstring(PATH.read_bytes())

    def text(self, path):
        return self.root.findtext(path, namespaces=NS)

    def test_registration_never_enables_or_runs(self):
        self.assertEqual(self.text('t:Settings/t:Enabled'), 'false')
        self.assertEqual(self.text('t:Settings/t:AllowStartOnDemand'), 'false')
        self.assertEqual(self.text('t:RegistrationInfo/t:URI'), '\\Home Energy Reporter')

    def test_exactly_two_local_daily_triggers(self):
        triggers = self.root.findall('t:Triggers/*', NS)
        self.assertEqual(len(triggers), 2)
        self.assertEqual([t.findtext('t:StartBoundary', namespaces=NS) for t in triggers],
                         ['2026-10-15T00:00:00', '2026-10-15T12:00:00'])
        for trigger in triggers:
            self.assertEqual(trigger.tag, '{' + NS['t'] + '}CalendarTrigger')
            self.assertEqual(trigger.findtext('t:Enabled', namespaces=NS), 'true')
            self.assertEqual(trigger.findtext('t:ScheduleByDay/t:DaysInterval', namespaces=NS), '1')
            self.assertIsNone(trigger.find('t:Repetition', NS))
            self.assertIsNone(trigger.find('t:RandomDelay', NS))
            self.assertIsNone(datetime.fromisoformat(
                trigger.findtext('t:StartBoundary', namespaces=NS)).tzinfo)

    def test_trigger_expiry_matches_existing_grant(self):
        expected = datetime.fromtimestamp(1794556410, timezone.utc)
        for end in self.root.findall('t:Triggers/*/t:EndBoundary', NS):
            self.assertEqual(datetime.fromisoformat(end.text), expected)

    def test_no_retry_catchup_wake_or_overlap(self):
        for key in ('StartWhenAvailable', 'WakeToRun', 'RunOnlyIfIdle', 'Hidden'):
            self.assertEqual(self.text('t:Settings/t:' + key), 'false')
        self.assertEqual(self.text('t:Settings/t:RunOnlyIfNetworkAvailable'), 'true')
        self.assertEqual(self.text('t:Settings/t:MultipleInstancesPolicy'), 'IgnoreNew')
        self.assertEqual(self.text('t:Settings/t:ExecutionTimeLimit'), 'PT10M')
        self.assertIsNone(self.root.find('t:Settings/t:RestartOnFailure', NS))
        self.assertIsNone(self.root.find('t:Settings/t:DeleteExpiredTaskAfter', NS))

    def test_existing_interactive_user_without_elevation(self):
        self.assertEqual(len(self.root.findall('t:Principals/*', NS)), 1)
        self.assertEqual(self.text('t:Principals/t:Principal/t:UserId'),
                         'S-1-5-21-2042942067-679905730-4140847586-1001')
        self.assertEqual(self.text('t:Principals/t:Principal/t:LogonType'), 'InteractiveToken')
        self.assertEqual(self.text('t:Principals/t:Principal/t:RunLevel'), 'LeastPrivilege')

    def test_fixed_windowless_isolated_runner_only(self):
        self.assertEqual(len(self.root.findall('t:Actions/*', NS)), 1)
        self.assertEqual(self.text('t:Actions/t:Exec/t:Command'),
            r'C:\Users\anest\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\pythonw.exe')
        self.assertEqual(self.text('t:Actions/t:Exec/t:Arguments'),
            r'-I -B "C:\Users\anest\Documents\New project\home-energy-repo\server\insights_recurring_run.py" --run-due')
        self.assertEqual(self.text('t:Actions/t:Exec/t:WorkingDirectory'),
            r'C:\Users\anest\Documents\New project\home-energy-repo')

    def test_midnight_noon_spacing_remains_above_guard_at_dst_change(self):
        # The approved dates straddle Athens' autumn offset change. Calendar
        # triggers retain local labels; do not replace them with an elapsed loop.
        occurrences = []
        for date, hours in ((datetime(2026, 10, 24), 3),
                            (datetime(2026, 10, 25), None),
                            (datetime(2026, 10, 26), 2)):
            for hour in (0, 12):
                offset = hours if hours is not None else (3 if hour == 0 else 2)
                occurrences.append(date.replace(hour=hour, tzinfo=timezone(timedelta(hours=offset))))
        elapsed = [(b.astimezone(timezone.utc) - a.astimezone(timezone.utc)).total_seconds()
                   for a, b in zip(occurrences, occurrences[1:])]
        self.assertEqual(elapsed, [43200, 43200, 46800, 43200, 43200])
        self.assertTrue(all(value >= 21600 for value in elapsed))


if __name__ == '__main__':
    unittest.main()
