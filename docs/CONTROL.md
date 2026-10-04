# Optional password-protected dongle restart

## Scope and status

The overview has a small **Restart dongle** dialog, localized in English, Russian
and Greek. It controls only the EyeBond **Restart Collector** button through Home
Assistant. It does not reboot the inverter, server, Home Assistant or VPN, and it
cannot edit inverter settings. History and readings stay public and read-only.

This is an opt-in backend, not a static-site-only feature. Until installed and
privately configured, the dialog reports unavailable and does not send a command.
Adding files to Git does not activate the service. No production password or token
is supplied by this repository. No new Python packages are required.

## Security policy

- The password is required for every restart. There is no login cookie, remembered
  authentication or localStorage/sessionStorage password. The dialog clears the
  field on submission and close. Browser password managers are outside our control.
- A random salt and PBKDF2-HMAC-SHA256 (600,000 iterations) protect the stored hash.
  Setup requires a 15–128 character password; use a unique random passphrase.
- Two wrong passwords within five minutes lock that client network out for five
  minutes from the second failure. Correct passwords are also blocked during the
  lockout. Successful verification clears the failure counter.
- IPv4 is limited per public IP (shared NAT users share a lockout). IPv6 privacy
  addresses share a /64 bucket. Cookies, page reloads and another browser do not
  reset this state. Changing networks can change the client bucket.
- An additional global budget permits at most **five password verifications in a
  rolling 20-minute period**, shared across all client IPs. Once full, further
  checks are rejected until a counted check expires; this is not a separate
  five-minute block or an all-at-once counter reset. Correct and incorrect
  password checks both consume a slot. Requests already blocked by a rate limit
  do not extend the wait or consume additional slots. It limits distributed
  guessing and password-hash CPU load. An attacker can temporarily exhaust this budget and deny restart
  access; public monitoring remains unaffected. This is not DDoS protection.
- The **five-minute restart cooldown is global**, reserved atomically before
  contacting Home Assistant. Concurrent clients cannot send duplicate restarts.
  Timeout, ambiguous result and upstream failure all keep the cooldown.
- Password verification now precedes cooldown feedback. A correct password
  during the cooldown returns the remaining wait, without sending another
  command or extending the deadline. Wrong passwords still count toward the
  two-attempt lockout during a cooldown; they reveal no restart state. All such
  password checks, including correct-password cooldown checks, consume the
  existing global verification budget.
- SQLite stores limits persistently across control-service restarts and sessions.
  Do not remove the state database to bypass limits. Keep the server clock synced.
- Only exact API routes are exposed. POST requires JSON and an exact allowed
  HTTPS Origin; missing/foreign origins and cross-site browser requests fail.
  No CORS grants, generic Home Assistant proxy or browser-specified entity exists.
- `/api/collector/status` is internal-only. The public Nginx route returns 404;
  the backend independently rejects non-loopback client identities on that GET
  route. Do not proxy it publicly or trust arbitrary client IP headers.
- Home Assistant is contacted only at loopback, using a token read from a private
  file. It is not sent to the browser. A Home Assistant token may have permissions
  beyond this action: use the least-privileged dedicated account that can press
  this button, verify its permissions, and protect/revoke the token appropriately.
- Nginx must overwrite the trusted client-IP header. Only loopback may call this
  backend. Host-local administrators/processes remain part of the trust boundary.
- Do not log request bodies, authorization headers or passwords at any layer.
  Disable core dumps and do not capture real-password requests in debug tooling.

**There is no absolute "never exposed" guarantee.** The entered password exists
briefly in browser/server memory and travels encrypted over HTTPS. If Cloudflare
proxies the site, it terminates TLS and can technically inspect the request.
Use Full (strict) TLS to the origin, review request-body logging/security products,
and never reuse another account's password. Avoiding Cloudflare's visibility would
require a separately approved direct/VPN endpoint or different authentication
design; hashing a password in frontend JavaScript is not a substitute.

## Private status and restart dialog

Opening the dialog asks for a password immediately, without fetching status.
Readiness, another client's recent restart time and the global cooldown are not
published through a status API. A submitted password is verified before returning
restart/cooldown information; rejected credentials expose only authentication
and throttling errors. A password submission is still an explicit **Confirm
restart** request, not a login: if no cooldown or other block applies, it dispatches
the fixed restart action. A correct password is never permission to bypass the
global cooldown, including from a different IP or after restarting this service.

The dialog keeps deadlines learned from its own responses only in page memory,
including when it is closed while a response is in flight. Closing/reopening the
dialog retains that wait; reloading the page does not retain the local countdown,
but the server still enforces the persistent limits after password submission.
No password or authenticated session is stored, and no POST is retried
automatically. Lost responses keep a conservative five-minute local wait. Fresh
energy readings can still be checked after a submitted request; that public
monitoring endpoint is unrelated to private restart status.

For server-local health checks over SSH (never expose this port):

```sh
curl --fail --silent --show-error -H 'X-Real-IP: 127.0.0.1' http://127.0.0.1:8767/api/collector/status
```

This localhost interface remains inside the trusted host boundary, not a separate
administrator login. Website visitors cannot reach it. Administrator event
inspection remains through the private journal described below.

## Private security-event log

The control service emits allowlisted, single-line JSON events to standard output,
which its systemd unit sends to the system journal. Each record has
`component=collector_security`. There is no dashboard log viewer, downloadable log
file, new API, login session or extra dependency. No Nginx, token, password,
lockout, cooldown or public-response policy changes are needed for logging.

### Administrator access

Connect to the server with SSH, then use sudo:

```sh
# Today's events, with journal timestamps displayed in Athens time:
sudo env TZ=Europe/Athens journalctl -u homeenergy-control.service --since today -o short-iso

# Follow new events (Ctrl+C exits the viewer, not the service):
sudo env TZ=Europe/Athens journalctl -u homeenergy-control.service -f -o short-iso

# JSON message bodies, convenient for searching a client IP or request ID:
sudo journalctl -u homeenergy-control.service --since '1 hour ago' -o cat --no-pager
```

The JSON `timestamp` is always UTC ISO 8601 with an explicit offset. The outer
journal timestamp can be displayed in Athens time with the commands above.
Journal access is controlled by server permissions: root, sudo administrators
and authorized journal-reader groups can read it, not website visitors. Do not
add ordinary users to `adm` or `systemd-journal` to share these logs. Root cannot
be excluded. The current host forwards journal records to its restricted syslog
as well; that copy has its own retention/access rules. IP addresses are personal
information even though credentials are excluded.

### Event fields and interpretation

| Field | Meaning |
| --- | --- |
| `request_id` | Server-generated correlation ID linking one POST's authentication, dispatch and result; never taken from a header |
| `client_ip` | Validated full client IP supplied by the trusted local proxy; null if identity cannot be established |
| `client_bucket` | IPv4 /32 or IPv6 /64 used by the existing limiter, not a second client |
| `attempt_number` | Password-verification slot in the current failure window, assigned under the existing transaction; omitted when no slot is allocated |
| `failed_attempts` | Failure count at the event; successful authentication clears it. Concurrent checks can finish in a different order from their slot numbers |
| `password_checked` | Whether a password comparison actually ran; blocked requests are not extra wrong-password guesses |
| `blocked` | Whether this request was refused; this alone does not mean a five-minute lockout started |
| `lockout_started` | True only for a checked wrong password that reaches the failure threshold |
| `retry_after_seconds` | Lockout, capacity/budget or cooldown wait at that event |
| `reason`, `http_status` | Fixed safe outcome category and response status when known |

`password_rejected` records checked wrong passwords. `restart_blocked` distinguishes
`lockout`, `concurrent_verification`, `global_budget`, `verification_expired` and
`cooldown`. `request_rejected` records malformed/forbidden POSTs without copying
their contents. `authentication_accepted` means the password was accepted; a
concurrent request can still reserve the cooldown first. `restart_requested`
means dispatch is about to be attempted, not that the dongle rebooted.
`restart_accepted` means Home Assistant acknowledged the request;
`restart_failed` means it rejected the request; `restart_unknown` means transport
uncertainty. None is independent proof of a physical reboot. `service_ready`
records startup and whether configuration enabled control. `control_error`
records a fixed error category, never exception text or a traceback.

Never serialized: passwords (including wrong guesses), password hashes/salts,
Home Assistant tokens, cookies, authorization headers, request bodies, paths,
query strings, Origin/User-Agent values, entity IDs, or raw exception messages.
Field names, event types and reason codes are allowlisted; addresses, request IDs,
numbers and booleans are validated before serialization.

### Retention and coverage limits

- Routine successful status polling is not logged. Requests rejected by Nginx or
  Cloudflare before reaching Python, or malformed HTTP rejected before POST
  handling, are outside this service log's coverage.
- To bound log flooding, at most 60 request-rejection, pre-verification-block or
  generic error details are emitted per 60-second monotonic window, shared across
  clients. Further events are counted, not individually attributed. A periodic
  `audit_suppressed` summary reports the omitted count, even when no new POST
  arrives. This limits logging only; it does not change API request limits.
- Actual password-result and restart-dispatch events are not dropped by that
  application logging cap. Existing authentication limits bound their frequency.
  Journald may still apply its own limits, retention and disk-space policies.
- Journal rotation/retention remains under existing server policy; logging does
  not change system-wide settings or guarantee a particular number of days.
- An output failure does not bypass authentication, prevent a valid request, or
  retry an action. The next successful record includes `prior_log_write_failures`.
  Abrupt shutdown/disk failure can lose records or a pending suppression summary;
  this is not a tamper-proof forensic archive.
- IPs identify connections, not people. NAT, VPNs and mobile networks may share or
  change addresses. No username exists for this single-password feature.

## Deployment (administrator-run)

These are manual instructions, not an automatic installer. They require root/sudo
for systemd and Nginx. Do not bypass missing privileges using Docker or weaken the
existing exporter service. Do not put credentials in chat, Git, shell arguments,
an environment file or the web root.

From a reviewed copy of this repository **on the server**:

```sh
sudo install -d -o root -g root -m 0755 /opt/homeenergy-control
sudo install -m 0644 server/collector_control.py /opt/homeenergy-control/collector_control.py
sudo install -d -o root -g root -m 0700 /etc/homeenergy-control
sudo install -m 0600 deploy/control.env.example /etc/homeenergy-control/control.env
sudoedit /etc/homeenergy-control/control.env
```

Set `CONTROL_ALLOWED_ORIGINS` to the exact HTTPS origins, comma-separated if
there are aliases (no trailing slash). Set `CONTROL_COLLECTOR_ENTITY` to the
existing **Restart Collector** button entity copied from Home Assistant. Never
substitute an inverter reset, Apply Collector Changes or a wildcard.

In your own interactive terminal, enter the password privately:

```sh
sudo python3 -B /opt/homeenergy-control/collector_control.py setup-password /etc/homeenergy-control/password.hash
```

Provision a dedicated Home Assistant access token privately, then enter it at
the hidden prompt (do not paste it into a command or send it to the assistant):

```sh
sudo python3 -B /opt/homeenergy-control/collector_control.py setup-token /etc/homeenergy-control/ha.token
```

Both commands refuse to overwrite an existing file, require an interactive
terminal and create mode-0600 files. The service receives read-only copies via
systemd credentials, rather than secrets in command arguments/environment.
The original files remain mode `0600`. The reader also accepts systemd's
root-owned `0440` copies only for these two credential names in
`/run/credentials/homeenergy-control.service`, matching `CREDENTIALS_DIRECTORY`.
That exception requires a read-only, non-public credential directory and
root-owned, non-group/world-writable ancestors, with no symlinks. It does not
relax permissions for ordinary secret files.
The supplied unit needs a systemd version supporting `LoadCredential` and
`DynamicUser`; inspect `systemd --version` and validate the unit before activation.

```sh
sudo install -m 0644 deploy/homeenergy-control.service.example /etc/systemd/system/homeenergy-control.service
sudo systemd-analyze verify /etc/systemd/system/homeenergy-control.service
sudo systemctl daemon-reload
sudo systemctl enable --now homeenergy-control.service
```

### Nginx and Cloudflare

Back up the existing Home Energy virtual-host configuration. Add the contents of
`deploy/collector-control.nginx.example` inside its **HTTPS server block**. Keep
the existing TLS, CSP, anti-framing and other security headers. Do not replace the
entire virtual host with a generic example. Port 8767 must remain loopback-only;
never publish it through Docker or open it in a firewall.

If Cloudflare proxies this hostname, also include
`deploy/cloudflare-realip.nginx.example` in that server block after checking its
CIDRs against [Cloudflare's official list](https://www.cloudflare.com/ips/).
Only those trusted proxy ranges may supply `CF-Connecting-IP`; never trust
`X-Forwarded-For` or `CF-Connecting-IP` from every address. Do not use
`set_real_ip_from 0.0.0.0/0` or `::/0`. The example then passes Nginx's validated
`$remote_addr` as `X-Real-IP`, replacing any client-supplied header.

For Cloudflare, keep Pseudo IPv4 set to **Off** or **Add Header**, not **Overwrite
Headers**, so genuine IPv6 addresses reach the /64 limiter. Do not attach Workers
that rewrite the visitor IP or log request bodies on these API routes. Ensure
Cloudflare and Nginx do not cache `/api/collector/*`.

```sh
sudo nginx -t
sudo systemctl reload nginx
```

Reload completion does not guarantee new workers are already serving requests.
Use a bounded retry of the server-local read-only status endpoint, check the HTTP
status before parsing JSON, and roll back if it never becomes ready. Separately
verify every public hostname returns 404 for `/api/collector/status`, including
requests with query strings or forged client-IP headers. Publish the matching
dialog script and cache-busted HTML when removing the old public status proxy.
Do not retry the restart POST as part of deployment health checks.

Publish the reviewed `index.html`, `styles.css` and `collector-control.js` together
to the existing static web root using the usual backup/hash verification process.
The new script has an explicit Nginx static allowlist entry. The exporter and
history files do not need changing.

### Required activation checks

1. Verify `ss -ltn` shows only `127.0.0.1:8767`; no public interface binding.
2. Check `/api/collector/status` over public HTTPS: it must return 404 and no
   status fields. Check the loopback endpoint from SSH with `X-Real-IP: 127.0.0.1`:
   `enabled` should be true. Requests with non-loopback client identity must fail
   at the backend too, even if a public proxy is accidentally restored.
3. Verify actual client-IP restoration through Cloudflare, then a **direct-origin**
   request with forged forwarding headers: those headers must not alter its IP.
   Do this with disposable test credentials before enabling the real action.
4. Missing/foreign Origin, form-encoded POST and GET restart requests must fail.
5. Two bad passwords produce five-minute lockout; refresh/new browser must retain
   it. Correct password during lockout must not dispatch an action. Shared-IP
   clients share this restriction; another IP has its own failure counter.
6. With the owner's approval for one real restart, submit the correct password.
   Verify the correct password from another device/IP cannot send a restart during the global cooldown and
   that the cooldown survives a service restart. Never loop hardware restarts to
   test concurrency: automated tests use a fake dispatcher.
7. Check fresh inverter timestamps after the request. A timeout is **unknown**,
   not proof of failure or success. Fresh readings prove monitoring is updating,
   not that the physical dongle rebooted. An unreachable dongle may require local
   intervention. No automatic restart retries occur.

## Maintenance and rollback

The single state database is `/var/lib/homeenergy-control/state.sqlite3`, outside
the public directory. All control workers must use the same local file; this is
not a multi-host distributed deployment. Secrets are loaded at service startup.
For rotation, create a new private file under a new name with the setup command,
update the unit's credential source and restart the service; retain the state DB.
Revoke the old Home Assistant token separately.

To disable the feature, stop/disable **homeenergy-control.service** and remove its
restart API proxy location (validate/reload Nginx; keep status blocked), or restore the prior three
frontend assets to remove the button. Do not stop the read-only exporter. Preserve
private state/secrets for rollback; do not include them in source backups or Git.

Run isolated tests from the repository root:

```sh
python3 -B -m unittest discover -s server -p 'test_collector_control.py' -v
node --check collector-control.js
node server/test_collector_ui.cjs
node server/test_overview.cjs
```

## Verification record — 04/10/2026 (global budget, pre-deployment)

- All 68 isolated backend tests passed on Linux. Coverage includes the fifth
  allowed check and sixth rejection, 20-minute expiry boundaries, rolling slot
  release, distinct-IP concurrency, database reopen, retained records from the
  previous larger budget and blocked requests not extending the wait.
- HTTP tests confirm correct, incorrect and cooldown password checks consume
  the same global budget. Budget rejection performs no password check or device
  action, returns the wait in `Retry-After`, and retains secret-free audit events.
- All 11 isolated dialog scenarios passed, including the existing countdown at
  20 minutes, close/reopen and expiry without automatic resubmission. No production
  frontend changes are needed. Per-IP lockout and restart cooldown remain five
  minutes; no production restart POST was used to validate this update.
- Activation changes only the control-service source and restarts that service.
  Keep the existing state database and credentials. These test results alone do
  not confirm production activation.

## Verification record — 04/10/2026 (private status, pre-deployment)

- All 62 isolated backend tests passed on Linux. Correct passwords during the
  global cooldown return the remaining wait without another dispatch or extending
  the cooldown, including different IPs, concurrent requests and reopened state.
  Wrong passwords do not reveal restart status. Failed/unknown dispatches retain
  the same cooldown protection. All dispatchers used by these tests are fake.
- Eleven isolated dialog scenarios and 52 existing overview assertions passed.
  The dialog tests reject any attempt to fetch the public status route and cover
  closing/reopening during a request, late responses, lockout and cooldown expiry,
  lost responses, password clearing and all three languages.
- Local in-app browser checks verified the ready dialog and cancellation in
  English, Russian and Greek, with no console warnings/errors. No real password
  was entered and no restart was submitted. Production activation remains a
  separate administrator step; these checks do not establish live deployment.

## Verification record — 03/10/2026

- The security-logging update passed all 56 isolated backend tests on Linux.
  Coverage includes redaction of secret-bearing inputs/errors, atomic attempt
  metadata, IPv6 attribution, concurrent request/error correlation, bounded log
  flooding, suppressed-event summaries, and output-failure behavior. Test
  dispatchers are fake; no device restart is needed to run this suite. These
  results do not by themselves confirm production activation.
- All 34 backend tests passed on the Linux deployment host, including systemd
  credential permissions, persistent limits, concurrent requests and fail-closed
  configuration handling. The 11 Unix permission tests are skipped on Windows.
- Five isolated dialog scenarios and 52 existing overview assertions passed.
- Live HTTPS checks verified invalid-origin/content-type rejection, two-attempt
  lockout, retention across connections and separation between source IPs.
  A local-origin test verified that forged forwarding headers did not change
  the lockout bucket; direct external origin access timed out from the test client.
- Browser checks covered English, Russian and Greek, desktop/mobile layouts,
  lockout feedback, cancellation, Escape and password-field clearing.
- The owner submitted one restart through the published button. The backend
  recorded an accepted request, the owner reported success, and fresh readings
  were verified afterward. This confirms the command path and resumed monitoring,
  not independent proof of a physical power cycle. The real global cooldown was
  not challenged with a second restart; concurrency/persistence were tested with
  a fake dispatcher instead. Startup was confirmed enabled and the service active.

References: [OWASP authentication throttling](https://cheatsheetseries.owasp.org/cheatsheets/Authentication_Cheat_Sheet.html),
[Home Assistant service API](https://developers.home-assistant.io/docs/api/rest/),
[Nginx trusted real-IP configuration](https://nginx.org/en/docs/http/ngx_http_realip_module.html),
[Cloudflare visitor-IP restoration](https://developers.cloudflare.com/support/troubleshooting/restoring-visitor-ips/restoring-original-visitor-ips/).
