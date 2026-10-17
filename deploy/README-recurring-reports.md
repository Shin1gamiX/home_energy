# Recurring report runner: authorization and verification record

**Superseded operational status, 17/10/2026:** scheduled reporting was disabled at
the owner's request. After manual reporting was activated and committed, the
disabled task was backed up and removed, along with the twelve approved runner,
test and task-template files. Preserve this historical runbook and the original
authorization/run evidence; do not renew, activate or use this runner for manual
reviews. Its original source remains in commit `b07e8f0`.
The replacement workflow is [manual reporting](../docs/MANUAL_REPORTS.md).
Everything below is a historical record, including references to enabled tasks.

**Current status, 16/10/2026 after the noon failure:** the helper initialization
fix and evidence-preserving recovery are **active**, following separate owner
approval. No-model startup and sandbox checks passed after activation. The
**Home Energy Reporter** task definition is unchanged: enabled for **00:00 and
12:00 Athens time**, with the next trigger at **17/10/2026 00:00 Athens**. The
original grant, two completed runs and consumed failed attempt `000003` remain
intact: **117 attempts remain**, expiring **13/11/2026 at 09:53:30 Athens time**.
No report was manually started, and real publication after this fix is not yet
verified. The unchanged six-hour admission floor permits the next attempt no
earlier than **16/10/2026 18:00:05 Athens**; that is not an additional scheduled
trigger. The final dated section supersedes historical runtime/status records
below; older records and Dummy-task references are retained as history.

## Original local-only preparation record

Prepared and checked on 14/10/2026, approximately 07:25 UTC / 10:25 Athens.
This is local preparation only. No model turn, SSH connection, live-history
preparation, publication, server operation, installation, download, Git commit or
push was performed in this stage.

The earlier successful public trial and its consumed authorization remain intact.
The existing server publication gate was **not changed**. This stage does not
close that previously enabled gate, nor does an open gate authorize local runs.
The Windows `Home Energy Reporter - Dummy` task remains disabled, with unchanged
exported task XML. No task or Codex app automation was created or updated.

## New files and reuse boundary

- `server/insights_recurring_run.py`: separate supervisor and no-model checks.
- `server/insights_recurring_store.py`: bounded authorization, six-hour admission,
  exclusive supervisor lock, separate immutable run records and audit validation.
- `server/test_insights_recurring.py`: disposable synthetic regression tests.
- This runbook.

No earlier source, source pin, instruction, fixture, deployment helper, global
Codex setting or private trial artifact was edited. The new supervisor loads
the exact pinned bytes of the original trial definitions without executing its
CLI. It reuses the verified bootstrap, runtime checks, one-turn session, owned
Windows process jobs, strict report transport and result validation. It does not
call the trial's reservation, worker, supervisor or storage-check entrypoints.

The model remains **GPT-6.1 Sol (`gpt-6.1-sol`), Medium reasoning effort**. Runtime
and thread checks enforce that choice; this administrator chat's settings do not
select the reporter's model.

## Controls that remain separate

1. **Windows task:** decides when to invoke the candidate; currently disabled and
   still points to the old dummy runner.
2. **Local recurring authorization:** permits a reviewed code/configuration
   bundle to reserve runs. It is currently absent. The runner cannot create it.
3. **Server gate and restricted reporting SSH account:** independently permit
   only the installed prepare/publish operations. This runner cannot modify the
   gate, account, SSH policy, website, history or devices.

There is no scheduling loop, task-registration mode, auto-renewal, permission
fallback, model fallback, automatic source re-pinning or repair mode.

## Per-invocation contract

1. Validate the fixed installation, source-only import closure, pinned executable,
   instruction and saved-configuration hashes, production connection metadata,
   host pin and private ACLs. Private-key contents are not read by these checks.
2. Require the separately approved recurring authorization. Missing or invalid
   authorization stops before creating a lock, spawning a runtime or contacting
   a server. No environment variable or model input can supply a different path.
3. Hold an exclusive Windows file handle throughout the supervisor lifetime.
   Another invocation is refused immediately. The empty lock file is never
   deleted or truncated; the OS releases the handle if the owner dies.
4. Validate **every** earlier recurring run: contiguous identifiers, matching
   authorization/bundle, chained terminal digests, completed markers, bounded
   audit sequence, translated submission, matching analysis/result identifiers
   and artifact hashes. Old snapshots are validated at their original run time.
   Incomplete, malformed, changed, failed or uncertain evidence stops the series.
5. Require at least 21,600 seconds since the previous reserved attempt, and the
   approved not-before time. An early invocation returns `not_due` and
   `next_due_unix`, without creating a run or starting a model. A late invocation
   creates **one current review**, never a catch-up queue of missed reports.
6. Exclusively create the next receipt **before** spawning anything. It stores
   a digest of an unpredictable run token, not the token itself. Partial writes
   and failures consume that attempt. Receipts cannot be reset or reused.
7. Start the owned worker suspended, assign its Windows Job, then resume it.
   The runtime owns a second process Job. The actual runtime must confirm the
   existing restricted production profile, disabled delegation/notifications,
   read-only model filesystem, denied model network, exact two report tools and
   ephemeral thread. No per-turn model/permission override is accepted.
8. Arm the run and persist a one-turn model marker before `turn/start`. Only one
   MCP session can claim the token. It prepares one frozen 24-hour review against
   the preceding seven days, then sends one validated EN/EL/RU submission. Server
   code still fixes facts and severity. No weather source has been added.
9. Persist the exact submission before its network send and the matching
   acknowledgement before success. Identical calls after confirmed success use
   the saved acknowledgement; an uncertain or rejected request is never resent.
10. Require a completed model turn, independently validate saved result/evidence,
    and finish cleanup of owned processes before the supervisor seals a completed
    terminal record. Failure is uncertain, not successful. The next invocation
    remains blocked even if six hours have passed or the computer restarted.

The preserved bounds are 45 seconds for runtime preflight, 240 seconds for the
model turn, 360 seconds for the outer worker/run lifetime and 50 seconds per SSH
process. Two network sends are expected: one prepare and one publish. Raw model
events, reasoning, arbitrary exception messages and credentials are not logged.

The protocol checks follow the [official OpenAI app-server lifecycle](https://learn.chatgpt.com/docs/app-server):
initialization precedes thread/turn operations, and `turn/completed` is accepted
only with a successful completion status. A failure or interruption is not success.

## Authorization policy to review before activation

The fixed file would be:

`C:\ProgramData\HomeEnergyReporterProduction\recurring-v1-authorization.json`

**It has not been created.** Its exact schema requires:

| Field | Required meaning |
| --- | --- |
| `schema_version`, `scope`, `purpose` | `1`, `production`, `recurring_public_reports` |
| `bundle_sha256` | Exact reviewed recurring bundle, including the preserved runtime/configuration closure |
| `issued_unix`, `not_before_unix`, `expires_unix` | Explicit UTC validity and first-run boundaries |
| `interval_seconds` | Exactly `21600` |
| `max_runs` | Explicit cap, at most `120` reserved attempts |
| `max_model_turns_per_run`, `max_reports_per_run` | Exactly one each |
| `server_activation_verified` | Administrator assertion after separate server verification, not a remote attestation |

This initial candidate bounds an authorization to **at most 30 days and 120
attempts**, whichever ends first; shorter grants are supported. These limits are
not active permissions and should be reviewed with the owner before activation.
The runner will not renew a grant or overwrite it. Old receipts bind its exact
contents, so changing its dates/cap in place will fail validation. Renewal,
recovery, source upgrades and a different cadence require a separately reviewed
procedure that preserves the existing series; none is implemented here.

Do not create a throwaway one-run grant and then silently edit it into a broader
one. A later controlled first invocation and the intended bounded series must
be explicitly authorized with that lifecycle understood. No granting or enabling
command is supplied by this preparation stage.

## Timing, failures and administrator visibility

The six-hour minimum uses UTC elapsed time, not wall-clock labels; daylight-saving
changes do not shorten it. Clock reversal relative to the last completed attempt
is refused. A delayed trigger (sleep/offline PC) runs at most one review when next
eligible, so reporting can be later than six hours. A subsequent trigger arriving
too early is skipped, not queued. Future task timing must account for this strict
minimum and scheduling jitter; no Windows trigger has been selected or changed.

The invocation exits with a fixed secret-free error on a rejected/uncertain run.
There is no new email, desktop notification or public status endpoint. Private
per-run files can be reviewed by the owner through the existing protected folder.
The JSONL audit records UTC timestamps, fixed event/method names, send-attempt
numbers and fixed result codes. Analysis, submission and acknowledgement are
separate private files, not copied into the audit log.

Disable the eventual Windows task to prevent future scheduled launches. That is
not cancellation of a currently accepted operation. The existing server gate-only
stop is a separate administrator action; no emergency action is run here. Never
delete a receipt, edit a terminal to `completed`, remove partial evidence, or
rerun an uncertain publication to make the task appear healthy. Inspect local
evidence and server state first, then obtain approval for recovery.

The current Windows user and administrators remain trusted. They can deliberately
change code, grants or credentials. This is **not** protection against arbitrary
programs running as that same Windows user, nor a replacement for a separate OS
identity. Model confinement and the server's restricted SSH command are the
enforced boundaries. No separate Windows account was added.

## Retained harmless local check files

Only these new files were created outside the repository:

- `C:\ProgramData\HomeEnergyReporterProduction\recurring-v1-storage-probe.json`
- `C:\ProgramData\HomeEnergyReporterProduction\recurring-v1-lock.lock`

The first contains fixed synthetic flags; the second is empty. They inherit the
verified private directory's ACL. Repeating the check verifies their existing
bytes/metadata rather than overwriting them. Neither authorizes a run. No
recurring receipt, run token, analysis, submission, result or terminal exists.

## Repeatable no-model checks

Run from the existing repository with the already installed Python:

```powershell
$hePython = 'C:\Users\anest\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
& $hePython -I -B server/insights_recurring_run.py --check
& $hePython -I -B server/insights_recurring_run.py --storage-check
& $hePython -I -B server/insights_recurring_run.py --preflight
& $hePython -I -B server/insights_recurring_run.py --os-check
& $hePython -B -m unittest discover -s server -p test_insights_recurring.py -q
& $hePython -B -m unittest discover -s server -p 'test_insights*.py' -q
```

These checks deliberately require the recurring authorization and all recurring
run evidence to be absent. They do not require deleting or hiding the earlier
public-trial evidence. The MCP preflight uses a closed sentinel and a blocked
adapter that cannot even construct a live report session. The OS check uses
fixed synthetic canaries, not real credential contents.

`--run-due` is a future execution entrypoint, **not** a no-model test command.
It is not wired to a task and was not invoked against production in this stage.

## Verification results and boundaries

- **52 new synthetic tests passed.** These cover strict grants, early/expired
  calls, six-hour timing, overlapping supervisors, multiple completed runs,
  skipped missed intervals, quotas, clock reversal, chained/immutable evidence,
  partial writes, revocation, malformed JSON/audits, changed payloads, provenance,
  lost acknowledgements, no retries, disabled preflight and owned-worker cleanup.
- Full insights suite: **1,165 tests run; 1,146 passed, 19 skipped**. Skips are not
  passes. Provisioning/reload messages emitted by this suite came from mocked
  fixtures, not real server operations.
- Actual static/integration checks passed for the new bundle.
- Actual no-model runtime preflight confirmed the unchanged restricted profile,
  pinned model/effort, disabled delegation and notification commands, exact MCP
  contracts, ChatGPT account availability and ephemeral thread. No turn started.
- Actual private storage check confirmed opened-handle ACL/path/link checks,
  exclusive access, rejection of a second lock handle, and successful lock
  reacquisition after release. No actual overlapping model runs were launched.
- Separate actual OS-profile probe: allowed synthetic read succeeded; denied
  synthetic read, root read, write and loopback network access returned explicit
  denial. Host loopback positive controls passed before/after; the forbidden
  synthetic output file remained absent. This is OS-profile evidence, not a new
  model-driven bypass test or a probe of real credentials.
- **153 pre-existing files were hash-checked with no change**, including the
  saved Codex configuration, prior private snapshot/result, successful public
  trial evidence and existing server/deployment/docs source. Private-key bytes
  were excluded; their metadata/permissions were checked by the pinned guard.
- Exported Windows task XML was identical before/after and the task remained
  disabled. No existing evidence was reset or rewritten.

This does not certify an actual recurring publication, wake-from-sleep behavior,
a scheduler trigger, future grant renewal, or every possible model bypass. Those
are not implied by synthetic/no-model checks. Server state was not remotely
re-inspected or changed during this local-only preparation.

## Reviewed hashes

| Component | SHA-256 |
| --- | --- |
| Recurring runner | `80b3dd4d727d14a03eb8656aa5bdbef874f3728f5f4ca386918a10af4c0ebe8b` |
| Recurring store | `46b76e87f70ffc8cae44a8422b3753a89049891dc65ab0b2810b371d0b1096e8` |
| New tests | `5d211e74237b91e8243f1e143f484d33f1f33a05d0beeb468d08d4e36ed3ce2c` |
| Preserved trial supervisor | `211ba1404f040516f874d5deb157a8c846d52356443be2aaa21d54a863eca54a` |
| Recurring authorization bundle | `57904a44cf521c05026b55d93a31d4c9001e36856c3a9bff308625098e116c0c` |

The bundle includes the preserved trial bootstrap's full reviewed closure and
the new runner/store bytes. Editing those inputs invalidates the authorization;
do not update a hash merely to suppress a failed check.

## Next approval boundary

Review the bounded series authorization and operational schedule plan. After
separate approval, supervise the first real invocation through this new runner,
verify its publication/evidence and preserved state, and only then enable the
intended recurring task. Scheduling remains disabled until explicitly approved.
Do not reuse the consumed `public-trial-01` authorization or connect its one-shot
entrypoint to the schedule.

## Startup correction and supervised authorization: 14/10/2026

The owner approved the bounded 30-day/120-attempt authorization and exactly one
supervised public report, with scheduling disabled and existing evidence retained.
The subsequent targeted startup correction and regression tests were approved
before changing the new recurring runner. No original trial source or pin was
changed, and no global Codex setting, task, server policy or device was modified.

### Corrected thread binding

The initial actual no-model preflight rejected
`production_trial_unexpected_thread`. A closed diagnostic established that the
validated `thread/start` response preceded an MCP startup notification for the
same thread, while the evidence recorder was still waiting to bind that ID.

The recurring session now binds the ID immediately after the complete existing
thread-response policy check succeeds. It still rejects an unvalidated early
notification, a different thread, replacement of a bound thread, or a response
with different permissions/model/instructions. Binding alone does not authorize
a model turn. No event was ignored and no permission check was weakened.

Seven regression tests were added. Verification after the correction:

- **59 recurring tests passed.**
- Full insights suite: **1,172 tests run; 1,153 passed, 19 skipped**. Reload and
  provisioning messages in test output were mocked fixtures, not server actions.
- Actual static bundle validation and no-model runtime preflight passed.
- Actual private-storage/lock checks and synthetic OS-profile denial checks
  passed. The latter retained explicit denied read/write/network results and
  successful host-side loopback positive controls; no real secret was probed.

### Installed grant and supervised result

The private `recurring-v1-authorization.json` was created only after read-only
server checks confirmed the already-enabled publication gate and expected
production files. Its canonical schema and private file ACL were verified before
the one supervised invocation of `--run-due`.

| Limit | Approved value |
| --- | --- |
| Issued / not before | 14/10/2026 07:53:30 UTC / 10:53:30 Athens |
| Expires | 13/11/2026 07:53:30 UTC / 09:53:30 Athens |
| Admission interval | At least 21,600 elapsed seconds between reserved attempts |
| Maximum attempts | 120, or expiry, whichever ends first |
| Per attempt | One model turn and at most one report |
| Model / effort | GPT-6.1 Sol (`gpt-6.1-sol`) / Medium |

The Athens offset changes during this grant; elapsed-time enforcement uses UTC.
The canonical authorization digest is
`74f438fc5970f8c7f17df618a913cb7131646440e3c5380afeef6dd1c008accf`.
Do not edit its dates or cap in place: all receipts bind the exact grant. Renewal
or a source/configuration upgrade requires a separately reviewed procedure.

Run `000001` was reserved at 07:54:00 UTC and completed at 07:55:10 UTC on
14/10/2026. Exactly one model turn, one prepare send and one publish send were
observed. The matching publication acknowledgement and independent completed
evidence validation both passed. No retry or second model invocation was made.

- Public analysis/report ID:
  `9483b030ec7aeb6f552adf66c1fdbde1e5b575a6fb1faa954f9a093e8b675c28`.
- Submission SHA-256:
  `ec5fda958abf0be1a1d9f4f7fbba3cb2085307bed54a2052b0d96a4e92ffef48`.
- Private evidence uses the new `recurring-v1-000001-` prefix in
  `C:\ProgramData\HomeEnergyReporterProduction`; the original trial evidence was
  neither reused nor changed.
- Read-only history validation confirmed one completed, correctly chained run,
  validated artifact digests and the expected 15-event audit sequence.
  **119 authorized attempts remain**, subject to the grant expiry.

Both HTTPS origins served the exact updated feed bytes, with JSON MIME type,
`no-store` and `nosniff` headers. The feed contains the new report first and the
earlier public trial report unchanged. The live `.com` dashboard rendered the
latest report, findings and limitations in English, Greek and Russian; English
was restored afterward, and no browser errors or warnings were captured.

The report explicitly describes incomplete recordings and measured changes, not
a diagnosis. Its translations were checked against the saved analysis. No weather,
occupancy, appliance identity or equipment-fault explanation was invented.

### Preservation and remaining boundary

- **152 pre-existing non-credential files were rechecked unchanged** against the
  saved preparation baseline. One credential-named entry was excluded from this
  content recheck; the guard verifies credential metadata without exposing it.
- Nine protected server source/configuration/frontend hashes were unchanged.
- Nginx, exporter and control-service active states, main PIDs and restart
  counters matched the before-run snapshot. No service or device restart or
  history-data modification was requested.
- Exported Windows task XML remained byte-for-byte identical. The existing
  `Home Energy Reporter - Dummy` task is **disabled** and still has its old dummy
  action. No task/automation was created, updated or enabled.
- No package installation, download, Git commit or push was performed.

The no-model CLI checks documented above deliberately reject an installed grant
or existing recurring evidence. Do not delete/hide the grant or receipts to make
them pass again. The post-run verification used read-only bootstrap, grant and
stored-history validation, not `--run-due`.

This certifies one supervised publication through the recurring runner, not a
Windows scheduled trigger, wake-from-sleep behavior or grant renewal. Scheduling
requires separate approval and a reviewed change from the dummy action to this
runner, accounting for the strict six-hour interval and scheduling jitter.
An open server gate and installed grant are not evidence of an enabled schedule.

### Current reviewed hashes

| Component | SHA-256 |
| --- | --- |
| Corrected recurring runner | `1bc901f9010c7d26b4fd2bf71f50304f4f291c90f3f15bc8f6af7231b149e67e` |
| Unchanged recurring store | `46b76e87f70ffc8cae44a8422b3753a89049891dc65ab0b2810b371d0b1096e8` |
| Recurring tests | `1b999c69ccad3f091b2dc930216013942e94b9c76c09794884848abb87685b60` |
| Unchanged trial supervisor | `211ba1404f040516f874d5deb157a8c846d52356443be2aaa21d54a863eca54a` |
| Authorized recurring bundle | `24998eee162fd7b6160c1fb41daf33708cf2a3f68a1b14d66b9d1de72ff4c6f9` |

## Midnight/noon schedule enabled: 14/10/2026

The owner separately approved enabling the schedule, changing its requested
cadence to 12 hours starting at midnight. A new task was registered disabled,
its stored definition was checked, and only then was it enabled. The historical
`Home Energy Reporter - Dummy` task and its XML remain unchanged and disabled.
The production task has no registration/boot/logon trigger, and was not run
during setup. Windows reported `Ready`, `Enabled=true`, next run
**15/10/2026 00:00:00**, and result `0x41303` (task has not yet run).

### Schedule and execution

- **Location:** Task Scheduler Library > **Home Energy Reporter** (root folder).
- **Times:** two daily calendar triggers, at 00:00 and 12:00 local Athens time.
  First midnight: 15/10/2026; first noon: 15/10/2026. Start times intentionally
  omit a UTC offset so the PC's `GTB Standard Time` timezone applies. Keep the PC
  timezone set to Athens. The autumn DST transition preserves midnight/noon
  labels; the elapsed interval across that transition is 13 hours, not 12.
- **Availability:** the existing Windows account must be logged in, the PC
  awake and on AC power, and Windows must report a network connection. A locked
  desktop is compatible with an existing login; a signed-out session is not.
  Internet/server reachability is still checked by the runner, not guaranteed
  by Windows' network condition.
- **Missed times:** skipped. No wake timer, catch-up, queued overlapping runs,
  failure restart or on-demand start is enabled. Missed slots do not consume an
  attempt unless the runner actually starts and reserves it.
- **Action:** the already installed, signed `pythonw.exe` launches the unchanged
  runner with `-I -B` and the fixed `--run-due` argument. No console window, shell
  wrapper, user-supplied command, new credential or elevated principal was added.
  The runner still starts its worker with the existing pinned `python.exe`.
- **Limits:** 10-minute scheduler execution limit; the runner's shorter owned
  process/time limits and `IgnoreNew`/exclusive-lock protections remain active.
  These settings do not authorize automatic recovery of an uncertain run.

The existing six-hour **admission floor** is an independent safety check, not a
schedule. It was deliberately not edited to change the schedule: the exact grant
and source bundle are already bound into preserved receipts. The two calendar
triggers now request only midnight/noon launches, which satisfy that floor.

Both triggers expire at **13/11/2026 07:53:30 UTC**, matching the existing grant
(09:53:30 Athens). Windows exported that same instant as
`2026-11-13T10:53:30+03:00`; the offset-bearing value was compared in UTC, not by
its displayed clock label. Expiration does not delete the task, renew the grant,
reset attempts or erase evidence. As of activation, 119 attempts remain, but
this twice-daily schedule reaches the existing expiry before that quota.

### Verification and retained evidence

- Seven new offline schedule tests and all 59 recurring tests passed.
- Native Task Scheduler XML validation passed without registering/running a
  task. The UTF-8 file declaration was removed from the in-memory COM string
  to avoid a BSTR encoding conflict; no scheduling field was changed.
- The stored action, principal, two daily triggers, exact expiry instant,
  no-retry/catch-up settings and next-run time were checked before and after
  enabling. Limited privileges and the current interactive account were retained.
- The signed windowless executable was checked, and a real windowless
  bootstrap/grant/history check exited successfully without a model turn,
  connection, preparation or publication.
- The PC's actual timezone data confirmed the October 25 transition from
  UTC+03:00 at midnight to UTC+02:00 at noon. No clock/timezone setting was changed.
- All 14 pre-existing recurring authorization/evidence files were hash-checked
  unchanged. No earlier report, receipt, source pin, saved Codex setting or
  server setting was modified. No model call, report, service/device restart,
  installation, download, Git commit or push occurred during schedule setup.

Private before/after task records are retained, without credentials, under:

- `C:\ProgramData\HomeEnergyReporterProduction\schedule-20261014-before.json`
- `C:\ProgramData\HomeEnergyReporterProduction\schedule-20261014-active.json`

The deployable XML is `deploy/homeenergy-reports-task.xml`; it is deliberately
disabled in source and specific to this installation, first date and grant.
Do not blindly import/enable it after expiry or overwrite another task. Its
SHA-256 is `dcfd406d251827cf67972513196339adaafa8576a717e3cc5a49b134def5dfac`.
The verified installed `pythonw.exe` SHA-256 is
`520a1eee620afae58c70b680969a31f4e21d12a9d38f71a2151a71173408c78a`.

Task Scheduler reports the exit status of the fixed runner. A nonzero result
requires administrator review of the preserved evidence; do not automatically
retry, reset a receipt or extend a grant to work around it. A future trigger can
still start the supervisor, but retained uncertain evidence blocks another model
run. This setup has **not yet observed the first real timed trigger**.

### Administrator controls

To inspect without running anything:

```powershell
Get-ScheduledTask -TaskName 'Home Energy Reporter' -TaskPath '\'
Get-ScheduledTaskInfo -TaskName 'Home Energy Reporter' -TaskPath '\'
```

To stop future scheduled launches, right-click **Home Energy Reporter** and
choose **Disable**, or run:

```powershell
Disable-ScheduledTask -TaskName 'Home Energy Reporter' -TaskPath '\'
```

Disabling is not cancellation of an already-running publication. It does not
delete the task or change the server gate, grant, report feed or evidence. Do not
enable the old Dummy task. Renewing authorization or changing the runner remains
a separately reviewed operation; the schedule does not do either automatically.

The schedule uses Microsoft's documented
[daily calendar triggers](https://learn.microsoft.com/en-us/windows/win32/taskschd/taskschedulerschema-schedulebyday-calendartriggertype-element)
and [validate-only registration](https://learn.microsoft.com/en-us/windows/win32/taskschd/taskfolder-registertask).
It does not create a Codex app automation: the already verified restricted runner
remains the execution boundary, rather than relying on the app's default
[scheduled-task permissions](https://learn.chatgpt.com/docs/automations#permissions-and-security-model).

## Runtime repair and startup logging: 16/10/2026

The owner approved the repair and secret-free startup-error logging, with the
schedule and all existing evidence unchanged and no report triggered yet.

### Observed failure and repair scope

Run `000002` completed after the 15/10 midnight trigger. The later startup
check rejected changed reviewed inputs: the saved Codex configuration and
managed Python executable had changed, and the previously pinned Codex
executable path no longer existed. Another Codex update occurred during this
review. Windows recorded the 16/10 midnight launch exiting with result `1`.
The disabled Task Scheduler operational history did not independently establish
whether the 15/10 noon trigger launched; do not infer that from its absent report.

This revision explicitly reviews and pins the currently installed runtime,
`codex-cli 0.163.0-alpha.10.2`, including its command-runner, code-mode host and
sandbox-setup companions, Python, pythonw and saved configuration. It does not
change the saved settings or discover/download/re-pin replacements automatically.
The model remains **GPT-6.1 Sol / Medium**. Its effective runtime/thread
permissions, disabled delegation/notifications/plugins, exact report-only MCP
contracts, account authentication and OS denials are checked independently of
those file hashes. The OpenAI documentation informed the compatibility checks;
the actual installed runtime, not documentation alone, was tested.

Changed/new implementation files:

- `server/insights_recurring_run.py`: loads the explicit revision, checks the
  separate repair gate before live entrypoints, and saves fixed-code failures
  even when bootstrap stops before a run receipt exists.
- `server/insights_recurring_revision.py`: reviewed in-memory runtime metadata,
  additive repair binding and legacy/new-bundle evidence validation. Older
  staging/trial/store source files and their pinned bytes remain unchanged.
- `server/insights_recurring_startup.py`: independent, bounded private log writer
  and read-only access to the two fixed repair-record filenames. It cannot
  overwrite old files, change a schedule, read keys, invoke reports or run a model.
- `server/test_insights_recurring_revision.py` and the updated recurring tests:
  synthetic runtime-drift, repair binding, evidence-chain, handoff and logging
  regressions. Temporary test fixtures are not production evidence.

The legacy grant is not replaced or extended. Its exact digest and the completed
run-2 terminal digest bind the new revision. Existing receipts retain their old
bundle; any future run must use the approved repair bundle, continue at run 3,
and retain the original grant, previous terminal, expiry, quota and admission
floor. A pending run still blocks another supervisor. The token-authenticated
worker/MCP handoff validates that pending receipt and its completed prefix
without misclassifying it as a completed run or hiding it from another launch.

### Additive approval, separate activation

The new private repair record is:

`C:\ProgramData\HomeEnergyReporterProduction\runtime-repair-20261016-approval.json`

It records only the exact reviewed bundle, original grant/history bindings and
approval time. It does **not** authorize execution. This separate file is
deliberately absent:

`C:\ProgramData\HomeEnergyReporterProduction\runtime-repair-20261016-activation.json`

Without a separately approved, exact-bound activation record, `--run-due`, its
worker and live report MCP entrypoint fail closed before execution. The task can
still launch on its unchanged schedule, but will record
`recurring_repair_not_activated`; it will not reserve an attempt or start a model.
No task, public server gate, report feed, SSH policy or authorization was altered.
Do not create the activation record merely to clear that deliberate diagnostic.

### Administrator-only startup diagnostics

New events use create-once files in the existing private directory:

`C:\ProgramData\HomeEnergyReporterProduction\startup-<UTC timestamp>-<random id>.json`

Each record contains the UTC timestamp, a fixed event name, allowlisted command
mode, startup phase, fixed rejection code and a synthetic-test flag. Raw
exceptions, credentials, configuration contents, model prompts and request
payloads are never copied into the log. Unknown exceptions become
`unexpected_failure`. Files are at most 2 KiB, with a 1,024-file ceiling and no
automatic deletion or rotation of evidence. The directory and opened-file ACLs
are checked for access by only the current owner, SYSTEM and Administrators.
The fixed PowerShell helper is hash-checked and receives data through JSON stdin,
never executable command interpolation.

Read the latest ten events from PowerShell under the existing Windows account:

```powershell
Get-ChildItem -LiteralPath 'C:\ProgramData\HomeEnergyReporterProduction' -Filter 'startup-*.json' |
    Sort-Object Name -Descending |
    Select-Object -First 10 |
    ForEach-Object { Get-Content -LiteralPath $_.FullName -Raw | ConvertFrom-Json } |
    Format-Table timestamp_utc, mode, phase, code, synthetic -AutoSize
```

Raw files use UTC explicitly; PowerShell versions may display parsed timestamps
in local time. These logs cover Python-level startup/run rejections. They cannot
record a failure to launch Python itself, an unparseable entry script, an
unavailable/unverified log helper, inaccessible private storage or a full log
directory. Windows still retains the task result, but no alerting service was
added. Current-user programs and administrators remain trusted; this is not
isolation from other programs running under that same Windows identity.

Two intentional diagnostic records were retained during verification: one with
`synthetic_startup_check`, and a hidden-pythonw invocation rejected with
`recurring_live_probe_refused`. The latter tests the real exception/log path,
not a report. It preceded adding `storage-check` to the fixed mode labels and
therefore retains `mode: unknown`. Neither record has been rewritten.

### Current no-model checks and evidence

The updated `--check`, `--preflight` and `--os-check` can inspect an existing
grant and completed history read-only. Unlike the historical instructions above,
they do not require hiding or deleting authorization/evidence. `--storage-check`
now deliberately refuses the old mutating live-storage probe. Do not use it for
routine health checks. `--logging-check` is an explicit diagnostic that adds one
synthetic startup record; it does not run a model or report.

```powershell
$hePython = 'C:\Users\anest\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
& $hePython -I -B server/insights_recurring_run.py --check
& $hePython -I -B server/insights_recurring_run.py --preflight
& $hePython -I -B server/insights_recurring_run.py --os-check
```

The no-model session explicitly rejects `turn/start` in addition to the existing
closed report adapter. These checks cannot prepare history or publish. The
separate OS probe reads fixed synthetic canaries; it is not a model-driven
penetration test or a probe of credential contents.

Verification for this revision:

- **324 targeted synthetic regression tests passed**, including the complete
  reserved-receipt / worker / MCP / completed-history handoff with no model/SSH.
- Actual no-model runtime and sandbox-profile checks passed. The allowed canary
  was readable; denied/root reads, writes and network access were explicitly
  denied. Host-side loopback positive controls passed before and after.
- Hidden-pythonw startup-error logging was exercised and its private ACL checked.
- The original **565 evidence/Reporter files**, their hashes/metadata/ACLs, and
  the exported production task definition were verified unchanged against the
  pre-repair baseline. Private-key contents are excluded; their metadata is checked.
- No live model/report, SSH connection, server/device restart, download,
  installation, Git commit or push was performed for this repair.

The runtime is still installed in app-managed locations. A future app/runtime or
configuration change can intentionally stop reporting again. Logs now make that
failure diagnosable, but this repair does not promise update-proof operation or
automatic recovery. Re-review is required; never weaken a failed guard or renew
the grant just to suppress an error.

The source rollback copy is retained privately at
`C:\ProgramData\HomeEnergyReporterProduction\runtime-repair-20261016-original-run.py`
(SHA-256 `1bc901f9010c7d26b4fd2bf71f50304f4f291c90f3f15bc8f6af7231b149e67e`).
Restoring it is a separate approved operation and would restore the earlier
broken runtime binding, not magically restore the removed runtime installation.
All older evidence is retained. Real publication after this repair remains
unverified until a separately approved execution.

| Repaired component | SHA-256 |
| --- | --- |
| Runner | `d2f3ca8e54984f6a66bbbc57fc6485a6ad3988abe0c633d9fa27f33d37d7a37a` |
| Runtime revision | `f6e85fe31c7a431d2f8888c551064943a6d965685b4e11232d889d18f816770b` |
| Startup logger | `5c463cffa847a3dc17a6e2717ff06e8ef2d3abfdd22b9aa384299a51ca454d79` |
| Repair bundle | `5e5d80cb0b44231212cadb67a50e848ce8834237291e3bd54238511d10fed7ca` |

## Noon startup-order fix and preserved failure review: 16/10/2026

The earlier runtime repair was subsequently activated by separate approval.
The noon task launched at **12:00:01 Athens**, reserved attempt `000003` at
12:00:05, claimed its worker at 12:00:08 and stopped at 12:00:10. Its terminal
outcome remains `uncertain`. There is no armed/model/claim marker, analysis,
submission, result or diagnostic for that attempt. The exact audit contains
only `reserved`, `worker_claimed` and `closed` with the uncertain outcome.
The existing worker log reports `production_trial_runtime_rpc_failed`; the
supervisor then reports `recurring_prior_run_needs_review`. None of those files
has been edited, reset, hidden or converted into successful evidence.

### Cause and targeted correction

The worker starts its runtime and required MCP helper before it can arm the
report session. The old helper entrypoint incorrectly required the arm marker
to exist during initialization. That circular dependency rejected the required
helper during `thread/start`, before any model turn or SSH report call. Current
Codex/Python/configuration hashes still matched the previous runtime review;
this failure was not another runtime update.

The [OpenAI app-server documentation](https://learn.chatgpt.com/docs/app-server)
confirms that required MCP initialization failures block thread creation. The
earlier closed preflight used a deliberately inert adapter and the old live
handoff test created its arm marker too early, so neither exercised this order.

The corrected helper validates the exact current receipt, token, worker marker,
history, source/configuration pins and activation during discovery, without
requiring an arm marker that the worker has not created yet. Actual `prepare`
and `publish` calls still use the original store's armed/model/claim guards
before constructing any SSH operation. The worker still arms only after all
runtime checks succeed. No device-control or arbitrary-execution capability
was introduced.

Changed/new files for this correction:

- `server/insights_recurring_run.py`: binds the new recovery layer and corrected
  worker/helper discovery handoff. Original session guards remain in place.
- `server/insights_recurring_recovery.py`: read-only, exact-failure recovery.
  Accepts only the hash-pinned artifacts and logs of failed attempt `000003`,
  verifies prior repair records and every successful run, and requires a
  separate review plus activation. It contains no file writer or process launch.
- `server/insights_recurring_startup.py`: extends its exact read allowlists to
  the two new recovery record names and the two original failure logs, plus
  fixed recovery rejection codes. No arbitrary path or read API is exposed.
- `server/test_insights_recurring_recovery.py`: synthetic real-entrypoint and
  worker-order tests, absent-arm/model rejection, exact-failure recovery,
  evidence tampering, quota/expiry, separate activation and future-failure cases.
  Existing runner/revision fixtures were updated for the new binding.

The original recurring store, runtime revision and all trial/staging runtime
sources remain byte-for-byte unchanged by this correction. The recovery layer
continues the original six-hour admission floor and 120-attempt/30-day grant.
The failed attempt counts against the quota; it is not refunded or retried.
A future approved attempt would be `000004`, chained to the **actual uncertain
terminal digest**. Any later incomplete/uncertain attempt still requires a new
administrator review; this is not general automatic recovery.

### New review record and activation boundary

During preparation, only these three new private files were added under
`C:\ProgramData\HomeEnergyReporterProduction`:

- `runtime-recovery-20261016-original-run.py`: pre-correction source backup,
  SHA-256 `d2f3ca8e54984f6a66bbbc57fc6485a6ad3988abe0c633d9fa27f33d37d7a37a`.
- `runtime-recovery-20261016-original-startup.py`: pre-correction logger backup,
  SHA-256 `5c463cffa847a3dc17a6e2717ff06e8ef2d3abfdd22b9aa384299a51ca454d79`.
- `runtime-recovery-20261016-review.json`: exact new bundle, original grant,
  previous repair, failed-artifact and original-log bindings. It records that
  the attempt remains consumed and requires separate activation.

The old approval and activation files remain present and unchanged. They do
not activate this new bundle. At the preparation boundary, the following new
activation file was absent:

`C:\ProgramData\HomeEnergyReporterProduction\runtime-recovery-20261016-activation.json`

The preparation `--check` confirmed `repair_active: false`, two completed
attempts, one reviewed failed attempt, three consumed attempts and 117 remaining.
The separately approved activation is recorded below. Manual report execution
requires its own owner approval and remains subject to the existing limits.

### Verified results and limits

- **253 targeted synthetic regression tests passed** across recurring,
  revision, recovery, production trial/MCP/launcher, private-review runner and
  schedule tests. These use synthetic responses/files, not live report calls.
- The actual `main --mcp-report` initialization and tool discovery succeed in
  the synthetic worker-before-arm sequence without writing evidence. Premature
  report calls remain rejected before transport. Before arm, the existing guard
  terminates the helper because even rejection auditing lacks an armed token;
  the test does not mistake that termination for a successful JSON-RPC response.
- Real `--check` and `--preflight` passed with the installed runtime and the
  recovered history. The no-model session blocks `turn/start`, uses the closed
  report adapter and reported no model, SSH connection or report calls.
- The real `--os-check` allowed only the intended synthetic read and explicitly
  denied the restricted/root reads, write and network probe. Host-side loopback
  positive controls succeeded; no unexpected canary write exists.
- All **576 pre-existing production/staging/Reporter evidence files**, including
  their bytes, metadata and ACLs, matched the pre-correction baseline after the
  checks. Private-key content is excluded; its metadata/ACL are checked. The
  three specifically approved new files above are excluded from that old-file
  comparison and checked separately.
- Exported production task XML is unchanged. Windows still reports enabled,
  ready, last result `1` for the noon failure and next trigger
  **17/10/2026 00:00:00 Athens**. Its result is not cleared by preparation.
- No manual model/report, server/SSH operation, authorization extension,
  schedule change, service/device restart, installation, download, Git commit
  or push was performed.

These preparation checks verified the startup fix and preserved recovery locally,
not a subsequent real publication. The recovery activation was still closed at
that stage; the later activation is recorded below. Other runtime/server/model
failures remain possible. Restore/review of the source backups is a separate
operation, not a reason to erase old receipts or bypass validation.

| Corrected component | SHA-256 |
| --- | --- |
| Runner | `8d546839085c0063ea8f1c589615f13fa76c534d524da77dc70646a4e00e99ab` |
| Startup logger | `d3e88224158935c38c4b23c3c1e7d8fe5ffe2d3a44ac850918fbe9bfe6220068` |
| Recovery layer | `4215432e8811150c850110bd1306271686f2f036a7d8c072f56a4082899f2b06` |
| Reviewed bundle | `f2e0ccb521779ed13940fe5384d8a72932e043782b813c7d9f0da32f371df8f2` |

## Corrected runner activated: 16/10/2026

The owner explicitly approved activating the corrected runner while preserving
the midnight/noon schedule and all existing evidence, without a manual report.
Only this new private record was added:

`C:\ProgramData\HomeEnergyReporterProduction\runtime-recovery-20261016-activation.json`

It binds the exact reviewed bundle and review record. Its SHA-256 is
`b5fda87b1b476119ecaeb9542971f0fb3ed372151e63a5e61ed9e90da944fbfd`.
The activation record and all private grants, logs, keys and report evidence
remain outside this repository; they must not be included in a commit.

- Post-activation `--check` and `--preflight` passed with `repair_active: true`,
  three consumed attempts and 117 remaining. No model turn or report call ran.
- `--os-check` again verified the allowed synthetic read and explicit denial of
  restricted/root reads, writes and network access, with loopback controls.
- All **579 files that existed before activation**, including their metadata
  and ACLs, were unchanged. Private keys were compared by metadata/ACL only.
  The one new activation record brings the preserved inventory to **580 files**.
- The exported task XML is unchanged. The task remains enabled and ready for
  **17/10/2026 00:00 Athens**, then the existing noon/midnight cadence. Its last
  result remains `1` from the noon failure; activation does not clear that result.
- Model/effort, saved Codex settings, authorization expiry/quota, source bytes,
  SSH/server policy and public report feed were not changed by activation.

The subsequent owner-authorized manual report request was checked read-only at
approximately 14:50 Athens. The runner's existing six-hour floor still applies
to the consumed noon attempt, including its pre-model failure. Admission is not
due until **18:00:05 Athens on 16/10/2026**. No manual launch or automatic catch-up
was scheduled, and no exception, extra attempt or model/report run was made.
An earlier run would require a separately reviewed exception; the publication
approval alone does not change the installed grant or its safety checks.

The runner correction, regression tests and this runbook form one focused Git
change. The exact runbook path has an LF rule in `.gitattributes`; existing
reporting-source LF rules and all reviewed runtime bytes remain unchanged.
