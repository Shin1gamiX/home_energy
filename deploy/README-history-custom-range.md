# Custom History range release

Status: activated and verified on both HTTPS hostnames on 17 November 2026.
The five installed files matched the staged release. Origin and public checks
passed; the private rollback backup and staging evidence are retained outside Git.

## Scope

`activate-history-custom-range.py` installs `history-custom-range.css` and
`history-custom-range.js`, updates `history-calendar.js`, `history.js` and
`history.html`, and adds two exact Nginx routes plus versioned asset caching.
It uses the existing `activate-interface.py` verification and atomic-write helpers.

Only Nginx is reloaded. Overview, exporter/health configuration, reports and their
database, history storage/code, reporting/staging access and controls are preserved.
Normal recording continues; no historical data is restored, backfilled or deleted.

## Staging and activation

Stage a private `homeenergy-custom-range-release.<unique>` directory under
`/home/shini` containing the five files, both Python helpers and `manifest.json`.
The private manifest pins before/after file hashes, both helpers and the installed
Nginx configuration. Keep installation manifests, backups and evidence out of Git.

Run the helper with `--check` first. Changed baselines or pre-existing new assets
stop the release for inspection. Activate with:

```text
sudo /usr/bin/python3 -I -B /home/shini/homeenergy-custom-range-release.<unique>/activate-history-custom-range.py --activate
```

Activation creates and verifies a root-private rollback backup before replacing
anything. It installs and verifies the new asset routes first, updates the shared
calendar and History script, then installs the HTML last. Older HTML remains
compatible with the new script while files are replaced.

Both local HTTPS origins are verified for asset hashes, MIME/cache and security
headers, private 404 routes, unchanged report feed, daily History/304 responses
and fresh exporter snapshots. Protected file hashes/metadata and service PIDs
must match their pre-activation state.

The deployed release also passed public browser interactions and 36 responsive
language/theme combinations, with 24 calendar checks and no browser errors.
Public asset checks accounted for the existing Cloudflare HTML transformations
on the `.gr` domain and the unchanged, cached `app.js` URL, whose bytes matched
its earlier Git version. All new and changed JavaScript/CSS assets matched this
release exactly. Report-feed hashes, Overview source, daily History/304 responses,
fresh telemetry and the original service PIDs were verified. Existing private
report/storage/access preservation checks passed during privileged activation.

## Rollback and tests

Failures invoke rollback. To restore manually, use the same helper with:

```text
sudo /usr/bin/python3 -I -B /home/shini/homeenergy-custom-range-release.<unique>/activate-history-custom-range.py --rollback /home/shini/homeenergy-custom-range-backup.<unique>
```

Rollback restores the original three History files and Nginx configuration,
including ownership/mode/timestamps, and reloads Nginx. New unused assets and
existing evidence are retained. Reports and history data are never overwritten.

Eight synthetic deployment tests cover exact route changes, the write allowlist,
metadata preservation, protected state, privilege checks, installation order,
verified backups and automatic rollback on asset/final HTTP verification failure.
Twelve relevant Node suites cover the UI, date calculations, calendar, caching,
cancellation and retained History behaviour. See [feature verification](../docs/HISTORY_CUSTOM_RANGE.md)
for the responsive and translation checks. Verify public Cloudflare responses
separately after activation, then commit and push this feature alone.
