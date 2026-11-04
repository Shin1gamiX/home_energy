# Overview Energy trends

Compares the last 7 completed Athens calendar days with the preceding 7;
1/14/30-day choices compare equal preceding calendar windows. The 1-day option
compares yesterday with the day before and shows each day's battery peak directly.
It follows Power
flow, with expandable paired bars, daily values, coverage and battery activity.
English, Greek and Russian, light/dark themes and keyboard controls are retained.

House, solar and grid cards sum recorded kWh. Battery shows the equally weighted
average of daily maximum recorded SOC minute averages. Each battery bar is that
day's maximum, with changes expressed in percentage points. This is not battery
capacity or instantaneous peak capture. Missing readings may understate peaks.

## Data and performance

`server/energy_summary.py` reuses the existing daily energy cache and adds a
derived `energy_trends_soc` SQLite table for closed-day battery extrema and
coverage. It never changes raw readings. The public history index gains
`energy_summary.trends`, containing exactly 60 completed calendar rows. Existing
monthly summary version 2 and totals remain compatible. Empty days are explicit;
Athens DST days use 23/25-hour denominators. Closed SOC days are read once.

`trends-data.js` validates and calculates comparisons. `trends.js` loads one
compact index when the section approaches the viewport; periods and metric
selection make no additional requests. Refresh is explicit, with timeout,
cancellation, translated error/retry, and revalidation after returning to the
page on a new Athens date. Missing chart values remain gaps, not zeros.
Differences require at least 95% coverage **on every day** in both periods;
partial totals remain visible with coverage. Highlights and ratios are withheld
when their contributing days are incomplete. No new dependency or external call.

## Release procedure

Stage `activate-trends.py`, the unchanged `activate-interface.py` verifier,
the five allowlisted files (preserving public/server subdirectories), and a
private manifest in `/home/shini/homeenergy-trends-release.<unique>`.
The manifest pins before/after SHA-256 values, helper/verifier and Nginx baseline.
New assets must not already exist. Run `--check`, then `sudo ... --activate`.

Activation privately backs up replaced code, Nginx, history index, and a
consistent SQLite snapshot before writing. It installs three exact asset routes,
reloads Nginx, and restarts only the Home Energy exporter. The Overview switches
only after the 60-day index is verified. Existing report feeds/database, closed
history exports, reporting/staging access and control files remain protected.
Current telemetry/history recording continues normally; no device restart or
report publication occurs.

Failure restores original code/routes. Manual rollback uses
`sudo .../activate-trends.py --rollback /home/shini/homeenergy-trends-backup.<unique>`.
Rollback never replaces the active history database or removes new readings.
Unused new assets/cache and all evidence are retained. Inspect before retrying.

## Validation and status

Deployed and verified on 04/11/2026 on both HTTPS hostnames. Calculation tests cover calendar boundaries, DST,
missing/invalid/zero values, per-metric coverage, daily peaks and percentage
points. Deployment tests check route preservation, write allowlist and service
scope. All 23 JavaScript suites passed. The retained Python run completed with
26 passing tests and 3 skips for unavailable Node-based parity checks on Linux.
Windows Python lacks zoneinfo data, so timezone tests run in the private Linux
release using existing system data; no package installation was needed.

A private in-memory copy of live history verified unchanged raw readings,
60 daily rows and identical cold/warm results. One sample measured 293 ms cold,
42 ms warm and 25,785 bytes for the complete summary; these are diagnostic
measurements, not performance guarantees.

Public Cloudflare browser verification confirmed the 7/14/30-day controls,
daily battery maxima, coverage suppression, click-again collapse and keyboard
reopening. English, Greek and Russian, both themes and the 320px mobile layout
were checked without console warnings/errors. Installed hashes match the
release, both origins pass asset/cache/security/history checks, and the current
index contains 60 days ending before 04/11/2026. The activation verified protected
reports, raw history exports and access; only Nginx was reloaded and the exporter
restarted. The data-cache and dashboard features are published separately.

## One-day follow-up (deployed 04/11/2026)

The 1-day option retains the 7-day default and the existing 95%-per-day coverage
rule. It omits mini line charts for a single point and retains the expanded paired
bars and exact daily values. All three languages use a singular day label, and
the four period controls wrap into a two-by-two grid on narrow screens. The
existing 60-day snapshot is reused; no backend change or extra request is needed.

Calculation and UI regression tests cover yesterday/day-before boundaries,
single-day energy and battery peaks, missing data, DST denominators, translated
labels and period switching without new requests. Browser checks passed in both
themes, all languages and at 320 CSS pixels. Six deployment tests cover the
write allowlist, metadata preservation, protected state and failure rollback.
All four installed files match the approved release hashes. Origin checks on
both hostnames passed for assets, security headers, report feed, history/304
and fresh telemetry. Public Cloudflare browser checks confirmed the 1-day
selection, dates, recorded totals and battery peak bars without console errors.

Stage `activate-trends-day.py`, the unchanged `activate-interface.py` verifier,
and `trends-data.js`, `trends.js`, `trends.css`, `index.html` in a private
`/home/shini/homeenergy-trends-day-release.<unique>` directory. `manifest.json`
pins each installed/candidate hash, both helpers and the Nginx baseline. Run
`--check`, then `sudo .../activate-trends-day.py --activate`. The helper backs up
these four files and restores them if verification fails. It preserves reports,
closed history, reporting access, Nginx configuration and running services.
No reload or restart is required. Manual rollback uses `--rollback` with the
printed `homeenergy-trends-day-backup.<unique>` path. Check the public pages after
activation before committing and pushing this follow-up separately.

Do not rerun activation after it has installed the release. The helper expects
the original file hashes and deliberately rejects an already-updated baseline.
If this happens, compare all four installed hashes with the manifest's `after`
values and verify the live pages before deciding whether recovery is needed.
