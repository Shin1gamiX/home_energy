"""Read-only network observations, executed inside the existing HA network namespace.

No discovery packets, configuration commands, HA token or device controls. A TCP
session proves a connection exists, not that the dongle is delivering readings.
"""
import argparse
import ipaddress
from pathlib import Path
import re
import socket
import subprocess
import json


def tcp_observation(text, router, port):
    listening = connected = False
    for line in text.splitlines()[1:]:
        fields = line.split()
        if len(fields) < 4:
            continue
        try:
            local, remote, state = fields[1:4]
            if int(local.rsplit(':', 1)[1], 16) != port:
                continue
            listening |= state == '0A'
            address = bytes.fromhex(remote.split(':')[0])
            # /proc/net/tcp uses native-endian words, including mapped IPv4 in tcp6.
            address = b''.join(address[i:i + 4][::-1] for i in range(0, len(address), 4))
            peer = ipaddress.ip_address(address)
            if isinstance(peer, ipaddress.IPv6Address):
                peer = peer.ipv4_mapped or peer
            connected |= state == '01' and str(peer) == router
        except (ValueError, IndexError):
            continue
    return listening, connected


def observe(router, interface, port, run=subprocess.run, read=lambda p: Path(p).read_text()):
    result = dict(vpn='unknown', dongle='unknown', collector='unknown')
    try:
        route = run(['ip', '-4', 'route', 'get', router], capture_output=True, text=True, timeout=2)
        if route.returncode != 0:
            return result
        if not re.search(r'\bdev\s+' + re.escape(interface) + r'(?:\s|$)', route.stdout):
            result['vpn'] = 'down'
            return result
        reply = run(['ping', '-n', '-c', '1', '-W', '2', router],
                    capture_output=True, text=True, timeout=3)
        result['vpn'] = {0: 'up', 1: 'down'}.get(reply.returncode, 'unknown')
    except (OSError, subprocess.TimeoutExpired):
        return result
    if result['vpn'] != 'up':
        return result
    try:
        tables = [read('/proc/net/tcp')]
        try:
            tables.append(read('/proc/net/tcp6'))
        except FileNotFoundError:
            pass
        observations = [tcp_observation(table, router, port) for table in tables]
        listening = any(item[0] for item in observations)
        connected = any(item[1] for item in observations)
        result['collector'] = 'up' if listening or connected else 'down'
        result['dongle'] = 'up' if connected else 'down' if listening else 'unknown'
    except OSError:
        pass
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--router', required=True, type=ipaddress.IPv4Address)
    parser.add_argument('--interface', required=True)
    parser.add_argument('--port', required=True, type=int)
    args = parser.parse_args()
    if not re.fullmatch(r'[a-zA-Z0-9_.-]{1,15}', args.interface) or not 1 <= args.port <= 65535:
        parser.error('Invalid interface or listener port')
    print(json.dumps(observe(str(args.router), args.interface, args.port), separators=(',', ':')))
