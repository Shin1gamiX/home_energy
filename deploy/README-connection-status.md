# Overview connection-status release

Status: deployed and verified on both domains on 16 November 2026. The origin and
public Cloudflare checks confirmed exact versioned assets, fresh healthy checks,
normal telemetry/recording, security/cache headers, unchanged reports and History.
The exporter service restrictions and Home Assistant/WireGuard containers match
the pre-deployment baseline. Only Nginx was reloaded and the exporter restarted.

Browser verification covered phone and desktop layouts on both domains, History
navigation, and a browser-only offline/recovery simulation in English, Greek and
Russian across light/dark themes. Last readings remained visible, remote checks
became unknown while disconnected, and the warning cleared after reconnection.
No production outage or equipment failure was induced. Local verification passed
48 Python tests, the relevant JavaScript suites and 198 responsive checks.

## Scope

`activate-connection-status.py` installs the two connection-status assets, updates
Overview HTML and its app script, and installs the exporter plus its two health
modules. It adds two exact Nginx asset routes and immutable caching for their
versioned URLs. A four-value systemd drop-in enables the fixed health probe.

The existing exporter unit and its security restrictions stay unchanged. Nginx
is reloaded; the exporter is restarted once, briefly pausing new snapshots.
Home Assistant, WireGuard, the control service, SSH and devices are not restarted.
Reports, reporting access, History UI and history recording code are protected.
Normal new readings continue; no data is rewritten, backfilled or deleted.

## Staging and activation

Stage a private `homeenergy-connection-release.<unique>` directory under
`/home/shini`, containing this helper, the existing `activate-interface.py`
verifier, the seven allowlisted files in `public/` and `server/`, and a private
`manifest.json`. The manifest records before/after hashes, helper/verifier hashes,
the Nginx/unit baseline and the installation's fixed health configuration. Do not
put installation manifests or evidence in Git.

The installed exporter retains its existing Home Assistant database path. The
helper refuses changed baselines or pre-existing health files/configuration.
Run `--check` first, then `sudo python3 -I -B activate-connection-status.py --activate`.

Activation backs up the existing targets before changing them. Assets and routes
are installed first. The new exporter must produce fresh, healthy diagnostics and
normal recorded readings before Overview HTML switches to the new version.
Both HTTPS origins are checked for asset hashes/MIME/cache, security headers,
unchanged reports, protected 404 routes, History and conditional daily reads.

## Rollback

Failures invoke rollback automatically. Manual rollback uses:

```text
sudo python3 -I -B activate-connection-status.py --rollback /home/shini/homeenergy-connection-backup.<unique>
```

Rollback restores the original frontend, exporter and Nginx bytes/metadata,
removes only this release's exact health drop-in, reloads Nginx/systemd and
restarts the exporter. New unused assets/modules and all evidence are retained.
It never restores a historical data snapshot over new readings or reports.

## Verification boundaries

Run `server/test_connection_deploy.py`, `server/test_connection_health.py` and
`server/test_connection_status.cjs` alongside the exporter/history and existing
Overview checks. Deployment tests use synthetic fixtures. The production probe
only observes routing, ping and existing TCP state; it sends no discovery or
configuration packet to the dongle. See [status meanings and limitations](../docs/CONNECTION_STATUS.md).

Check both public Cloudflare pages in a browser after origin verification, then
commit and push this feature separately. No deliberate production outage is
needed; failure/recovery rendering is exercised with the local scenario preview.
