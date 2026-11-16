# Overview connection status

Deployed and verified on both domains on 16 November 2026. The running exporter
produces fresh route, listener and dongle TCP observations; public HTTP and browser
checks passed. Reports, existing history data, controls and reporting access were
preserved. Failure/recovery rendering was checked locally and with a browser-only
offline simulation; no production network or device outage was induced.

## Files and responsibilities

- `connection-status.js`: pure status classification and the translated panel.
  English, Greek and Russian strings live together. Server text is never inserted
  as HTML. Missing, old or contradictory diagnostics produce an unknown cause.
- `connection-status.css`: Overview-only layout, readable stale values, neutral
  battery activity colours and narrow translated Electrical details headings.
- `app.js`: passes request/online state to the panel; keeps the newest complete
  timestamped snapshot in memory; retries automatically and on reconnection.
- `server/connection_probe.py`: read-only route, ping and TCP-session observations
  inside the existing Home Assistant network namespace. It does not send dongle
  discovery/configuration packets or invoke any equipment controls.
- `server/connection_health.py`: a single bounded background probe per exporter,
  at most once every 30 seconds. Results expire after 90 seconds. Three consecutive
  failed observations establish an unavailable path; errors are unknown. A long
  observation gap resets that sequence. Successful observations recover immediately.
- `server/export_energy.py`: adds the allowlisted `health` object to the existing
  energy response. The recording call and its readings are unchanged. Diagnostic
  failures, invalid configuration or missing tools do not stop recording.
- `server/preview_connection.py`: a loopback-only scenario preview with synthetic
  readings, history and reports; no upstream requests or write/control routes.

## What each state means

1. Viewer offline: the browser reports offline and the request fails. This is a
   hint, not proof that the visitor has no internet. A successful fresh request
   wins over that hint. First visits while offline still need the browser to load
   the page; no offline application cache is introduced.
2. Data service unreachable: an HTTP/network/validation failure. The server's VPN
   and dongle diagnostics become unknown, not falsely green or red.
3. VPN unreachable: repeated checks cannot reach the configured router through
   the configured WireGuard interface. This describes path reachability, not a
   proven router/WireGuard fault. ICMP filtering can also prevent a response.
4. Dongle disconnected: VPN reachability is confirmed, the monitoring listener
   exists, but its expected peer has no established TCP connection.
5. Inverter readings delayed: VPN reachability and a dongle TCP session are
   present, but the inverter heartbeat is old. An established TCP socket is not
   proof that the dongle is responsive; a half-open connection can remain present.
6. Listener unavailable: the VPN works but no listener/session is present on the
   configured monitoring port. Do not blame the dongle for a server-side failure.

Partial readings remain distinct from connection failures. Missing values remain
dashes, valid zero readings remain zero, and no measurements are interpolated or
backfilled. A failed/empty/older response never replaces a newer saved snapshot.
Cached observations are ignored when the exporter response itself is stale.

## Deployment configuration

The exporter health probe is disabled unless explicitly configured. The existing
service account must already have access to the named container; this feature
does not grant Docker access, mount its socket into the website or create an API
that executes commands. Fixed arguments come only from service configuration:

```ini
ENERGY_HEALTH_CONTAINER=homeassistant
ENERGY_HEALTH_ROUTER=<home-router-vpn-ip>
ENERGY_HEALTH_INTERFACE=wg0
ENERGY_HEALTH_LISTENER_PORT=8899
```

The selected container must have Python, `ip`, `ping`, `/proc/net/tcp`, and the
actual WireGuard route. The probe gets eight seconds maximum; an individual ping
waits two seconds. No raw keys, addresses, identifiers, stderr or diagnostics are
published. The public payload contains only fixed check names, states and times.
Browser retries refresh the cached response; they do not trigger extra probes.

The [release helper](../deploy/README-connection-status.md) verifies those assumptions
read-only, adds the two static asset routes and versioned references, preserves
backups and checks actual probe output after the exporter restart. Local synthetic
tests do not certify live device detection. Production failure states are not
created deliberately for verification.

## Preview and tests

With an installed IANA timezone database:

```text
python -B server/preview_connection.py --port 8778
python -B -m unittest discover -s server -p test_connection_health.py
node server/test_connection_status.cjs
```

Open `http://127.0.0.1:8778/`. The local-only selector covers ten scenarios and
can simulate viewer-offline without disconnecting the computer. Switching from
Live to a failed request demonstrates retained readings; switching back to Live
demonstrates recovery. A fresh `?state=empty` page demonstrates no stored readings.
The simulation controls are injected by the preview server and are not present
in deployable HTML. Existing calendar, history and reporting code remains intact.
