"""Loopback-only connection preview. Synthetic readings/history/reports; no upstream."""
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import argparse
import json
from pathlib import Path
import re
from urllib.parse import parse_qs, urlsplit

from connection_health import HealthMonitor
from export_energy import FIELDS
from preview_history import ATHENS, preview_days, preview_day, preview_reports, preview_summary, preview_trends, combine_days

ROOT = Path(__file__).resolve().parents[1]
STATIC = {p.relative_to(ROOT).as_posix() for pattern in ('*.js', '*.css', '*.html', 'themes/**/*.js', 'themes/**/*.css', 'assets/solar-home.webp') for p in ROOT.glob(pattern)}
SCENARIOS = ('live', 'vpn', 'dongle', 'inverter', 'browser', 'service', 'unknown', 'empty', 'partial', 'collector')


def energy(scenario, now):
    values = dict(grid=0, pv=1580, load=910, battery=670, soc=84, battery_voltage=53.2,
                  pv_voltage=382.9, grid_voltage=232, pv_current=4.1, battery_current=12.7, load_current=4.5)
    for channel, power, voltage, current in ((1, 948, 382.9, 2.5), (2, 632, 237.2, 2.7)):
        values.update({f'pv{channel}_power': power, f'pv{channel}_voltage': voltage, f'pv{channel}_current': current})
    fresh = scenario in ('live', 'partial')
    if scenario == 'partial':
        values['battery_voltage'] = None
    if scenario == 'empty':
        values = dict.fromkeys(FIELDS)
    payload = dict(values=values, mode='Off-Grid', updated_at=now - 3 if fresh else None if scenario == 'empty' else now - 900,
                   generated_at=now, status='partial' if scenario == 'partial' else 'live' if fresh else 'stale')
    observation = dict(vpn='up', dongle='up', collector='up')
    if scenario == 'vpn':
        observation = dict(vpn='down', dongle='unknown', collector='unknown')
    elif scenario == 'dongle':
        observation['dongle'] = 'down'
    elif scenario == 'collector':
        observation.update(dongle='unknown', collector='down')
    elif scenario in ('empty', 'unknown'):
        observation = dict.fromkeys(observation, 'unknown')
    monitor = HealthMonitor()
    for _ in range(3):
        monitor.accept(observation, now - 10)
    payload['health'] = monitor.snapshot(now, payload)
    return payload


CONTROLS = r"""(() => {
  const params = new URLSearchParams(location.search);
  let scenario = params.get('state') || 'inverter';
  const originalFetch = window.fetch.bind(window);
  const actualOnline = Object.getOwnPropertyDescriptor(Navigator.prototype, 'onLine').get;
  Object.defineProperty(navigator, 'onLine', { configurable: true, get: () => scenario === 'browser' ? false : actualOnline.call(navigator) });
  window.fetch = (url, options) => {
    const parsed = new URL(typeof url === 'string' ? url : url.url, location.href);
    if (parsed.pathname === '/api/energy') {
      if (scenario === 'browser') return Promise.reject(new TypeError('Synthetic offline preview'));
      parsed.searchParams.set('state', scenario);
      return originalFetch(parsed, options);
    }
    return originalFetch(url, options);
  };
  const labels = {
    en: ['LOCAL PREVIEW · Synthetic readings', 'Connection scenario', 'Live / recovered', 'VPN unreachable', 'Dongle disconnected', 'Inverter data delayed', 'Viewer offline', 'Website request failed', 'Cause unknown', 'No readings yet', 'Partial readings', 'Listener unavailable'],
    el: ['ΤΟΠΙΚΗ ΠΡΟΕΠΙΣΚΟΠΗΣΗ · Υποθετικές μετρήσεις', 'Σενάριο σύνδεσης', 'Ζωντανά / επανασύνδεση', 'VPN μη προσβάσιμο', 'Προσαρμογέας εκτός σύνδεσης', 'Καθυστέρηση δεδομένων', 'Επισκέπτης εκτός δικτύου', 'Αποτυχία αιτήματος', 'Άγνωστη αιτία', 'Χωρίς μετρήσεις', 'Μερικές μετρήσεις', 'Υπηρεσία λήψης μη διαθέσιμη'],
    ru: ['ЛОКАЛЬНЫЙ ПРЕДПРОСМОТР · Условные показания', 'Сценарий соединения', 'Связь восстановлена', 'VPN недоступен', 'Адаптер отключён', 'Задержка данных', 'Посетитель не в сети', 'Ошибка запроса', 'Причина неизвестна', 'Показаний пока нет', 'Часть показаний', 'Сервис приёма недоступен'],
  };
  document.addEventListener('DOMContentLoaded', () => {
    const select = document.querySelector('#preview-state');
    select.value = scenario;
    function translate() {
      const l = labels[document.documentElement.lang] || labels.en;
      document.querySelector('#preview-caption').textContent = l[0];
      document.querySelector('label[for=preview-state]').textContent = l[1];
      [...select.options].forEach((option, i) => { option.textContent = l[i + 2]; });
    }
    select.addEventListener('change', () => {
      scenario = select.value;
      const url = new URL(location.href); url.searchParams.set('state', scenario); history.replaceState(null, '', url);
      window.dispatchEvent(new Event(scenario === 'browser' ? 'offline' : 'online'));
    });
    window.addEventListener('languagechange', translate);
    translate();
  });
})();"""
TOOLBAR_CSS = '#connection-preview{display:flex;align-items:center;justify-content:center;flex-wrap:wrap;gap:8px 16px;padding:12px 20px;background:var(--theme-elevated);color:var(--theme-text);font-size:12px}#connection-preview label{font-size:12px}#preview-state{max-width:100%;min-height:44px;padding:8px;background:var(--theme-surface);color:var(--theme-text);border:1px solid var(--theme-border);border-radius:6px}'


class Preview(BaseHTTPRequestHandler):
    def respond(self, body, mime='application/json', status=200):
        self.send_response(status)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlsplit(self.path)
        route = parsed.path
        now = datetime.now(ATHENS)
        stamp = now.timestamp()
        if route == '/__preview__/controls.js':
            return self.respond(CONTROLS.encode(), 'text/javascript; charset=utf-8')
        if route == '/__preview__/controls.css':
            return self.respond(TOOLBAR_CSS.encode(), 'text/css; charset=utf-8')
        if route == '/api/energy':
            scenario = parse_qs(parsed.query).get('state', ['inverter'])[0]
            if scenario not in SCENARIOS:
                return self.respond(b'{}', status=400)
            if scenario == 'service':
                return self.respond(b'{}', status=503)
            return self.respond(json.dumps(energy(scenario, stamp), allow_nan=False).encode())
        if route == '/reports/index.json':
            return self.respond(json.dumps(preview_reports(stamp)).encode())
        if route == '/history/index.json':
            days = preview_days(now)
            summaries = [preview_summary(day, stamp) for day in days]
            summary = combine_days(summaries, stamp)
            summary['trends'] = preview_trends(now.strftime('%Y-%m-%d'))
            return self.respond(json.dumps(dict(days=days, updated_at=stamp, energy_summary=summary)).encode())
        if re.fullmatch(r'/history/\d{4}-\d{2}-\d{2}\.json', route):
            try:
                return self.respond(json.dumps(preview_day(route[-15:-5], stamp)).encode())
            except ValueError:
                return self.respond(b'{}', status=404)
        name = 'index.html' if route == '/' else route[1:]
        if name not in STATIC:
            return self.respond(b'{}', status=404)
        body = (ROOT / name).read_bytes()
        if name == 'index.html':
            html = body.decode('utf-8').replace('</head>', '<link rel="stylesheet" href="/__preview__/controls.css"><script src="/__preview__/controls.js"></script></head>')
            options = ''.join(f'<option value="{state}">{state}</option>' for state in SCENARIOS)
            toolbar = '<aside id="connection-preview"><strong id="preview-caption"></strong><label for="preview-state"></label><select id="preview-state">' + options + '</select></aside>'
            html = html.replace('<body class="overview">', '<body class="overview">' + toolbar)
            html = re.sub(r'<script src="collector-control\.js[^>]+></script>', '', html)
            html = html.replace('<button id="restart-dongle"', '<button hidden disabled id="restart-dongle"')
            body = html.encode('utf-8')
        mime = {'.html': 'text/html', '.css': 'text/css', '.js': 'text/javascript', '.webp': 'image/webp'}[Path(name).suffix]
        return self.respond(body, mime)

    def log_message(self, *_):
        pass


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8778)
    args = parser.parse_args()
    print(f'Local synthetic connection preview: http://127.0.0.1:{args.port}/', flush=True)
    ThreadingHTTPServer(('127.0.0.1', args.port), Preview).serve_forever()
