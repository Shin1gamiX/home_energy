# Architecture and maintenance

## Scope and ownership

Home Energy's presentation and recording layer is read-only. Home Assistant, EyeBond Local, inverter firmware, battery BMS and VPN routing are external prerequisites, not bundled components. An optional, separately deployed [collector-control service](CONTROL.md) can request a monitoring-dongle restart through one fixed Home Assistant button. It cannot change inverter settings or establish the VPN connection. Existing monitoring does not depend on this service.

The original installation uses an ANENJI ANJ-HHS-11KW-48V-WIFI inverter and an ANJ-314AH-S LiFePO4 battery. Model names describe context, not a guarantee of protocol support. Entity availability and interpretation must be verified against the installed integration/firmware.

## File map

| File | Responsibility |
| --- | --- |
| `index.html`, `styles.css` | Overview structure, SVG scene, responsive layout |
| `app.js` | Overview translations, fetch/presentation, freshness, mode and animation |
| `history.html`, `history.css` | History controls and chart layout |
| `history.js` | History fetch, aggregation, summaries, SVG charts, zoom, shared cursor, peaks, translations |
| `history-calendar.js`, `server/test_history_calendar.cjs` | Accessible date picker and tests for recorded-date availability, calendar arithmetic, keyboard focus and selection |
| `404.html`, `404.css`, `404.js` | Standalone translated not-found page; no telemetry or control requests |
| `server/test_not_found_ui.cjs`, `server/test_not_found_nginx.py` | Error-page UI checks and isolated Nginx status, routing and logging-privacy checks |
| `server/export_energy.py` | Read-only Recorder query, validation, live snapshot publication, recording loop |
| `server/history_store.py` | Deduplication, minute totals/counts, private SQLite and public daily JSON |
| `server/energy_summary.py` | Versioned derived daily cache, monthly and all-time energy totals |
| `server/test_energy_summary.py` | Summary integration, cache, timezone, missing-data and browser-parity tests |
| `server/test_export_energy.py` | Synthetic Recorder tests: freshness, invalid readings, grid composition |
| `server/test_history_store.py` | History tests: deduplication, averages, signs and missing values |
| `server/preview_history.py` | Loopback-only synthetic history preview |
| `deploy/*.example` | Sanitized manual deployment templates, not active configuration |

No build pipeline is required. Generated data belongs outside the public source tree and outside Git. Legacy one-time activation scripts and outdated deployment notes are intentionally excluded.

The history calendar highlights dates from the existing `history/index.json` day list.
A highlight means at least one reading exists (including zero values), not complete
daily coverage or a positive kWh total. Availability follows the history snapshot and
updates on refresh; a failed index request is shown as unknown, never as no readings.
Date selection preserves the current day/week/month view and uses Athens dates.

## Source entities

PV1/PV2 channel power, voltage and current are additionally read using
`ENERGY_PV_ENTITY_PREFIX` (default `sensor.living_room_anenji_anj_11kw_48v_wifi_p_`).
Their suffixes are `pv1_power`, `pv1_voltage`, `pv1_current` and the corresponding
`pv2_*` names. This separate prefix accommodates the existing HA entity registry.
The original `pv` aggregate is retained unchanged for summaries and flow estimates;
channel values are not added to it. Compare it against both channel powers during
generation before changing its source. Voltages are displayed separately, not summed.
Channel history starts when recording is deployed; older files remain valid and
missing channel readings are gaps, never fabricated zeros. Generic PV voltage/current
remain selectable in history for access to older records.

Generic `pv_voltage` history is retired: existing rows are retained and selectable
as "PV voltage (legacy)", but new samples are not recorded and Select all excludes
this series. The live API/HA sensor is unchanged; PV1/PV2 voltage recording continues.

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
- Public `runtime/history/index.json`: available day strings, latest recorded timestamp and compact `energy_summary` totals.
- Public `runtime/history/YYYY-MM-DD.json`: `day` and `points`; each point has Unix timestamp `t`, per-metric `values` averages and `counts`.

Missing values are excluded from each metric's denominator. Daily files and the index are atomically replaced individually, not as one multi-file transaction. The private store has no automatic pruning. Monitor disk growth and back it up using a SQLite-consistent method.

Outages do not count stale readings as new energy. Recording resumes after fresh reports return, leaving gaps. There is no historical backfill/replay path. Restarting does not duplicate already recorded heartbeat timestamps. A badly future-dated checkpoint can suppress later reports until timestamps catch up; inspect clock synchronization if recording unexpectedly stops.

## Charts and summaries

Overview motion speed uses each route's estimated wattage, not total PV generation
for every solar branch. Grid/battery-to-house wattage is capped by house load;
solar-to-battery is capped by both PV generation and positive charging power.
At 300 W or below, dots move one 64-pixel spacing every 3 seconds (the original
speed). Speed increases linearly up to 3x at 2,000 W and remains capped above
that. Duration is `3 / (1 + 2 * clamp((watts - 300) / 1700, 0, 1))` seconds.
Zero/inactive routes stay hidden; stale-data and reduced-motion behavior remain.

Day/week/month views use Athens calendar boundaries; weeks start Monday. Auto averaging selects a resolution for the displayed range; explicit intervals include 1 minute, 5 minutes, 15 minutes and 1 hour. Grouping weights minute averages by their sample counts. Refer to the resolution selection in `history.js` for the exact current Auto thresholds.

History does not periodically reload readings: it fetches on entry/range selection and through the explicit Refresh button, which has a five-minute cooldown. A small countdown timer is not data polling. A full browser reload starts a new visit.

The cached index retains its fetch time as well as the latest report timestamp. House-supply gaps are inferred only up to that fetch time; time beyond it is "Not refreshed yet", not an outage. The toolbar shows when the snapshot was loaded. Refreshing can reveal real gaps after the previous snapshot, while re-rendering or changing language cannot invent one.

Dragging selects a zoomed time window; Reset zoom restores the period. Hover/tap inspection shares a timestamp across charts. Power, battery charge, voltages and currents use separate chart groups.

Energy summaries integrate minute-average watts over represented time, dividing watt-seconds by 3,600,000 for kWh. They summarize the selected calendar period; missing intervals are excluded rather than filled. They are estimates, not raw high-frequency meter totals, and incomplete recording means incomplete totals.

- Solar generated: integrated PV power.
- Grid consumed: integrated estimated grid import.
- House usage: integrated load power.
- Battery supplied: integrated `max(0, -battery_power)`; charging is not subtracted from discharge energy.
- Battery charged: integrated `max(0, battery_power)`, using the same signed readings and coverage as Battery supplied. It includes charging from any source, not just solar, and is not remaining capacity or an estimate of losses. Zero battery power is recorded coverage; missing/nonfinite power is not. Because each direction is split after minute averaging, charge/discharge reversals within one minute can undercount both directions.
- Solar to house: matching intervals of `min(pv, max(0, load - grid - max(0, -battery_power)))`. All four readings must be available. Each interval is bounded by reported PV generation, preventing nighttime measurement residuals from being counted as solar. This remains an estimate and assumes grid charging is disabled; conversion losses, timing differences and estimated entities affect accuracy. Existing recorded data is unchanged; summaries are recalculated when viewed.

### Cumulative energy cache

All time reads `energy_summary` from the existing history index, without loading
all daily JSON files. The version-2 contract contains `from`/`to` Unix timestamps,
`totals` for `pv`, `grid`, `load`, `battery`, `battery_charged`, `solar_to_house`, and ascending `months`
rows with `month` (`YYYY-MM`), `from`, `to` and the same totals. Every metric stores
unrounded `kwh` and represented `seconds`. Empty history uses null boundaries and
no month rows. The browser validates bounds and verifies that monthly totals
reconcile with the grand total before displaying them.

The frontend also accepts version 1 during an upgrade: its five existing totals
remain visible, while Battery charged displays an em dash and "Not available".
Version 2 requires charging totals in every month and in the grand total, with
the same finite-value, coverage and reconciliation checks as other metrics.
Deploy the compatible frontend before upgrading the summary backend. Old browser
tabs need a reload after the backend version changes.

`energy_daily_summary` is a derived table in private history SQLite, not new raw
telemetry. On the next accepted fresh report, missing or old-version cached days
are calculated from minute averages. Subsequent reports recalculate the current
day and last observed day (to finalize the previous day's partial last minute).
The version-2 upgrade rebuilds old derived caches on the next fresh report to
recover charging energy from existing signed battery readings; raw history is
not modified. Afterwards, older cached days are not scanned again. The small daily summaries are combined
into monthly and overall totals; changes commit with the normal history record.
The existing raw day/metric rows and heartbeat deduplication remain unchanged.

Integration matches `energyTotals` in `history.js`: each recorded minute contributes
at most 60 seconds, clipped at the latest report timestamp. Missing minutes and
invalid metric values contribute no represented time. Solar-to-house requires all
four matching readings and is bounded by PV power. No imputation or rounding is
applied before aggregation. Coverage is represented seconds divided by elapsed
recording span, not a guarantee of individual-sample completeness or meter accuracy.
Calendar months use Athens time, including daylight-saving changes. Entirely missing
months have zero coverage and display No report, not a measured zero.

The first backfill costs a scan of existing raw history. Deploy the helper alongside
`history_store.py`; an offline exporter cannot publish the new summary until a
fresh report arrives. A frontend-only update gracefully leaves All time unavailable.
Back up private SQLite before deployment. If historical raw rows are deliberately
repaired/imported later, invalidate the corresponding derived cache rows as part
of that approved maintenance, or bump `SUMMARY_VERSION` when calculation rules
change. Normal collection does not mutate completed historical days.

Peaks refer to displayed averaged values in the visible range, not guaranteed instantaneous hardware extremes. Multi-series charts intentionally use solid lines and per-series min/max buttons below, avoiding overlapping peak callouts. Selecting a peak inspects its time and temporarily emphasizes that series. Single-series charts can show inline labels. A persisted toggle hides peak indicators.

## Display conventions and decisions to preserve

The overview uses the original SVG house at 25% opacity beneath direct box-to-box SVG paths. A ResizeObserver recalculates endpoints at actual card edges after resizing or translation. Only active estimated routes appear: grid to house, solar to house, battery to house, and solar to battery. Solar-to-house visibility requires all relevant readings and uses the same load-minus-grid-minus-discharge assumption as summaries, capped by PV power. Solar-to-battery assumes grid charging remains disabled. Arrows are qualitative, not separately measured branch wattages. Missing data does not imply a zero flow; stale/offline data hides every route. Reduced-motion preferences disable moving dots.

Secondary values that round to zero (current below 0.05 A, battery power below 0.5 W in magnitude) are hidden. Main power readings and SOC remain. Battery amperage is omitted from the lower summary, but remains in the scene while battery power is active. In Mains mode, idle battery power with reported SOC below 40% displays Waiting to charge, based on the owner's configured return threshold, not a BMS status. Unknown battery power shows No report. EN/RU/EL translations cover the new labels. These presentation rules do not change recorded readings or history.

- Positive battery power means charging; negative means discharging. Green/red/gray indicate charging/discharging/idle or unavailable as implemented by the current UI.
- Battery percentage remains visible. Battery current is A (average). Do not relabel it as Ah or present SOC multiplied by nameplate capacity as measured BMS remaining capacity.
- The Inverter badge displays `Mains` as Grid and preserves `Off-Grid` as Off-Grid, not Solar. A separate House supply line estimates Grid, Solar, Battery or Mixed from the house-bound flows using the same 20 W / 2% threshold as recorded supply history. Neither label is an independent source meter.
- Grid is visibly labelled estimated. Small nonzero estimates are not proof of physical grid import.
- Power uses W or kW with trimmed decimals. Voltage and current retain their own units.
- All user-facing additions need English, Greek and Russian translations.
- Preserve desktop layout when changing mobile scene labels. Check narrow screens and longer translated labels.

## Interface refinements (02/10/2026)

- Overview/History share the same navigation. The overview keeps its house illustration and animated routes, with secondary electrical readings in a collapsed details section. Live freshness is visible near the heading. Grid is purple on both pages.
- History keeps a sticky date/period selector and chart jump links. Its selected statistics are grouped in a disclosure; generic old PV voltage/current are in Legacy readings. On phones, summary cards scroll horizontally without widening the page.
- Numerical chart axes use rounded outward bounds and include a labelled zero. Battery power/current explain positive charging and negative discharging; SOC remains fixed at 0-100%. Series measurements and averaging are unchanged.
- Supply history displays only rows present in the selected range. Indistinguishable consecutive transitions share one neutral Rapid changes lane, never simultaneous copies on several source rows. Selecting that block zooms in. Original intervals still determine exact inspection and totals; missing/unrecorded/unknown intervals are never grouped into source transitions.
- Fit recorded time shares its zoom with every graph and has a two-minute minimum. Reset zoom restores the chosen calendar period. The supply timeline remains independent of numeric averaging.
- All added production labels have English, Russian and Greek versions. No new dependencies or external assets are required.

Regression commands (no installation needed):

```text
node --check app.js
node --check history.js
node server/test_overview.cjs
node server/test_history_ui.cjs
node server/test_language_picker.cjs
python -B -m unittest discover -s server -p 'test_*.py'
```

## Shared language menu

Overview and History share their language menu in `app.js` and `styles.css`.
It matches the standalone 404 picker, reads the same `homeenergy-language`
preference (English by default), and keeps the `languagechange` event used by
readings, charts and the restart dialog. Arrow keys, Home/End, initial-letter
navigation, Escape and Tab are supported; reduced-motion preferences are respected.

## Security and maintenance checklist

1. Treat the public JSON allowlist as a security boundary. Never proxy unrestricted Home Assistant endpoints or place its token in browser code.
2. Keep certificates, private keys, account paths, serials, runtime data and logs out of commits. `.gitignore` does not protect already tracked files; inspect staged content before pushing.
3. Keep Nginx's explicit route allowlist. Never expose runtime SQLite, Python source or a whole Home Assistant directory.
4. Before deploying, run tests and JS syntax checks; inspect frontend in all languages and both phone/desktop layouts. Tests alone do not verify responsive rendering.
5. After a Home Assistant/integration upgrade, verify entity names, units, heartbeat, signs and Recorder schema. Compare with the device display at matching timestamps.
6. Before modifying formulas, document source assumptions and test gaps, nulls, negative values and partial periods.
7. Back up runtime separately, monitor service logs and storage, and verify restore procedures. Git stores code, not historical measurements.

The initial publication sanitized deployment settings, added an environment-configurable entity prefix, and corrected test-fixture IDs. Subsequent commits may include explicitly approved frontend deployments; verify deployed hashes instead of assuming Git and production match. No license has been selected in this repository.
