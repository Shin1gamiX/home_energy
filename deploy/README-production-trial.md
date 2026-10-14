# Production single-report runner: prepared, not authorized

Verified on 14/10/2026, approximately 05:38-05:42 UTC / 08:38-08:42 Athens.
This is a separate preparation stage. The previous preflight launcher, staging
and private-model evidence have not been rewritten.

## Current state

The single-report execution supervisor and private audit storage are prepared.
**No public trial is authorized or enabled.** No authorization, consumed-run
receipt, model marker, submission or result has been created in production
storage. The server's root-owned publication gate remains false, and the
Windows `Home Energy Reporter - Dummy` task remains disabled.

No model turn, live analysis preparation, report publication, server mutation,
service/device restart, schedule change, Git commit or push was performed.
Actual server inspection used a separate read-only administrator SSH session;
the reporting launcher did not connect to SSH.

## Added files

- `server/insights_production_trial_run.py`: pinned bootstrap, exact runtime
  checks, one-turn supervisor and bounded worker-process ownership.
- `server/insights_production_trial_store.py`: fixed private filenames,
  administrator-grant validation, create-once markers, evidence and audit log.
- `server/insights_production_trial_session.py`: two report-only operations,
  fixed production SSH destination, durable submission and acknowledgement.
- `server/test_insights_production_trial.py`: disposable synthetic tests with
  mocked transport/model processes. They never request a paid model turn.

The only new file outside the repository is the harmless retained probe:

`C:\ProgramData\HomeEnergyReporterProduction\public-trial-01-storage-probe.json`

It contains only fixed synthetic status fields, inherits the verified private
directory DACL, and is not an authorization. A repeat storage check verifies it
without overwriting it. Private keys and earlier evidence were not changed.

## Execution contract for a later approved trial

1. A separately installed administrator grant must match the exact runner,
   imported-source, runtime, instruction and configuration bundle. It has an
   exact schema, expires within 15 minutes, permits one model turn and one
   report, and asserts that server activation was separately verified. This
   assertion is not a remote-state check or a cryptographic server attestation.
   This runner cannot create the grant or enable the remote gate.
2. Before spawning a worker, an exclusive durable receipt consumes the one
   trial slot. Partial failures also consume it. No reset/reuse command exists.
   All retained artifacts block another run, even an incomplete receipt.
3. The worker starts suspended and joins its owned Windows Job before execution.
   A second Job owns its runtime and MCP children. Cleanup attempts the Job,
   parent, reader and pipe independently; any failure cannot be called success.
   Unrelated processes are never cleanup targets.
4. The runtime must confirm the existing restricted production profile,
   ephemeral thread, read-only/no-network model sandbox, disabled delegation,
   empty notification commands, fixed provider/model and exact two-tool MCP
   inventory. Installed ChatGPT authentication is checked without requesting
   token refresh. Saved/global settings are never modified.
5. Only after those checks does a separate create-once armed marker permit a
   single fixed `turn/start`. Its durable model marker is written before sending
   the request. No per-turn model, permission, sandbox or other overrides are
   accepted; there is no second-turn, resume, fork or configuration-write route.
6. The report session must separately claim its one-use slot. It can prepare one
   frozen 24-hour analysis against the preceding seven days, then submit one
   English/Greek/Russian narrative for that exact analysis. Server code remains
   responsible for facts, severity and publication validation. No weather source
   has been added, and missing data must not be described as zero usage/outage.
7. The exact validated submission is saved before transmission. A matching,
   validated server acknowledgement is saved before reporting success. Repeating
   an identical call after confirmed success returns the saved acknowledgement,
   without another network send. An uncertain/rejected send ends the session:
   no automatic retry, replacement snapshot or second publication attempt.
8. The parent independently validates the saved analysis/submission/result chain
   and completion diagnostics. It closes owned children and writes an immutable
   terminal outcome. Losing an acknowledgement or failing cleanup means
   **uncertain**, even if the server may already have published. Inspect existing
   evidence and the server before any separately authorized recovery.

Limits: 45-second runtime preflight, 240-second model turn, 360-second outer
worker lifetime and receipt lifetime, 50-second SSH subprocess limit, bounded
protocol frames and transport output. The model has no destination/path/key
arguments; SSH identity, host key and binary/ACL checks are repeated before
each allowed send. Forwarding, passwords, agents, proxies, local commands,
certificates, multiplexing and TTY are disabled in this transport.

The [OpenAI app-server documentation](https://learn.chatgpt.com/docs/app-server)
informed the explicit separation between thread discovery and `turn/start`,
and requiring `turn/completed` with completed status. Runtime checks use the
already reviewed pinned installation, not an assumption that saved settings
alone are effective.

## Private records and access boundaries

All run files are fixed children of
`C:\ProgramData\HomeEnergyReporterProduction`, prefixed `public-trial-01-`.
Writes use exclusive opened handles and create-new semantics, except bounded
append-only audit events. The code checks the actual handle DACL, canonical
path, ownership assumptions, reparse flags and single-link requirement. It does
not accept model-selected paths. A changed grant or source bundle blocks use.

The future audit JSONL contains only UTC timestamps, enumerated event names,
method, attempt number and fixed rejection codes. It is limited to 96 events /
32 KiB. Raw transport errors, credentials, runtime events, model reasoning and
report prose are not placed in that log. Audit persistence failure fails closed.
Only a hash of the per-run token is saved; the token is scoped to the supervised
worker/MCP environment, not model instructions or output.

Separate private artifacts retain the validated analysis, intended report and
server acknowledgement for ambiguity review. These contain report data, not
credentials, and are never written into the public website directory. Nothing
has created these live artifacts yet.

This is a model/runtime and report-only-server boundary, **not isolation from
the same Windows user, local administrators, compromised trusted host software,
or a person editing the launcher itself**. Those principals can change local
files; hashes and grants do not establish a separate-account security boundary.
Stopping local processes also cannot undo a publication already accepted by
the server. Full production model behavior remains untested in this stage.

## Verification results

- 42 new offline tests passed, including missing/expired/malformed grants,
  source pins, consumed partial receipts, one-worker/one-turn/one-session fuses,
  audit failure, immutable submissions, no transport retry, restricted RPCs,
  bad acknowledgements, bounded cleanup and independent parent validation.
- Full insights suite: **1,060 tests; 1,041 passed, 19 skipped**. Skips are not
  passes. Provisioning/reload messages printed by tests came from mocked
  fixtures, not actual remote changes.
- Actual `--check` and `--preflight` passed with authorization/receipt/terminal
  absent. The effective profile, disabled capabilities, exact MCP schemas and
  ChatGPT account type passed. No `turn/start` or reporting SSH call was made.
- Actual closed-token MCP subprocess checks advertised the expected schemas,
  rejected both `prepare` and `publish`, and stopped protocol dispatch after
  rejection. Only the harmless storage probe existed before and after.
- Actual private storage check created/read back the fixed synthetic probe
  using opened-handle ACL/path validation. It created no grant or run receipt.
- Read-only server inspection verified 10 protected hashes unchanged, the
  root-owned disabled gate, root-owned report state, unchanged empty public feed,
  absent `reports.sqlite3`/`publisher.lock`, and absent
  `/opt/homeenergy-reports/activation.json`.
- Nginx, exporter and control services remained active with unchanged PIDs and
  restart counts. No service reload/restart or live-history mutation occurred.
- The Windows task is `Disabled`, `Enabled=False`. Global Codex configuration,
  the previous launcher and the original private snapshot/result hashes match
  the preceding stage. No app automation was modified.

The initial new-test failures were a synthetic clock-fixture mismatch; the
fixture now explicitly injects its clock into the existing validator. Production
freshness checks were not relaxed. No model-backed bypass test or public trial
was run. Previous OS-denial/private-model evidence remains separate and intact.

## Repeatable checks while disabled

From the repository, using the already installed Python:

```powershell
& 'C:\Users\anest\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -I -B server/insights_production_trial_run.py --check
& 'C:\Users\anest\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -I -B server/insights_production_trial_run.py --preflight
& 'C:\Users\anest\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -I -B server/insights_production_trial_run.py --storage-check
```

Checks refuse an authorization, receipt or terminal state where relevant rather
than silently treating an armed/consumed slot as an inactive preflight. Preserve
unexpected artifacts; do not reset the slot, repin changed inputs automatically,
or connect `--run-once` to a schedule. No execution/activation command is provided
here because that separate approval and server preparation have not occurred.

## Reviewed hashes

| Input | SHA-256 |
| --- | --- |
| Trial runner | `211ba1404f040516f874d5deb157a8c846d52356443be2aaa21d54a863eca54a` |
| Trial store | `bd7aea56314f24e2b2f5f694adb8e4a169e46e68fd072f361a37c7e93ddbe656` |
| Trial session | `bbc788a8fbc9630ad9478c4285757ebe92f64927b5c6f3c9b21a2b33619b1020` |
| Trial tests | `e880dbf40ed40c645985c675c9ab4d95ff3aec3ba98bde3444015875a6b1d3d8` |
| Exact trial bundle | `0d88fe6a16e2cdd6f7629fa33cc7526cb99752092d8b97c8343795b6508e19e6` |

Preserved input/evidence hashes are recorded in
`deploy/README-production-launcher.md` and verified by the existing pinned
loader. This new document does not replace that earlier evidence.

## Next approval boundary

Prepare/review a reversible, narrowly scoped server activation and one-use grant
procedure. Enabling publication, creating the grant, making one real model run,
and subsequently enabling a six-hour schedule are separate actions requiring
explicit authorization. The present preparation does none of them.
