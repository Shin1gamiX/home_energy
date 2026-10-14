"""Fixed-command, report-only publisher. Install under a dedicated restricted user.

No shell, path, URL, HTML, file name or system action is accepted from the caller.
The OS/sshd boundary in deploy/reports-sshd.conf.example is mandatory in production.
"""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import tempfile
import time

# -I excludes the caller's cwd and PYTHONPATH; this directory must be root-owned.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from insights_analysis import analyze

STATE = Path('/var/lib/homeenergy-reports')
HISTORY = Path('/var/www/homeenergy/runtime/history')
LANGUAGES = frozenset(('en', 'el', 'ru'))
MAX_INPUT = 32768
MAX_ANALYSIS = 32768
MAX_REPORT = 65536
MAX_FEED = 2_000_000
MIN_INTERVAL = 300
MAX_DAILY = 12
SNAPSHOT_LIFETIME = 1800


class Rejected(ValueError):
    """Safe, fixed public failure reason; never include submitted text."""


def require(condition, message):
    if not condition:
        raise Rejected(message)


def encoded(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True,
                      separators=(',', ':')).encode('utf-8')


def object_pairs(pairs):
    value = {}
    for key, item in pairs:
        require(key not in value, 'Duplicate JSON key')
        value[key] = item
    return value


def decode(raw):
    require(isinstance(raw, bytes) and 0 < len(raw) <= MAX_INPUT, 'Invalid request size')
    try:
        return json.loads(raw.decode('utf-8'), object_pairs_hook=object_pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(Rejected('Invalid number')))
    except (UnicodeError, json.JSONDecodeError, RecursionError):
        raise Rejected('Invalid JSON') from None


def text(value, maximum):
    require(isinstance(value, str) and 1 <= len(value.strip()) <= maximum, 'Invalid text length')
    require(not re.search(r'[<>\x00-\x1f\x7f\u202a-\u202e\u2066-\u2069]', value), 'Markup or control characters are not allowed')
    require(not re.search(r'(?:https?://|www\.|data:|javascript:)', value, re.I), 'Links are not allowed')
    return value.strip()


def localized_text(value, maximum):
    require(isinstance(value, dict) and set(value) == LANGUAGES, 'All report languages are required')
    return {language: text(value[language], maximum) for language in sorted(LANGUAGES)}


def narrative(payload, analysis):
    require(isinstance(payload, dict) and set(payload) == {'analysis_id', 'summary', 'notes'}, 'Unexpected request fields')
    require(isinstance(payload['analysis_id'], str) and re.fullmatch(r'[a-f0-9]{64}', payload['analysis_id']), 'Invalid analysis ID')
    summary = localized_text(payload['summary'], 600)
    require(isinstance(payload['notes'], list) and len(payload['notes']) <= 6, 'Invalid notes')
    known = {item['id'] for item in analysis['findings']}
    used, notes = set(), []
    for item in payload['notes']:
        require(isinstance(item, dict) and set(item) == {'finding_id', 'commentary'}, 'Unexpected note fields')
        key = item['finding_id']
        require(isinstance(key, str) and key in known and key not in used, 'Unknown or repeated finding')
        used.add(key)
        notes.append({'finding_id': key, 'commentary': localized_text(item['commentary'], 400)})
    return {'summary': summary, 'notes': notes}


def safe_path(path):
    require(path.is_absolute() and path.resolve() == path and not path.is_symlink(), 'Unsafe configured path')
    return path


@contextmanager
def locked(state):
    # Serializes database + public feed updates, including crash-recovery retries.
    safe_path(state)
    require(state.is_dir() and (state / 'public').is_dir(), 'Report directories are not provisioned')
    path = safe_path(state / 'publisher.lock')
    with path.open('a+b') as stream:
        if os.name == 'nt':
            import msvcrt
            stream.seek(0, 2)
            if stream.tell() == 0:
                stream.write(b'0')
                stream.flush()
            def acquire():
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            def release():
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            def acquire():
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            def release():
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        deadline = time.monotonic() + 3
        while True:
            try:
                acquire()
                break
            except OSError:
                require(time.monotonic() < deadline, 'Publisher busy; retry later')
                time.sleep(.05)
        try:
            yield
        finally:
            release()


@contextmanager
def database(state):
    path = safe_path(state / 'reports.sqlite3')
    db = sqlite3.connect(path, timeout=3)
    try:
        db.execute('CREATE TABLE IF NOT EXISTS snapshots (id TEXT PRIMARY KEY, created INTEGER NOT NULL, analysis TEXT NOT NULL)')
        db.execute('CREATE TABLE IF NOT EXISTS reports (id TEXT PRIMARY KEY, published INTEGER NOT NULL, request TEXT NOT NULL, report TEXT NOT NULL)')
        db.commit()
        yield db
    finally:
        db.close()


def write_feed(state, db):
    directory = safe_path(state / 'public')
    destination = safe_path(directory / 'index.json')
    reports = [json.loads(row[0]) for row in db.execute('SELECT report FROM reports ORDER BY published DESC,id DESC LIMIT 100')]
    require(all(report['analysis']['schema_version'] == 2 for report in reports), 'Unsupported archived report schema')
    body = encoded({'schema_version': 2, 'reports': reports})
    # Translated prose has variable UTF-8 size. Bound the public window without
    # dropping archived reports or preventing a new valid report being visible.
    while len(body) > MAX_FEED and len(reports) > 1:
        reports.pop()
        body = encoded({'schema_version': 2, 'reports': reports})
    require(len(body) <= MAX_FEED, 'Report feed exceeds size limit')
    if destination.is_file() and destination.stat().st_size == len(body):
        with destination.open('rb') as stream:
            if stream.read(MAX_FEED + 1) == body:
                return
    fd, name = tempfile.mkstemp(prefix='.feed-', suffix='.tmp', dir=directory)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
            os.chmod(name, 0o644)
        os.replace(name, destination)
        if os.name == 'posix':
            directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def prepare(state=STATE, history=HISTORY, now=None):
    now = int(time.time() if now is None else now)
    with locked(state), database(state) as db:
        recent = db.execute('SELECT id,analysis FROM snapshots WHERE created>? ORDER BY created DESC LIMIT 1', (now - 60,)).fetchone()
        if recent and json.loads(recent[1]).get('schema_version') == 2:
            return {'analysis_id': recent[0], 'analysis': json.loads(recent[1])}
        require(db.execute('SELECT count(*) FROM snapshots WHERE created>?', (now - 3600,)).fetchone()[0] < 12, 'Preparation rate limit reached')
        analysis = analyze(safe_path(history), now)
        require(analysis.get('schema_version') == 2, 'Unsupported analysis schema')
        body = encoded(analysis)
        require(len(body) <= MAX_ANALYSIS, 'Analysis too large')
        identifier = hashlib.sha256(body).hexdigest()
        with db:
            # Only expired staging snapshots are removed; published reports are immutable.
            db.execute('DELETE FROM snapshots WHERE created<?', (now - 2 * 86400,))
            db.execute('INSERT OR IGNORE INTO snapshots VALUES (?,?,?)', (identifier, now, body.decode()))
        return {'analysis_id': identifier, 'analysis': analysis}


def publish(raw, state=STATE, now=None):
    now = int(time.time() if now is None else now)
    payload = decode(raw)
    require(isinstance(payload, dict) and isinstance(payload.get('analysis_id'), str)
            and re.fullmatch(r'[a-f0-9]{64}', payload['analysis_id']), 'Invalid analysis ID')
    identifier = payload['analysis_id']
    with locked(state), database(state) as db:
        previous = db.execute('SELECT request FROM reports WHERE id=?', (identifier,)).fetchone()
        if previous:
            require(previous[0] == encoded(payload).decode(), 'Report already exists; overwrite refused')
            write_feed(state, db)
            return {'status': 'already_published', 'id': identifier}
        snapshot = db.execute('SELECT created,analysis FROM snapshots WHERE id=?', (identifier,)).fetchone()
        require(snapshot is not None and 0 <= now - snapshot[0] <= SNAPSHOT_LIFETIME, 'Analysis expired or unknown; prepare again')
        analysis = json.loads(snapshot[1])
        require(analysis.get('schema_version') == 2, 'Unsupported analysis schema; prepare again')
        commentary = narrative(payload, analysis)
        latest = db.execute('SELECT max(published),count(*) FROM reports').fetchone()
        require(latest[0] is None or now - latest[0] >= MIN_INTERVAL, 'Publication cooldown active')
        require(latest[1] < 20000, 'Archive capacity reached; administrator action needed')
        require(db.execute('SELECT count(*) FROM reports WHERE published>?', (now - 86400,)).fetchone()[0] < MAX_DAILY, 'Publication daily limit reached')
        report = {'id': identifier, 'published_at': now, 'analysis': analysis, 'narrative': commentary}
        require(len(encoded(report)) <= MAX_REPORT, 'Report too large')
        with db:
            db.execute('INSERT INTO reports VALUES (?,?,?,?)', (identifier, now, encoded(payload).decode(), encoded(report).decode()))
        # If publication fails after commit, resubmitting the identical payload repairs the feed.
        write_feed(state, db)
        return {'status': 'published', 'id': identifier}


def main():
    os.umask(0o077)
    require(len(sys.argv) == 1, 'Command arguments are not allowed')
    command = os.environ.get('SSH_ORIGINAL_COMMAND', '')
    require(command in ('prepare', 'publish'), 'Only prepare and publish commands are allowed')
    if command == 'prepare':
        result = prepare()
    else:
        # SSH sessions are additionally bounded by the fixed timeout in ForceCommand.
        result = publish(sys.stdin.buffer.read(MAX_INPUT + 1))
    print(encoded(result).decode('utf-8'))


if __name__ == '__main__':
    try:
        main()
    except (Rejected, OSError, sqlite3.Error, ValueError, TypeError, KeyError):
        # Avoid echoing filesystem details or arbitrary submitted text to logs/caller.
        print('{"error":"Report request rejected or temporarily unavailable"}', file=sys.stderr)
        sys.exit(1)
