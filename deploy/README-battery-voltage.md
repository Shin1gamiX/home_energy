# Battery voltage and control spacing

This installation-specific release changes only five existing public files and
the exporter/history recorder. It adds measured battery voltage to Overview,
History and future minute aggregates, plus spacing corrections for chart
controls. No new dependency or Nginx route is required.
The staged generic exporter is adapted at activation to preserve the existing
installation's Home Assistant database path; the default in Git stays portable.

The Overview/History navigation redesign is **not** part of this release.

Stage `activate-battery-voltage.py`, the unchanged `activate-interface.py`
read-only verification helpers, and the seven allowlisted candidate files under
`/home/shini/homeenergy-voltage-release.<unique>/`. The private `manifest.json`
must contain the exact before/after SHA-256 of each file, plus `helper_sha256`,
`verifier_sha256` and `nginx_sha256`. Keep that manifest and all backup evidence
outside Git.

Run the staged helper using `/usr/bin/python3 -I -B` with `--check` (read-only)
or `--activate` under sudo. It refuses a changed baseline, backs up original
bytes and metadata privately, and restarts **only `homeenergy.service`**.
It verifies fresh live battery voltage and a new minute aggregate on both
hostnames, asset delivery, headers, private 404, conditional History reads,
unchanged reports/access/control files and unaffected service PIDs.

The helper rolls back code on failure. For an explicit later rollback, use the
same helper with `--rollback /home/shini/homeenergy-voltage-backup.<unique>`.
Collected readings are preserved even during rollback: no backfill, deletion,
report publication or change to kWh calculations is performed.

Local checks use synthetic readings. Successful activation verifies delivery
and recording, not independent electrical sensor accuracy. Public Cloudflare
pages must also be checked in the browser before declaring deployment complete.
