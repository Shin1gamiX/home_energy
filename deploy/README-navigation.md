# Overview / History navigation

This release installs the approved icon-and-label tabs, transparent hover,
48px targets, keyboard focus and reduced-motion handling. Existing links,
English/Greek/Russian translations and both themes are retained.

Only `interface.css`, `index.html` and `history.html` may be replaced.
Metric icons and the removal of visible battery-state text are a separate
local preview and must not be included in this release.

Stage `activate-navigation.py`, the unchanged `activate-interface.py` verifier,
and the three frontend files in `/home/shini/homeenergy-navigation-release.<unique>/`.
The private manifest pins the before/after SHA-256 of each file and includes
`helper_sha256`, `verifier_sha256` and `nginx_sha256`. Never commit the manifest
or operational backups.

Run `/usr/bin/python3 -I -B <release>/activate-navigation.py --check` for a
read-only preflight, then use sudo with `--activate` to preserve existing
ownership and modes. Activation backs up all originals privately, verifies
both local HTTPS hostnames, and rolls back the three files on failure.
It checks report bytes, closed history, reporting/staging access, controls,
security/cache headers, conditional History reads and unchanged service PIDs.
No Nginx/SSH reload or application/device restart is needed.

For rollback, use the same staged helper under sudo with
`--rollback /home/shini/homeenergy-navigation-backup.<unique>`.
Public Cloudflare pages must be checked separately before pushing this feature
to GitHub. Later metric-preview edits must not be staged into its commit.
