# Energy insights: phase-A deployment preparation

This helper is a reviewed release mechanism, not an installed reporting runner.
It changes production **only** with the explicit `--activate` mode and root access.
Do not run activation until the owner has approved the exact staged release.
There are no downloads, dependencies or installers in this procedure.

## Scope

Phase A publishes the translated Overview section, its two static assets, and the
exact read-only `/reports/index.json` route. The feed starts empty with schema 2;
no synthetic or AI-authored report is published. It stages three Python modules
root-owned under `/opt/homeenergy-reports`, without executing the publisher.

It **does not** create an OS account, key, broker, database, schedule, SSH rule,
API token or live report. Only Nginx is reloaded. Existing telemetry, History,
restart controls, exporter/control-service processes and equipment are untouched.
The separate isolation and publishing gates in [INSIGHTS.md](../docs/INSIGHTS.md)
remain mandatory before unattended reports are possible.

## Build and stage after local tests

1. Finalize the six source files listed in `SOURCE_FILES` in the helper. Generate
   the manifest locally with `python -B deploy/activate-insights.py --build-manifest
   ABSOLUTE_REPOSITORY_PATH`. This prints JSON only; it does not write a file.
2. Save that exact JSON as `manifest.json` in an approved private release directory.
   The manifest includes SHA-256 digests of the helper and all six candidate files,
   the exact live baselines, and the version tokens from Overview. If any candidate
   changes afterward, regenerate the manifest and repeat the review/tests.
3. An administrator stages the bundle at a newly created, private path matching
   `/home/shini/homeenergy-insights-release.SUFFIX`, with this exact layout:

   ```text
   activate-insights.py
   manifest.json
   index.html
   insights.js
   insights.css
   server/insights_analysis.py
   server/insights_i18n.py
   server/insights_publish.py
   ```

   Copy only these files. Never stage the synthetic preview server, credentials,
   collected history, development outputs or a full repository checkout here.

4. From the verified release path, run `python3 -B activate-insights.py --check`.
   This is read-only: it checks exact source/live hashes, absence of the reporter
   account and new directories, fixed Nginx anchors, system timezone support,
   existing service state and both loopback HTTPS origins. It never initializes
   the publisher or writes a database. A changed baseline means stop and review,
   not replace the pinned hashes until the actual change has been understood.
5. After explicit owner approval, run `sudo python3 -B activate-insights.py
   --activate` from the same verified release. Keep an administrator session open.
6. Verify with `sudo python3 -B activate-insights.py --verify`, then visually check both
   public domains in all three languages and both themes. `--verify` is specific
   to this empty-feed, no-reporter phase; do not use it after phase B has provisioned
   publishing or added reports. This is read-only; sudo is needed to inspect the
   private root-owned state directory, not to change anything.

The helper is not directly runnable from the repository for production operations:
it requires the reviewed staging path, complete manifest and exact current baselines.
The manifest is release-specific and must not be fabricated from old hashes.

## Ordering, permissions and rollback

The helper creates a root-owned 0700 backup under `/home/shini` before mutation.
It writes root-owned modules (0755 directory / 0644 files), a root-owned 0711
report state directory and 0755 public subdirectory, and the empty feed (0644).
No reporter can write those paths in phase A because no reporting account exists.
Phase B must deliberately assign the state/public directories to the restricted
reporter after its isolation has been reviewed; modules stay root-owned.

New assets, feed and routes are verified before Overview references them. Every
replacement is hash-checked and read back. Normal security headers and no-store
for JSON are checked on both origins; only versioned static assets are immutable.
Private report paths and collector status must remain custom 404, and POST to the
feed is forbidden. Overview is switched last. The helper verifies unrelated public
files and exporter/control-service PID/restart counters remain unchanged.

On a failure after a replacement, the helper independently attempts restoration
of both the previous Overview and Nginx configuration, tests/reloads Nginx, and
verifies the previous HTTP routes. Failure of one restoration does not skip the
other. It refuses to overwrite a later administrator change and explicitly reports
when manual recovery is needed. New inactive assets/modules and the empty report directory
are retained for inspection; it never recursively deletes them or any user data.
With the original Nginx routes restored, the new files are not publicly routed.
An administrator must review retained paths before another activation attempt.
Do not bypass the absence checks. The private backup keeps original files and
ownership metadata for manual recovery if automatic rollback is blocked.

Actual Linux filesystem permissions, Nginx syntax and effective SSH restrictions
must be validated on the target host. Local unit tests do not establish those
runtime properties, and ordinary desktop automation does not supply agent isolation.
