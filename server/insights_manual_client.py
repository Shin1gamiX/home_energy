"""Small manual SSH client. Standard library only; does not launch another AI.

review prints read-only evidence. prepare saves a private publication snapshot.
publish requires explicit confirmation and reads the translated submission stdin.
The server, not this client, restricts the credential to report operations.
"""
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(r'C:\ProgramData\HomeEnergyReporterProduction')
SSH = r'C:\Windows\System32\OpenSSH\ssh.exe'
DESTINATION = 'homeenergy-reporter@91.98.44.219'
MAX_INPUT = 32768
MAX_RESPONSE = 262144


def ssh_arguments(command):
    if command not in ('review', 'prepare', 'publish'):
        raise ValueError('Unsupported operation')
    return [SSH, '-F', 'NUL', '-T', '-o', 'BatchMode=yes', '-o', 'IdentitiesOnly=yes',
            '-o', 'StrictHostKeyChecking=yes', '-o', 'UpdateHostKeys=no',
            '-o', 'ConnectTimeout=10', '-o', 'ClearAllForwardings=yes',
            '-o', 'UserKnownHostsFile=' + str(ROOT / 'known_hosts'),
            '-i', str(ROOT / 'id_ed25519'), DESTINATION, 'manual-' + command]


def call(command, request):
    body = json.dumps(request, ensure_ascii=False, allow_nan=False).encode('utf-8')
    if not 0 < len(body) <= MAX_INPUT:
        raise ValueError('Request too large')
    try:
        result = subprocess.run(ssh_arguments(command), input=body, capture_output=True,
                                timeout=55, check=False,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except subprocess.TimeoutExpired:
        raise RuntimeError('SSH timed out. Publication outcome may be uncertain; do not automatically retry.') from None
    if result.returncode != 0:
        raise RuntimeError('Report request failed. Check connectivity/server activation; do not automatically retry publication.')
    if len(result.stdout) > MAX_RESPONSE:
        raise ValueError('Unexpected response size')
    envelope = json.loads(result.stdout)
    if not isinstance(envelope, dict) or envelope.get('scope') != 'production' or envelope.get('synthetic') is not False:
        raise ValueError('Unexpected report server response')
    response = envelope.get('result')
    if not isinstance(response, dict):
        raise ValueError('Missing report result')
    if command != 'publish':
        analysis = response.get('analysis')
        digest = hashlib.sha256(json.dumps(analysis, ensure_ascii=False, allow_nan=False,
                                          sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()
        if response.get('analysis_id') != digest or response.get('saved') is not request['save']:
            raise ValueError('Unexpected analysis or save status; manual server support is required')
    elif response.get('id') != request.get('analysis_id') or response.get('status') not in ('published', 'already_published'):
        raise RuntimeError('Publication not confirmed. Check the public feed before any further submission.')
    return response


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('review', 'prepare'):
        sub = commands.add_parser(name)
        sub.add_argument('--hours', type=int, default=24, choices=range(1, 169), metavar='1..168')
        sub.add_argument('--baseline-days', type=int, default=7, choices=range(1, 91), metavar='1..90')
        sub.add_argument('--end', help='ISO timestamp with an explicit UTC offset, e.g. 2026-10-17T12:00:00+03:00')
    commands.add_parser('publish').add_argument('--confirm-publication', action='store_true')
    args = parser.parse_args(argv)
    if args.command == 'publish':
        if not args.confirm_publication:
            parser.error('Publishing requires --confirm-publication and the owner\'s explicit request')
        raw = sys.stdin.buffer.read(MAX_INPUT + 1)
        if not 0 < len(raw) <= MAX_INPUT:
            parser.error('Expected a bounded JSON submission on stdin')
        request = json.loads(raw)
        command = 'publish'
    else:
        request = {'hours': args.hours, 'baseline_days': args.baseline_days, 'save': args.command == 'prepare'}
        if args.end:
            end = datetime.fromisoformat(args.end)
            if end.tzinfo is None or end.second or end.microsecond:
                parser.error('--end needs an explicit UTC offset and a whole minute')
            request['end'] = int(end.timestamp())
        command = args.command
    response = call(command, request)
    sys.stdout.buffer.write(json.dumps(response, ensure_ascii=False, allow_nan=False, indent=2).encode('utf-8') + b'\n')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, RuntimeError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
