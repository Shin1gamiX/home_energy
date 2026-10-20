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
        values.update(grid_voltage=232, battery_voltage=52 + math.sin(minute / 240) * 2,
                      battery_current=battery / 52, load_current=load / 230, pv_current=solar / 300)
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


def preview_reports(cutoff):
    """UI fixtures only; neither the analyser nor publisher uses these invented values."""
    def localized(en, el, ru):
        return {'en': en, 'el': el, 'ru': ru}

    reports = []
    examples = [
        ('notice', localized(
            'Solar generation was lower than the recent baseline, and the battery peaked at 68%.',
            'Η ηλιακή παραγωγή ήταν χαμηλότερη από τη βάση σύγκρισης και η μέγιστη φόρτιση της μπαταρίας έφτασε το 68%.',
            'Солнечная выработка была ниже недавней базы сравнения, а максимальный заряд батареи составил 68%.'),
         localized('Lower solar yield', 'Χαμηλότερη ηλιακή παραγωγή', 'Снижение солнечной выработки'),
         localized(
            'Recorded solar generation was 8.4 kWh, compared with a 16.8 kWh daily baseline. Battery charge reached 68%.',
            'Η καταγεγραμμένη ηλιακή παραγωγή ήταν 8,4 kWh, έναντι ημερήσιας βάσης σύγκρισης 16,8 kWh. Η φόρτιση της μπαταρίας έφτασε το 68%.',
            'Записанная солнечная выработка составила 8,4 кВт·ч при суточной базе сравнения 16,8 кВт·ч. Заряд батареи достиг 68%.'),
         localized(
            'Weather observations are not available, so rain or cloud cannot be confirmed as the cause of the lower solar yield.',
            'Δεν υπάρχουν μετεωρολογικές παρατηρήσεις, επομένως η βροχή ή η νέφωση δεν μπορούν να επιβεβαιωθούν ως αιτία της χαμηλότερης ηλιακής παραγωγής.',
            'Метеонаблюдения недоступны, поэтому нельзя подтвердить, что причиной снижения солнечной выработки были дождь или облачность.'), 98.2, 8.4),
        ('routine', localized(
            'Recorded household consumption and solar output were within the comparison thresholds.',
            'Η καταγεγραμμένη κατανάλωση του σπιτιού και η ηλιακή παραγωγή ήταν εντός των ορίων σύγκρισης.',
            'Записанные потребление дома и солнечная выработка находились в пределах порогов сравнения.'),
         localized('No threshold exceeded', 'Δεν ξεπεράστηκε κανένα όριο', 'Пороги не превышены'),
         localized(
            'House consumption was 11.3 kWh against a daily baseline of 11.6 kWh.',
            'Η κατανάλωση του σπιτιού ήταν 11,3 kWh, έναντι ημερήσιας βάσης σύγκρισης 11,6 kWh.',
            'Потребление дома составило 11,3 кВт·ч при суточной базе сравнения 11,6 кВт·ч.'),
         localized(
            'This describes the recorded period only; it does not certify equipment health.',
            'Αυτό περιγράφει μόνο την καταγεγραμμένη περίοδο· δεν πιστοποιεί την καλή λειτουργία του εξοπλισμού.',
            'Это описание относится только к записанному периоду и не подтверждает исправность оборудования.'), 99.1, 16.4),
        ('warning', localized(
            'House consumption was more than twice the recent baseline, with 14.4 kWh extra recorded.',
            'Η κατανάλωση του σπιτιού ήταν υπερδιπλάσια της βάσης σύγκρισης, με 14,4 kWh επιπλέον καταγεγραμμένη ενέργεια.',
            'Потребление дома более чем вдвое превысило недавнюю базу сравнения; дополнительно записано 14,4 кВт·ч.'),
         localized('Substantially higher consumption', 'Σημαντικά αυξημένη κατανάλωση', 'Значительное увеличение потребления'),
         localized(
            'Recorded house consumption was 26 kWh against an 11.6 kWh daily baseline: 124.1% higher and 14.4 kWh extra.',
            'Η καταγεγραμμένη κατανάλωση του σπιτιού ήταν 26 kWh, έναντι ημερήσιας βάσης 11,6 kWh: αύξηση 124,1% και 14,4 kWh επιπλέον.',
            'Записанное потребление дома составило 26 кВт·ч при суточной базе 11,6 кВт·ч: на 124,1% выше, или дополнительно 14,4 кВт·ч.'),
         localized(
            'The readings do not identify which appliance was responsible.',
            'Οι μετρήσεις δεν προσδιορίζουν ποια συσκευή προκάλεσε την αύξηση.',
            'Показания не позволяют определить, какой прибор вызвал увеличение потребления.'), 97.8, 17.2),
        ('data_issue', localized(
            'A monitoring gap limits this review. Missing readings have not been counted as zero.',
            'Ένα κενό στην καταγραφή περιορίζει αυτή την ανασκόπηση. Οι μετρήσεις που λείπουν δεν υπολογίστηκαν ως μηδενικές.',
            'Пробел в мониторинге ограничивает этот обзор. Отсутствующие показания не учитывались как нулевые.'),
         localized('Incomplete recorded history', 'Ελλιπές καταγεγραμμένο ιστορικό', 'Неполная история показаний'),
         localized(
            'Readings covered 73% of the review window. Comparisons for the missing period are unavailable.',
            'Οι μετρήσεις κάλυψαν το 73% της περιόδου ανασκόπησης. Δεν είναι διαθέσιμες συγκρίσεις για την περίοδο που λείπει.',
            'Показания охватывают 73% периода обзора. Сравнения для отсутствующего периода недоступны.'),
         localized(
            'A gap in recorded data cannot establish that the inverter stopped operating.',
            'Ένα κενό στα καταγεγραμμένα δεδομένα δεν αποδεικνύει ότι ο μετατροπέας σταμάτησε να λειτουργεί.',
            'Пробел в записанных данных не доказывает, что инвертор прекратил работу.'), 73.0, 9.8),
        ('routine', localized(
            'No comparison threshold was exceeded in the recorded readings.',
            'Δεν ξεπεράστηκε κανένα όριο σύγκρισης στις καταγεγραμμένες μετρήσεις.',
            'В записанных показаниях не превышен ни один порог сравнения.'),
         localized('Within comparison thresholds', 'Εντός των ορίων σύγκρισης', 'В пределах порогов сравнения'),
         localized(
            'Solar generation and household consumption were close to the preceding-week baseline.',
            'Η ηλιακή παραγωγή και η κατανάλωση του σπιτιού ήταν κοντά στη βάση σύγκρισης της προηγούμενης εβδομάδας.',
            'Солнечная выработка и потребление дома были близки к базе сравнения предшествующей недели.'),
         localized(
            'Short events may not be visible in minute averages.',
            'Σύντομα συμβάντα ενδέχεται να μην είναι ορατά στους μέσους όρους ανά λεπτό.',
            'Кратковременные события могут быть не видны в средних значениях за минуту.'), 98.9, 16.6),
    ]
    newest = int(cutoff // 3600) * 3600 - 300
    for index, (severity, summary, title, observation, note, coverage, solar) in enumerate(examples):
        end = newest - index * 86400
        start = end - 86400
        finding_id = 'preview-finding-' + str(index)
        analysis = {
            'schema_version': 2, 'generated_at': end + 30,
            'window': {'start': start, 'end': end},
            'baseline': {'start': start - 7 * 86400, 'end': start, 'days': 7},
            'data_through': end - 60, 'coverage_pct': coverage, 'severity': severity,
            'metrics': [
                {'key': 'solar_generated', 'label': localized('Solar generated', 'Ηλιακή παραγωγή', 'Солнечная выработка'), 'unit': 'kWh', 'value': solar,
                 'baseline': 16.8, 'change_pct': round((solar / 16.8 - 1) * 100, 1) if coverage >= 90 else None,
                 'coverage_pct': coverage, 'baseline_days': 7},
                {'key': 'house_usage', 'label': localized('House consumption', 'Κατανάλωση σπιτιού', 'Потребление дома'), 'unit': 'kWh', 'value': 11.3 if index != 2 else 26,
                 'baseline': 11.6, 'change_pct': (-2.6 if index != 2 else 124.1) if coverage >= 90 else None,
                 'coverage_pct': coverage, 'baseline_days': 7},
                {'key': 'battery_max_soc', 'label': localized('Highest battery charge', 'Μέγιστη φόρτιση μπαταρίας', 'Максимальный заряд батареи'), 'unit': '%', 'value': 68 if index == 0 else 99,
                 'baseline': 99, 'change_pct': None, 'coverage_pct': coverage, 'baseline_days': 7},
            ],
            'findings': [{'id': finding_id, 'category': 'data' if severity == 'data_issue' else 'energy',
                          'severity': severity, 'title': title, 'observation': observation,
                          'start': start, 'end': end, 'metric': {0: 'pv', 2: 'load'}.get(index)}],
            'limitations': [localized(
                'Synthetic design preview; all figures and findings are invented.',
                'Δοκιμαστική προεπισκόπηση· όλα τα στοιχεία και τα ευρήματα είναι υποθετικά.',
                'Демонстрационный макет; все цифры и наблюдения вымышлены.'), localized(
                'Minute averages can hide short events. Estimated grid readings are not a revenue meter.',
                'Οι μέσοι όροι ανά λεπτό μπορεί να αποκρύπτουν σύντομα συμβάντα. Οι εκτιμήσεις του δικτύου δεν αποτελούν μετρητή χρέωσης.',
                'Средние значения за минуту могут скрывать кратковременные события. Расчётные показания сети не являются расчётным счётчиком.'), localized(
                'No weather observations were used. Missing periods are excluded.',
                'Δεν χρησιμοποιήθηκαν μετεωρολογικές παρατηρήσεις. Οι περίοδοι χωρίς δεδομένα εξαιρούνται.',
                'Метеонаблюдения не использовались. Периоды без данных исключены.')],
        }
        if coverage < 90:
            analysis['limitations'].append(localized(
                'Coverage is below the comparison minimum; trend comparisons are withheld.',
                'Η κάλυψη είναι κάτω από το ελάχιστο όριο σύγκρισης· δεν παρουσιάζονται συγκρίσεις τάσεων.',
                'Охват ниже минимума для сравнения; сравнения тенденций не приводятся.'))
        reports.append({'id': hashlib.sha256(f'preview-report-{end}'.encode()).hexdigest(),
                        'published_at': end + 60, 'analysis': analysis,
                        'narrative': {'summary': summary, 'notes': [{'finding_id': finding_id, 'commentary': note}]}})
    return {'schema_version': 2, 'reports': reports}


CACHEABLE_ASSETS = {'styles.css', 'app.js', 'theme.js', 'theme.css', 'history.js',
                    'history-cache.js', 'history-calendar.js', 'history.css', '404.css', '404.js',
                    'power-history.js', 'power-history.css', 'insights.js', 'insights.css'}


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
        if route == '/history.html' and parse_qs(urlsplit(self.path).query).get('compare') == ['1']:
            # Local preview only: never included in the deployable HTML or JS.
            body = (ROOT / 'history.html').read_text(encoding='utf-8')
            body = body.replace('</head>', '<link rel="stylesheet" href="/preview/history-comparison.css"></head>')
            body = body.replace('</body>', '<script src="/preview/history-comparison.js"></script></body>')
            encoded = body.encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)
            return
        elif route == '/reports/index.json':
            payload = preview_reports(cutoff)
        elif route == '/history/index.json':
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
