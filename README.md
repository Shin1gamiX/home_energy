# Home Energy

A mobile-first home energy dashboard for a solar inverter and battery monitored through Home Assistant and EyeBond Local. Readings and history remain read-only; an optional, separately deployed password-protected service can restart the monitoring dongle. Includes an overview and interactive history, in English, Greek and Russian.

This repository contains application code and generic deployment examples, **not a backup of Home Assistant or a running installation**. It contains no production readings, credentials, certificates or device serial numbers. Existing public project branding is retained.

## What it does

- Displays solar, estimated grid import, house consumption, battery charge percentage and signed battery power, with available voltage/current readings.
- Shows inverter mode and a translated communication-loss warning when reports stop arriving.
- Records fresh readings into minute aggregates, with daily, weekly and monthly history, plus all-time kWh totals and a monthly breakdown.
- Supports metric filters, averaging intervals, drag-to-zoom, synchronized inspection and optional minimum/maximum indicators.
- Publishes a deliberately public, no-login view. It does not change inverter settings.
- Offers an optional **Restart dongle** password dialog: two wrong passwords lock out the client network for five minutes; a five-minute restart cooldown is global. It remains unavailable until privately configured. See [Collector restart setup and security](docs/CONTROL.md).
- Records credential-free restart security events in the private system journal, accessible to server administrators over SSH; see [log access and event meanings](docs/CONTROL.md#private-security-event-log).

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

The exporter reads an allowlist of Home Assistant entities directly from Recorder SQLite. **The exporter uses no Home Assistant REST/WebSocket API or access token.** The optional collector-control service is separate: it uses a private server-side Home Assistant token for one fixed button action. The browser never receives that token or database access. A remote installation needs an independently configured network path between the collector and Home Assistant.

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

Open `http://127.0.0.1:8766/history.html` and select **All time**. Run only one of these preview servers at a time. The history helper supplies three calendar months of simulated readings, including gaps, and uses the same cumulative-energy calculations as the exporter. It is not a production server or complete hardware simulator; none of its readings are real.

## Checks

```sh
python3 -B -m unittest discover -s server -p 'test_*.py' -v
node --check app.js
node --check history.js
node --check history-calendar.js
node server/test_overview.cjs
node server/test_comets.cjs
node server/test_history_ui.cjs
node server/test_history_calendar.cjs
node server/test_language_picker.cjs
node server/test_not_found_ui.cjs
python3 -B -m unittest discover -s server -p 'test_not_found_nginx.py' -v
```

Node is needed only for these optional JavaScript syntax checks. Tests use synthetic in-memory/temporary databases, never a live Home Assistant database. Tests do not certify hardware accuracy or compatibility with every Home Assistant version.

The intended backend target is Linux. On Windows, Python may lack IANA timezone data, and history tests can encounter temporary-file cleanup errors because SQLite connections are not explicitly closed by the current history code. The initial publication passed all eight Python tests on Linux and both JavaScript syntax checks; Windows backend portability remains unverified.

## Deployment

These are manual preparation steps, not an automated installer. Review examples for your host before applying them.

1. Place `index.html`, `styles.css`, `app.js`, `history.html`, `history.css`, `history.js`, `history-calendar.js`, `404.html`, `404.css` and `404.js` in `/var/www/homeenergy/public/`. The optional restart dialog has separate [deployment instructions](docs/CONTROL.md).
2. Place `server/export_energy.py`, `server/history_store.py` and `server/energy_summary.py` in `/var/www/homeenergy/server/`.
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

### Custom not-found page

Missing URLs use a small branded page with Overview/History links and English,
Russian and Greek translations. It uses the saved `homeenergy-language` preference,
defaults to English and remains navigable without JavaScript. It does not load
telemetry or restart controls, or display the requested path or query string.
The styled language menu supports pointer/touch, arrow keys, Home/End, initial-letter
navigation and Escape; Tab leaves the menu without trapping focus. Its semantics follow
the [WAI-ARIA menu-button pattern](https://www.w3.org/WAI/ARIA/apg/patterns/menu-button/).
Menu animation is disabled when the browser requests reduced motion.

Deploy all three `404.*` files before enabling the **Home Energy not found** block
in the Nginx example. Merge that block into the existing HTTPS server; do not replace
an installed vhost or its collector-control configuration with the generic example.
Root-absolute asset URLs work even for missing nested paths. Keep `error_page 404
/404.html;` without `=200`: the original request must remain HTTP 404. The exact
HTML location is internal, and the existing public-file allowlist stays intact.

All responses using this error page have access logging disabled, including the
blocked collector-status route. This intentionally sacrifices missing-page access
statistics to preserve the control route's privacy after Nginx's internal redirect.
Private control-service security events are unaffected. See Nginx's
[error-page](https://nginx.org/en/docs/http/ngx_http_core_module.html#error_page) and
[access-log](https://nginx.org/en/docs/http/ngx_http_log_module.html#access_log) semantics.

The Node test checks translations and isolated browser logic. The Python integration
test starts an installed Nginx on an ephemeral loopback port with synthetic control
routes; it never contacts Home Assistant or requests a real restart. It skips when
Nginx is unavailable. A plain Python static server can preview `/404.html`, but does
not reproduce production error routing: verify missing/deep URLs and the blocked
status route against Nginx before considering deployment complete.

## Privacy and operational limits

The website has no login by design. Public live readings and history can reveal household activity. Add access control at the proxy if this is not acceptable. Never publish the Recorder database, history SQLite database, `.env`, SSH keys or TLS keys.

Grid import and some derived readings are estimates, not billing-grade measurements. Battery current is **A (average)**, not remaining **Ah**. History begins when recording starts and does not recover missing outage periods. Keep runtime backups separately from Git; there is currently no automatic retention limit.

## Working on this project later

Start with this README and [ARCHITECTURE.md](docs/ARCHITECTURE.md), then inspect current code and deployment settings. Do not assume repository defaults describe an existing server. Production updates and inverter-setting changes are separate operations requiring explicit approval. This initial publication intentionally leaves the deployed installation untouched.

## All-time energy

History's **All time** option shows recorded Solar generated, Grid consumed,
House usage, Battery supplied, Battery charged and Solar to house in kWh. It includes the recording
date span, coverage for each reading, monthly totals and a grand total. Select a
month to open its existing graphs; Today returns to the current day.
Recorded duration uses days plus remaining hours (for example, `5 d - 21.2 h`),
or just hours below one day. Hours are rounded to one decimal for display only;
one recorded day represents 24 hours of data, not a calendar day.

These are totals **since this project began recording**, not inverter lifetime
counters. Outages are excluded, not filled with zeros. Totals use unrounded minute
averages; displayed monthly values may differ from the displayed grand total by
a rounding fraction. Monthly rows include completely missing months and mark the
current month as in progress. The solar aggregate is counted once; PV1 and PV2
are not added on top of it.

**Battery charged** counts positive recorded battery power; **Battery supplied**
counts negative power as positive discharge energy. Neither offsets the other.
Charging can come from solar or grid; this card is not remaining battery capacity
and does not measure losses. Both use minute averages, so reversals within a minute
can undercount charge/discharge throughput. Daily, weekly and monthly summaries
calculate both from existing history; all-time totals use the derived cache.

On the first fresh report after installing the backend update, the exporter
builds a derived daily cache from existing private history SQLite rows and adds
compact monthly totals to the existing public `history/index.json`. All time does
not fetch every daily history file. No additional service, route or dependency is
required. Until that summary exists, the UI explains that it is unavailable;
Day, Week and Month continue to work. With a version-1 summary, existing totals
still work and Battery charged is shown as unavailable rather than zero. The
version-2 backend rebuilds old derived caches from saved readings on the next
fresh report, without altering raw history. Deploy the compatible frontend first
and reload older browser tabs after the backend update. See
[the cache contract](docs/ARCHITECTURE.md#cumulative-energy-cache).

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
