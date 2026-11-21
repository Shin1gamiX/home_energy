"""Loopback-only connection and power-flow previews with synthetic data."""
from contextlib import closing
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import argparse
import json
from pathlib import Path
import re
from tempfile import TemporaryDirectory
from urllib.parse import parse_qs, urlsplit

from connection_health import HealthMonitor
from export_energy import SNAPSHOT_KEYS, estimate_charging, estimate_grid_house, estimate_house_sources
from flow_observations_fixture import SyntheticRecorder
from history_store import record
from preview_history import ATHENS, preview_days, preview_day, preview_reports, preview_summary, preview_trends, combine_days

ROOT = Path(__file__).resolve().parents[1]
STATIC = {p.relative_to(ROOT).as_posix() for pattern in ('*.js', '*.css', '*.html', 'themes/**/*.js', 'themes/**/*.css', 'assets/solar-home.webp') for p in ROOT.glob(pattern)}
SCENARIOS = ('live', 'vpn', 'dongle', 'inverter', 'browser', 'service', 'unknown', 'empty', 'partial', 'collector')
FLOW_SCENARIOS = {
    'shared-charge': dict(grid_house=1850, pv_house=0, grid_battery=2510, pv_battery=2970),
    'separate-destinations': dict(grid_house=0, pv_house=1720, grid_battery=1750, pv_battery=0),
    'grid-only': dict(grid_house=1570, pv_house=0, grid_battery=2100, pv_battery=0),
    'solar-only': dict(grid_house=0, pv_house=910, grid_battery=0, pv_battery=670),
}

# Watts. The first case replays a representative PV-only reading. The other
# cases are synthetic; all destinations come from the same estimator as live.
BRIDGE_SCENARIOS = {
    'pv-priority': dict(grid=720, bridge=-68, load=662, battery=2964, pv=3125),
    'combined-charge': dict(grid=4650, bridge=-2900, load=1675, battery=5480, pv=2960),
    'inverting': dict(grid=400, bridge=900, load=1280, battery=-180, pv=800),
    'bridge-missing': dict(grid=720, bridge=None, load=662, battery=2964, pv=3125),
}

# The default case matches the rounded readings in the reported screenshot.
# Flags 597 / 853 mean solar only / solar and mains active on this inverter.
CHARGING_SCENARIOS = ('pv-only', 'mixed-charging', 'brief-mismatch', 'persistent-mismatch', 'status-unavailable')
CHARGING_READINGS = {
    'pv-only': dict(grid=1037, bridge=-72, load=992, load_current=5, battery=1046, pv=996,
                    power_flow_status=597, inverter_charging_power=0),
    'mixed-charging': dict(grid=4657, bridge=-2900, load=1679, load_current=8.3, battery=5483, pv=2958,
                           power_flow_status=853, inverter_charging_power=2800),
    'mismatch': dict(grid=1600, bridge=-620, load=992, load_current=5, battery=1496, pv=996,
                     power_flow_status=597, inverter_charging_power=0),
}


def charging_sample(scenario):
    source = {'brief-mismatch': 'mismatch', 'persistent-mismatch': 'mismatch', 'status-unavailable': 'pv-only'}
    values = CHARGING_READINGS[source.get(scenario, scenario)].copy()
    if scenario == 'status-unavailable':
        values['power_flow_status'] = 'unavailable'
    pv = values['pv']
    pv1, pv2 = (941, 50) if pv == 996 else (round(pv * .6), pv - round(pv * .6))
    values.update(soc=37, battery_voltage=53.4, battery_current=round(values['battery'] / 53.4, 1),
                  grid_voltage=232,
                  pv_voltage=173.5, pv_current=round(pv1 / 173.5 + pv2 / 156.1, 1),
                  pv1_voltage=173.5, pv1_power=pv1, pv1_current=round(pv1 / 173.5, 1),
                  pv2_voltage=156.1, pv2_power=pv2, pv2_current=round(pv2 / 156.1, 1))
    return values


def charging_snapshot(scenario, now):
    # Replay independent fresh reports so the preview exercises the collector's
    # actual persistence timer without making the reviewer wait two minutes.
    duration = 30 if scenario == 'brief-mismatch' else 150
    with closing(SyntheticRecorder()) as recorder:
        for elapsed in range(0, duration + 1, 30):
            packet = recorder.read(now - duration + elapsed, charging_sample(scenario))
    monitor = HealthMonitor()
    monitor.accept(dict(vpn='up', dongle='up', collector='up'), now)
    packet['health'] = monitor.snapshot(now, packet)
    return packet


def charging_history_preview(today):
    start = (today - timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
    first = int(start.timestamp())
    # Six-minute blocks. The brief discrepancy lasts one minute then clears.
    block = 360
    end = first + len(CHARGING_SCENARIOS) * block
    links = {'/charging-history': f'/history.html?from={first}&to={end}&metric=grid'}
    with TemporaryDirectory(prefix='charging-history-preview-') as temporary, closing(SyntheticRecorder()) as recorder:
        runtime = Path(temporary)
        for index, scenario in enumerate(CHARGING_SCENARIOS):
            for elapsed in range(0, block, 30):
                state = 'pv-only' if scenario == 'brief-mismatch' and elapsed >= 60 else scenario
                stamp = first + index * block + elapsed
                record(recorder.read(stamp, charging_sample(state)), runtime)
        record(recorder.read(end, charging_sample('pv-only')), runtime)
        record(recorder.read(today.timestamp(), charging_sample('pv-only')), runtime)
        bodies = {'/history/' + path.name: path.read_bytes() for path in (runtime / 'history').glob('*.json')}
    return bodies, links


def bridge_readings(scenario):
    values = BRIDGE_SCENARIOS[scenario].copy()
    grid, bridge, load, battery, pv = (values[key] for key in ('grid', 'bridge', 'load', 'battery', 'pv'))
    mains, solar = {'pv-priority': (False, True), 'combined-charge': (True, True),
                    'inverting': (False, False), 'bridge-missing': (None, None)}[scenario]
    house = estimate_grid_house(grid, bridge, load)
    charging, solar_battery, _ = estimate_charging(grid, bridge, battery, pv, mains, solar,
                                                   2520 if scenario == 'combined-charge' else None)
    solar_house, battery_house = estimate_house_sources(house, solar_battery, bridge, load, battery, pv)
    values.update(grid_house=house, grid_battery=charging, solar_house=solar_house, battery_house=battery_house,
                  solar_battery=solar_battery)
    return values


def bridge_history_preview(today):
    """Exercise the recorder and its cutover in an isolated temporary database."""
    start = (today - timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
    first = int(start.timestamp())
    previous = int((start - timedelta(days=1)).timestamp())
    block = 30 * 60
    end = first + len(BRIDGE_SCENARIOS) * block
    links = {'/bridge-history': f'/history.html?from={first}&to={end}&metric=grid',
             '/previous-method': f'/history.html?from={previous}&to={previous + block}&metric=grid'}
    with TemporaryDirectory(prefix='bridge-history-preview-') as temporary:
        runtime = Path(temporary)
        legacy = dict(grid=720, grid_house=0, grid_battery=720, load=662, battery=2964, pv=3125)
        for stamp in range(previous, previous + block, 60):
            record(dict(status='live', updated_at=stamp, values=legacy), runtime)
        for index, scenario in enumerate(BRIDGE_SCENARIOS):
            values = bridge_readings(scenario)
            begin = first + index * block
            for stamp in range(begin, begin + block, 60):
                record(dict(status='partial' if values['bridge'] is None else 'live',
                            updated_at=stamp, values=values), runtime)
        # A final heartbeat closes the last minute for the two-hour selection.
        record(dict(status='partial', updated_at=end, values=values), runtime)
        # The Overview trends index uses today's Athens date. Feed it the same
        # current synthetic observation shown when the preview opens.
        record(dict(status='live', updated_at=today.timestamp(), values=bridge_readings('pv-priority')), runtime)
        bodies = {'/history/' + path.name: path.read_bytes() for path in (runtime / 'history').glob('*.json')}
    return bodies, links


def grid_history_preview(today):
    """Record matching synthetic mornings on either side of the split rollout."""
    links = {}
    with TemporaryDirectory(prefix='grid-history-preview-') as temporary:
        runtime = Path(temporary)
        for label, offset in (('legacy', 2), ('split', 1)):
            start = today.replace(hour=9, minute=0, second=0, microsecond=0) - timedelta(days=offset)
            first = int(start.timestamp())
            end = first + 2 * 3600
            links['/' + label] = f'/history.html?from={first}&to={end}&metric=grid'
            for stamp in range(first, end + 1, 60):
                # Forty minutes each of solar house supply, mixed supply and grid supply.
                elapsed = stamp - first
                if elapsed < 40 * 60:
                    house, charging, pv, load = 0, 1000, 1400, 800
                elif elapsed < 80 * 60:
                    house, charging, pv, load = 300, 500, 900, 900
                else:
                    house, charging, pv, load = 900, 0, 0, 900
                battery = house + charging + pv - load
                values = dict(grid=house + charging, battery=battery, pv=pv, load=load,
                              soc=60, battery_voltage=53, grid_voltage=230)
                if label == 'split':
                    values.update(grid_house=house, grid_battery=charging)
                record(dict(status='live', updated_at=stamp, values=values), runtime)
        bodies = {'/history/' + path.name: path.read_bytes() for path in (runtime / 'history').glob('*.json')}
    links['/'] = links['/split']
    return bodies, links


def energy(scenario, now):
    if scenario in CHARGING_SCENARIOS:
        return charging_snapshot(scenario, now)
    values = dict(grid=0, grid_house=0, grid_battery=0, pv=1580, load=910, battery=670, soc=84, battery_voltage=53.2,
                  pv_voltage=382.9, grid_voltage=232, pv_current=4.1, battery_current=12.7, load_current=4.5)
    for channel, power, voltage, current in ((1, 948, 382.9, 2.5), (2, 632, 237.2, 2.7)):
        values.update({f'pv{channel}_power': power, f'pv{channel}_voltage': voltage, f'pv{channel}_current': current})
    if scenario in BRIDGE_SCENARIOS:
        values.update(bridge_readings(scenario))
    elif scenario in FLOW_SCENARIOS:
        flow = FLOW_SCENARIOS[scenario]
        pv = flow['pv_house'] + flow['pv_battery']
        battery = flow['grid_battery'] + flow['pv_battery']
        load = flow['grid_house'] + flow['pv_house']
        values.update(grid=flow['grid_house'] + flow['grid_battery'], grid_house=flow['grid_house'],
                      grid_battery=flow['grid_battery'], pv=pv, battery=battery, load=load)
    flow_preview = scenario in FLOW_SCENARIOS or scenario in BRIDGE_SCENARIOS
    if flow_preview:
        pv = values['pv']
        values.update(soc=56, battery_voltage=54.4, battery_current=values['battery'] / 54.4,
                      load_current=values['load'] / 232, pv_voltage=225.5 if pv else 24.5, pv_current=pv / 225.5)
        for channel, share in ((1, .6), (2, .4)):
            power = pv * share
            values.update({f'pv{channel}_power': power, f'pv{channel}_voltage': values['pv_voltage'],
                           f'pv{channel}_current': power / values['pv_voltage']})
    fresh = scenario in ('live', 'partial') or flow_preview
    if scenario == 'partial':
        values['battery_voltage'] = None
    if scenario == 'empty':
        values = dict.fromkeys(SNAPSHOT_KEYS)
    payload = dict(values=values, mode='Mains' if values['grid'] else 'Off-Grid', updated_at=now - 3 if fresh else None if scenario == 'empty' else now - 900,
                   generated_at=now, status='partial' if scenario in ('partial', 'bridge-missing') else 'live' if fresh else 'stale')
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
  const defaultState = document.currentScript.dataset.defaultState;
  let scenario = params.get('state') || defaultState;
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
    const flowLabels = {
      en: ['Power flow', 'Grid + solar charge the battery; grid powers the house', 'Grid charges the battery; solar powers the house', 'Grid alone supplies both', 'Solar alone supplies both'],
      el: ['Ροή ενέργειας', 'Δίκτυο και ηλιακά φορτίζουν την μπαταρία · δίκτυο προς σπίτι', 'Δίκτυο προς μπαταρία · ηλιακά προς σπίτι', 'Μόνο δίκτυο για σπίτι και μπαταρία', 'Μόνο ηλιακά για σπίτι και μπαταρία'],
      ru: ['Потоки энергии', 'Сеть и солнце заряжают батарею · дом питается от сети', 'Сеть заряжает батарею · дом питается от солнца', 'Только сеть питает дом и заряжает батарею', 'Только солнце питает дом и заряжает батарею'],
    };
    const bridgeLabels = {
      en: ['Power flow', 'PV charges the battery', 'Grid and solar charge together', 'Solar and battery supply the house', 'Inverter power unavailable'],
      el: ['Ροή ενέργειας', 'Φόρτιση από τα φωτοβολταϊκά', 'Φόρτιση από δίκτυο και φωτοβολταϊκά', 'Τροφοδοσία σπιτιού από φωτοβολταϊκά και μπαταρία', 'Χωρίς μέτρηση ισχύος αντιστροφέα'],
      ru: ['Поток энергии', 'Батарея заряжается от солнца', 'Зарядка от сети и солнца', 'Дом питается от солнца и батареи', 'Нет показаний мощности инвертора'],
    };
    const chargingLabels = {
      en: ['Charging scenario', 'PV only · screenshot readings', 'Grid and solar charging together', 'Brief 500 W difference', 'Persistent 500 W difference', 'Charging status unavailable'],
      el: ['Σενάριο φόρτισης', 'Μόνο φωτοβολταϊκά · τιμές στιγμιότυπου', 'Ταυτόχρονη φόρτιση από δίκτυο και ήλιο', 'Στιγμιαία απόκλιση 500 W', 'Παρατεταμένη απόκλιση 500 W', 'Άγνωστη κατάσταση φόρτισης'],
      ru: ['Сценарий зарядки', 'Только солнце · показания со снимка', 'Зарядка от сети и солнца', 'Краткое расхождение в 500 Вт', 'Длительное расхождение в 500 Вт', 'Статус зарядки недоступен'],
    };
    select.value = scenario;
    function translate() {
      const l = labels[document.documentElement.lang] || labels.en;
      document.querySelector('#preview-caption').textContent = l[0];
      const scenarioLabels = { 'pv-only': chargingLabels, 'pv-priority': bridgeLabels, 'shared-charge': flowLabels }[defaultState];
      const choices = scenarioLabels ? scenarioLabels[document.documentElement.lang] || scenarioLabels.en : l.slice(1);
      document.querySelector('label[for=preview-state]').textContent = choices[0];
      [...select.options].forEach((option, i) => { option.textContent = choices[i + 1]; });
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
    scenarios = SCENARIOS
    default_state = 'inverter'
    history_fixtures = None
    history_links = {}

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
        if route in self.history_links:
            self.send_response(302)
            self.send_header('Location', self.history_links[route])
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            return
        if self.history_fixtures is not None and route.startswith('/history/'):
            if route in self.history_fixtures:
                return self.respond(self.history_fixtures[route])
            return self.respond(b'{}', status=404)
        if route == '/__preview__/controls.js':
            return self.respond(CONTROLS.encode(), 'text/javascript; charset=utf-8')
        if route == '/__preview__/controls.css':
            return self.respond(TOOLBAR_CSS.encode(), 'text/css; charset=utf-8')
        if route == '/api/energy':
            scenario = parse_qs(parsed.query).get('state', [self.default_state])[0]
            if scenario not in self.scenarios:
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
        if name == 'history.html' and self.history_fixtures is not None:
            notice = 'Design preview · simulated history, not live inverter data.'
            body = body.replace(b'</h1>', f'</h1><p data-i18n="{notice}">{notice}</p>'.encode('utf-8'), 1)
        if name == 'index.html':
            html = body.decode('utf-8').replace('</head>', f'<link rel="stylesheet" href="/__preview__/controls.css"><script src="/__preview__/controls.js" data-default-state="{self.default_state}"></script></head>')
            options = ''.join(f'<option value="{state}">{state}</option>' for state in self.scenarios)
            toolbar = '<aside id="connection-preview"><strong id="preview-caption"></strong><label for="preview-state"></label><select id="preview-state">' + options + '</select></aside>'
            html = html.replace('<body class="overview">', '<body class="overview">' + toolbar)
            if '/bridge-history' in self.history_links:
                html = html.replace('href="history.html"', 'href="/bridge-history"')
            if '/charging-history' in self.history_links:
                html = html.replace('href="history.html"', 'href="/charging-history"')
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
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--flows', action='store_true', help='Show live power-flow scenarios')
    modes.add_argument('--grid-history', action='store_true', help='Compare aggregate-only and split grid history')
    modes.add_argument('--bridge', action='store_true', help='Show signed inverter power estimates on Overview and History')
    modes.add_argument('--charging', action='store_true', help='Replay charging status and discrepancies on Overview and History')
    args = parser.parse_args()
    if args.flows:
        Preview.scenarios = tuple(FLOW_SCENARIOS)
        Preview.default_state = 'shared-charge'
    if args.grid_history:
        Preview.history_fixtures, Preview.history_links = grid_history_preview(datetime.now(ATHENS))
    if args.bridge:
        Preview.scenarios = tuple(BRIDGE_SCENARIOS)
        Preview.default_state = 'pv-priority'
        Preview.history_fixtures, Preview.history_links = bridge_history_preview(datetime.now(ATHENS))
    if args.charging:
        Preview.scenarios = CHARGING_SCENARIOS
        Preview.default_state = 'pv-only'
        Preview.history_fixtures, Preview.history_links = charging_history_preview(datetime.now(ATHENS))
    print(f'Local synthetic preview: http://127.0.0.1:{args.port}/', flush=True)
    ThreadingHTTPServer(('127.0.0.1', args.port), Preview).serve_forever()
