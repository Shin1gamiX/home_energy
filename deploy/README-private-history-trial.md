# One real-history review, private output only

This is a separate, administrator-approved trial. It does not activate recurring
reporting or public publication. Do not reuse the approval for another model run.
The existing synthetic launchers, accounts, fixtures and evidence are unchanged.

## Separation of responsibilities

1. `server/insights_private_review_capture.py` runs only from the administrator
   capture command. It uses the existing administrator SSH identity to execute
   fixed, hash-verified, root-owned analysis code read-only. The remote command
   reads the last completed 24 hours and the preceding seven 24-hour windows.
   Only one bounded aggregate is transferred; no raw history is downloaded.
2. `server/insights_private_review_run.py` starts an ephemeral, restricted model
   session after checking the pinned runtime, configuration, source and tool
   inventory. Capture is not a model tool and is not imported in the MCP process.
3. `server/insights_private_review_mcp.py` exposes only `prepare` and `publish`.
   Prepare reads the frozen aggregate. Despite its compatibility name, publish
   saves one immutable local private report. It has no server/network transport.
4. `server/insights_private_review_store.py` owns fixed `private-review-01-*`
   files in the existing protected Windows evidence directory. It cannot select
   arbitrary paths, delete or overwrite evidence, reset an approval, or modify
   the old `one-shot-*` files.

The namespace is `home_energy_private_review`. Data and output explicitly use
`scope: "private_review"` and `synthetic: false`; real readings are never disguised
as synthetic fixtures. The directory's historical staging name does not change
this provenance.

## Guards and limits

- Capture has its own permanent, exclusive receipt, consumed before connecting.
  Failure does not authorize another capture or an automatic retry.
- The frozen analysis is bounded to 32 KiB, its envelope to 64 KiB, and must be
  no more than 30 minutes old when the model starts. Missing readings stay missing.
- The model uses GPT-6.1 Sol, medium effort, with the previously reviewed named
  permission profile. Effective configuration must show agents disabled,
  notifications disabled, read-only/root-denied filesystem access and network
  disabled. Shell, file-edit, browser, app and collaboration tools are disabled.
- All project imports in the new runner execute verified source bytes rather
  than potentially stale Python bytecode caches. Existing caches are preserved.
- A permanent one-shot receipt is bound to the canonical snapshot hash and a
  random token whose hash, not plaintext, is stored. Arming follows runtime
  verification. One MCP process may claim it; one model turn may be sent.
- The receipt expires after 360 seconds; the turn is limited to 240 seconds. A
  Windows Job Object owns the worker/runtime descendants and kills that tree on
  exit or timeout. There is no resume/reset command or automatic model retry.
- The adapter permits one prepare and one save, plus at most one identical
  submission retry. Different content, IDs, metadata, paths or operations fail
  closed. A permanent terminal marker closes the trial afterward.
- Results require English, Greek and Russian summaries and notes. IDs and facts
  stay bound to the frozen analysis. Validation checks structure and limits, not
  the truth of arbitrary prose; administrator review is still needed for this
  trial's factual quality. No weather, occupancy, appliance or fault diagnosis
  may be inferred from the energy readings alone.

The model cannot read the private evidence directory directly. The administrator
and other programs running as that same Windows user are outside this sandbox
boundary. This is not a dedicated Windows account or a defence against a
compromised administrator session.

## Evidence

The fixed local prefix is
`C:\ProgramData\HomeEnergyReporterStaging\private-review-01-`.

- `capture.json`: permanent capture-attempt marker.
- `snapshot.json`: frozen real-data aggregate and canonical analysis ID.
- `receipt.json`, `armed.json`, `claim.json`, `terminal.json`: single-use lifecycle.
- `audit.jsonl`: bounded UTC timestamps and fixed event names, never credentials
  or report prose.
- `result.json`: the frozen preparation, translated submission and private ack.
- `diagnostic.json`: bounded runtime counts, final message and fixed failure code.

The result and diagnostic may contain private energy information. They are not
security-only logs and must not be copied to the public web root or Git.

## Operator entry points

The entry is `server/insights_private_review_run.py`, invoked with the reviewed
Python executable using `-I -B`. `--preflight` verifies the new runtime without
starting a model or creating a receipt. `--capture-once` and `--run-once` are only
for the individually approved one-time trial, never a scheduled action. Do not
repeat either after a failure without review and fresh authorization.

At this stage the Windows scheduled task remains disabled, the public feed is
unchanged, and no public reporting credential or deployment is added. A successful
private review is not authorization to enable either scheduling or publishing.

## Verification record

Local implementation checks and the restricted runtime preflight are complete.
The existing synthetic simulator verified rejection of all 18 collaboration
probes; a separate OS-profile probe denied root/canary reads, writes and loopback
network access. Those probes test the reused restrictions, not report accuracy.
The new namespace and honest real/private descriptions are independently checked
by the new runner's runtime preflight. The real-history trial result is recorded
below only after capture, model completion and preservation checks succeed.

### Completed 14/10/2026

- 131 new offline tests passed, including a poisoned-bytecode-cache regression.
  The existing suite ran 629 tests: 611 passed and 18 were skipped. No failed
  test remains after running from the correct directory.
- The final source-only launcher passed the new runtime preflight without a
  model call. The separate OS-profile probe denied read/write/network canaries;
  the existing fixed local simulator rejected all 18 collaboration attempts.
- Exactly one 12,249-byte real aggregate was captured. Exactly one model turn
  started and completed, with one prepare and one private save; no failure
  events or automatic retries occurred. The model was `gpt-6.1-sol`, medium,
  and reported 42,127 total tokens. The model phase lasted about 32 seconds.
- The immutable private result contains English, Greek and Russian content,
  a matching analysis ID, and `saved_private`. Its figures, caveats and
  translations were reviewed against the snapshot; no unsupported weather or
  device-fault cause was asserted. JSON validation alone is not that review.
- The terminal marker is present; the one-shot approval is consumed. Nine new
  private evidence files exist under the dedicated prefix. No report prose or
  raw history was copied into repository documentation.
- All 28 earlier `one-shot-*` files retain their original hashes and lengths.
  The task XML and disabled state, global Codex configuration, public report
  feed, Overview/History HTML and Nginx configuration are unchanged. Nginx,
  exporter and control-service PIDs/restart counters are also unchanged.
- No production deployment, public report, schedule activation, Git commit,
  push, or device restart was performed. Production report-only publishing
  remains a separate, unapproved phase.

Reviewed code hashes:

| Module | SHA-256 |
| --- | --- |
| `insights_private_review_run.py` | `af65c7429aea1e115b5a9aae0d3c6b86b966c1385ceb25925103edc0fcebe214` |
| `insights_private_review_store.py` | `cea669160f1a7366d809a7c7e44714be49da31222206d4407b8546ebb9eb80d7` |
| `insights_private_review_mcp.py` | `0a0ef1216f415c84888fc5ba50787293f4cb09294f2e8f452f03395106a091f4` |
| `insights_private_review_capture.py` | `856588502e25396a7bca35d1131c8a22557b2ecf7fed897f98af05fbb671c111` |

Private evidence SHA-256 (content stays outside the repository): snapshot
`42b72f63085b348f7b3a9251917881d9ad4c5c6d96a60346d8e404aa8eb673a1`;
result `dc6f29cc0b15bcbff44ff42ffd343baa42fa0b34dff90b9eb1ee92287e81c9fd`.
