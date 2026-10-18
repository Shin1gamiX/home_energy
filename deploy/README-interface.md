# Approved Overview B and chart-first History release

Activated on 18/10/2026 on `home.shin1gamix.com` and `home.shin1gamix.gr`.
The administrator's activation checks passed, including protected-file
fingerprints, unchanged report storage and closed history, and a private
rollback backup. Only Nginx was reloaded.

Public Cloudflare checks subsequently verified Overview and History, current
telemetry availability, the 12-hour popup and zoom/reset, exact report links,
daily/monthly/all-time views, calendar, mode intervals, source filters, themes,
and English/Greek/Russian layouts at desktop and mobile widths. Browser console
checks found no warnings or errors. Both public report feeds retained the
pre-release SHA-256; new CSS/WebP assets matched the release bytes, MIME types,
immutable cache policy and security headers. HTML and reports remain no-store,
and the private status route still returns 404. These checks do not certify
electrical-measurement accuracy or exercise device controls.

All 17 frontend regression scripts and six deployment-helper tests passed.
The nine Nginx integration tests also passed in an isolated server fixture;
they did not call the live control service. No report was published, no
reporting access was changed, and no history data was modified by this release.

`activate-interface.py` is an installation-specific, standard-library helper.
Its private manifest records the inspected source and destination hashes. Stage
it in a fresh `/home/shini/homeenergy-interface-release.*` directory, preserving
the `assets/solar-home.webp` relative path, and run `--check` first. The helper
requires sudo for `--activate`; it never requests a password itself.

The write allowlist contains eight frontend files and the existing Home Energy
Nginx configuration. It adds only the exact `/interface.css` and
`/assets/solar-home.webp` routes and their versioned-cache entries. It does not
expose the `assets` directory, provenance, documentation, or any private files.
Both HTML pages, report/history JSON, errors and control endpoints stay no-store.
Every changed JS/CSS asset and the illustration have explicit versioned URLs.

Activation creates a root-owned mode-0700 rollback directory before changing
production. It preserves original frontend/configuration bytes and metadata,
records protected-file fingerprints, installs assets before referencing HTML,
checks Nginx syntax and performs one graceful Nginx reload. Local HTTPS checks
cover both hostnames, content hashes, MIME/cache/security headers, unchanged
public reports, custom/private 404 and daily-history conditional requests.

Report storage/database, reporting/staging modules and SSH access, control code,
exporter code and closed historical exports are fingerprinted but never written.
Today's live history and database are allowed to keep advancing normally. No
report, application/device restart, SSH reload or history-data write is requested.

Failure restores the old frontend and Nginx configuration. New unused assets,
release directories and private evidence remain for review; do not rerun blindly.
Explicit rollback uses the printed private backup:

```sh
sudo /usr/bin/python3 -I -B /home/shini/homeenergy-interface-release.RELEASE/activate-interface.py --rollback /home/shini/homeenergy-interface-backup.BACKUP
```

After activation, verify Overview, History, themes, languages, source popups and
report links through the public Cloudflare hostnames. Only then publish the Git
features separately. Never commit the private manifest, backups or readings.

Local synthetic checks:

```sh
python3 -B -m unittest discover -s server -p test_interface_deploy.py -v
python3 -B -m unittest discover -s server -p test_not_found_nginx.py -v
node server/test_interface.cjs
node server/test_history_requests.cjs
```

The Nginx integration test uses an existing Nginx binary, an ephemeral loopback
port, temporary fixture files and a synthetic control sentinel. It skips if
Nginx is unavailable; it never contacts the live control service.
