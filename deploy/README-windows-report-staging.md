# Windows reporting transport: staging preparation

## Current status — 14/10/2026

Staging preparation is approved. A dedicated key has been generated locally in
the private Windows material directory. Its Ed25519 host pin was obtained through
the existing, strictly verified administrator SSH connection. Private-key contents
were never read/displayed by the preparation code or uploaded. Only the public half
goes into the private server release. Initial activation stopped at an incorrect
sudo-listing exit-code assertion and rolled back. The administrator subsequently
ran the corrected recovery helper and supplied successful privileged account,
filesystem and effective SSH-policy verification. Recovery preserved the retained
UID and synthetic fixtures and reloaded only SSH. Independent client authentication
and denial checks then passed. One explicitly approved, deterministic private
prepare/publish/identical-retry test also passed; see the separate evidence below.

A separate local **preflight-only launcher candidate** has been prepared.
It advertises the staging schemas but unconditionally rejects reporting calls
before constructing any SSH transport. It has no model-generation mode. This
does not install or enable the reporting workflow; see the candidate section below.

The separately approved next step added a **one-use private evaluation wrapper**
and durable private audit sink. The first actual model turn completed without a
verified report. A separately approved second diagnostic turn also completed:
it attempted one MCP call, received a preparation rejection, and stopped. Neither
attempt created an MCP claim or admitted SSH transport request. Both one-use
receipts are consumed and terminal; neither may be reset or retried. The second
attempt retained its final response and counters in separate `one-shot-02-*`
files; all first-attempt evidence remains unchanged. The later, separately approved
settings review and notification hardening cleared the local configuration block:
the real launcher's no-model preflight passes with reporting closed. A separately
approved third model test has now completed and stopped at local metadata
validation, without a report. Its new `one-shot-03-*` evidence is retained and
terminal; both earlier sets remain unchanged. See the final evaluation section.

Secret-free rejection diagnostics are now implemented in the local candidate.
A separate no-model, direct-MCP fixture probe observed `protocol_metadata_shape`
for both fixed tool calls, before reaching its closed reporting gate. A subsequent
approved field-shape capture identified the extra `_meta.threadId` string beside
the supported `progressToken` integer. The approved narrow compatibility fix now
validates and discards that context field on tool calls only. A new no-model
direct-client check reaches the intentionally closed reporting gate for both
operations. This proves
a compatibility issue on that client path, not the exact cause of the earlier
model-driven failures. Unknown metadata remains rejected and no authorization
check was relaxed. The subsequent model-driven test still returned
`protocol_metadata_shape`; the direct-client correction did not establish
compatibility for that different call path. The approved local fixed-response
replay has now reproduced that rejection and identified five additional metadata
fields on the `functions.exec` path. The separately approved compatibility change
now bounds and discards those fields. Both the local fixed-response functions
path and the direct MCP path reach the intentionally closed reporting gate;
the actual no-model preflight also passes. A separately approved fourth model
test has now completed successfully: one private synthetic preparation and one
publication, with a matching staging acknowledgement. All four approval slots
are consumed and terminal. See the final sections for the precise evidence.

The existing `Home Energy Reporter - Dummy` task remains disabled; its launcher
and permissions are unchanged. The real Reporter's source-integrity pins now track
the reviewed compatibility and notification-hardening edits. A separate approved
review pinned the exact current global configuration without modifying that saved
file. Reporter runs explicitly disable inherited notification commands and reject
runtime drift. The latest approved model turn used the fresh fourth slot; no
automatic rerun or publication retry occurred. No public report, Git push, new Windows account or
live-data modification occurred. The private evaluation does not activate
recurring or public reporting.

The earlier native desktop automation did not apply the intended permissions.
The separate Windows launcher was tested with an explicit MXC profile, root-deny,
read-only model access and no model network. It now forces and verifies
`agents.enabled=false`; six collaboration operations were absent and 18 forced
calls rejected. That earlier forced-tool evidence belongs to the dummy launcher.
The new candidate has passed no-generation configuration/schema preflight, its
own separate OS-profile canary checks, and a fixed local runtime-tool evaluation.
The evidence is described separately below; none of these checks enables the
real transport.

The OpenAI app-server protocol used by that launcher is experimental and documented
as unsupported for production workloads. This remains a pinned, fail-closed
personal-use candidate, not a vendor-supported production runner. Do not silently
upgrade it or enable automation on the strength of application tests alone.

## Trust boundaries and implementation

```text
restricted model -> two typed MCP operations -> trusted Windows transport
                 -> dedicated staging SSH identity -> fixed staging publisher
                 -> private synthetic staging database/feed
```

- `server/insights_staging_mcp.py` exposes only `prepare()` and
  `publish(analysis_id, summary, notes)`. No resource/file/shell tools exist here.
  Its direct executable entrypoint deliberately exits without starting a server.
  It provides no credential or destination configuration loader. The separate
  preflight candidate constructs only a rejecting stub, not the real transport;
  a later administrator-reviewed integration must construct that transport.
- `server/insights_windows_transport.py` is a host-side library, not model code.
  Trusted deployment values are a literal IP, port, executable digest and verified
  known-hosts digest. Tool callers cannot select these. It uses only the installed
  `C:\Windows\System32\OpenSSH\ssh.exe`, fixed staging username, and fixed
  `C:\ProgramData\HomeEnergyReporterStaging\id_ed25519` / `known_hosts` paths.
  This directory now has a protected DACL granting only the current user, SYSTEM
  and Administrators access. The key files have explicit protected DACLs; the host
  pin inherits the restricted directory policy.
- `server/insights_staging_integration.py` supplies the prepared host-side
  connection factory. It loads only the fixed private binding, verifies its
  reviewed digest/schema, checks Windows ACLs through `insights_staging_acl.py`,
  and wraps the existing transport and stateful adapter. Tool requests cannot
  select the binding, host, port, executable, key or audit sink. Its direct
  executable entrypoint fails before project imports; it is not an activated
  reporting service. Existing launcher preflight modes do not import this module.
- No shell, default SSH configuration, agent, proxy, forwarding, multiplexing,
  password, prompt, local command, certificate or administrator-key fallback.
  Known-host verification is strict and Ed25519-pinned. Verify the host pin
  through a trusted administrator connection; never trust a new scan by itself.
  The private key contents are never read by the Python transport or returned to
  the model; SSH necessarily reads the dedicated key for authenticated connections.
- Input is bounded to 32,768 bytes; stdout to 65,536; stderr to 8,192. Concurrent
  pipe workers enforce one 50-second deadline including blocked stdin. The child
  runs hidden, without inherited token/proxy/agent variables. A Windows Job Object
  limits it to one active process and kills it on closure. Assignment happens
  immediately after creation, before request bytes: this is not an arbitrary
  executable sandbox and depends on the fixed, pinned SSH executable.
- `PROGRAMDATA=C:\ProgramData` is included in the otherwise minimal Windows
  environment: installed OpenSSH 9.5p2 failed before parsing without it. No ambient
  user environment is copied. The fixed command was successfully checked using
  `ssh -G` without connecting or authenticating.

The trusted transport runs under the existing Windows user outside the model's
sandbox. It is **not** independent OS-account isolation from other programs run by
that user. A hash or path check does not prove correct NTFS permissions. Before
integration, verify DACLs and that the actual model cannot read/modify the key,
code, config or logs; refuse to run with a different permission/tool inventory.
Do not expose Python/import/eval or generic network tooling to make this work.

## Request, response and retry rules

MCP uses bounded newline JSON-RPC and a fixed protocol/tool allowlist. Duplicate
JSON keys, invalid/non-finite values, oversized/deep requests, unsupported tools,
unexpected fields, missing translations and unknown finding IDs are rejected.
Initialization is required before tool calls; notifications cannot trigger calls.
The pinned runtime includes optional `_meta.progressToken` on `tools/list`.
Only this exact, bounded metadata field is accepted and discarded; arbitrary
metadata, extra method parameters and arguments remain rejected. Progress tokens
are not forwarded to the transport, reflected in errors or included in audit data.
The adapter handles at most 64 frames and four upstream calls per instance,
including failures. These local bounds do not replace server-global limits.

One successful prepare freezes the session's analysis; subsequent prepares return
that same snapshot. The adapter checks schema version 2, top-level keys, finding
IDs, byte size and the canonical analysis digest. This does not independently
prove the truth of evidence or validate every analysis fact. The SSH/server and
deterministic analyzer remain trusted.

Every reply must be exactly `scope: staging`, `synthetic: true`, and `result`.
Publication acknowledges only `staged` or `already_staged` with the submitted ID;
production-shaped replies are rejected. Scope labels are not an access-control
boundary: server UID/filesystem/forced-command restrictions remain mandatory.

Once a publication is sent, even if its acknowledgement is lost, only the identical
submission can be retried in that session. Changed content is rejected without
another transport call. No automatic retry loop exists. The server reuses the
existing publisher's expiry, idempotency, immutable archive, global cooldown,
daily cap, translations and text rules. Server-generated facts are not writable
through the narrative fields. Plain-text hallucinations remain possible.

Audit output contains only UTC timestamp, fixed event/method/outcome and frame
attempt number. No prose, raw arguments, evidence, key, path, SSH stderr or arbitrary
error string is logged. There is no caller IP on a local stdio channel. Future
server SSH/journal records supply the authenticated account/source IP. Logs must
remain outside the model-readable profile.

## Fixed staging server contract — recovered, private transport tested

`insights_staging_publish.py` accepts exact SSH commands `prepare` and `publish`
only, with no command-line path overrides or environment-selected destinations.
It requires Linux and the dedicated nonroot `homeenergy-reporter-staging` UID.

| Location | Required ownership/mode | Role |
| --- | --- | --- |
| `/opt/homeenergy-reports-staging` | root, 0755 | Fixed staging code directory |
| Four publisher/analyzer/i18n modules inside | root, 0644 | Never writable by staging account |
| `/var/lib/homeenergy-reports-staging` | staging UID, 0700 | Private SQLite and lock |
| Its `public` subdirectory | staging UID, 0700 | Private test feed despite legacy subdirectory name |
| `/var/lib/homeenergy-report-fixtures/history` | root, 0755 | Administrator-supplied synthetic history only |
| Daily JSON fixtures inside | root, 0644 | Read-only, bounded synthetic inputs |

All ancestor directories must be trusted and non-writable by the staging account;
the wrapper rejects links and unexpected paths/modes. Extended ACLs/groups/sudo
and actual sshd Match policy require a separate target-host audit. Synthetic
provenance depends on the administrator putting only synthetic fixtures there;
the label does not inspect where readings originated. No live history is used.

The staging store must remain inaccessible to Nginx and absent from public routes.
Do not copy test reports to the public feed. `reports-staging-sshd.conf.example`
is installed only by the explicitly invoked helper below. The old production isolation
checker is not a staging-account audit and must not be presented as one.

## Provisioning and rollback

`deploy/prepare-staging-key.ps1` refuses to overwrite an existing directory. It
creates the private directory with its DACL already supplied, generates an
unattended Ed25519 key with installed OpenSSH, then normalizes/checks file DACLs.
`-VerifyOnly` checks metadata/ACLs and the public fingerprint without making changes.
The key has no passphrase: controls are the Windows permissions, runtime sandbox
and server restrictions. The candidate's synthetic sandbox checks are recorded
below; they did not attempt to read this real key. Same-user Windows programs are
not isolated from the key by these permissions.

Windows' installed SSH binary is a Microsoft-signed, TrustedInstaller-owned
System32/WinSxS servicing hardlink. Only that fixed, hash-pinned executable permits
multiple links. The key and host pin still require exactly one link; canonical,
reparse-point, file-type and size checks remain mandatory for all three files.

`deploy/provision-report-staging.py` accepts an exact seven-file hash manifest in
a private release directory. `--check` is root-only and read-only; `--activate`
runs preflight before provisioning; `--verify` checks its private activation receipt.
The manifest-version-2 recovery release adds a fixed plan identifying the original
release, its SHA-256 manifest pin, root-owned rollback backup and retained UID/GID.
Run the helper with the isolated interpreter (`python3 -I -B`).

- Rejects existing staging resources and a changed global SSH baseline. Tests the
  candidate with real Include semantics in anonymous memory before any mutation,
  including unchanged effective administrator policy. No main sshd_config edit.
- Creates a dedicated locked-password, no-sudo server identity with root-owned
  code/home/fixtures and private staging-owned state. Eight days of generated
  synthetic fixtures are used, never copied live readings.
- Installs/audits the account-bound SSH include, reloads SSH, then authorizes the
  key **last**. The key also carries `restrict,command=...` independently.
- Checks effective access (including ACLs): no staging writes to production code,
  configuration or reports, and no Nginx-worker access to private staging state.
  Access probes inspect metadata, not credential contents.
- Preserves public/static file hashes, application service PIDs and administrator
  SSH policy. Live telemetry is allowed to continue normally.
- On failure revokes its matching key first, expires its created account, removes
  only its matching include, then syntax-checks/reloads SSH. Created private
  resources remain for inspection; a blind repeat is refused. No recursive deletes.

Keep the administrator connection open during activation. Fresh administrator and
restricted-key connections, denial tests, and actual model-boundary tests remain
required afterward. A dedicated Linux UID plus a forced command is not a complete
filesystem sandbox against publisher compromise: ordinary OS-readable files and
temporary-directory rights still exist. This is not a universal escape proof.

### Recovery after the sudo-check failure

Root diagnostics confirmed sudo 1.9.15p5 returned exit **0**, the exact target-user
"not allowed" message on stdout and empty stderr. That is a successful listing
of no permissions. The previous exit-1 assertion was a validation bug, not a grant
of sudo rights. The corrected check accepts only this complete observed result;
password prompts, policy failures, extra output or granted-command lists fail.

Filesystem checks now launch a fixed isolated Python probe with kernel UID/GID/
supplementary groups dropped to the intended identity. Every probe must complete
successfully and return exact identity fields and a Boolean access result. A failed
launch is never counted as denial. This avoids PAM's expired-account rejection
and permits genuine access checks while the staging account remains expired.
Positive write checks on its private state supplement the protected-path denials.

`--check-recovery` performs a read-only preflight. `--recover` performs that same
preflight before changes; the original `--activate` still refuses existing state.
Recovery requires:

- Root-owned rollback evidence and the exact original manifest pin; unchanged
  publisher modules, SSH template and public key in old/new/installed material.
- The same locked-password UID/GID, no supplementary groups or running processes,
  and the exact rollback expiry. Key, managed include and activation receipt absent.
- Empty home and private state, only the four installed modules, and a byte-exact
  match to all eight days of deterministically generated synthetic fixtures. The
  original helper did not persist fixture hashes, so labels alone are not trusted.
- Unchanged original public/static/configuration hashes, application PIDs and
  administrator SSH policy. Any mismatch stops recovery without changes.

Only then is the restrictive include installed and audited while the account is
still expired. SSH is reloaded; account expiry is restored without unlocking the
password; the key is authorized last. A subsequent failure revokes the matching key
first and re-expires the account, retaining data for inspection. Original release,
rollback backup, UID and fixture contents are never deleted or replaced. This
does not start the schedule, run a model, publish a report or restart a device.

## Verified locally

With the existing analyzer, publisher, broker and deployment tests: **221 tests
run, 203 passed, 18 existing platform/privilege-specific skips**. Provisioning tests
use mocks and temporary synthetic files, not privileged Linux operations. No new
staging test is skipped. Skipped Linux checks are not claimed as verified.

Checks include exact tools/commands, malformed frames, sanitized errors, valid
string request IDs on failures, canonical digest/scope rejection, translation
validation, unknown findings, pending identical retries, call budgets and fixed
SSH option parsing. Real local child processes tested bounded stdout/stderr,
nonzero exit, blocked stdin, deadlines, reaping/closed pipes and Windows helper
process rejection. A protocol -> child process -> real analyzer/SQLite/feed test
prepared, staged and identically retried one report; only one record existed,
all three translations survived, and unrelated sentinel/history stayed unchanged.

Actual SSH executable, dedicated-key metadata, host pin and NTFS permissions
passed local checks. Administrator SSH connectivity and restricted-key client
checks are now verified separately below. Candidate access-denial checks with
synthetic canaries are also recorded below; real-transport model evaluation remains
pending. Temporary unit-test fixtures are cleaned up; provisioned
credentials, server fixtures and the approved private staging report are retained.

Run from `server/`, using the already available Python (no installation):

```text
python -B -m unittest test_insights_staging_transport test_insights_staging_publish
python -B -m unittest test_insights_staging_recovery test_insights_staging_provision test_insights_staging_transport test_insights_staging_publish test_insights_broker test_insights_isolation test_insights_publish test_insights_analysis test_insights_deploy
```

## Actual server/client verification — 13/10/2026

These checks are distinct from the mocked/local tests above and from a model run.
The successful root recovery output was supplied by the administrator. Subsequent
client checks used the dedicated staging key, verified host pin and fixed Windows
SSH executable; fresh administrator connections were also verified.

| Check | Observed result |
| --- | --- |
| Unknown command, empty shell request, `id`, arguments and command separator | Public-key authentication succeeded; exit 1, empty stdout and the exact generic wrapper rejection |
| Requested PTY | Explicit terminal-allocation refusal after authentication |
| Direct TCP forwarding | Explicit administratively-prohibited refusal |
| Remote TCP forwarding | Explicit remote-forwarding refusal |
| SFTP subsystem, including a protocol initialization packet | Authentication succeeded, then exit 1 with no protocol reply or file-transfer session; wrapper stderr was not observed for this request |

PTY/forwarding probes removed the relevant client-side disabling option from a
copy of the fixed argument list, so they tested the server rather than merely the
client defaults. Agent/X11 forwarding were not actively exercised; their configured
denial must not be described as an observed runtime-channel test.

### Approved private round trip

At **20:46:38–20:46:42 UTC**, a transient administrator-controlled, in-memory
JSON-RPC harness initialized the adapter, checked the exact two-tool inventory,
and exercised the real `StagingReports` -> `StagingSSH` -> installed publisher
path. No harness file, model invocation or launcher integration was created.

- Exactly three upstream calls occurred: `prepare`, `publish`, `publish`.
- Preparation returned schema 2, a complete-minute 24-hour window, a preceding
  seven-day baseline and a verified canonical analysis digest. Every response
  retained the exact staging scope and synthetic-data marker.
- The fixed summary contained English, Greek and Russian text stating that this
  was a synthetic transport test, not a live energy report. Notes were empty.
- The first submission returned `staged`; the byte-identical retry returned
  `already_staged`, both for analysis ID
  `269db13528446c7f76d87f3e5eca4972c7fa492f3ae4c5b57919009ddb4955aa`.
  The adapter made both publication calls; it did not cache an acknowledgement.
- Matching JSON-RPC IDs and three accepted tool-call audit events were checked.
  Audit fields contained timestamps, method, attempt number and fixed outcomes,
  not report prose or credentials. This harness kept its detailed results in
  memory; it did not install persistent audit logging or expose a new endpoint.

The snapshot's 97.36% coverage and older `data_through` came from the retained,
static synthetic fixtures. They are not live-system observations. No fixture was
refreshed to conceal that gap.

Before/after checks found identical content hashes for **23 readable protected
files**, including the public report feed, plus all **four installed staging
modules** and **nine synthetic history files**. Nginx, exporter and control-service
PIDs, active states and restart counters were unchanged. The initial preflight
stopped before any reporting request when the full root-level snapshot helper
could not read `/etc/ssh/sshd_config.d/50-cloud-init.conf`. Its permissions were
not changed: that one file was explicitly covered by unchanged ownership, mode,
size, timestamps and inode only, **not a content-hash check** in this client test.

The server acknowledged the identical retry as already staged. Private database
row counts, saved translations and private-feed byte stability were **not
independently inspected** in this remote test because the administrator connection
was unprivileged. The local temporary SQLite/feed tests above cover those
properties independently; do not conflate that evidence with a privileged server
inspection or a universal proof of isolation.

The private staging snapshot/database/lock/feed created by the approved test are
retained. The Windows task remains disabled; global and Reporter configuration
hashes are unchanged. No public report, Git push, model run, service/device restart,
permission change or live-history modification was requested by the test.

## Preflight-only launcher candidate — 14/10/2026

Approved scope: prepare the restricted launcher integration without enabling the
schedule, starting a model turn or submitting another report. The original dummy
workspace, its launcher/project configuration and the global Codex configuration
are unchanged. The existing Windows task remains **Disabled**.

Files:

- `server/insights_staging_launcher.py`: independent supervisor and blocked stdio
  entrypoint. Paths and reviewed input hashes are pinned for this installation.
- `deploy/windows-report-staging-candidate/`: prepared instructions plus an allowed
  and denied synthetic canary; no keys, reports or live history.
- `server/test_insights_staging_launcher.py`: offline guard/cleanup regressions.

The candidate derives configuration in memory, not by changing saved settings.
It selects a named root-deny/read-only MXC permission profile, disables model
network access, collaboration, shell, apps and other configured capabilities,
and checks the effective runtime configuration before and after thread creation.
Code, both real material/config locations and the old dummy workspace are explicit
denied paths. These are configuration assertions, not new observed denial probes.

Only `initialize`, `config/read`, ephemeral `thread/start`,
`mcpServerStatus/list` and the initialization notification can be sent by this
supervisor. There is no generation command or approval-response path. The only
enabled MCP server advertises exactly `prepare` and `publish`, with their full
reviewed input schemas. Both always reject before a transport is constructed.
The disabled `node_repl` server has no tools. This MCP inventory is **not** a claim
that all model-visible built-in utility tools have been enumerated.

The process starts suspended, is assigned to a private kill-on-close Windows Job
Object, and only then resumes. All descendants belong to that group. Bounded,
unbuffered output parsing and cleanup also cover constructor failure, malformed
output and a parent exiting before its children. This is process ownership and
cleanup, **not** an additional model security sandbox.

Verified during this preparation:

- **271 local tests: 253 passed, 18 platform/privilege-specific skips.** Includes
  46 candidate tests and 30 transport/adapter tests. Mock provisioning output is
  not a real server operation. No skipped Linux check is claimed as verified.
- Actual pinned `codex-cli 0.162.0-alpha.17.2` static and no-generation preflight
  passed. The ephemeral thread reported the intended named profile, read-only
  sandbox, network disabled, approval policy `never`, empty additional workspace
  roots, collaboration disabled and the exact two MCP schemas.
- The original startup rejection was specifically `tools/list` carrying
  `_meta.progressToken`, not a protocol-version mismatch. The narrow metadata
  compatibility fix was tested against malformed tokens, extra fields, initialization
  and tool allowlists, unchanged upstream payloads and identical publication retries.
- An actual local Windows smoke check launched a harmless fixed parent/child pair.
  After the parent exited, the child's retained process handle was still active;
  closing the owned job terminated that child within the five-second bound.
  No network, model, SSH or file operations were performed by that fixture.
- Input hashes and final task state confirmed the old launcher/project settings,
  global settings and disabled schedule were preserved.

**Instruction boundary:** the first preflight reported only the global Codex
`AGENTS.md` as an automatically discovered instruction source. The later approved
local-test change reads and rechecks the candidate's pinned bytes, then supplies
that complete text as explicit `developer_instructions`. Effective configuration
must contain the exact same text. The global instruction file remains pinned and
the exact discovered source list is still required; no global file is edited.
Configuration acceptance is not proof of delivery to a model request: the local
runtime probe separately checks the complete text in an outgoing developer-role
message. The ordinary preflight says `candidate_instructions_configured: true`
and still says `isolation_approved: false`. It cannot start a generation turn.

The blocked preflight discards adapter audit events rather than installing a new
persistent audit log. No SSH credential is loaded, no reporting request reaches
the server, and no public/private report is created. Runtime housekeeping may use
the existing Codex state directory; this is not a promise of zero runtime disk I/O.

From the repository root, using the already installed isolated Python:

```powershell
& 'C:\Users\anest\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -I -B server/insights_staging_launcher.py --check
& 'C:\Users\anest\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -I -B server/insights_staging_launcher.py --preflight
```

No-argument execution and `--run` are rejected. A changed pinned input requires
administrator review, not an automatic digest refresh. No scheduled action points
to this candidate, and the current approval does not authorize one.

## Approved local synthetic isolation checks — 14/10/2026

These checks are independent of the earlier private-server round trip. No SSH
connection, model-service generation, public/private report submission, schedule
change, global configuration change or production deployment is authorized here.
Temporary unit-test fixtures and fixed synthetic canaries contain no credentials.

### Separate OS-profile probe

`server/insights_staging_sandbox_probe.py` uses the pinned runtime's sandbox
command and the same named MXC profile. The host invokes a fixed PowerShell
canary probe; model shell tools remain disabled. This is intentionally separate
from proving that a model-visible tool is unavailable.

The actual Windows run passed all of the following:

- The allowed canary was readable and matched its expected synthetic content.
- The explicitly denied canary and another pinned canary outside the readable
  workspace both raised an access-denied exception. Neither contained secrets.
- A `CreateNew` attempt for a fixed, initially absent synthetic file was denied;
  the target remained absent. Unexpected output would be preserved, not deleted.
- A temporary loopback listener was reachable from the host before and after
  the sandbox attempt. The sandbox received Windows socket error 10013
  (`AccessDenied`), not a timeout or generic connection failure.

No real key, configuration, source or log contents were used as denial targets.
Their configured deny rules are checked separately; representative canaries do
not prove every possible credential-access path or a universal sandbox escape
guarantee. The trusted host supervisor still runs as the existing Windows user.

From the repository root, with the already installed isolated Python:

```powershell
& 'C:\Users\anest\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -I -B server/insights_staging_sandbox_probe.py --with-loopback
```

### Fixed local runtime-tool probe

`server/insights_staging_runtime_probe.py` is a separate test harness, not a
reporting execution mode in the launcher. A temporary loopback responder supplies
only predefined synthetic Responses events. The provider is explicitly local,
has no authentication, retries or WebSockets, and has standalone web search
disabled. Provider configuration is checked before the turn starts; HTTP
authentication/cookie headers, unexpected routes and unreviewed tools are refused.
The configured provider route is not a claim of OS-wide egress monitoring of the
trusted supervisor.

The fixed plan enumerates nested tools, probes all six collaboration operations
in three calling formats, tries unavailable shell/read/write functions, and calls
the two blocked reporting operations. It uses synthetic targets only. Each exact
call ID and argument is paired with its runtime output; the next provider request
must also contain that output before another test call is issued. Unexpected
children, writes, provider/tool changes or incomplete evidence are failures.
The owned process tree and local responder are closed on success or failure.

Full reporting instructions must occur in a developer-role message, not merely
in user text, tool output or configuration. A rejected file-tool call is recorded
as tool unavailability, never as OS access-denial evidence. Blocked report results
are also backed by the pinned preflight implementation, which rejects before
constructing an SSH transport; error wording alone does not establish this.

Initial local-tool runs stopped safely on an explicit disabled provider default,
the model-catalog route and reviewed runtime metadata header names. The final
handler accepts only `GET /v1/models?client_version=0.162.0` (at most twice) and
bounded `POST /v1/responses` requests. Model-catalog replies contain only an empty
synthetic list. Authentication headers, arbitrary query strings and unreviewed
headers remain rejected. Compatibility handling does not relax sandbox permissions
or enable an external model.

The actual pinned-runtime run passed:

| Evidence | Observed result |
| --- | --- |
| Candidate instructions | Complete pinned text present in the outgoing developer message, SHA-256 `68a356705be884ddd8f7fdabbe6af2b214d4113789ba718065cc9b7bca63a5b6` |
| Collaboration | 18 matched rejections: six operations in namespaced, qualified and bare forms |
| Shell/read/write functions | Three matched unavailable-function errors; no synthetic output file appeared |
| Reporting operations | Both `prepare` and `publish` rejected by the blocked preflight implementation |
| Threads | Only the parent ephemeral thread remained loaded; no child-thread event |
| Local responder | One model-catalog request; all 24 fixed tool calls had paired results; fixed final turn completed |

The observed top-level tool inventory was exactly `functions.exec`,
`functions.wait`, `functions.request_user_input`,
`functions.request_user_input_async` and `clock.sleep`. The nested inventory was
exactly `clock__curr_time`, `list_mcp_resource_templates`, `list_mcp_resources`,
`read_mcp_resource` and the two staging report operations. No shell, filesystem,
browser, app-control or collaboration tool was present. Only the staging MCP
server is enabled and its schemas are checked independently by the launcher.

The probe reports `local_synthetic_probe_passed: true`, but deliberately retains
`isolation_approved: false` and `report_transport_enabled: false`. Its
`os_filesystem_denial_proven: false` refers to this tool-level test alone; the
separate OS-profile probe above supplies the actual canary access-denial results.
These are bounded tests of a pinned personal-use candidate, not certification of
every possible escape, same-user compromise or future runtime version.

```powershell
& 'C:\Users\anest\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -I -B server/insights_staging_runtime_probe.py
```

Offline regression checks across the launcher, both probes, staging transport,
recovery/provisioning, analyzer, publisher, broker and deployment suites ran
**310 tests: 292 passed and 18 existing platform/privilege-specific tests skipped**.
All 47 launcher, 23 OS-probe and 15 runtime-probe tests passed. Mock provisioning
messages in the test output do not indicate real server operations. Skipped Linux
checks are not claimed as verified.

The final no-generation preflight also passed. The original Windows task remained
disabled and its exported definition had the identical before/after SHA-256
`D848586A7C16B2B4FF92980126E09896216FC823AB5ECD83E8BA9F3664B3CDA4`.
The pinned dummy launcher/configuration and global configuration checks still
passed. No scheduling, SSH, server, Git or reporting activation was performed.

## Prepared private-staging connection — 14/10/2026

Approved scope: prepare the integration and test it locally, without a model run,
SSH connection, server report submission or schedule activation. This step changed
only local integration/check/test/documentation files and added one local binding:

`C:\ProgramData\HomeEnergyReporterStaging\integration-binding.json`

That file is outside the repository, inherits the existing private directory's
restricted DACL, and contains only schema version, staging/synthetic scope,
literal host/port and the previously retained host-pin/executable fingerprints.
It contains no key, password, token or administrator credential. Its exact SHA-256
is reviewed and pinned in the integration module; the loader never learns or
updates pins from current files. Missing/changed inputs require review. Only the
public host pin and executable were hashed; private-key contents were not read.

### Separate checks and execution boundary

The launcher adds only `--integration-check`. After validating its existing inputs,
it verifies the new modules' reviewed hashes/import origins and invokes the local
binding/material checker. That checker performs no model or SSH invocation and
does not construct `StagingSSH` or `StagingSession`.

`--check`, `--preflight` and `--mcp-preflight` retain their previous blocked
behavior. There is no `--run`, `--run-staging` or `--mcp-staging` command. The
`serve_staging` function is a dormant host-side library hook for a future reviewed
execution wrapper, not a tool callable by the current candidate.

The ACL guard uses the fixed, hash-pinned Windows PowerShell executable with no
profile, no interactive input, a minimal system-only environment and a ten-second
process timeout. Its fixed script reads security descriptors for only the material
directory, private key, host pin and binding. It emits bounded anonymous ACL
metadata, not credential contents or raw identities. Python requires the current
owner, a non-null DACL, and exactly three unique full-control allow entries for
the current user, SYSTEM and Administrators. It also checks the raw descriptor
has exactly three ordinary, non-callback allow ACEs with no opaque data or
unreviewed flags, rather than trusting only the simplified .NET rule projection.
The root and private-key DACLs must
be protected; host-pin/binding inheritance is accepted only from that verified
private root with the reviewed ACE flags. Reparse paths are rejected. The existing
transport also rechecks canonical paths, sizes, hardlinks and material hashes.

### Prepared session behavior

- One stateful adapter instance retains the frozen analysis, four-attempt upstream
  budget and byte-identical publication-retry restriction. There is no automatic
  whole-session retry. Calls are serialized, and closed sessions reject even
  cached preparation results.
- Each upstream request rechecks the binding, ACLs and material pins before
  constructing the fixed staging SSH client. Settings never come from tool
  arguments or inherited environment variables.
- A secret-free audit intent must be written and flushed before any upstream
  request. Allowed fields are UTC timestamp, fixed event, method, outcome and
  attempt number. No prose, response body, path, destination, identity or SSH
  diagnostic is recorded. The supplied sink has byte/event limits; a failed
  write/flush permanently blocks further work in that session.
- A `returned` transport event does not claim publication succeeded. The adapter
  still checks staging/synthetic scope, analysis digest and acknowledgement. A
  logging or acknowledgement failure after transmission can mean the server
  already accepted the report: never infer that a fresh submission is safe.
- The library's 240-second session window and per-attempt reserves govern
  admission and reject observed overruns. They are **not a hard wall-clock
  watchdog** for file reads, logging or idle stdio. Existing no-generation
  preflight remains at 45 seconds and is not silently extended.

A future execution wrapper must enforce the complete model/process lifetime and
provide a fixed model-denied private persistent audit sink with checked ACLs and
bounded writes. `AuditLog` deliberately does not create that file; an arbitrary
host stream and `flush()` alone do not prove private, durable logging. No audit
file, scheduled action or active stdio reporting service was installed here.

### Observed checks in this step

The actual local `--integration-check` passed private-binding hash/schema,
Windows ACL and SSH executable/host-pin checks, with `ssh_connected: false`,
`model_started: false` and `report_calls_enabled: false`. The existing actual
no-generation runtime preflight also passed with unchanged restricted permissions,
disabled collaboration and the exact two blocked tool schemas. These checks do
not repeat or replace the previously recorded server authentication tests.

The combined offline suite ran **379 tests: 361 passed, 18 existing
platform/privilege-specific skips**. All 24 new ACL and 41 new integration tests,
plus 51 launcher tests, passed. They cover binding/schema drift, ACL rejection
before reads, zero SSH construction in check modes, repeated local validation,
cached-analysis lifecycle, failed/identical retries, expiry and audit failures.
The MCP round trip used only in-memory mocked SSH responses; it was not a new
server publication. The Windows task remained disabled with the identical
exported-definition digest recorded above. No Git push or production change
was performed.

```powershell
& 'C:\Users\anest\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -I -B server/insights_staging_launcher.py --integration-check
```

The model sandbox, host-side MCP process and SSH account are separate boundaries.
Windows programs running as the same user remain outside this model-sandbox
isolation guarantee. See the [OpenAI permission documentation](https://learn.chatgpt.com/docs/permissions)
for the distinction between sandboxed commands and MCP/service transports.

## Approval gates after the one-shot evaluation

1. Private staging provisioning and key generation are approved. No reuse of the
   administrator key as a reporting identity, and no new Windows account required.
2. Recovery and the bounded client/transport checks above have completed. An
   independent inspection of private server storage requires a separate privileged
   read-only check; do not rerun recovery or publish another report for that purpose.
3. The administrator approved the bounded one-shot wrapper, private audit sink and
   one staging model evaluation. That evaluation has now been consumed without a
   verified report. The attempted run's private evidence must be retained.
4. The administrator subsequently approved one additional diagnostic test,
   preserving the first attempt and keeping public publishing/scheduling off.
   This second model turn is now consumed too. Do not clear either receipt,
   rerun a model or start a fresh reporting session automatically. Any further
   model test requires new explicit approval. Public publishing and the six-hour
   schedule still require later approval; neither starts after a staging test.

## One-shot evaluation — 14/10/2026

### Implementation and verified local gates

`server/insights_staging_run.py` is separate from the old preflight launcher.
Its `--preflight` and fixed-local `--probe` modes never arm reporting or request
model generation. The old launcher's four modes are unchanged. `--run-once`
owns a worker in a Windows Job Object, with a 360-second outer deadline that
also covers worker file I/O and MCP/SSH descendants. The runtime retains a
45-second preflight budget and a 240-second model-turn budget. Only one exact
`turn/start` payload is allowed, with no permission/model overrides, resumption
or automatic fresh-session retry. A failed/partial send consumes that instance.

Before arming, the worker verifies the pinned installation, restricted configuration,
active thread profile, disabled collaboration, exact reporting schemas, and existing
ChatGPT sign-in. It does not use a Platform API key or print account identifiers.
Permissions remain root-deny/read-only/no model network. The two MCP operations
advertise while closed, but construct no transport until a matching durable gate
and exclusive MCP claim pass. The one-use token is supplied only to the trusted
MCP process, not in the prompt or inherited model/shell environment. Its digest,
not its value, is retained in the private receipt.

`server/insights_staging_run_audit.py` creates fixed files only below the already
private `C:\ProgramData\HomeEnergyReporterStaging` directory. Native handle checks
verify canonical paths, no reparse points, one hardlink, current owner and the
exact inherited current-user/SYSTEM/Administrators DACL. New files use exclusive
creation with no sharing or truncation; logs are bounded and durably flushed.
The receipt, gate, session claim and terminal markers are never automatically
removed. This is model-sandbox confinement, not isolation from another program
running as the same Windows user. The operator-reviewed entrypoint remains a
trust anchor; embedding its own self-hash would not create an independent one.

The secret-free security logs contain only fixed events, UTC timestamps,
method/outcome and attempt counts. A separate private result artifact, if created,
contains the synthetic analysis, submitted translations and validated private
acknowledgement. It is not a public file and is not an audit-log field.

Actual no-generation checks passed for the changed MCP command:

- Native private-storage canary creation, handle/DACL verification, durable write
  and read-back. The retained file is `one-shot-storage-check.json`; it is not an
  approval receipt and contains no credential.
- Effective config, active profile, existing ChatGPT authentication and exact
  tool schemas; reporting gate stayed closed.
- Fixed local provider verified the complete outgoing developer instructions,
  six nested tools, eighteen collaboration rejections, three absent dangerous
  tools, two closed-gate report rejections and only the parent thread loaded.
  This called no external model service.
- Separate Windows profile canaries verified allowed read, denied explicit/root
  reads, denied file creation, and network access denied with working host-side
  loopback checks before and after. Actual private key bytes were not probed.

### Actual model evaluation and failure boundary

Exactly one real model turn used `gpt-6.1-sol`, effort `medium`, on the existing
ChatGPT/Codex sign-in. The private supervisor audit recorded:

| UTC, 13/10/2026 | Event |
| --- | --- |
| 22:14:02.828975 | Approval reserved |
| 22:14:04.388315 | Runtime verified |
| 22:14:04.399829 | Model turn start recorded |
| 22:14:11.889620 | Model turn completed |
| 22:14:11.906648 | Post-run verification failed |

These were 01:14 on 14/10/2026 in Athens. The original implementation labeled
the last event `verification_failed` after closing the runtime; the retained log
has not been rewritten. The receipt and terminal marker remain present, the
MCP audit is zero bytes, and no MCP claim or private result exists. Consequently
no request entered this wrapper's admitted SSH transport and this attempt did
not publish a staging report. The reported `private_audit_open_failed` occurred
while opening the absent result; it does **not** identify the earlier cause.

Absence of a claim does not establish zero attempted tool calls. The model may
have finished without attempting a tool, tool invocation may have failed before
MCP, or gate validation may have rejected a request before claiming the session.
The ephemeral model response was not retained by that version, so these cases
cannot honestly be distinguished and its token usage cannot be recovered from
the retained fixed-field log. No second model run was made.

### Diagnostic correction, locally tested only

The wrapper now retains a separate bounded private diagnostic before checking
the result: model-started/completed flags, unique observed tool-call/MCP-item
counts, token usage when provided, and completed final-message text only. It
excludes reasoning, tool arguments, raw tool output, configuration and credentials.
The artifact is exclusive, cannot overwrite an earlier diagnostic, and does not
re-arm or clear the receipt. It can never be reconstructed retroactively for the
already-finished ephemeral turn. No diagnostic artifact was fabricated for it.

Success now checks accepted prepare/publication audit evidence before reading the
result, yielding a clear evidence failure for an empty audit. Generation state
is retained independently of the closed runtime object, preventing the misleading
failure classification. Primary errors survive a failed fallback diagnostic write.
No-generation preflight and the fixed-local boundary probe passed again after
this correction. A new real model test has not been performed.

The final combined offline regression run completed **492 tests: 474 passed and
18 existing platform/privilege-specific tests skipped**. This includes 71 wrapper
tests and 42 private-audit tests. Provisioning/reload messages in mocked test
output were not real server actions. These tests and the no-generation probes
do not establish successful model-to-private-report operation; that remains
unverified after the consumed attempt.

### Unchanged external state

Before/after SSH read-only checks found identical SHA-256 values for the public
report feed, Overview, History and Nginx site configuration. Nginx, the exporter
(`homeenergy.service`) and the control service remained active with identical
process IDs and restart counters. No service reload/restart or device operation
was requested. No live-history or public-report write was made.

The Windows task remains disabled with exported-definition SHA-256
`D848586A7C16B2B4FF92980126E09896216FC823AB5ECD83E8BA9F3664B3CDA4`.
The local wrapper changes have not been committed or pushed. The consumed
one-use evidence is retained; scheduling and public publishing remain off.

References: [OpenAI app-server](https://learn.chatgpt.com/docs/app-server),
[MCP progress metadata](https://modelcontextprotocol.io/specification/2025-06-18/basic/utilities/progress),
[OpenSSH client options](https://man.openbsd.org/ssh_config).

## Additional diagnostic evaluation — 14/10/2026

The administrator explicitly approved **one additional diagnostic model test**,
with existing evidence preserved and scheduling/public publishing disabled.
The audit helper now uses fixed `one-shot-02-*` names for this single approval.
There is no caller-selected run ID, reset, deletion, or overwrite operation. Only
the existing read-only storage-check canary is shared with the first attempt.

Before generation, the actual no-generation runtime preflight passed. The local
fixed-response simulator then explicitly verified the diagnostic observer: turn
completion, the exact final response, and all 24 raw synthetic tool calls. Its
18 collaboration rejections, three absent dangerous tools, two closed reporting
calls, complete developer instructions and single loaded parent thread also
passed. This simulator made no external model or SSH call. Diagnostic capture is
best-effort on normal completion/caught failure; an abrupt supervisor kill can
still prevent a diagnostic artifact from being written.

The combined offline suite passed **497 tests: 479 passed, 18 existing
platform/privilege-specific skips**. The 118 wrapper/audit tests include fixed
second-slot naming, first-slot preservation and rejection of incomplete observer
evidence. These tests do not claim successful end-to-end report generation.

Exactly one real `gpt-6.1-sol` / `medium` turn then used the existing ChatGPT/Codex
sign-in. No Platform API key was used. The second private supervisor log records:

| UTC, 13/10/2026 | Event |
| --- | --- |
| 22:29:22.272812 | Approval reserved |
| 22:29:24.172631 | Runtime verified |
| 22:29:24.183657 | Model turn start recorded |
| 22:29:30.841184 | Model turn completed |
| 22:29:30.864728 | Evaluation failure recorded as `model_failed` |

This was 01:29 on 14/10/2026 in Athens. The last event denotes failure of the
overall evaluation; it does **not** contradict the completed model turn. The
returned fixed error was `private_audit_evidence_incomplete`. The retained
diagnostic reports one raw tool call, one MCP item and 21,661 total tokens.
Its final response says preparation was rejected or unavailable and that it
stopped without retrying, publishing or receiving a private acknowledgement.

The receipt, armed marker, terminal marker and diagnostic exist. No MCP claim or
private result exists; the MCP audit remains zero bytes. The source requires a
claim and durable transport intent before constructing/sending an SSH request,
so this attempt did not reach the admitted SSH reporting transport or write a
staging report. It is **not** evidence of a server/SSH authentication failure.

The failure is narrowed to protocol validation or the local pre-claim gate.
Current diagnostics deliberately do not retain a reason from that boundary, so
the exact cause is still unproven. Post-run native root/material ACL checks and
read-handle checks on five fixed second-attempt files passed without reading
credential bytes, altering evidence or reopening the gate. Those host checks
do not reproduce the rejected MCP request or establish its original cause.

The next proposed diagnostic improvement at that step was a bounded, secret-free pre-claim
reason code plus a local synthetic gate test, before considering another model
turn. The later approved local-only implementation is documented below. Do not weaken the gate, grant
broader permissions, enable publishing or reset either attempt to investigate.

All six existing first-attempt evidence/canary SHA-256 values remained identical
before and after this test. The disabled Windows task retained its unchanged
exported-definition digest recorded above. Public feed, Overview, History and
Nginx configuration hashes, and service PIDs/restart counters also remained
identical. No model retry, public report, scheduled activation, service/device
restart, production deployment or Git push occurred.

## Secret-free rejection diagnostics — local checks only, 14/10/2026

The administrator approved adding rejection codes and local tests, explicitly
forbidding another model test and requiring both old evidence sets, the disabled
schedule and disabled public publishing to be preserved.

### Implemented boundaries

- `insights_staging_mcp.serve()` accepts an optional trusted rejection callback.
  Static codes distinguish frame/JSON/envelope/ID/parameter/metadata/handshake/
  dispatch/response errors. Reporting-gate codes are selected from a closed enum.
  Protocol acceptance rules and the generic MCP error are unchanged.
- `open_mcp_audit()` classifies the existing checks: root, receipt, expiry,
  terminal marker, armed marker, exclusive claim, audit opening and final
  gate recheck. It does not bypass or broaden any of them. `GatedReports` accepts
  only the exact trusted `AuditFailure` type and a known code, never arbitrary
  exception text. Session/audit setup and result persistence have distinct codes.
- The separate private sink contains exactly UTC timestamp, bounded attempt
  number, fixed operation (`unknown`, `prepare`, `publish`) and allowlisted code.
  No IDs, tokens, paths, request metadata, argument values, translations, stack
  traces or exception text are stored. Writes are durable, capped at 64 events
  and 32 KiB, and permanently fail closed after a sink error.
- The sink is pre-created exclusively only when a new approval is reserved.
  MCP discovery does not open it: `LazyRejectionLog` opens it only on the first
  rejection, avoiding conflicts between discovery and per-thread MCP clients.
  It checks the matching receipt and native file-handle ACL/canonical-path
  restrictions, refuses terminal receipts and nonempty logs, and cannot create,
  truncate, reset or repair a log. An expired receipt may record its rejection
  before terminal closure; this grants no reporting claim or authority.
- MCP startup/sink failures leave stdout empty and emit only the generic failure
  on stderr. Internal codes cannot escape through the CLI's normal administrator
  error output path. If the diagnostic sink itself cannot open, no unsafe
  fallback is used; a missing diagnostic is not evidence of success.

The existing `one-shot-02-*` receipt is already terminal. **No rejection file was
backfilled into either old attempt, no receipt was changed, and no fresh model
slot was provisioned.** Logging readiness does not authorize a new execution.

### Actual local observation, without generation

`server/insights_staging_rejection_probe.py` launches the pinned app-server only
as an MCP client. It uses the documented `mcpServer/tool/call` RPC with two fixed
synthetic requests directed to an always-closed fixture. The harness cannot send
`turn/start`, resume an existing chat, invoke arbitrary tools, or construct the
real integration/SSH transport. No local model responder is used either.

The fixture records only allowlisted codes in a fresh temporary test directory.
An initial harness check stopped at inventory verification because eager file
opening conflicted with multiple discovery clients. Lazy opening fixed that
test-harness issue; a regression test now covers discovery without file creation.
No reporting parser change was needed or made.

The completed direct-RPC check recorded:

| UTC, 13/10/2026 | Attempt | Code |
| --- | --- | --- |
| 22:42:47.852852 | 4 | `protocol_metadata_shape` |
| 22:42:47.855362 | 5 | `protocol_metadata_shape` |

Both fixed operations stopped at metadata validation, before `ClosedReports.call`.
The synthetic diagnostic artifact is retained locally at
`C:\Users\anest\AppData\Local\Temp\homeenergy-rejection-probe-dn035_hz\rejections.jsonl`.
It contains no actual request metadata or secret. A locally generated CLI RPC
schema was used to verify argument names; no external resource was downloaded.

This establishes that the installed client's **direct MCP RPC path** supplies
metadata outside the adapter's current accepted shape. It does not establish
which exact metadata fields were present, or prove the earlier model-driven
path failed identically. It is not evidence of an SSH/server/account failure.
Any compatibility change should be separately reviewed and tested locally first;
do not accept arbitrary metadata or enable another model run by inference.

### Verification and unchanged state

- Final combined offline suite: **562 tests, 544 passed, 18 existing
  platform/privilege-specific skips**, including 49 rejection tests and 16
  no-model probe tests. Reload/provision messages in mocked tests are not real
  server actions.
- The actual one-shot `--preflight` passed with its gate closed and no model turn.
  Pinned source hashes were updated only for the reviewed local changes; global
  Codex settings, the old dummy launcher and saved permission profiles are intact.
- All 12 retained first/second-attempt evidence/canary files match their before
  hashes and sizes. No new file exists in the private evidence directory.
- The Windows task remains disabled with identical exported-definition SHA-256
  `D848586A7C16B2B4FF92980126E09896216FC823AB5ECD83E8BA9F3664B3CDA4`.
- No model turn, SSH request, server deployment, device action, public report,
  schedule activation, history modification or Git push was performed.

Reference: [OpenAI app-server MCP operations](https://learn.chatgpt.com/docs/app-server).

## Metadata field identification — direct-client capture completed, 14/10/2026

The approved follow-up extends only the local rejection probe and its tests.
`MetadataSource` observes the two exact synthetic `tools/call` requests and
forwards the original bytes unchanged. It records only the fixed operation,
whether `_meta` exists, its JSON type, and bounded top-level field names/types.
Metadata values, nested contents, request IDs and argument text are not retained.
Field-name filtering is diagnostic hygiene for this pinned-client fixture, not
a general-purpose secret sanitizer for arbitrary incoming requests.

Each operation's descriptor is written exclusively to a separate
`metadata-prepare.json` or `metadata-publish.json` in the new temporary evidence
directory. It is flushed and fsynced before returning the request to the adapter:
the runtime's Job Object cleanup can terminate the fixture without running its
Python cleanup handlers. Discovery does not create either file; existing files
are never overwritten. The fixture still always rejects execution and cannot
construct a real reporting transport. The real adapter's parser and pins are
unchanged.

The first attempted direct-client capture stopped in `load_inputs()`, **before runtime
launch, thread creation, either synthetic call, or a new evidence directory**.
The saved global `config.toml` no longer matches its previously reviewed hash.
All other pinned input files and the adapter import closure still match. The
global file was not edited, its pin was not updated, and no check was bypassed.
The exact configuration change has not been established; model/effort-only and
simple line-addition hypotheses did not reproduce the old hash.

The administrator then explicitly approved a diagnostic-only review and pin.
The reviewed current global configuration has SHA-256
`66eb0479fc51a914aae18782cda79de5d107a57fa7ac88f20d938703f6ac3905`.
Only `load_probe_inputs()` uses this exact pin in a temporary copied pin table;
it restores the original table in `finally`, including on failure. All other
input pins remain mandatory. Runtime configuration, restricted thread permissions
and the two-tool inventory are checked as before; the global file's hash is also
checked again after the calls. The real launcher file and its old global pin are
unchanged, so its normal preflight remains blocked by the settings change.

The completed capture at 23:18 UTC on 13/10/2026 (02:18 on 14/10/2026 in Athens)
observed exactly these top-level metadata fields for **both prepare and publish**:

| Field | JSON type | Adapter accepted it at capture time? |
| --- | --- | --- |
| `progressToken` | integer | Yes, within its existing bounds |
| `threadId` | string | No |

Both requests returned `protocol_metadata_shape` before reporting dispatch.
The retained descriptor files contain no values. They and `rejections.jsonl`
are in `C:\Users\anest\AppData\Local\Temp\homeenergy-rejection-probe-1txqg5sh`.
An offline regression replays this shape with synthetic values, then removes
only `threadId`: the former is rejected at metadata validation; the latter reaches
the fixture's `report_gate_closed` rejection. Neither variant can execute a report.

Read-only binary inspection had suggested `codex/sandbox-state-meta`, but that
field was **not present in this captured direct-client request**. The actual
observation, not the static candidate, determines the compatibility diagnosis.
The earlier model-driven request was not retained, so its exact metadata and
failure cause remain unproven. No third model test was made.

The next proposed change is a separately approved, bounded compatibility rule
for `threadId`, treated as untrusted context rather than identity or authority,
and stripped before tool dispatch. Unknown metadata must not be accepted merely
to make a test pass. No such parser change was made in this identification step.

Final verification: **577 offline tests, 559 passed and 18 existing skips**,
including 31 probe tests. The original launcher, adapter and private audit module
hashes are unchanged. All 12 retained one-shot evidence/canary files retain their
original hashes and sizes, with no added private evidence file. The reviewed
global configuration retained its new hash throughout the completed capture.
The disabled Windows task retained the exact exported-definition hash recorded
above. Mocked provisioning messages in the offline suite are not server actions.

The schedule and public publishing remain disabled. No model/provider request,
SSH request, server/device action, Git push, or previous-evidence change was
performed during this investigation.

## Narrow thread metadata compatibility fix — local verification, 14/10/2026

The administrator approved the fix and local tests only. The adapter now accepts
`_meta.threadId` only when all of the following hold:

- The JSON-RPC method is exactly `tools/call`.
- The existing required `progressToken` is present and passes its unchanged rules.
- The optional `threadId` is a string of 1–128 ASCII letters, digits, underscores
  or hyphens. Empty, oversized, Unicode, path-like and other malformed values fail.
- No metadata keys other than `progressToken` and optional `threadId` are present.

Both values are discarded before dispatch. `threadId` does not select a chat,
file, transport, approval receipt or destination; it is never identity, authority,
a gate token or a logged identifier. `initialize`, `ping`, `tools/list`, unknown
methods and notifications did not gain thread metadata support. Invalid thread
values use the fixed private code `protocol_metadata_thread`; the public MCP
error and audit schema are unchanged.

At this compatibility step, only the adapter's source hash in
`insights_staging_launcher.py` and the resulting launcher hash in
`insights_staging_run.py` were refreshed. The real launcher's global-settings pin
still held the old reviewed hash, so that edit did not clear its configuration
block. Consumed one-shot receipts remained terminal. The diagnostic-only settings
pin was confined to the always-closed fixture and restored in memory after input
validation. The subsequent, separately approved settings review is recorded below.

The installed pinned client was exercised through direct MCP RPC again, without
`turn/start`, inference, SSH or a local model responder. At 23:22 UTC on 13/10/2026
(02:22 on 14/10/2026 in Athens), both fixed synthetic requests included the same
`progressToken` integer plus `threadId` string shape. Unlike the previous check,
both passed metadata validation and returned **`report_gate_closed`**, with
operation names `prepare` and `publish`. No reporting operation succeeded.

New metadata-only descriptors and rejection evidence are retained separately in
`C:\Users\anest\AppData\Local\Temp\homeenergy-rejection-probe-2k6dp56x`.
The prior diagnostic directories and original private evidence are preserved.
This verifies the direct-client compatibility correction, not the earlier
model-driven failure's exact cause or end-to-end reporting readiness.

The schedule and public publishing remain disabled. No model run, server
deployment, SSH request, device operation, history modification or Git push was
performed. The real workflow is not enabled by these local tests.

Verification: **585 offline tests, 567 passed and 18 existing skips**, including
88 rejection/probe tests. An independent read-only review found no blockers in
the scoped fix. Tests cover value/length/character restrictions, unknown keys,
method restrictions, metadata stripping and non-disclosure, unchanged progress
token rules, tool/argument validation and rejection by the real closed one-shot
gate with synthetic inputs. No mocked provisioning output represents a server
operation.

Before/after hashes and sizes match for all 12 original one-shot evidence files;
all prior diagnostic artifacts, the private audit module, global settings and
saved dummy launcher/profile are unchanged. The Windows task remains disabled
with the identical exported-definition digest. New source hashes match their
updated pins, while the real global-settings pin at that step still rejected the
current settings. A separate review was required before any future approved
real-model evaluation; the compatibility fix was not permission for one.

## Reporter notification isolation and settings review — 14/10/2026

The administrator explicitly approved a scoped launcher change, regression tests,
documentation and a no-model preflight. Saved global settings, the disabled
schedule, public publishing and existing evidence were to remain unchanged; no
model evaluation was authorized.

### Finding and targeted change

The current global configuration contains a nonempty top-level `notify` command.
This is separate from the hooks feature: disabling hooks did not by itself clear
that inherited command. This review found an unhandled configuration path, not
evidence that the command had executed during earlier Reporter attempts. Command
contents were not copied into diagnostics or this documentation.

`insights_staging_launcher.build_config()` now sets `notify = []` in the per-run
configuration. `verify_effective()` requires an exact empty list, rejecting missing,
non-list and nonempty values with the fixed `runtime_notify_mismatch` error. Both
initial and subsequent effective-configuration reads enforce it. The one-shot
wrapper retains this override; saved settings and ordinary Codex notifications
are not changed.

The separate review approved only this exact current global-configuration SHA-256:
`66eb0479fc51a914aae18782cda79de5d107a57fa7ac88f20d938703f6ac3905`.
The real launcher's input pin was updated accordingly. Future settings changes
still fail the exact-hash check; this is not blanket approval for new settings.
The reviewed launcher SHA-256, also pinned in `insights_staging_run.py`, is
`c92003f7c74486211a9521895051e0e28f16ed74f7b8d33f85e04253a577c232`.
No other authorization, tool inventory or transport permission was added.

### Verified results and limits

The actual pinned client passed `insights_staging_run.py --preflight`, returning:

```json
{
  "chatgpt_authentication": true,
  "model_started": false,
  "one_shot_runtime_verified": true,
  "report_gate": "closed",
  "schedule_changed": false
}
```

This verified the local input/source pins and material checks, existing ChatGPT
sign-in, restricted ephemeral runtime, effective empty notification command and
exact allowed tool inventory. It did not start a model turn, authenticate over
SSH, submit a reporting request, reserve a new approval slot or create a new
private attempt. The existing storage-check canary was read, not replaced.

- Combined offline suite: **593 tests, 575 passed and 18 existing
  platform/privilege-specific skips**. Mock provisioning/reload output does not
  represent actual server operations.
- The two focused launcher/wrapper suites passed **134 tests**, including eight
  added tests for inherited-command removal, source immutability, strict type and
  value validation, one-shot propagation and rejection at runtime gates.
- Independent source review found no blockers in this scoped hardening. The actual
  launcher digest matches the wrapper's updated integrity pin.
- Before/after hashes and sizes match for all 12 retained first/second-attempt
  evidence/canary files. No old evidence was overwritten or new slot created.
- Global Codex settings, the saved dummy launcher/profile and private audit module
  hashes are unchanged. The Windows task remains disabled with the same exported
  definition SHA-256 recorded above.

No model call, SSH request, server/device operation, public report, schedule
activation, history-data modification or Git push occurred. Scheduling and public
publishing remain disabled. Successful no-model preflight is **not** end-to-end
model-to-report certification; any further private synthetic model evaluation
requires its own explicit approval and fresh evidence slot, preserving both
consumed attempts.

Reference: [OpenAI configuration reference: notify](https://learn.chatgpt.com/docs/config-file/config-reference).

## Third private synthetic model evaluation — 14/10/2026

The administrator explicitly approved one further private synthetic model test,
preserving all earlier evidence and leaving scheduling/public publishing disabled.
The nine fixed per-attempt paths now use `one-shot-03-*`. The shared storage canary
is unchanged. The audit module's reviewed SHA-256 is
`fe60e32a1d3d3d581281bc27b034bee520a51b61624b0c6cf62e2285082fc5d5`,
and the wrapper's matching source pin was updated. The preservation regression
now protects both earlier prefixes and verifies refusal of a second reservation.
No model permissions, prompt, destination or retry allowance changed.

Before generation, 265 focused tests and the actual no-model preflight passed.
The fixed local-response simulator also passed: diagnostic capture, 24 synthetic
tool calls, 18 collaboration rejections, three absent dangerous tools, two closed
report calls and only the parent thread loaded. That simulator made no external
model call and does not independently establish OS filesystem denial.

Exactly one `gpt-6.1-sol` / `medium` turn used the existing ChatGPT sign-in. Its
supervisor records the following UTC events on 13/10/2026 (02:34 on 14/10/2026
in Athens):

| UTC | Event |
| --- | --- |
| 23:34:35.411498 | Approval reserved |
| 23:34:37.120685 | Runtime verified |
| 23:34:37.131203 | Model turn start recorded |
| 23:34:43.085736 | Model turn completed |
| 23:34:43.104759 | Overall evaluation failed |

The wrapper returned `private_audit_evidence_incomplete`. The diagnostic records
one tool call, one MCP item, a completed model turn and 21,650 total tokens. The
model stated that preparation was rejected and stopped without retrying or
publishing. Its prose is not used as proof of successful transport or publication.

The new secret-free rejection log records attempt 4 at
`2026-10-13T23:34:41.422804+00:00`, with `protocol_metadata_shape` and the initial
method label `unknown`. Source review confirms that this code means `_meta` was
not an object, lacked the required `progressToken`, or contained an unsupported
key. It does **not** identify which case or field occurred. The `unknown` label is
assigned before tool-name validation; it is not proof of an unknown tool request.
No metadata values or raw request were retained.

There is no MCP claim or private result, and the MCP audit is empty. The failure
occurred before reporting dispatch or construction of its SSH transport. This
is evidence of a local protocol-compatibility failure, not an inverter, VPN,
staging-server or SSH-authentication fault. The receipt and terminal marker are
retained; this third approval is consumed and must not be reset or retried.

Verification after the attempt:

- Combined offline suite: **593 tests, 575 passed, 18 existing platform-specific
  skips**. Mocked provisioning output represents no real server action.
- All 12 original evidence/canary files retain exactly the same hashes and sizes.
  The seven new third-attempt artifacts are separate and remain in the private
  Windows material directory; no old artifact was overwritten.
- Saved global settings and the dummy launcher/profile hashes are unchanged.
  The Windows task remains disabled with its identical exported-definition hash.
- Read-only administrator SSH snapshots before/after match for the public report
  feed, Overview, History and Nginx site configuration. Nginx, exporter and control
  service remain active with unchanged PIDs/restart counters. These checks are
  separate from the model's reporting path, which made no SSH request.

No private report was produced, no public report or recurring schedule was enabled,
and no service/device restart, history modification, deployment or Git push
occurred. No further model test was run.

Recommended next investigation, requiring separate edit approval: extend the local
fixed-response simulation to observe metadata names/types on the actual
`functions.exec` to report-tool path, using an always-closed fixture. Repeating the
direct `mcpServer/tool/call` probe alone would retest a different path. Keep the
diagnostic local, value-free and without a model/provider call, SSH transport or
new approval slot; do not broaden metadata acceptance speculatively.

## Local functions.exec metadata-path capture — 14/10/2026

The administrator approved adding and running the local-only diagnostic. The new
`server/insights_staging_metadata_probe.py` uses the existing, hash-pinned runtime
simulator unchanged. A loopback-only HTTP responder supplies its 24 fixed synthetic
tool calls, including `functions.exec` calls to prepare/publish. It performs no
inference and connects to no external model provider. Credential-bearing HTTP
headers are rejected; runtime configuration, permissions and tool inventory are
verified as in the existing simulator.

Only the diagnostic capture helper gained a reusable fixed-plan source hook. The
new fixture checks its exact synthetic arguments against the simulator's last two
calls. It observes metadata without changing the original bytes and permanently
rejects report execution. It neither imports the real one-shot/audit/integration
entrypoints nor accesses their approval receipts or SSH material. Source pins bind
both helper implementations, including the unchanged runtime simulator.

Both metadata descriptors are flushed before their corresponding reply, retained
exclusively in new files, bounded and schema-checked before returning the result.
They contain only the fixed tool name, metadata presence/type and top-level field
names/types. No values, nested contents, request IDs, prompts, headers or arguments
are retained. This is diagnostic hygiene for a fixed installed client, not a
general-purpose sanitizer for arbitrary untrusted field names.

At 23:40:18 UTC on 13/10/2026 (02:40:18 on 14/10/2026 in Athens), the local replay
recorded the same metadata shape for both prepare and publish:

| Field | JSON type | Current adapter accepts it? |
| --- | --- | --- |
| `progressToken` | integer | Yes, within its existing bounds |
| `threadId` | string | Yes, within its existing bounds |
| `callId` | string | No |
| `itemId` | string | No |
| `sessionId` | string | No |
| `windowId` | string | No |
| `x-codex-turn-metadata` | object | No |

The rejection log contains `protocol_metadata_shape` for both calls (attempts 4
and 5). Thus this replay reaches the real adapter and reproduces the failed
validation on the `functions.exec` path; it stops before the closed report gate.
The earlier direct MCP RPC probe sent only `progressToken` and `threadId`, which
explains why that narrower check passed after its compatibility edit.

The fresh retained directory is
`C:\Users\anest\AppData\Local\Temp\homeenergy-rejection-probe-is6y1vte`, containing
`metadata-prepare.json`, `metadata-publish.json` and `rejections.jsonl`. No old
artifact was reused or overwritten. The result explicitly reports
`original_request_captured: false`: this is a simulated reproduction using the
installed runtime, not a recording of the previously failed real model request.

At this diagnostic step, no metadata acceptance rule or real Reporter source pin
changed. The real model runner remained blocked by the incompatible metadata and
its consumed one-shot approvals. A proposed compatibility correction must be
separately approved, narrowly validate the observed fields, and discard them
before report dispatch; metadata must never become identity or authorization.
No further model test, SSH request, new approval slot, schedule activation,
public report, deployment, Git push or device action was performed.

Verification: **616 offline tests, 598 passed and 18 existing platform-specific
skips**, including 23 new metadata-path tests. The observed seven-field shape was
replayed with invented values: both calls return `protocol_metadata_shape`;
removing only the five additional fields reaches `report_gate_closed`. This is a
regression test of the unchanged parser, not a metadata-acceptance change.
Independent review found no blockers in the diagnostic. Mocked provisioning
messages in test output do not represent real server actions.

Before/after checks match for all 19 retained one-shot evidence/canary files, all
files in the three earlier diagnostic directories, saved global configuration,
the dummy launcher/profile, and the real launcher, one-shot wrapper, audit,
adapter and runtime-simulator source hashes. The Windows task remains disabled
with the identical exported-definition digest. Scheduling and public publishing
remain off; the diagnostic did not contact the server.

## Bounded client-metadata compatibility fix — 14/10/2026

The administrator approved a targeted parser correction and local verification,
without another model run. On `tools/call` only, the adapter now accepts the five
additional top-level context fields observed by the fixed-response simulation:

- `callId`, `itemId`, `sessionId` and `windowId`: exact strings, zero to 256
  printable ASCII characters each. Empty values are allowed because only types
  were observed; no undocumented UUID format is assumed.
- `x-codex-turn-metadata`: an exact JSON object, at most 4,096 encoded UTF-8 bytes,
  four container levels (root is level one), 64 total value nodes including the
  root, and 16 entries per object/array. Keys are at most 128 characters and string
  values at most 1,024 characters. Scalar leaves below level-four containers are
  allowed. Only JSON values are accepted; non-finite floats and invalid Unicode
  are rejected. These are resource/structure bounds on an opaque discarded
  object, **not** a claimed schema for its unobserved nested contents.

The existing required `progressToken`, optional `threadId` and their validators
are unchanged. All other RPC methods retain the progress-token-only metadata
allowlist. Unknown top-level fields, malformed values and over-limit objects fail
closed. The entire `_meta` object is discarded before tool/report dispatch.
Context never supplies identity, credentials, approval, destination, arguments
or report text. The full-frame limit is unchanged.

Private diagnostics add only the fixed rejection codes
`protocol_metadata_context` and `protocol_metadata_turn`. Public errors remain
generic; metadata values are not added to replies, audit events or evidence.
The real gate, receipt paths, consumed approvals and SSH transport are untouched.
Only the adapter source pin in the launcher and the launcher's pin in the wrapper
are refreshed; no saved Codex configuration, dummy launcher or diagnostic helper
has been changed.

Local verification against the pinned installed client now confirms:

- The fixed-response `functions.exec` replay sends the same seven-field shape.
  Both prepare and publish return `report_gate_closed`, not the previous metadata
  rejection. New names/types-only evidence is retained separately in
  `C:\Users\anest\AppData\Local\Temp\homeenergy-rejection-probe-3qklrzjw`.
- The direct MCP RPC path also reaches `report_gate_closed` for both operations.
  Its separate evidence is in
  `C:\Users\anest\AppData\Local\Temp\homeenergy-rejection-probe-7bkxb2_l`.
- The actual wrapper's `--preflight` reports runtime verified, model not started,
  report gate closed and schedule unchanged.
- Independent read-only review found no blocking issue in the validation or
  dispatch boundary. These checks establish local client compatibility, not a
  successful end-to-end model report or permission to publish.

The combined offline suite ran **629 tests: 611 passed and 18 existing
platform-specific skips**. The three focused metadata/diagnostic modules passed
124 tests. Coverage includes exact size/depth/node boundaries, UTF-8 byte limits,
duplicate nested keys, non-finite exponents, no retained values, unknown fields,
unchanged token/thread validation, real argument rejection and a permanently
closed real gate. Mocked provisioning messages do not represent server actions.

Before/after hashes and sizes match for all 19 one-shot evidence/canary files and
all files in the four earlier diagnostic directories. Saved global settings, the
dummy launcher/profile, audit module and all three diagnostic helpers are
unchanged. The scheduled task is still disabled with its identical exported
definition. Reviewed source pins now bind adapter SHA-256
`e0dd02eb2758e463d4f871351bb62770aa99c70f8208ddcd2d3255b0d4054939`
and launcher SHA-256
`83ac3d40b826d35ab58a33af2ad315089fca6e5743bb631a8ab38ed39d0555a0`.

No external model/provider call, SSH connection, report, new approval slot,
production change, service/device restart, schedule activation or Git push was
performed. A future real model evaluation requires a new explicit approval; none
of the three terminal one-shot slots may be reset or reused.

## Fourth private synthetic model evaluation — 14/10/2026

The administrator explicitly approved one new single-use private synthetic model
test, keeping the schedule and public publishing disabled. The nine per-attempt
audit paths now use `one-shot-04-*`; the existing shared storage canary is
unchanged. The wrapper pins the audit module's new SHA-256:
`af60ff89fd890234938ee7c2b38c937edb182da4042d354711035f5547115db4`.
The preservation regression protects the original, second and third attempts and
still rejects a second reservation. No model, effort, prompt, runtime permission,
destination, gate logic or retry allowance changed.

Before generation:

- The real no-model preflight verified the restricted runtime and ChatGPT account,
  with the report gate closed.
- The local fixed-response simulator verified diagnostic capture, 24 synthetic
  tool calls, 18 collaboration rejections, three unavailable dangerous tools,
  two closed report calls and only its parent thread loaded.
- A separate OS-profile probe confirmed permitted canary read, denied excluded
  and root-canary reads, denied write (no unexpected file), and denied loopback
  access with successful host controls before and after. This is independent
  OS-profile evidence, not a claim that the model attempted those operations.
- Independent source review and 162 focused offline tests passed.

Exactly one `gpt-6.1-sol` / `medium` model turn was run. Supervisor times are UTC
on 13/10/2026 (02:52 on 14/10/2026 in Athens):

| UTC | Event |
| --- | --- |
| 23:52:25.456957 | Single-use approval reserved |
| 23:52:26.959594 | Runtime verified |
| 23:52:26.970109 | Model turn started |
| 23:52:54.487191 | Model turn completed |
| 23:52:54.522252 | Verification finished successfully |

The wrapper returned `private_test_completed: true`, status `staged`, and matching
analysis/submission/acknowledgement ID
`d2f81a2a6667e74669810c07fddacb0044377605801327585e1cb33c9742fc58`.
Both the prepared result and acknowledgement declare `scope: staging` and
`synthetic: true`. The durable audit contains six events: one accepted prepare,
one accepted publish, and two transport requests each started and returned.
There were no failed transports, rejected adapter calls or publication retries;
the rejection log is empty. The diagnostic contains two MCP items, three raw
tool calls and 49,335 total tokens. These are observed counters, not an inference
from the model's final prose or a count of provider HTTP requests.

Independent read-only review of the retained report confirmed matching IDs and
faithful English, Greek and Russian text. It reports the fixture's 84.44% coverage,
missing final 224 minutes, withheld comparisons below 90% coverage, and the lack
of evidence for an outage, device fault or weather cause. These are **synthetic
test findings**, not observations about the actual home.

Nine new `one-shot-04-*` artifacts are retained privately, including the validated
result, audit, diagnostic and terminal marker. All 19 earlier evidence/canary
files have identical hashes and sizes. The new receipt is consumed and terminal;
it must not be reset or reused. Saved global configuration, dummy launcher/profile,
real launcher/adapter/integration and reporting guidance hashes remain unchanged.
The Windows task is still disabled with its identical exported-definition hash.

Read-only administrator SSH snapshots before and after match for the public
report feed, Overview, History and Nginx site configuration. Nginx, exporter and
control service remain active with identical PIDs and restart counters. These
verification connections are distinct from the runner's two restricted staging
transport requests. No public-report or live-history write, service/device action,
production deployment, schedule activation, commit or Git push was performed.

The combined offline suite also passed: **629 tests, 611 passed and 18 existing
platform-specific skips**. Mocked provisioning/reload messages are test output,
not real server actions. This result verifies an end-to-end **private synthetic**
run; it does not enable or certify recurring public reports against real history.
Any further model run, real-history integration, public publication or scheduling
requires separate explicit authorization.
