# Phase B: report-only publishing preparation

## Status and non-goals

**Prepared, not installed or activated.** The translated Overview and empty report
feed were deployed on 13/10/2026 (phase A). This phase adds broker/client code,
configuration templates and a read-only account audit. It creates no account,
credential, socket, timer, model subscription, live report or production database.
No dependency installation is needed for the Python standard-library components.

The owner subsequently selected their existing Windows account. Native desktop
scheduled runs failed the permission-profile prerequisite; the former automation
file was later absent. The separate `Home Energy Reporter - Dummy` Windows task is
**disabled**. Its restricted dummy workflow and collaboration-blocking checks
passed, but the reporting connection is not activated. This Linux broker is not
a Windows installation. See the newer
[Windows staging preparation](README-windows-report-staging.md) and the
[PC scheduler activation gate](../docs/INSIGHTS.md#pc-scheduler-activation-gate).
Do not schedule this administrator-powered workspace. Do not
give the model general shell, Python execution, browser, arbitrary network or
filesystem tools to make the two report tools work. The adapter is trusted code,
not something the model may rewrite.

## Three separate identities

| Identity | Permitted role | Must not possess |
| --- | --- | --- |
| `homeenergy-report-runner` | Receives evidence; submits multilingual prose using two typed tools | SSH/API credentials in model-readable context, admin keys, arbitrary tools, server/config write access |
| `homeenergy-report-broker` | Accepts a local socket request and performs a fixed reporting SSH command | sudo, shell supplied by a caller, HA/control access, model tools |
| `homeenergy-reporter` | Forced-command account on the publishing server; reads history, appends reports | sudo, writable program/config files, unrestricted SSH, frontend/history/control write permission |

The broker and runner must share a Linux host but use distinct static UIDs. The
publishing server can be that host or another approved host. Socket permissions
and Linux peer credentials restrict calls to the runner UID. Root remains trusted;
this is not protection against a compromised host administrator or kernel.

## Code and wire contract

`server/insights_report_client.py` offers only `prepare()` and
`publish(analysis_id, summary, notes)` for an eventual trusted model-tool adapter.
It connects to the fixed `/run/homeenergy-report-broker.sock` path. Do not expose
the module's internals, imports or generic Python evaluation to the model.

Each connection carries one four-byte unsigned network-order length followed by
one UTF-8 JSON object. The client then half-closes its write side. The broker
requires EOF, rejects extra frames, and returns one similarly framed response.
The only request shape is `{"method":"prepare","payload":{}}` or a `publish`
request with exactly `analysis_id`, `summary` and `notes` in `payload`. No path,
hostname, credential, executable, command arguments or URL field is accepted.
The existing publisher remains authoritative for text rules, mandatory languages,
known finding IDs, server-generated facts, expiry, duplicate handling and limits.

The SSH child uses an absolute executable, no shell, no SSH configuration files,
no agent/proxy/multiplexing, a fixed user and literal IP, one exact command, and a
root-owned pinned Ed25519 host key. Credentials come from systemd `LoadCredential`
and are never returned through the model-facing interface. No upstream stderr or
request content is returned on failure. The operator must verify the host key
through a trusted existing connection; do not use trust-on-first-use or copy a
new network scan as a trusted pin.

Requests are capped at 32,896 bytes, report payloads at 32,768, replies at 65,536.
The reply cap includes the wrapper around a prepared analysis. Framing has a
five-second total deadline; the SSH subprocess has a 50-second deadline and
bounded stdout/stderr while streaming. The service has a 65-second lifetime cap.
No automatic retry loop is provided. After an uncertain publish outcome, retry
the *identical* submission; never generate a second report to work around failure.

Audit events go to the system journal with `event`, peer UID, allowed method and
outcome. Journald supplies timestamp and service identity. They contain no prose,
evidence, password, key, raw SSH diagnostic or caller-supplied error string.
Only an administrator should have journal access. An `accepted` event means the
publisher replied successfully; it does not certify that the prose is correct.

## Templates (not installation commands)

- `homeenergy-report-broker.socket.example`: root-owned Unix socket, runner group
  access, at most four concurrent connections and 24 activations per minute.
- `homeenergy-report-broker@.service.example`: separate broker UID, read-only host
  filesystem, private temporary directory, no privileges, restricted network
  families, resource limits and a systemd-managed reporting credential.
- `report-broker-config.json.example`: documentation-only `192.0.2.10` and port 22.
  This is not the production address. Replace both the config destination and
  service `IPAddressAllow` together only after the execution host is approved.
- `reports-sshd.conf.example`: server-side fixed `prepare`/`publish` dispatcher.
- `check-report-isolation.py`: read-only server account/sshd/permissions gate;
  does not install, fix, publish, reload or restart anything.

Socket trigger limiting is fail-closed abuse protection, **not a five-minute
cooldown**. Exhaustion can leave the socket failed until an administrator resets
and starts it. Normal scheduled runs must not spin/retry repeatedly. The existing
publisher's five-minute/12-per-day limits apply independently across sessions.

### Ownership contract

On the broker host, root owns `/opt/homeenergy-report-broker` and all its code
(0755 directory, 0644 files) and the service units. Config and known_hosts are
root-owned 0644. The source `report-key` is root-owned 0600; systemd passes it as
a private **0400** service credential. The code intentionally rejects 0600
credentials. Never give the runner access to the source key directory or the
service credential mount.

Use `/etc/homeenergy-report-broker` as root-owned **0755** for config/known_hosts
and a separate `/etc/homeenergy-report-credentials` root-owned **0700** directory
for the source private key (0600). This split is mandatory: the broker reads public
configuration directly; PID 1 reads the private source for `LoadCredential`.
The service template uses the latter credential path. No real keys are included.

On the publishing server, follow the ownership contract in `docs/INSIGHTS.md`.
Use `/var/lib/homeenergy-reporter-home` as a root-owned 0755 home, dash login shell,
no supplementary groups, disabled passwords, and a root-owned authorized-keys
file containing one `restrict ssh-ed25519 BASE64` line without extra options.
Never reuse the administrator key. Code remains root-owned; only the dedicated
report state and public feed directories become reporter-owned at activation.

## Gates before any activation

1. Approve the execution host and runner integration separately. Verify that the
   model sees exactly two typed operations, cannot read credentials or other
   processes, and has no general shell/network/mount access. Any model provider
   credential must also stay in trusted transport code outside model-visible data.
2. Review target SSH/systemd versions and effective settings, not just templates.
   The inspected publishing host currently has Python 3.12.3, systemd 255 and
   OpenSSH 9.6p1. Recheck these before installing. Audit global AcceptEnv and
   PermitUserEnvironment; a Match block does not undo inherited environment rules.
3. Stage and checksum the exact release privately. Back up SSH/service configuration
   and account/state metadata. Keep an administrator recovery session open. Do not
   create an active authorized key or enable the socket until the checks below pass.
4. Use a **separate staging account and report store**, never the public feed, for
   real SSH integration tests. Validate permitted preparation/publication, retries,
   immutable reports, concurrency and rejection of shell, SCP/SFTP, PTY, forwarding,
   tunnelling and arbitrary command arguments. Do not test writes against live files.
5. Check effective Unix permissions/ACLs and sudo policy. Run the read-only audit
   after approved account provisioning: `sudo python3 -B check-report-isolation.py
   --peer-ip BROKER_IP --peer-host VERIFIED_PEER_HOST --local-ip SERVER_IP
   --local-port SSH_PORT`. Use the exact connection context (including any reverse
   hostname used by sshd); other Match contexts require separate checks. It fails
   closed for missing accounts, unsafe settings, ambiguous sudo failures and
   out-of-scope symlink trees. Review legitimate external links and additional
   private credential paths on this host separately;
   this checker is scoped to known project paths, not a whole-host access audit.
6. Validate both systemd units with `systemd-analyze verify`, the server config with
   `sshd -t` and `sshd -T -C`, and actual cgroup/network restrictions on the chosen
   host. Check that the installed Python, SSH binary and root-owned parent paths
   cannot be changed by either service user. IP filtering is not port filtering;
   only the fixed SSH transport may choose the port. Host confinement must not be
   claimed from a successful application unit test.
7. Explicitly approve activation, then publish the first real report and verify all
   three languages. Model/cadence activation is a further explicit step. Missing
   weather, model hallucination, mistranslation and lack of incident lifecycle
   tracking remain limitations, as documented in `docs/INSIGHTS.md`.

## Disable/recovery

An administrator can stop the broker socket and revoke the report-only key to
prevent future publication. Existing public reports remain. No inverter, dongle,
Home Assistant, exporter or restart-control service needs restarting. Preserve
the private report archive for review; corrections are administrator-only.

## Verification boundaries

Local contract tests and unprivileged Linux socket/process tests use synthetic
data and temporary directories. They cannot prove real SSH Match policy, account
ACLs, systemd credential separation or model tool restrictions. Those remain the
mandatory target-host gates above; no credential or live report is used by tests.

Preparation verification on 13/10/2026:

- Linux Python 3.12: **128 tests passed, zero skipped**, covering the broker,
  isolation checker contracts, publisher, analyzer and phase-A release helper.
- Windows bundled Python: **110 passed, 18 platform-specific skips**, for the
  same 128-test suite. The skipped Linux checks were covered by the Linux run.
- The exact staged broker sources were SHA-256 checked before running tests.
  No test used a real reporting credential or opened a live SSH publishing session.
- Both service templates passed `systemd-analyze verify --man=no --generators=no`
  on systemd 255, without installing or starting them. An unrelated existing
  `wings.service` legacy PIDFile warning was observed; that unit was not changed.
- Both production origins still served the phase-A assets/empty feed and protected
  404 responses. Existing service PIDs were unchanged, and the three reporting
  accounts and broker socket remained absent.

Reproduce the application tests from `server/` using:

```text
python3 -B -m unittest test_insights_broker test_insights_isolation test_insights_publish test_insights_analysis test_insights_deploy
```

These results are preparation evidence, **not certification of a deployed security
boundary**. No active key, account configuration or model runner was tested.

Reference semantics: [OpenSSH client options](https://man.openbsd.org/ssh_config),
and the target host's installed `systemd.socket(5)` / `systemd.exec(5)` manuals.
