# Reversible publication activation: local preparation only

Prepared and checked on 14/10/2026. **Nothing in this stage was uploaded or
activated. Public publishing and scheduling remain disabled.**

This document adds a new stage rather than revising earlier evidence. The
existing preparation/access helpers, production launcher, one-report runner,
source pins, credentials and private-trial records remain unchanged.

## Files prepared

- `deploy/activate-report-production.py`: explicitly invoked Linux/root-only
  procedure, with read-only preflight, activation, rollback and gate-only stop.
- `server/test_insights_production_activation.py`: local synthetic tests; no real
  privileged operation, SSH call, model run or scheduler interaction.
- This runbook.

No package, dependency, download or global configuration change is required.
The helper must later be staged in its own private mode-0700 administrator
directory named `/home/shini/homeenergy-report-activation.<unique-id>`, containing
only the reviewed helper, mode 0600. That directory has **not** been created.

## Current barriers

1. `/opt/homeenergy-reports/production-gate.json` is root-owned, mode 0644, with
   `public_publishing_enabled: false`. Both report operations stop at that gate.
2. `/var/lib/homeenergy-reports` and its `public` directory remain root-owned,
   modes 0711/0755. The feed is the existing empty feed; no report database,
   publisher lock or activation intent exists.
3. The PC has no `public-trial-01-authorization.json` or run receipt. The only
   `public-trial-01-*` file is the previous harmless storage probe.
4. The Windows `Home Energy Reporter - Dummy` task remains disabled. No app
   automation or task configuration was changed.

The dedicated SSH account can authenticate to its already installed restricted
command, but authentication alone does not enable preparation/publication.

## Procedure modes

| Mode | Intended effect after separate approval |
| --- | --- |
| `--check` | Read-only verification against the original completed access/preparation evidence; no gate/storage change |
| `--activate --approve-public-publishing` | Create private rollback evidence and immutable intent, enable only report-directory ownership, verify, then open the gate last |
| `--rollback` | Close gate, drain the fixed reporting UID, restore root ownership while retaining all report bytes and evidence |
| `--close-gate` | Independent gate-only emergency stop; does not depend on old helper/evidence checks or revert ownership |

Every mode requires isolated Python (`sudo /usr/bin/python3 -I -B`), the exact
private release layout, and a root-owned module directory. An advisory lock on
that existing directory serializes these administrator operations without
creating a lock file. A concurrent procedure is refused, not overlapped or
killed. Gate-only stop cannot race another instance into reopening the gate.

Do not run enabling flags yet. A concrete checked server path and read-only
administrator command will be supplied only after staging approval.

## Activation ordering and limits

The helper loads the exact pinned source of the completed SSH-access helper,
which loads its already reviewed preparation/finalization chain. It never
executes their mutation entrypoints or accepts replacement source from the
caller. The exact original access baseline/completion receipt, locked password,
UID/GID, SSH restrictions, private-path denials, preserved file hashes and
service state must still verify. Legitimate drift also requires review; it is
not silently adopted as a new baseline.

Activation then:

1. Confirms the gate is closed, the storage directories are root-owned, the
   feed is still the original empty feed, and no activation/database/lock exists.
2. Creates a **new** root-owned mode-0700 backup under
   `/home/shini/homeenergy-report-activation-backup.<unique-id>` with a mode-0600
   baseline. Previous evidence is never rewritten. It repeats preflight before
   proceeding, rather than adopting changes seen between checks.
3. Exclusively creates root-owned mode-0600 `/opt/homeenergy-reports/activation.json`.
   This immutable intent records the backup, baseline digest and helper digest,
   and is held locked throughout the operation. A partial/failed intent consumes
   the activation slot; it is never removed automatically to permit a retry.
   A losing concurrent invocation cannot roll back another activation.
4. While the gate is still false, changes only the two recorded report-directory
   inodes to the existing reporter UID/GID (996/986), preserving modes 0711/0755.
   This uses checked file descriptors, not recursive ownership changes. Unexpected
   links, ACLs, replaced inodes or metadata stop the procedure.
5. Verifies that only report directories have the intended write access, while
   protected website/history/configuration/code/private paths remain restricted.
   It repeats the observed closed-gate rejection without entering the writer.
6. Atomically changes the exact root-owned gate to true as the **last enabling
   mutation**. A bounded readiness probe drops to the reporting UID and calls
   only the installed wrapper's environment verification and pinned-module
   loader. It never calls `main`, `dispatch`, `prepare`, `publish` or the analyzer.
   The state must remain empty: imports/checks cannot create a report database.
7. Writes a new private activation receipt. Failure after intent creation attempts
   rollback. A failed/uncertain rollback is explicitly not recorded as successful.

The helper changes no key, account/password/expiry, SSH policy, Nginx route,
website asset, history record, model setting or schedule. It reloads/restarts no
service or device. Opening the server gate enables the report-only endpoint;
it does **not** grant a local model run, enforce a server-side one-report quota,
or enable a six-hour schedule. The separately reviewed one-use launcher/grant
provides the controlled trial limit. Existing server publication limits remain.

## Rollback means stop and preserve, not erase

Rollback acquires the same locks, verifies the original activation intent and
baseline, and closes the gate **before** unrelated preserved-state audits.
It then waits, bounded to 55 seconds, for processes under the fixed reporter
UID to disappear. The installed forced command already has a 45-second timeout
and five-second kill grace. The helper never kills processes, changes account
expiry, or restarts SSH to drain them. A process-query error is not treated as
proof of absence; failure to drain leaves the gate closed and requires review.

After draining:

- Restore only the recorded report-directory inodes to root:root, same modes.
- Inventory the small, fixed first-trial state: SQLite database/lock/sidecars,
  `public/index.json`, and bounded normal publisher temporary-feed filenames.
  Unknown entries, symlinks, hardlinks, special files, unexpected ACLs or files
  above 32 MiB are retained and cause review, not followed or deleted.
- Save a new private inventory, then freeze those exact file handles to root.
  Keep the public index mode 0644; private database/lock/sidecars/temp files become
  mode 0600. Verify that all file hashes and inodes are unchanged.
- Verify effective reporter write denial and the actual closed-gate refusal.
  Save a new rollback-completion record only after all checks pass.

Previously published reports **remain publicly visible**. No older empty index
is copied over the current one; no database, journal, partial file, receipt or
backup is removed. A crash may leave a database commit and public feed out of
step, or a hot journal: retain them for separately approved investigation rather
than opening/repairing/deleting them automatically.

The intent also remains after successful rollback, blocking reactivation through
this first-use procedure. A later reactivation requires a new reviewed plan.
Repeated rollback can record additional evidence, but is not an automatic retry.

### Gate-only stop

`--close-gate` does not require old access-helper imports or prior receipts; it
validates the fixed root-owned gate and atomically closes it. It leaves all
report data, ownership and evidence intact. It is useful if unrelated source
drift prevents the full rollback verifier from loading.

It is **not** cancellation of an already accepted command. An in-flight report
can still finish; use the subsequent reviewed drain/full rollback to verify
quiescence. A competing administrator operation is refused by the lock. Never
assume the gate closed if the command failed or its result was lost.

SIGKILL, power loss or termination outside Python's exception handling cannot
guarantee automatic rollback. The immutable intent and baseline support manual
recovery. First establish whether the gate is closed; do not rerun activation,
delete the intent or replace evidence after an uncertain outcome.

## Verification performed in this preparation stage

- **53 new offline tests passed**, including gate schema, exclusive evidence,
  failed atomic replacement, consumed intent/concurrency, activation ordering,
  ownership failures, uncertain readiness, evidence-write failure, rollback
  preservation, bounded drain, malformed process results, read-only readiness,
  changed directory/file handles, hardlinks and hash/inode preservation.
- Full insights suite: **1,113 tests; 1,094 passed, 19 skipped**. Skips are not
  passes. Provisioning/reload output from the suite came from mocks only.
- File-byte/create-new tests used disposable local files. Linux privilege,
  ACL, flock, ownership and subprocess effects were synthetic/mocked on Windows.
  This does **not** certify a root-level server preflight, actual activation,
  actual rollback, or a production model run.
- Read-only server inspection at 06:26 UTC / 09:26 Athens confirmed 10 protected
  hashes, unchanged empty feed, root-owned disabled gate and report directories,
  absent activation/database/lock, no current reporter-UID processes, and unchanged
  Nginx/exporter/control PIDs and restart counts. The installed `ps` returned 1
  with empty output/no diagnostics for the absence case used by the drain check.
- The Windows task remains `Enabled=False`. Earlier launcher/store/session,
  access-helper, global Codex configuration and private-trial snapshot/result
  hashes were reconfirmed unchanged. No new local grant/evidence slot was used.
- No remote upload, root helper execution, model call, public report, scheduling
  change, service/device restart, Git commit or push was performed.

## Reviewed local hashes

| File | SHA-256 |
| --- | --- |
| Activation helper | `3ca28f1f47f0da2895a29781b29db97c9565f6900105d6adf8afda23f532420c` |
| New tests | `9b4acf85f811326c9253295260e1c7680d2042efb6ba0dd994687dbf6185bee3` |
| Preserved access-helper dependency | `850600758a3ef63c290fb42aea5360d6000257f5600294c9dacfb7ee2a536cef` |

These pins are review evidence, not a security boundary against the current
Windows user, the trusted server administrator, or someone who can replace
trusted code. The report-only runtime and forced command remain essential.

## Next approval boundary

1. Approve staging this exact helper into a new private server release directory.
   Verify its bytes and metadata after upload; preserve all previous releases.
2. Supply/run **only** its read-only `--check` under sudo and review the output.
   Publishing and scheduling remain disabled during this step.
3. Separately approve actual public-endpoint activation and review its rollback
   path. Do not create the PC grant or start a model as part of activation.
4. Separately approve and supervise one real public-report trial, then verify its
   report, translations, evidence and unchanged protected state.
5. Consider recurring scheduling only after that trial is reviewed. Do not wire
   the one-use trial launcher directly into the six-hour task.
