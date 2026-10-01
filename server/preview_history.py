"""Local UI preview with explicitly synthetic history, never used in production."""
import json
import math
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class Preview(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def do_GET(self):
        now = datetime.now().astimezone()
        day = now.strftime('%Y-%m-%d')
        days = [(now - timedelta(days=i)).strftime('%Y-%m-%d') for i in range(7)]
        if self.path == '/history/index.json':
            start = int((now - timedelta(days=6)).replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
            payload = {'days': sorted(days), 'updated_at': now.timestamp(), 'mode_recorded_from': start}
        elif self.path in ['/history/' + date + '.json' for date in days]:
            requested = datetime.strptime(self.path.split('/')[-1][:-5], '%Y-%m-%d').astimezone()
            start = int(requested.timestamp())
            points = []
            for i in range(1440):
                if start + i * 60 > now.timestamp():
                    break
                if 600 <= i < 620:  # Exercise missing-report gaps.
                    continue
                solar = max(0, math.sin((i - 360) / 720 * math.pi)) * 2400 if 360 < i < 1080 else 0
                values = {'pv': solar, 'grid': 300, 'load': 700 + math.sin(i / 30) * 100, 'battery': solar - 700, 'soc': 50 + math.sin(i / 240) * 20, 'pv_voltage': 200 if solar else 30}
                for channel, share, voltage in [(1, .6, 382.9), (2, .4, 237.2)]:
                    power = solar * share
                    values.update({f'pv{channel}_power': power,
                                   f'pv{channel}_voltage': voltage if solar else 30,
                                   f'pv{channel}_current': power / voltage})
                values.update(grid_voltage=232, battery_current=values['battery'] / 52,
                              load_current=values['load'] / 230, pv_current=solar / 300)
                points.append({'t': start + i * 60, 'values': values, 'counts': dict.fromkeys(values, 1)})
            boundaries = [(0, 420, 'grid'), (420, 520, 'mixed'), (520, 600, 'solar'),
                          (620, 720, 'solar'), (780, 1050, 'solar'), (1050, 1220, 'battery'), (1220, 1440, 'grid')]
            # Dense 15-second transitions exercise grouping and zoom on phones.
            boundaries.extend((720 + i / 4, 720 + (i + 1) / 4, 'solar' if i % 2 else 'mixed') for i in range(240))
            boundaries.sort()
            modes = [{'start': start + a * 60, 'end': min(start + b * 60, now.timestamp() + 90), 'state': state}
                     for a, b, state in boundaries if start + a * 60 < now.timestamp()]
            payload = {'points': points, 'modes': modes}
        else:
            return super().do_GET()
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(body)


if __name__ == '__main__':
    ThreadingHTTPServer(('127.0.0.1', 8766), Preview).serve_forever()
