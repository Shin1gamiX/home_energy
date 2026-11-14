# Overview seasonal release

The first release adds only `themes/theme-loader.js`, `themes/schedule.js`,
`themes/christmas/theme.css`, `themes/christmas/theme.js`, one loader reference
in `index.html`, and the four exact Nginx asset routes/cache allowlist entries.
Existing light/dark settings, History HTML/scripts/styles, reports, raw history,
reporting access and controls are preserved. Christmas is active annually from
1 December 00:00 until 1 January 00:00, Athens time (end exclusive).

Stage the five frontend files, `activate-seasonal-theme.py`, and the unchanged
`activate-interface.py` verifier in `/home/shini/homeenergy-seasonal-release.<unique>/`.
The private `manifest.json` pins each file's before/after hash (null before for
new assets), plus `helper_sha256`, `verifier_sha256` and `nginx_sha256`. Manifests,
server snapshots and rollback evidence stay outside Git and the public root.

Run `/usr/bin/python3 -I -B <release>/activate-seasonal-theme.py --check` first.
The sudo `--activate` action backs up Overview and Nginx in a root-private
directory, installs the assets/routes before the new HTML, and reloads only
Nginx. It verifies asset hashes, MIME/cache/security headers on both local HTTPS
hostnames, protected 404 routes, report bytes, closed history exports, reporting
state/access and unchanged application PIDs. A failure restores the original
Overview and Nginx; unused new theme files and all evidence are retained.

To undo, use the same staged helper under sudo with
`--rollback /home/shini/homeenergy-seasonal-backup.<unique>`.

Public Cloudflare checks must follow activation before the GitHub push. Outside
December, production should show the normal Overview and not request Christmas
assets. Active-season/date-boundary tests use local synthetic fixtures only;
production has no force-theme query parameter or date override.

Status: deployed and verified on both HTTPS hostnames on 14 November 2026.
Origin checks confirmed exact installed assets, MIME/cache/security headers,
unchanged History and report bytes, protected state and application PIDs. Only
Nginx was reloaded. Public checks confirmed the asset bytes and normal November
appearance, with the loader and schedule loading but no Christmas assets or
decorations. The `.gr` edge adds Cloudflare Rocket Loader markup; its application
HTML and static assets were verified separately, and module loading worked in
the browser. History rendered without seasonal assets, and the Overview had no
horizontal overflow at 320px. December/New Year transitions, cleanup, both
appearances and all three languages passed locally using synthetic fixtures.
