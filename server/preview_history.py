"""Loopback-only UI preview with synthetic history, never production readings."""
import json
import hashlib
import math
import re
from functools import lru_cache
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from energy_summary import ATHENS, combine_days, summarize_points

ROOT = Path(__file__).resolve().parents[1]


def preview_days(now):
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    for _ in range(2):
        start = (start - timedelta(days=1)).replace(day=1)
    days = []
    while start.date() <= now.date():
        # Whole-day gaps demonstrate why recorded coverage is not always 100%.
        if start.day % 17 or start.date() == now.date():
            days.append(start.strftime('%Y-%m-%d'))
        start += timedelta(days=1)
    return days


def preview_day(day, cutoff):
    requested = datetime.strptime(day, '%Y-%m-%d').replace(tzinfo=ATHENS)
    start = int(requested.timestamp())
    end = int((requested + timedelta(days=1)).timestamp())
    points = []
    for stamp in range(start, min(end, int(cutoff) + 1), 60):
        local = datetime.fromtimestamp(stamp, ATHENS)
        minute = local.hour * 60 + local.minute
        if 600 <= minute < 620:
            continue
        solar = max(0, math.sin((minute - 360) / 720 * math.pi)) * (2400 + requested.day * 15) if 360 < minute < 1080 else 0
        load = 700 + math.sin(minute / 30) * 100
        battery = solar - load if solar else -load if 1080 <= minute < 1320 else 0
        grid = load if not solar and not battery else 0
        values = {'pv': solar, 'grid': grid, 'load': load, 'battery': battery,
                  'soc': 50 + math.sin(minute / 240) * 20, 'pv_voltage': 200 if solar else 30}
        for channel, share, voltage in [(1, .6, 382.9), (2, .4, 237.2)]:
            power = solar * share
            values.update({f'pv{channel}_power': power, f'pv{channel}_voltage': voltage if solar else 30,
                           f'pv{channel}_current': power / voltage})
        values.update(grid_voltage=232, battery_current=battery / 52, load_current=load / 230, pv_current=solar / 300)
        points.append({'t': stamp, 'values': values, 'counts': dict.fromkeys(values, 1)})
    boundaries = [(0, 360, 'grid'), (360, 450, 'mixed'), (450, 600, 'solar'),
                  (620, 720, 'solar'), (780, 990, 'solar'), (990, 1080, 'mixed'),
                  (1080, 1320, 'battery'), (1320, (end - start) / 60, 'grid')]
    boundaries.extend((720 + i / 4, 720 + (i + 1) / 4, 'solar' if i % 2 else 'mixed') for i in range(240))
    boundaries.sort()
    modes = [{'start': start + a * 60, 'end': min(start + b * 60, cutoff + 90), 'state': state}
             for a, b, state in boundaries if start + a * 60 < cutoff]
    return {'day': day, 'points': points, 'modes': modes}


@lru_cache(maxsize=128)
def preview_summary(day, cutoff):
    return {'day': day, **summarize_points(preview_day(day, cutoff)['points'], cutoff)}


@lru_cache(maxsize=32)
def preview_body(day, cutoff):
    body = json.dumps(preview_day(day, cutoff), allow_nan=False, separators=(',', ':')).encode()
    return body, '"' + hashlib.sha256(body).hexdigest() + '"'


CACHEABLE_ASSETS = {'styles.css', 'app.js', 'theme.js', 'theme.css', 'history.js',
                    'history-cache.js', 'history-calendar.js', 'history.css', '404.css', '404.js',
                    'power-history.js', 'power-history.css'}


class Preview(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def end_headers(self):
        parsed = urlsplit(self.path)
        version = parse_qs(parsed.query).get('v', [''])[0]
        versioned_asset = parsed.path[1:] in CACHEABLE_ASSETS and re.fullmatch(r'[A-Za-z0-9_-]+', version)
        self.send_header('Cache-Control', 'public, max-age=31536000, immutable'
                         if versioned_asset and getattr(self, 'response_status', 0) in (200, 304)
                         else 'no-store, max-age=0')
        super().end_headers()

    def send_response(self, code, message=None):
        self.response_status = code
        super().send_response(code, message)

    def do_GET(self):
        now = datetime.now(ATHENS)
        cutoff = int(now.timestamp() // 5) * 5
        days = preview_days(now)
        route = urlsplit(self.path).path
        if route == '/history/index.json':
            summaries = []
            for day in days:
                end = (datetime.strptime(day, '%Y-%m-%d').replace(tzinfo=ATHENS) + timedelta(days=1)).timestamp()
                summaries.append(preview_summary(day, min(cutoff, end)))
            start = summaries[0]['from']
            payload = {'days': days, 'updated_at': cutoff, 'mode_recorded_from': start,
                       'energy_summary': combine_days(summaries, cutoff)}
        elif route in ['/history/' + day + '.json' for day in days]:
            day = route.split('/')[-1][:-5]
            end = int((datetime.strptime(day, '%Y-%m-%d').replace(tzinfo=ATHENS) + timedelta(days=1)).timestamp())
            body, etag = preview_body(day, min(cutoff, end))
            if self.headers.get('If-None-Match') == etag:
                self.send_response(304)
                self.send_header('ETag', etag)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('ETag', etag)
            self.end_headers()
            self.wfile.write(body)
            return
        else:
            return super().do_GET()
        body = json.dumps(payload, allow_nan=False, separators=(',', ':')).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(body)


if __name__ == '__main__':
    ThreadingHTTPServer(('127.0.0.1', 8766), Preview).serve_forever()
