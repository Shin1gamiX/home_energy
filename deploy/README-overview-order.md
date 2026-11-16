# Overview section ordering

The connection warning used an automatically placed grid row, while Energy
trends was fixed to row 2. When the warning appeared, Power flow could move
below trends on both desktop and mobile.

Named grid areas now keep the warning above Power flow and trends below it.
Energy insights remains beside Power flow on desktop and after trends below
the 1000px breakpoint. Hiding the warning removes its row. Only `trends.css`
and its version reference in `index.html` change on the server.

## Deployment

Stage `activate-overview-order.py`, the existing `activate-interface.py`
verifier, both frontend files and a private `manifest.json` in
`/home/shini/homeenergy-overview-order-release.<unique>/`.
The manifest uses the existing before/after file hashes, helper/verifier
hashes and Nginx baseline hash. Keep installation evidence outside Git.

Run `--check`, then `sudo python3 -I -B activate-overview-order.py --activate`.
Activation creates a private rollback backup, preserves file ownership and
permissions, installs CSS before HTML, and verifies both HTTPS origins.
Failure restores the originals. Manual rollback uses
`--rollback /home/shini/homeenergy-overview-order-backup.<unique>`.

No Nginx/SSH reload, service/device restart, report publication or history-data
write is performed. History, reporting access and control code stay unchanged.

## Verification

Local browser checks passed at 320, 393, 768, 1000, 1001 and 1440px. The stale
layout was checked in English, Greek and Russian in light and dark themes;
live and offline states were also checked across all six widths. All 54
ordering/overflow checks passed. Synthetic deployment tests cover the two-file
allowlist, metadata preservation, protected files and rollback after an HTTP
verification failure.

Status: deployed and verified on both domains on 16 November 2026. Both public
Cloudflare origins serve the exact updated CSS under its new version URL and
Overview references that version. Public report feeds are unchanged; History,
security/cache headers, the private custom 404 and live telemetry passed HTTP
checks. The `.gr` edge applies its existing Rocket Loader HTML transformation.
Installed frontend bytes match the release and History is unchanged. Nginx,
exporter and control service PIDs/restart counters are unchanged. Post-release
verification used HTTP checks; responsive rendering was verified locally above.
