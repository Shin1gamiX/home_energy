# Home Energy

A mobile-first, read-only home energy dashboard for a solar inverter and battery monitored through Home Assistant and EyeBond Local. Includes an overview and interactive history, in English, Greek and Russian.

This repository contains application code and generic deployment examples, **not a backup of Home Assistant or a running installation**. It contains no production readings, credentials, certificates or device serial numbers. Existing public project branding is retained.

## What it does

- Displays solar, estimated grid import, house consumption, battery charge percentage and signed battery power, with available voltage/current readings.
- Shows inverter mode and a translated communication-loss warning when reports stop arriving.
- Records fresh readings into minute aggregates, with daily, weekly and monthly history and energy summaries.
- Supports metric filters, averaging intervals, drag-to-zoom, synchronized inspection and optional minimum/maximum indicators.
- Publishes a deliberately public, no-login view. It does not change inverter settings.

## How it works

```text
Inverter / battery -> EyeBond collector -> Home Assistant + EyeBond Local
                                           |
                                  Recorder SQLite (read-only)
                                           |
                                    Python exporter
                                     /           \
                            energy.json      history SQLite + JSON
                                     \           /
                                      Nginx HTTPS
                                           |
                                    Browser dashboard
```

The exporter reads an allowlist of Home Assistant entities directly from Recorder SQLite. **No Home Assistant REST/WebSocket API or access token is used.** The browser receives only selected JSON values, not access to Home Assistant or its database. A remote installation needs an independently configured network path between the collector and Home Assistant.

See [Architecture and maintenance](docs/ARCHITECTURE.md) for the file map, entity mapping, calculations, limitations and future-work guidance.

## Installation hardware

The original installation uses an **ANENJI ANJ-HHS-11KW-48V-WIFI** inverter and an
**ANENJI ANJ-314AH-S** LiFePO₄ battery (51.2 V, 314 Ah, 16.07 kWh), with solar
connected to **PV1 and PV2**.

See [Hardware reference](docs/HARDWARE.md) for the photographed label
specifications, previously reported charge/BMS settings, monitoring equipment
and the panel/string details still to be confirmed. This reference records known
installation information; it does not verify current device settings.

## Requirements

- Python 3.9+ with SQLite and the `Europe/Athens` timezone available. Standard library only; no Python package installation is required on a Linux system with timezone data.
- Home Assistant Recorder using SQLite and matching EyeBond entities. MariaDB/PostgreSQL Recorder installations are not supported by this exporter.
- Nginx, an existing TLS certificate and a suitable hostname for production.
- A modern browser. Frontend is plain HTML/CSS/JavaScript: no npm dependencies or build step.

The default entity prefix reflects the original ANENJI inverter integration. It is configurable, but other models may also need changes to suffixes, units or sign conventions. Verify readings before relying on them.

## Local preview

From the repository root:

```sh
python3 -B -m http.server 8766 --bind 127.0.0.1
```

Open `http://127.0.0.1:8766/` for the overview's built-in demo. Demo readings are not live measurements.

For synthetic history:

```sh
python3 -B server/preview_history.py
```

Open `http://127.0.0.1:8767/history.html`. This development helper supplies a subset of metrics for today's date; it is not a production server or complete hardware simulator.

## Checks

```sh
python3 -B -m unittest discover -s server -p 'test_*.py' -v
node --check app.js
node --check history.js
node server/test_overview.cjs
```

Node is needed only for these optional JavaScript syntax checks. Tests use synthetic in-memory/temporary databases, never a live Home Assistant database. Tests do not certify hardware accuracy or compatibility with every Home Assistant version.

The intended backend target is Linux. On Windows, Python may lack IANA timezone data, and history tests can encounter temporary-file cleanup errors because SQLite connections are not explicitly closed by the current history code. The initial publication passed all eight Python tests on Linux and both JavaScript syntax checks; Windows backend portability remains unverified.

## Deployment

These are manual preparation steps, not an automated installer. Review examples for your host before applying them.

1. Place the six root HTML/CSS/JS files in `/var/www/homeenergy/public/`.
2. Place `server/export_energy.py` and `server/history_store.py` in `/var/www/homeenergy/server/`.
3. Create `/var/www/homeenergy/runtime/`, writable by a dedicated exporter account, e.g. `homeenergy`. Keep scripts and public source non-writable by that account where practical.
4. Set the environment values below and verify every entity suffix in `FIELDS`, plus `grid_to_battery_power`, `inverter_time` and `operating_mode`.
5. Give the exporter read/traverse access to the Recorder database and required SQLite WAL/shared-memory files and directories. Keep the source database private. Read-only WAL access can require existing readable sidecar files; test under the service account rather than granting broad write access or copying only a live `.db` file.
6. Adapt [the systemd example](deploy/homeenergy.service.example), install it as `homeenergy.service`, reload systemd and start it. Inspect service logs and generated JSON before enabling public access.
7. Adapt [the Nginx example](deploy/nginx.conf.example) for your hostname and existing certificate. Validate with `nginx -t` before reloading. If using a CDN/proxy, ensure HTTPS to the origin and bypass caching for live/history JSON.
8. Verify HTTPS, overview/history, each language, freshness warnings and mobile layouts. Confirm database, scripts and private configuration cannot be requested through Nginx. Enable the exporter at boot once validated.

| Variable | Default | Purpose |
| --- | --- | --- |
| `HA_DATABASE` | `/srv/homeassistant/config/home-assistant_v2.db` | Absolute path to Recorder SQLite |
| `ENERGY_OUTPUT` | `/var/www/homeenergy/runtime/energy.json` | Snapshot path; parent must already exist |
| `ENERGY_ENTITY_PREFIX` | `sensor.anenji_anj_11kw_48v_wifi_p_` | Prefix prepended to entity suffixes |

The supplied service restricts writes to runtime and network socket families to local Unix sockets. Adjust its paths/account deliberately; copying the repository alone does not deploy anything.

## Privacy and operational limits

The website has no login by design. Public live readings and history can reveal household activity. Add access control at the proxy if this is not acceptable. Never publish the Recorder database, history SQLite database, `.env`, SSH keys or TLS keys.

Grid import and some derived readings are estimates, not billing-grade measurements. Battery current is **A (average)**, not remaining **Ah**. History begins when recording starts and does not recover missing outage periods. Keep runtime backups separately from Git; there is currently no automatic retention limit.

## Working on this project later

Start with this README and [ARCHITECTURE.md](docs/ARCHITECTURE.md), then inspect current code and deployment settings. Do not assume repository defaults describe an existing server. Production updates and inverter-setting changes are separate operations requiring explicit approval. This initial publication intentionally leaves the deployed installation untouched.

## Supply-mode history

The History **House supply** toggle shows estimated house supply (Grid, Solar, Battery,
or Mixed), not the inverter's reported operating mode. It follows the selected
day/week/month and visible chart range, independently of the averaging selector.
Hover, tap, or focus an interval for observed timestamps and duration.
Drag across the timeline or use Zoom in to inspect short intervals; Reset zoom
restores the selected period. Zoom is shared with the numeric charts. Dense
consecutive intervals appear as striped groups that can be selected to zoom.
Grouping is visual only: totals and Previous/Next interval navigation retain
the individual recorded transitions. Future hours are shaded as Upcoming.

Fresh reports are compressed into `supply_intervals` in the existing history
SQLite database and exported as `modes` in daily JSON. Changes are timestamped
at the first observed report; precision is limited by polling. A report remains
valid for at most 90 seconds, then the timeline shows No data. Older history is
labelled Mode not recorded rather than reconstructed as exact transitions.

Classification assumes grid charging is disabled: solar-to-house is the smaller
of PV power and house demand minus grid and battery discharge. Sources count as
active above the larger of 20 W and 2% of house load. Multiple active sources mean
Mixed. Missing required readings mean Unknown supply; zero demand means Standby.
These are estimates, not independently metered source allocations.
