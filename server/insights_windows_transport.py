"""Trusted host-side Windows staging transport; no model-configurable settings.

Library only: no CLI, configuration loader, credentials or network listener.
An independently reviewed launcher must supply the destination and verified pins.
This is not an OS-account boundary against other programs owned by the user.
"""
from dataclasses import dataclass
import hashlib
import ipaddress
import os
from pathlib import Path
import queue
import re
import signal
import stat
import subprocess
import threading
import time

from insights_broker import MAX_PAYLOAD, MAX_RESPONSE, Rejected, require


SSH = Path(r'C:\Windows\System32\OpenSSH\ssh.exe')
PRIVATE_ROOT = Path(r'C:\ProgramData\HomeEnergyReporterStaging')
KEY = PRIVATE_ROOT / 'id_ed25519'
KNOWN_HOSTS = PRIVATE_ROOT / 'known_hosts'
STAGING_USER = 'homeenergy-reporter-staging'
MAX_STDERR = 8192
SSH_SECONDS = 50


@dataclass(frozen=True)
class Destination:
    """Trusted deployment values, NEVER fields in a model tool request."""
    host: str
    port: int
    ssh_sha256: str
    known_hosts_sha256: str

    def validate(self):
        require(isinstance(self.host, str))
        address = ipaddress.ip_address(self.host)
        require(str(address) == self.host and '%' not in self.host)
        require(not address.is_unspecified and not address.is_multicast)
        require(type(self.port) is int and 1 <= self.port <= 65535)
        for digest in (self.ssh_sha256, self.known_hosts_sha256):
            require(isinstance(digest, str) and re.fullmatch('[a-f0-9]{64}', digest))


def regular_file(path, limit, *, allow_system_hardlinks=False):
    # Windows servicing links its installed SSH binary into WinSxS. Credentials
    # must still have exactly one link; only the fixed, separately pinned binary
    # may use this exception.
    require(not allow_system_hardlinks or path == SSH)
    require(path.is_absolute() and path.resolve(strict=True) == path)
    for entry in (path, *path.parents):
        info = entry.lstat()
        require(not info.st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)
    info = path.stat()
    require(stat.S_ISREG(info.st_mode) and info.st_nlink >= 1 and 0 < info.st_size <= limit)
    require(allow_system_hardlinks or info.st_nlink == 1)


def verify_material(destination):
    """Pins executable/host key; never reads the private key's contents.

    NTFS ownership/DACL and model-denial probes are additional deployment gates,
    not established by these regular-file/hash checks.
    """
    require(os.name == 'nt')
    destination.validate()
    regular_file(SSH, 16 * 1024 * 1024, allow_system_hardlinks=True)
    regular_file(KEY, 16384)
    regular_file(KNOWN_HOSTS, 4096)
    require(hashlib.sha256(SSH.read_bytes()).hexdigest() == destination.ssh_sha256)
    pin = KNOWN_HOSTS.read_bytes()
    require(hashlib.sha256(pin).hexdigest() == destination.known_hosts_sha256)
    fields = pin.decode('ascii').strip().split()
    host = destination.host if destination.port == 22 else '[%s]:%d' % (destination.host, destination.port)
    require(len(fields) == 3 and fields[0] == host and fields[1] == 'ssh-ed25519'
            and re.fullmatch('[A-Za-z0-9+/]+={0,2}', fields[2]))


def ssh_command(destination, method):
    destination.validate()
    require(method in ('prepare', 'publish'))
    options = (
        'BatchMode=yes', 'IdentitiesOnly=yes', 'IdentityAgent=none', 'AddKeysToAgent=no',
        'CertificateFile=none', 'PKCS11Provider=none', 'SecurityKeyProvider=none',
        'StrictHostKeyChecking=yes', 'UpdateHostKeys=no', 'HostKeyAlgorithms=ssh-ed25519',
        'VerifyHostKeyDNS=no', 'CanonicalizeHostname=no', 'PubkeyAcceptedAlgorithms=ssh-ed25519',
        'UserKnownHostsFile=' + str(KNOWN_HOSTS), 'GlobalKnownHostsFile=none',
        'PasswordAuthentication=no', 'KbdInteractiveAuthentication=no',
        'PreferredAuthentications=publickey', 'GSSAPIAuthentication=no',
        'ForwardAgent=no', 'ForwardX11=no', 'ClearAllForwardings=yes',
        'ControlMaster=no', 'ControlPath=none', 'ProxyCommand=none', 'ProxyJump=none',
        'PermitLocalCommand=no', 'RequestTTY=no', 'ConnectionAttempts=1',
        'ConnectTimeout=10', 'ServerAliveInterval=10', 'ServerAliveCountMax=2', 'LogLevel=ERROR',
    )
    return [str(SSH), '-F', 'none', '-T', '-p', str(destination.port), '-i', str(KEY),
            *[part for option in options for part in ('-o', option)],
            STAGING_USER + '@' + destination.host, method]


def clean_environment():
    # No inherited agent socket, askpass command, proxy, API token or Python path.
    # Windows OpenSSH needs PROGRAMDATA even with all config files disabled.
    return {'SystemRoot': r'C:\Windows', 'WINDIR': r'C:\Windows', 'PROGRAMDATA': r'C:\ProgramData',
            'PATH': r'C:\Windows\System32\OpenSSH;C:\Windows\System32',
            'SSH_ASKPASS_REQUIRE': 'never'}


class ProcessGroup:
    """Own only the newly created child; kill its Windows job on every exit.

    Assignment follows Popen, before any request bytes. Only the fixed, pinned
    SSH executable is allowed in deployment; this is not a general code sandbox.
    """
    def __init__(self, process):
        self.process = process
        self.handle = None
        if os.name != 'nt':
            return
        import ctypes
        from ctypes import wintypes

        class Limits(ctypes.Structure):
            _fields_ = [('process_time', ctypes.c_longlong), ('job_time', ctypes.c_longlong),
                        ('flags', wintypes.DWORD), ('min_ws', ctypes.c_size_t), ('max_ws', ctypes.c_size_t),
                        ('active', wintypes.DWORD), ('affinity', ctypes.c_size_t),
                        ('priority', wintypes.DWORD), ('scheduling', wintypes.DWORD)]

        class Extended(ctypes.Structure):
            _fields_ = [('basic', Limits), ('io', ctypes.c_ulonglong * 6),
                        ('process_memory', ctypes.c_size_t), ('job_memory', ctypes.c_size_t),
                        ('peak_process', ctypes.c_size_t), ('peak_job', ctypes.c_size_t)]

        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        self.kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        self.kernel.CreateJobObjectW.restype = wintypes.HANDLE
        self.kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        self.kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.handle = self.kernel.CreateJobObjectW(None, None)
        require(bool(self.handle))
        limits = Extended()
        limits.basic.flags = 0x2000 | 0x0008  # KILL_ON_JOB_CLOSE | ACTIVE_PROCESS_LIMIT
        limits.basic.active = 1  # The pinned SSH command must not start helpers.
        try:
            require(self.kernel.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)))
            require(self.kernel.AssignProcessToJobObject(self.handle, int(process._handle)))
        except BaseException:
            self.close()
            raise

    def close(self):
        if self.handle is not None:
            self.kernel.CloseHandle(self.handle)
            self.handle = None
        elif os.name != 'nt':
            try:
                os.killpg(self.process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


def bounded_process(argv, body, seconds=SSH_SECONDS, *, env=None):
    """Trusted argv only. Windows pipes use three workers, with bounded buffers."""
    require(isinstance(body, bytes) and len(body) <= MAX_PAYLOAD)
    require(0 < seconds <= SSH_SECONDS)
    process = None
    group = None
    workers = []
    events = queue.Queue(maxsize=3)
    output = bytearray()
    deadline = time.monotonic() + seconds

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
            process.stdin.write(body)
            process.stdin.close()
        except OSError:
            ok = False
        finally:
            events.put(ok)

    try:
        process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, shell=False, close_fds=True,
                                   env=clean_environment() if env is None else env,
                                   cwd=str(SSH.parent) if os.name == 'nt' else '/',
                                   start_new_session=os.name != 'nt',
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        group = ProcessGroup(process)
        for target, args in ((reader, (process.stdout, MAX_RESPONSE, True)),
                             (reader, (process.stderr, MAX_STDERR, False)), (writer, ())):
            worker = threading.Thread(target=target, args=args, daemon=True)
            workers.append(worker)
            worker.start()
        for _ in workers:
            remaining = deadline - time.monotonic()
            require(remaining > 0 and events.get(timeout=max(.001, remaining)))
        require(process.wait(timeout=max(.001, deadline - time.monotonic())) == 0)
        return bytes(output)
    except (OSError, ValueError, queue.Empty, subprocess.SubprocessError):
        raise Rejected('Staging transport unavailable') from None
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


class StagingSSH:
    def __init__(self, destination):
        self.destination = destination

    def __call__(self, method, body):
        # Verify before EVERY connection; no saved-config or credential fallback.
        verify_material(self.destination)
        return bounded_process(ssh_command(self.destination, method), body)
