# Architecture and maintenance

## Scope and ownership

Home Energy is a read-only presentation and recording layer. Home Assistant, EyeBond Local, inverter firmware, battery BMS and VPN routing are external prerequisites, not bundled components. This code does not control the inverter or establish the collector connection.

The original installation uses an ANENJI ANJ-HHS-11KW-48V-WIFI inverter and an ANJ-314AH-S LiFePO4 battery. Model names describe context, not a guarantee of protocol support. Entity availability and interpretation must be verified against the installed integration/firmware.

## File map

| File | Responsibility |
| --- | --- |
| `index.html`, `styles.css` | Overview structure, SVG scene, responsive layout |
| `app.js` | Overview translations, fetch/presentation, freshness, mode and animation |
| `history.html`, `history.css` | History controls and chart layout |
| `history.js` | History fetch, aggregation, summaries, SVG charts, zoom, shared cursor, peaks, translations |
| `server/export_energy.py` | Read-only Recorder query, validation, live snapshot publication, recording loop |
| `server/history_store.py` | Deduplication, minute totals/counts, private SQLite and public daily JSON |
| `server/test_export_energy.py` | Synthetic Recorder tests: freshness, invalid readings, grid composition |
| `server/test_history_store.py` | History tests: deduplication, averages, signs and missing values |
| `server/preview_history.py` | Loopback-only synthetic history preview |
| `deploy/*.example` | Sanitized manual deployment templates, not active configuration |

No build pipeline is required. Generated data belongs outside the public source tree and outside Git. Legacy one-time activation scripts and outdated deployment notes are intentionally excluded.

## Source entities

`ENERGY_ENTITY_PREFIX` is prepended to these suffixes. Recorder tables used are `states_meta`, `states` and `state_attributes`; schema changes in Home Assistant may require exporter updates.

| JSON key | Suffix | Required unit |
| --- | --- | --- |
| `grid` | `grid_to_home_power` + `grid_to_battery_power` | W |
| `pv` | `pv_power` | W |
| `battery` | `battery_power` | W |
| `soc` | `battery_percent` | % |
| `load` | `load_power` | W |
| `pv_voltage` | `pv_voltage` | V |
| `grid_voltage` | `grid_voltage` | V |
| `pv_current` | `pv_current` | A |
| `battery_current` | `battery_average_current` | A |
| `load_current` | `output_current` | A |

`inverter_time` supplies the report heartbeat through its Recorder update timestamp. `operating_mode` supplies an allowlisted mode string. The exporter does not derive current from watts divided by volts; it reads integration entities. In particular, do not infer AC current from watts/volts without accounting for power factor.

Finite numeric values and matching units are required; SOC must be 0-100. Missing/invalid readings become `null`, not zero. Both grid components must be valid and nonnegative; grid is an import estimate, not an export meter.

## Live data lifecycle

Every five seconds the exporter opens Recorder with SQLite `mode=ro`, enables `query_only`, reads within a transaction, closes the connection, and atomically replaces `energy.json`. Only allowlisted data is published. It then records history.

Snapshot fields are `values`, `updated_at` (heartbeat Unix seconds), `generated_at` (exporter Unix seconds), `status`, and normally `mode`. Status is `live`, `partial`, `stale` or `offline`. A heartbeat is fresh from 30 seconds ahead to 90 seconds behind exporter time. Source errors publish null readings with `offline` status.

The overview polls every five seconds while visible. This does not increase the collector's reporting rate. It also checks snapshot freshness (20 seconds) and heartbeat age (90 seconds). The translated red communication-loss state means readings are not trustworthy as current; it cannot identify whether Wi-Fi, VPN, collector or another upstream component failed. If the web server itself is unreachable, a new visit cannot load the application at all.

The heartbeat is a device-level freshness check, not proof that every individual entity updated at the same instant. A publish filesystem failure can stop the exporter; systemd restarts it, so inspect logs and disk permissions for repeated failures.

## History storage and contracts

Only fresh `live`/`partial` reports with a newer heartbeat than the last recorded report are accepted. Each metric stores a sum and count in a UTC minute bucket. `Europe/Athens` determines its calendar day, including daylight-saving transitions.

- Private `runtime/history.sqlite3`: `metadata` checkpoint and `readings` minute/day/metric/total/count rows.
- Public `runtime/history/index.json`: available day strings and latest recorded timestamp.
- Public `runtime/history/YYYY-MM-DD.json`: `day` and `points`; each point has Unix timestamp `t`, per-metric `values` averages and `counts`.

Missing values are excluded from each metric's denominator. Daily files and the index are atomically replaced individually, not as one multi-file transaction. The private store has no automatic pruning. Monitor disk growth and back it up using a SQLite-consistent method.

Outages do not count stale readings as new energy. Recording resumes after fresh reports return, leaving gaps. There is no historical backfill/replay path. Restarting does not duplicate already recorded heartbeat timestamps. A badly future-dated checkpoint can suppress later reports until timestamps catch up; inspect clock synchronization if recording unexpectedly stops.

## Charts and summaries

Day/week/month views use Athens calendar boundaries; weeks start Monday. Auto averaging selects a resolution for the displayed range; explicit intervals include 1 minute, 5 minutes, 15 minutes and 1 hour. Grouping weights minute averages by their sample counts. Refer to the resolution selection in `history.js` for the exact current Auto thresholds.

History does not periodically reload readings: it fetches on entry/range selection and through the explicit Refresh button, which has a five-minute cooldown. A small countdown timer is not data polling. A full browser reload starts a new visit.

Dragging selects a zoomed time window; Reset zoom restores the period. Hover/tap inspection shares a timestamp across charts. Power, battery charge, voltages and currents use separate chart groups.

Energy summaries integrate minute-average watts over represented time, dividing watt-seconds by 3,600,000 for kWh. They summarize the selected calendar period; missing intervals are excluded rather than filled. They are estimates, not raw high-frequency meter totals, and incomplete recording means incomplete totals.

- Solar generated: integrated PV power.
- Grid consumed: integrated estimated grid import.
- House usage: integrated load power.
- Battery supplied: integrated `max(0, -battery_power)`; charging is not subtracted from discharge energy.
- Solar to house: matching intervals of `load - grid - max(0, -battery_power)`, with a nonnegative final total. This assumes grid charging of the battery is disabled; conversion losses, timing differences and estimated entities affect accuracy.

Peaks refer to displayed averaged values in the visible range, not guaranteed instantaneous hardware extremes. Multi-series charts intentionally use solid lines and per-series min/max buttons below, avoiding overlapping peak callouts. Selecting a peak inspects its time and temporarily emphasizes that series. Single-series charts can show inline labels. A persisted toggle hides peak indicators.

## Display conventions and decisions to preserve

The overview uses the original SVG house at 25% opacity beneath direct box-to-box SVG paths. A ResizeObserver recalculates endpoints at actual card edges after resizing or translation. Only active estimated routes appear: grid to house, solar to house, battery to house, and solar to battery. Solar-to-house visibility requires all relevant readings and uses the same load-minus-grid-minus-discharge assumption as summaries, capped by PV power. Solar-to-battery assumes grid charging remains disabled. Arrows are qualitative, not separately measured branch wattages. Missing data does not imply a zero flow; stale/offline data hides every route. Reduced-motion preferences disable moving dots.

Secondary values that round to zero (current below 0.05 A, battery power below 0.5 W in magnitude) are hidden. Main power readings and SOC remain. Battery amperage is omitted from the lower summary, but remains in the scene while battery power is active. In Mains mode, idle battery power with reported SOC below 40% displays Waiting to charge, based on the owner's configured return threshold, not a BMS status. Unknown battery power shows No report. EN/RU/EL translations cover the new labels. These presentation rules do not change recorded readings or history.

- Positive battery power means charging; negative means discharging. Green/red/gray indicate charging/discharging/idle or unavailable as implemented by the current UI.
- Battery percentage remains visible. Battery current is A (average). Do not relabel it as Ah or present SOC multiplied by nameplate capacity as measured BMS remaining capacity.
- `Mains` is displayed as Grid and `Off-Grid` as Solar. The latter is a friendly label, not proof that all load is supplied by PV rather than battery.
- Grid is visibly labelled estimated. Small nonzero estimates are not proof of physical grid import.
- Power uses W or kW with trimmed decimals. Voltage and current retain their own units.
- All user-facing additions need English, Greek and Russian translations.
- Preserve desktop layout when changing mobile scene labels. Check narrow screens and longer translated labels.

## Security and maintenance checklist

1. Treat the public JSON allowlist as a security boundary. Never proxy unrestricted Home Assistant endpoints or place its token in browser code.
2. Keep certificates, private keys, account paths, serials, runtime data and logs out of commits. `.gitignore` does not protect already tracked files; inspect staged content before pushing.
3. Keep Nginx's explicit route allowlist. Never expose runtime SQLite, Python source or a whole Home Assistant directory.
4. Before deploying, run tests and JS syntax checks; inspect frontend in all languages and both phone/desktop layouts. Tests alone do not verify responsive rendering.
5. After a Home Assistant/integration upgrade, verify entity names, units, heartbeat, signs and Recorder schema. Compare with the device display at matching timestamps.
6. Before modifying formulas, document source assumptions and test gaps, nulls, negative values and partial periods.
7. Back up runtime separately, monitor service logs and storage, and verify restore procedures. Git stores code, not historical measurements.

The initial publication sanitized deployment settings, added an environment-configurable entity prefix, and corrected test-fixture IDs. Subsequent commits may include explicitly approved frontend deployments; verify deployed hashes instead of assuming Git and production match. No license has been selected in this repository.
