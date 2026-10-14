"""Fixed production connection material, checked locally without opening SSH.

No caller-selected path, command, credential or destination is accepted. The
private key is examined through metadata only. There is deliberately no send()
or executable reporting mode in this preparation stage.
"""
import hashlib
import os
from pathlib import Path
import subprocess
import sys

if __name__ == '__main__':
    sys.exit('Production execution is not activated.')

from insights_broker import decode
import insights_staging_acl as acl
import insights_windows_transport as transport


ROOT = Path(r'C:\ProgramData\HomeEnergyReporterProduction')
KEY = ROOT / 'id_ed25519'
PUBLIC_KEY = ROOT / 'id_ed25519.pub'
HOST_PIN = ROOT / 'known_hosts'
BINDING = ROOT / 'integration-binding.json'
BINDING_SHA256 = '285abf34a087b2b364abd0224174e720cd6162647ba8555d98b6b3883ee0106a'
PUBLIC_KEY_SHA256 = '54f417401aa4cda931ece3cc30173787b268cab7e420168a2beb761b6200926d'
HOST_BYTES = b'91.98.44.219 ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOme+3Qm71em72ZSJCMN3fvhboJnjFwIgyUI7GHsEIWA\n'
EXPECTED_BINDING = {
    'schema_version': 1, 'scope': 'production', 'synthetic': False,
    'host': '91.98.44.219', 'port': 22,
    'ssh_sha256': '786ff14be7cd652b2b9770a57e9b1aa5e03a052ce3a3d641fb4760c0ff3fde05',
    'known_hosts_sha256': 'c9eac033de68d2fca971c5e77bb9cfd72343a7703ca4a266d0cb3f03c265ddfe',
}


def require(condition, code):
    if not condition:
        raise ValueError(code)


def acl_script():
    # Reuse the pinned, independently tested descriptor inspection and validator.
    # Only this fixed deployment root changes; no tool or environment supplies it.
    original = r'C:\ProgramData\HomeEnergyReporterStaging'
    require(acl.ACL_METADATA_SCRIPT.count(original) == 4, 'acl_template_changed')
    return acl.ACL_METADATA_SCRIPT.replace(original, str(ROOT))


def verify_permissions():
    require(os.name == 'nt', 'production_windows_required')
    acl._verify_powershell()
    result = subprocess.run(
        [str(acl.POWERSHELL), '-NoLogo', '-NoProfile', '-NonInteractive', '-Command', acl_script()],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env=acl.SAFE_ENV.copy(), timeout=acl.TIMEOUT_SECONDS, check=False,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
    )
    require(result.returncode == 0 and result.stderr == b'', 'production_acl_probe_failed')
    return acl.validate_snapshot(acl.parse_snapshot(result.stdout))


def parse_binding(raw):
    require(type(raw) is bytes and len(raw) <= 1024, 'production_binding_size')
    require(hashlib.sha256(raw).hexdigest() == BINDING_SHA256, 'production_binding_changed')
    value = decode(raw, 1024)
    require(type(value) is dict and value == EXPECTED_BINDING
            and type(value['schema_version']) is int and type(value['port']) is int
            and value['synthetic'] is False, 'production_binding_rejected')
    return transport.Destination(value['host'], value['port'], value['ssh_sha256'],
                                 value['known_hosts_sha256'])


def load_destination():
    verify_permissions()
    # The pinned system SSH executable alone may have Windows servicing links.
    transport.regular_file(transport.SSH, 16 * 1024 * 1024, allow_system_hardlinks=True)
    for path, limit in ((KEY, 16384), (PUBLIC_KEY, 4096), (HOST_PIN, 4096), (BINDING, 1024)):
        transport.regular_file(path, limit)
    destination = parse_binding(BINDING.read_bytes())
    destination.validate()
    require(hashlib.sha256(transport.SSH.read_bytes()).hexdigest() == destination.ssh_sha256,
            'production_ssh_binary_changed')
    require(HOST_PIN.read_bytes() == HOST_BYTES
            and hashlib.sha256(HOST_BYTES).hexdigest() == destination.known_hosts_sha256,
            'production_host_pin_changed')
    require(hashlib.sha256(PUBLIC_KEY.read_bytes()).hexdigest() == PUBLIC_KEY_SHA256,
            'production_public_key_changed')
    return destination


def check_integration():
    load_destination()
    return {'production_material_verified': True, 'private_key_contents_read': False,
            'credential_acl_verified': True, 'host_pin_verified': True,
            'ssh_connected': False, 'model_started': False, 'report_calls_enabled': False,
            'schedule_changed': False}
