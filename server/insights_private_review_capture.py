"""Administrator-only, single-use capture of a bounded real-history summary.

Library for the separately pinned private-review runner. This module is never
registered as a model tool. The server computes the aggregate in memory; raw
history, credentials and server files are neither transferred nor modified.
"""
import hashlib
import os
from pathlib import Path
import queue
import re
import subprocess
import sys
import threading
import time


if __name__ == '__main__':
    print('Use the reviewed private-review launcher.', file=sys.stderr)
    raise SystemExit(1)

from insights_broker import decode, encode
import insights_windows_transport as windows


SSH = Path(r'C:\Windows\System32\OpenSSH\ssh.exe')
KEY = Path(r'C:\Users\anest\Documents\New project\ssh\codex_access_key')
KNOWN_HOSTS = Path(r'C:\ProgramData\HomeEnergyReporterStaging\known_hosts')
HOST = 'shini@91.98.44.219'
SSH_SHA256 = '786ff14be7cd652b2b9770a57e9b1aa5e03a052ce3a3d641fb4760c0ff3fde05'
KNOWN_HOSTS_SHA256 = 'c9eac033de68d2fca971c5e77bb9cfd72343a7703ca4a266d0cb3f03c265ddfe'
MAX_RESPONSE = 65536
MAX_STDERR = 4096
CAPTURE_SECONDS = 60

# Fixed trusted source, never interpolated with model or server data. Loading
# verified source bytes avoids reopening a different file after the hash check.
REMOTE_SCRIPT = r'''import hashlib,json,os,stat,sys,time,types
from pathlib import Path
def require(value):
    if not value:
        raise ValueError("rejected")
def encoded(value):
    return json.dumps(value,ensure_ascii=False,allow_nan=False,sort_keys=True,separators=(",",":")).encode("utf-8")
def load(name,expected):
    path=Path("/opt/homeenergy-reports-staging")/(name+".py")
    require(path.resolve(strict=True)==path)
    for parent in path.parents:
        info=parent.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid==0 and not info.st_mode&0o022)
    before=path.lstat()
    require(stat.S_ISREG(before.st_mode) and before.st_uid==0 and before.st_nlink==1 and not before.st_mode&0o022)
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    with os.fdopen(fd,"rb") as stream:
        info=os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_uid==0 and info.st_nlink==1 and not info.st_mode&0o022)
        require((info.st_dev,info.st_ino)==(before.st_dev,before.st_ino) and 0<info.st_size<=65536)
        source=stream.read(65537)
    require(len(source)<=65536 and hashlib.sha256(source).hexdigest()==expected)
    module=types.ModuleType(name)
    module.__file__=str(path)
    sys.modules[name]=module
    exec(compile(source,str(path),"exec"),module.__dict__)
    return module
try:
    load("insights_i18n","957e784430904770d291bfaf2c402b513007d7ff953ae8c30a31f96ff11c31c5")
    analyzer=load("insights_analysis","3c48a5254c025c8d3bfaf190c4d785bfef2ab800015de0e4fdb69f5363e0c70e")
    history=Path("/var/www/homeenergy/runtime/history")
    require(history.resolve(strict=True)==history and history.is_dir())
    analysis=analyzer.analyze(history,time.time())
    result={"schema_version":1,"scope":"private_review","synthetic":False,"captured_at":int(time.time()),"analysis_id":hashlib.sha256(encoded(analysis)).hexdigest(),"analysis":analysis}
    body=encoded(result)+b"\n"
    require(len(body)<=65536)
    sys.stdout.buffer.write(body)
    sys.stdout.buffer.flush()
except Exception:
    sys.stderr.write("private_capture_rejected\n")
    raise SystemExit(1)
'''.encode('ascii')


class CaptureFailure(ValueError):
    """A fixed, secret-free rejection code only."""


def require(value, code):
    if not value:
        raise CaptureFailure(code)


def verify_material():
    """Pin the binary and public host key; never read private-key bytes."""
    require(os.name == 'nt', 'private_capture_windows_required')
    require(all(re.fullmatch('[a-f0-9]{64}', value) for value in
                (SSH_SHA256, KNOWN_HOSTS_SHA256)), 'private_capture_pins_unreviewed')
    windows.regular_file(SSH, 16 * 1024 * 1024, allow_system_hardlinks=True)
    windows.regular_file(KEY, 16384)
    windows.regular_file(KNOWN_HOSTS, 4096)
    require(hashlib.sha256(SSH.read_bytes()).hexdigest() == SSH_SHA256,
            'private_capture_ssh_changed')
    host_pin = KNOWN_HOSTS.read_bytes()
    require(hashlib.sha256(host_pin).hexdigest() == KNOWN_HOSTS_SHA256,
            'private_capture_host_pin_changed')
    fields = host_pin.decode('ascii').strip().split()
    require(len(fields) == 3 and fields[:2] == ['91.98.44.219', 'ssh-ed25519']
            and re.fullmatch('[A-Za-z0-9+/]+={0,2}', fields[2]),
            'private_capture_host_pin_invalid')


def ssh_command():
    """There is no caller-selectable command, destination, identity or option."""
    options = (
        'BatchMode=yes', 'IdentitiesOnly=yes', 'IdentityAgent=none',
        'AddKeysToAgent=no', 'CertificateFile=none', 'PKCS11Provider=none',
        'SecurityKeyProvider=none', 'StrictHostKeyChecking=yes', 'UpdateHostKeys=no',
        'HostKeyAlgorithms=ssh-ed25519', 'VerifyHostKeyDNS=no',
        'CanonicalizeHostname=no', 'PubkeyAcceptedAlgorithms=ssh-ed25519',
        'UserKnownHostsFile=' + str(KNOWN_HOSTS), 'GlobalKnownHostsFile=none',
        'PasswordAuthentication=no', 'KbdInteractiveAuthentication=no',
        'PreferredAuthentications=publickey', 'GSSAPIAuthentication=no',
        'ForwardAgent=no', 'ForwardX11=no', 'ClearAllForwardings=yes',
        'ControlMaster=no', 'ControlPath=none', 'ProxyCommand=none', 'ProxyJump=none',
        'PermitLocalCommand=no', 'RequestTTY=no', 'ConnectionAttempts=1',
        'ConnectTimeout=10', 'ServerAliveInterval=10', 'ServerAliveCountMax=2',
        'LogLevel=ERROR',
    )
    return [str(SSH), '-F', 'none', '-T', '-p', '22', '-i', str(KEY),
            *[part for option in options for part in ('-o', option)], HOST,
            '/usr/bin/python3 -I -B -']


def _read_remote():
    """Bound stdout, discard bounded stderr, and own/kill only this SSH child."""
    process = None
    group = None
    workers = []
    events = queue.Queue(maxsize=3)
    output = bytearray()
    deadline = time.monotonic() + CAPTURE_SECONDS

    def reader(pipe, limit, retain):
        count = 0
        ok = True
        try:
            while True:
                chunk = pipe.read(4096)
                if not chunk:
                    break
                count += len(chunk)
                if count > limit:
                    ok = False
                    break
                if retain:
                    output.extend(chunk)
        except OSError:
            ok = False
        finally:
            events.put(ok)

    def writer():
        ok = True
        try:
            process.stdin.write(REMOTE_SCRIPT)
            process.stdin.close()
        except OSError:
            ok = False
        finally:
            events.put(ok)

    try:
        process = subprocess.Popen(ssh_command(), stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False,
            close_fds=True, env=windows.clean_environment(), cwd=str(SSH.parent),
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        group = windows.ProcessGroup(process)
        for target, arguments in ((reader, (process.stdout, MAX_RESPONSE, True)),
                (reader, (process.stderr, MAX_STDERR, False)), (writer, ())):
            worker = threading.Thread(target=target, args=arguments, daemon=True)
            workers.append(worker)
            worker.start()
        for _ in workers:
            remaining = deadline - time.monotonic()
            require(remaining > 0 and events.get(timeout=max(.001, remaining)),
                    'private_capture_transport_rejected')
        require(process.wait(timeout=max(.001, deadline - time.monotonic())) == 0,
                'private_capture_transport_rejected')
        require(time.monotonic() <= deadline, 'private_capture_transport_rejected')
        return bytes(output)
    except (OSError, ValueError, queue.Empty, subprocess.SubprocessError):
        raise CaptureFailure('private_capture_transport_rejected') from None
    finally:
        if group is not None:
            group.close()
        if process is not None:
            if process.poll() is None:
                process.kill()
            process.wait()
            for worker in workers:
                worker.join(timeout=2)
            for pipe in (process.stdin, process.stdout, process.stderr):
                pipe.close()


def _validate_snapshot(value):
    # The launcher has verified this module before dispatching capture. Do not
    # load the model runtime, credentials or reporting transport here.
    from insights_private_review_mcp import validate_snapshot
    validate_snapshot(value, time.time(), fresh=True)


def capture_once(store):
    """Consume one capture approval before any network I/O; never overwrite."""
    require(not any(store.exists(kind) for kind in ('capture', 'snapshot', 'receipt')),
            'private_capture_already_consumed')
    verify_material()
    store.create('capture', {'schema_version': 1, 'scope': 'private_review',
        'synthetic': False, 'created_at': int(time.time())})
    raw = _read_remote()
    try:
        value = decode(raw, MAX_RESPONSE)
        require(raw == encode(value) + b'\n', 'private_capture_noncanonical')
        _validate_snapshot(value)
    except (ValueError, TypeError, KeyError, OverflowError):
        raise CaptureFailure('private_capture_snapshot_rejected') from None
    store.create('snapshot', value)
    return {'scope': 'private_review', 'synthetic': False, 'snapshot_saved': True,
        'analysis_id': value['analysis_id'], 'captured_at': value['captured_at'],
        'bytes': len(raw), 'model_started': False, 'public_published': False}
