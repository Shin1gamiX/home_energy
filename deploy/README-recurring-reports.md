# Recurring report runner: authorization and verification record

**Current status, 14/10/2026:** the corrected runner has a separately approved
30-day/120-attempt authorization, and its first supervised public report completed
successfully. The separately approved **Home Energy Reporter** Windows task is
now enabled for **00:00 and 12:00 Athens time**, starting **15/10/2026 at 00:00**.
See the dated activation and scheduling records at the end for current controls
and evidence. Earlier absent-authorization/disabled-schedule statements below
are historical; the original Dummy task still remains disabled.

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
