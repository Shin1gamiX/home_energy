# Overview metric icons

This release installs the approved compact SVG quantity icons for power,
voltage, current and battery charge. It removes the visible battery-state line
while retaining its localized accessible description on the battery card button.
All W/kW/V/A/% values, zero and missing readings, signed power, translations,
themes and existing power-history interactions are retained.

Only `interface.css`, `app.js`, `power-history.js`, `index.html` and `history.html`
may be replaced. History HTML changes only shared asset versions. No exporter,
measurement, recording, report, access-control or device-control code changes.

Stage `activate-metric-icons.py`, the unchanged `activate-interface.py` verifier,
and those five frontend files in `/home/shini/homeenergy-metrics-release.<unique>/`.
The private `manifest.json` pins each file's before/after SHA-256 and the helper,
verifier and Nginx baseline. Do not commit manifests or operational backups.

Run `/usr/bin/python3 -I -B <release>/activate-metric-icons.py --check`, then
activate under sudo using `--activate` to preserve existing ownership and modes.
The helper backs up all five originals privately before replacing any file.
On failure it restores only those five files. It verifies both local HTTPS
hostnames, asset bytes/MIME/cache, security headers, private/custom 404, unchanged
report bytes, conditional daily History reads and fresh exporter snapshots.
It also checks protected reporting files/databases, closed history exports,
reporting/staging access, controls and unchanged service PIDs.

Today's exports and the live history database continue to advance normally.
They are never written or replaced by this release. No Nginx/SSH reload,
application/device restart or report publication is requested.

For rollback, run the same staged helper under sudo with
`--rollback /home/shini/homeenergy-metrics-backup.<unique>`.
Retain the release and private backup.

Release status: deployed and verified on 20/10/2026 on both HTTPS hostnames,
including the narrow-screen correction. Public-browser checks confirmed the
versioned assets, visible metric icons, hidden battery-state line, all three
languages and both themes at 320px, desktop rendering, the keyboard-operated
12-hour Battery popup and History's Voltage view. All 21 JavaScript suites and
21 deployment tests passed. Installed file hashes, report-feed bytes, Nginx
configuration and service PIDs matched the preserved baselines; activation also
verified protected reports, history and access. Physical-phone testing and
device-control operations were not performed. This feature is published as one
separate Git commit; private release manifests and rollback evidence stay out
of Git.
