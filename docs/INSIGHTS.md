# Energy insights: reporting without equipment control

## Manual-only conversion, 17/10/2026

The Windows reporting task has been removed after a verified definition backup.
The owner now requests reviews in chat
when needed, selecting hours to review and days to compare. Chat-only review is
read-only; publication remains a separate, explicitly requested action.
See [manual reporting](MANUAL_REPORTS.md) for the current workflow and activation
status. Existing reports, snapshots, grants and run evidence are retained.
The old runner is not a fallback and its authorization need not be renewed for
manual reviews. After committing the manual conversion, a separate cleanup
removed only the six recurring runner modules, five runner-specific test files
and task XML template. The exact list and task backup path are recorded in
[manual reporting](MANUAL_REPORTS.md#approved-cleanup-17102026). Reports, existing
evidence, credentials and staging access are preserved.

The remaining sections document the earlier automated installation, not current
authorization to schedule or publish autonomously.

## Status and ownership

**Current status, 14/10/2026:** the translated public UI, restricted reporting
account and report-only publisher are active. Supervised public reporting passed.
The **Home Energy Reporter** Windows task is enabled for **00:00 and 12:00 Athens
time**, starting **15/10/2026**; its first actual timed trigger has not yet occurred.
It uses the verified restricted launcher, not this administrator chat or a native
Codex app automation. The earlier Dummy task remains disabled.

The current grant is bounded to 30 days and 120 reserved attempts, whichever ends
first. It expires **13/11/2026 at 09:53:30 Athens time**. Both scheduled triggers
also end at that instant. Renewal is not automatic: it requires a separate review
and preservation of the existing authorization/evidence series. Editing the old
grant's dates or cap in place invalidates its bound receipts. Expiry stops new
reviews, not the dashboard, history, existing reports or inverter operation.

The [recurring runner runbook](../deploy/README-recurring-reports.md) records the
current installation, checks, operating conditions and administrator controls.
Earlier phase templates and records below are retained for context; do not rerun
them over the active installation. The owner has approved public reports and
independent publication without per-report approval. That permission is limited
to reports. It does not authorize changing the site, history, Home Assistant,
controls, operating system or inverter settings.

Reports use English, Greek and Russian throughout: summaries, metric labels,
finding titles, observations, interpretation and limitations. Schema version 2
requires exact `en`/`el`/`ru` text objects; the renderer rejects incomplete reports
instead of silently showing English. Deterministic evidence translations come
from `server/insights_i18n.py`; the agent provides all three narrative versions.
Weather attribution is intentionally absent: no historical weather source is connected.

## Data flow

1. The isolated reporting runner uses its dedicated SSH identity with the exact
   command `prepare`. The server reads recorded history and computes a frozen
   analysis of the last completed 24 hours against seven preceding 24-hour windows.
2. The result contains an `analysis_id` and server-calculated evidence. The agent
   writes a concise summary and optional commentary on existing finding IDs.
3. It sends the JSON below to the exact SSH command `publish` on stdin. The server
   retrieves the saved analysis, validates text, appends one immutable report and
   atomically updates the public feed. No approval is needed for each valid report.
4. Overview reads `/reports/index.json`. Up to the newest 100 reports are available
   within a 2,000,000-byte public-feed cap, newest first. Older reports are omitted
   from that feed when needed; the private archive retains every published report.

```json
{
  "analysis_id": "64 lowercase hexadecimal characters returned by prepare",
  "summary": {"en": "No readings were available.", "el": "Δεν υπήρχαν μετρήσεις.", "ru": "Показания отсутствуют."},
  "notes": [
    {"finding_id": "an existing finding ID", "commentary": {
      "en": "Missing readings do not establish a power outage.",
      "el": "Η απουσία μετρήσεων δεν αποδεικνύει διακοπή ρεύματος.",
      "ru": "Отсутствие показаний не доказывает отключение электричества."
    }}
  ]
}
```

The summary is at most 600 characters per language; up to six notes are allowed,
each at most 400 characters per language. All three languages are mandatory;
the agent must preserve identical meaning and figures across translations.
Submission bytes are limited to 32768, a prepared analysis to 32768 bytes, and a
stored report to 65536 bytes. No extra fields, duplicate
JSON keys, unknown finding IDs, markup, web links, control characters or path fields are
accepted. Titles, severity, figures, report period and evidence are not AI-writable.
The agent can still write inaccurate, mistranslated or irrelevant *plain text*. Schema validation
and a skill cannot guarantee truth or semantic relevance; only the content field
and filesystem capabilities are constrained. Monitor the early reports and revoke
the reporting key if it misbehaves.

Identical retries are idempotent; changing an existing report is rejected. A retry
after a database commit/feed failure rebuilds the feed without duplicate records.
Snapshots expire after 30 minutes. One preparation per minute is reused, at most
12 fresh preparations/hour are accepted, publication has a five-minute global
cooldown, and at most 12 reports may be added in a rolling 24 hours. These are
abuse limits, not the eventual schedule. The archive stops at 20,000 reports and
requires administrator review rather than deleting report history.

## Evidence and limitations

`server/insights_analysis.py` is the source of the deterministic rules and tests.
It reads fixed, validated daily filenames; it never modifies readings. Energy is
integrated from minute averages with boundary clipping and per-metric coverage.
Missing values are not zeros. Battery charged and supplied are split by sign;
solar-to-house is not an independently metered channel. Coverage is a measure of
available minute aggregates, not a guarantee of complete sub-minute sampling.

Baseline comparisons require at least four sufficiently covered windows out of
seven; weak baselines and gaps are explicitly disclosed. The baseline excludes
the 24 hours being evaluated. Windows use elapsed time, while displayed dates use
Europe/Athens. A DST change, a short baseline, seasonal changes, changes to panels
or settings, and different weekday habits limit comparisons. No mode transitions
are labelled confirmed inverter faults; no claims about appliances or household
occupancy are permitted. Existing estimated grid and minute-average measurement
limitations remain. Thresholds describe unusual recorded behaviour, not electrical
safety limits. Reports are not real-time safety monitoring.

Stable finding codes permit comparison between runs, but this version stores
snapshot reports rather than a full incident lifecycle. A recurring finding can
appear in overlapping reports; do not describe it as a newly started incident
without checking previous evidence. Automatic resolved/ongoing incident tracking
is deferred until the reporting baseline is validated.

## Required production isolation (not optional)

Do **not** schedule this in the existing administrator-powered chat or a workspace
that can read the administrator SSH key. Prompts and skills are not a sandbox.

The original Linux design uses a dedicated OS account/container. The owner chose
to retain their existing Windows account instead; the installed runner therefore
relies on the verified MXC model sandbox plus trusted host-side transport, not a
second Windows account. The existing Windows user and administrators remain
trusted: this is not protection against other programs running as that user.
The [production launcher](../deploy/README-production-launcher.md) and
[recurring runner](../deploy/README-recurring-reports.md) document the current
boundary; the [staging record](../deploy/README-windows-report-staging.md) retains
the earlier checks. In either design:

- Only the reporting skill, read-only instructions, and a bounded scratch directory.
- A trusted broker exposes only prepare/publish to the model and holds the dedicated
  reporting SSH key outside model-readable files. No administrator key, GitHub token,
  HA token, password hash, Docker socket, host-home mount or sudo access.
- Network access restricted to the reporting SSH destination and model transport
  required by the selected runner. No browser, email or arbitrary connected tools.
- No write permission to the repository, production website or hardware controls.

The alternative Linux broker, client adapter and service templates are described
in [phase-B preparation](../deploy/README-report-isolation.md). Those Linux broker
services are not the installed Windows transport. Do not expose a general shell
or arbitrary network tool to the reporting model. Merely hiding the key's contents
from a prompt does not protect it if the model can read its file.

On the server, an administrator must provision `homeenergy-reporter` without sudo
or supplementary groups (especially docker, adm, systemd-journal and www-data).
Use a separate **root-owned, 0755 home**, not `/home/shini`, and `/bin/dash` (or a
verified `/bin/sh` pointing to dash) as its login shell. No reporter-writable shell
startup files are permitted. sshd invokes the forced command through the login
shell before Python isolation starts. Password login is disabled by the supplied
Match block; no unrestricted SSH command, interactive session or forwarding is permitted.
Audit effective `AcceptEnv`: it must not accept `BASH_ENV`, `ENV`, `LD_*`, `PYTHON*`,
`PATH`, `SHELL` or broad wildcard patterns. A new narrow allowlist does not necessarily
cancel inherited entries. Verify the **global** effective `PermitUserEnvironment no`
(not a Match-supported directive on commonly deployed versions), and `PermitUserRC no`.

Required ownership and paths:

| Path | Owner / mode | Purpose |
| --- | --- | --- |
| `/opt/homeenergy-reports` and Python modules | root, 0755 dir / 0644 files | Publisher and analyzer; never writable by reporter |
| `/etc/ssh/authorized_keys/homeenergy-reporter` | root, 0644 | Public key with `restrict`; parent root-owned 0755; readable but not writable by reporter |
| `/var/lib/homeenergy-reporter-home` | root, 0755 | Non-writable login home, separate from administrator and report state |
| `/var/lib/homeenergy-reports` | reporter, 0711 | Private SQLite, lock and staging snapshots (umask 077) |
| `/var/lib/homeenergy-reports/public` | reporter, 0755 | Only generated feed, readable by Nginx |
| `/var/www/homeenergy/runtime/history` | existing owner; reporter read-only | Input history; no reporter write permission anywhere here |

Provision empty report directories; the publisher never creates arbitrary paths.
An initial public feed can be `{"schema_version":2,"reports":[]}`. Ensure the
account cannot write any existing frontend/config directory. Inspect actual ACLs,
groups and ownership; account names alone do not enforce this. Keep any private
credentials in other accounts inaccessible to reporter.

Install `deploy/reports-sshd.conf.example` and test the effective Match configuration
with `sshd -T -C user=homeenergy-reporter,host=...,addr=...` before reloading sshd.
Keep an administrator recovery session open. The original SSH command is checked
against exactly `prepare` and `publish`; no arguments are interpolated into a shell.
The fixed 45-second timeout limits incomplete stdin sessions. `python3 -I` ignores
the runner's environment imports; only the root-owned module directory is added.

The Nginx example exposes only the exact read-only feed path, not the state directory,
SQLite database, submission endpoint or credentials. Report publishing happens over
SSH, not a public HTTP write endpoint. No modifications to restart security are needed.

Deploy the publisher modules, empty report store and exact Nginx feed/asset routes
before switching the Overview HTML. Do not deploy the synthetic preview server.
Only then validate the report-only broker and enable the chosen schedule. These
steps require a separately approved, backed-up production deployment; no live
credential is included in this repository. No schema-1 reports were deployed;
there is no automatic migration of the earlier English-only development format.

## Activation gates and rollback

A guarded **phase-A** helper and operator guide are available in
[deploy/README-insights.md](../deploy/README-insights.md). This first activation
publishes only the translated Overview section and an empty schema-2 feed, and
stages root-owned publisher modules. It creates no reporter account, credential,
database, SSH rule or schedule. The restricted account and broker are a separate
phase; running the UI deployment is not authorization to enable publication.

Before enabling unattended publication, independently verify with the reporting key:

- `prepare` works with read-only history and returns a recent snapshot.
- A permitted test report can be published only to a staging report store first.
- `id`, shell, SCP/SFTP, arbitrary paths, PTY, port forwarding and TUN/TAP tunnelling are refused.
- Attempts to alter unrelated files fail at the OS layer; reporter has no sudo.
- Repeated identical submission is idempotent, changed content cannot overwrite,
  global rate limits apply across sessions, and HTML cannot become executable UI.
- Both public domains serve only the feed with no-store and normal security headers;
  guessed private files and control status remain 404.
- Stop/revoke the dedicated key to stop future publication. A failed run leaves the
  last valid feed visible, with a report timestamp; it must not claim fresh analysis.

Only an administrator can correct/remove an erroneous report. Back up the private
SQLite store consistently. Rollback consists of disabling the reporting key/schedule
and restoring the previous frontend/config; retain reports for audit. No equipment
or data-collection restart is required.

## Scheduling and model selection

The approved runner uses **GPT-6.1 Sol (`gpt-6.1-sol`), Medium reasoning effort**.
Runtime checks enforce that choice independently of this administrator chat. The
Windows task requests one review at midnight and noon. The unchanged six-hour
admission floor is a separate safety check, not the active schedule.

The PC must be awake, on AC power, online and logged into the existing Windows
account. Missed slots are skipped: there is no wake timer, catch-up queue or
automatic retry of an uncertain report. See the current runbook before changing
the schedule or attempting recovery. A successful supervised run does not yet
certify a timed Windows trigger, nor does it establish general report accuracy.

Review early reports for evidence fidelity, restraint, translation and clarity.
The AI receives a small deterministic digest, not thousands of raw points. Any
future model comparison or model/effort change needs separate approval and
review of the pinned runner; it is not an automatic fallback.
Use [official model-selection guidance](https://developers.openai.com/api/docs/guides/model-selection)
when revisiting this choice. A normal desktop scheduled task inherits its execution
permissions; it does not acquire report-only isolation merely by naming this skill
([scheduled-task permissions](https://learn.chatgpt.com/docs/automations?surface=app)).
Keep broker credentials outside model-readable files as described in the
[sandbox security guidance](https://developers.openai.com/api/docs/guides/agents-api/environments/security).

The reusable skill is `home-energy-report`; the production launcher supplies only
the reviewed instructions and required context inside its verified model boundary.
Its instructions explicitly stop publication outside that approved isolated runner.
The canonical instructions are [REPORTING_SKILL.md](REPORTING_SKILL.md); keep the
local discoverable skill in sync when editing them. Report content belongs in runtime
storage, not Git. Git tracks code, tests, schema instructions and deployment templates only.

### PC scheduler activation gate

**Historical checkpoint, 13/10/2026:** the rest of this section preserves the
earlier rejected native-app proposal and staging gates, not today's settings.
The current midnight/noon task and its bounded authorization are documented in
the [recurring runner runbook](../deploy/README-recurring-reports.md).

The native desktop automation described below
was not a viable isolation mechanism. Its file is absent; do not recreate or run
it. The separate Windows task `Home Energy Reporter - Dummy` remains disabled.
Its pinned app-server launcher passed synthetic workflow and runtime permission
checks, and now verifies `agents.enabled=false` before any generation. All six
collaboration tools were absent and 18 forced calls rejected. It still points
only to the dummy MCP; no live or staging credential is installed there.

The new Windows transport/staging publisher is prepared separately, with 37 new
local tests passing. Its executable adapter entrypoint deliberately refuses to
start until a reviewed launcher is provided. Real SSH, credential denial, server
policy and model integration remain untested. The owner does not want a separate
Windows account. A separate restricted **server** staging account/key is still
required. Follow [Windows staging preparation](../deploy/README-windows-report-staging.md)
for current gates rather than enabling the older app schedule. Official docs
describe the app-server protocol as experimental/unsupported for production;
the personal-use candidate must stay pinned and fail closed on drift.

The following records the **earlier native-app proposal**, not active settings:

The native schedule is deliberately **projectless and paused** while a restricted
execution target is unresolved. The app currently serializes this placeholder
with the user's home as its working directory; that is **not** an isolated report
workspace. Do not enable it or use Run now. Bind and verify an approved reporting
environment first, without changing the owner's general development permissions.

The inspected scheduler interface has no documented per-job permission-profile
or tool-allowlist field. Official documentation says scheduled tasks use default
sandbox settings. Local filesystem/network profiles do not also restrict MCP,
browser or connected applications. A separate project, a read-only prompt, or a
two-tool wrapper cannot by itself establish report-only access. These are
activation blockers, not permission to fall back to the owner's admin SSH key.

The existing broker remains a Linux implementation. Its UID separation,
SO_PEERCRED check, Unix ownership and systemd-held credentials cannot be ported to
Windows by replacing paths. A PC integration needs an independently verified
Windows credential/service boundary or an approved isolated Linux environment.
Neither has been installed. WSL with Windows-drive mounts or host interop is not
automatically isolated from the owner's files and credentials.

Before activation, verify all of the following in the **actual scheduled run**:

- Only the reporting preparation and publication tools are exposed. No shell,
  code execution, browser, unrelated connectors or admin credentials are available.
- The task cannot read an administrator-only synthetic sentinel, modify an
  unrelated test file, access arbitrary network destinations, or alter its own
  instructions/configuration. Use test fixtures, not real secrets or live files.
- A trusted broker owns a new, report-only credential outside model-readable
  storage. The server's forced-command account and staging publication tests pass
  the existing phase-B checks. No service or account can broaden the request into
  a caller-selected path, host, command or device operation.
- One staging report passes evidence, translation, validation, duplicate/retry and
  immutability checks. No synthetic test report goes to the public feed.
- The owner reviews the effective isolation and approves activation. Only then
  enable the saved six-hour interval and verify the first public report.

The PC must remain awake, online, and running the desktop app. Missed runs are not
authorization for an unbounded catch-up loop. Each successful run reviews the most
recent completed 24 hours; overlapping reviews are expected. Pausing the task stops
future scheduled starts, not necessarily an already running publication. For an
immediate publication stop, interrupt the active run and revoke the report-only
credential separately. Existing reports remain visible.

Manage the saved task from **Scheduled** in the desktop app. It must stay paused
until these runtime checks pass. No global Codex settings, operating-system
accounts, credentials, server services or production reports were changed while
preparing the PC schedule.
