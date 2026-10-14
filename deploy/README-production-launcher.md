# Production launcher: disabled no-model preparation

Verified on 14/10/2026. This document records a new stage; older staging,
private-review and server-preparation evidence is preserved, not rewritten.

## State and scope

The production SSH account/key restriction stage has completed. That grants
authentication to a fixed server command, not permission to publish. The
root-owned production gate is still `public_publishing_enabled: false`; both
analysis preparation and publication remain blocked. Report storage is still
root-owned, the public feed is unchanged/empty, and no report database, publisher
lock or activation record exists.

The new launcher is a **preflight-only production candidate**, not a live or
scheduled publisher. It has no `turn/start`, `--run`, `--activate`, schedule,
device-control or actual SSH-send entrypoint. It cannot create a live analysis
snapshot or publish a report. Even a direct call to either advertised reporting
tool fails before any connection or credential check is constructed.

This does not imply that a full production model run has been certified. That
requires a separately reviewed execution supervisor, durable private run/audit
evidence, explicit arming, server-state activation and an authorized public trial.

## New files

- `server/insights_production_launcher.py`: verifies pinned inputs, derives
  production-only settings in memory, and supervises bounded no-generation
  runtime discovery with an exact RPC allowlist.
- `server/insights_production_connection.py`: read-only production material/ACL,
  public-key identity, destination, host-key and installed SSH-binary checks.
  There is no network-send function.
- `server/insights_production_os_probe.py`: synthetic canary read/write/network
  probes for the selected production profile, without model execution.
- `server/test_insights_production_launcher.py`: offline regression tests,
  including negative configuration, protocol and credential cases.
- `deploy/windows-report-production-candidate/`: separate scope instructions and
  two synthetic canaries, not a model-writable workspace.
- `deploy/windows-report-production-root-canary.txt`: synthetic root-denial
  positive-control file, separate from previous test fixtures.

Two non-secret files were added beside the **existing** production key in
`C:\ProgramData\HomeEnergyReporterProduction`: `known_hosts` and
`integration-binding.json`. They inherit the existing restricted directory DACL.
No private key was generated, replaced, copied, displayed or read by the checks.
The production host pin is the already verified server Ed25519 key; this was not
a network keyscan or trust-on-first-use operation. Only public-key bytes, pins
and configuration are hashed. Private-key validation is metadata-only.

## Enforced checks

- Existing runtime executable/version, Python, global configuration, original
  instructions, previous launcher, reporting contract and imported source pins
  must still match. Changes fail closed and require review, not automatic repinning.
- Project modules execute the exact hash-verified source bytes using the prior
  source-only loader. Unreviewed/preloaded project modules and cached project
  bytecode are not accepted. Old launcher files are not modified.
- Production profile: deny filesystem root by default, minimal runtime reads,
  read-only candidate workspace, explicit denial for both credential roots,
  source, original Reporter workspace and Codex home; no model network.
- In-memory settings disable shell, external apps/plugins/browser, delegation,
  inherited notification commands, hooks and unreviewed providers. The effective
  configuration is checked before and after ephemeral thread discovery.
- Runtime thread must confirm the exact named production profile, read-only/
  no-network sandbox, no additional roots, fixed model/provider, pinned
  instruction source and ephemeral status. No turn is submitted.
- MCP inventory must contain only the exact production `prepare`/`publish`
  schemas and descriptions, plus the disabled empty `node_repl` entry. Discovery
  is not an actual reporting operation; every reporting call is rejected locally.
- The runtime starts suspended, is assigned to its owned Windows Job before
  resuming, and is terminated/cleaned up on exit or failure. Stream output and
  runtime lifetime are bounded by the reused reviewed supervisor.
- Metadata-only ACL checks require exactly the current Windows user, SYSTEM and
  Administrators, with expected ownership/inheritance and supported raw ACEs.
  Reparse paths and hardlinked private files are refused. The exact pinned system
  SSH executable alone retains its reviewed Windows servicing-hardlink exception.
- The connection configuration accepts only the fixed production host/account
  material and hashes. It cannot be selected by model arguments or environment.

No Windows account, global Codex setting, old trial source, evidence, scheduled
task, application file, SSH configuration or service was changed in this stage.
The current Windows user and local administrators remain outside this sandbox's
threat model: this is not a separate-account boundary against other local programs.

## Results

No-model checks completed at 01:22 UTC / 04:22 Athens on 14/10/2026:

| Check | Observed result |
| --- | --- |
| Static pins and configuration | Passed |
| Production material/DACL/host pin | Passed; no private-key contents read |
| Actual ephemeral runtime | Exact production profile confirmed |
| Effective notifications and delegation | Disabled |
| MCP discovery | Exact two production tool contracts; REPL empty |
| Model generation / reporting / SSH from launcher | None |
| Allowed synthetic read | Succeeded |
| Denied synthetic read / root read / write | Explicit access denied |
| Synthetic loopback connection | Explicit access denied; host positive controls passed before and after |
| Unexpected synthetic write | Absent |

The OS probe executes fixed administrator-authored code through the sandbox CLI;
it is **separate OS-profile evidence**, not a model-driven tool-bypass test or a
probe of real credential contents. Collaboration bypass tests from the earlier
reviewed runner are retained; they were not rerun here. This stage confirms the
same pinned runtime and effective `agents.enabled=false`, not a newly observed
full model-call tool inventory.

All 28 new offline tests passed, including parameterized negative cases. The full
insights suite ran **1,018 tests: 999 passed, 19 skipped**. Platform skips are not
passes. Provisioning/reload text emitted during that suite came from mocked
fixtures, not actual server operations. No model-service request was made.

A final read-only administrator SSH inspection checked 10 protected hashes,
including application HTML, Nginx, the production SSH policy and reporting
modules. The root-owned disabled gate, empty feed, absent database/lock/activation
and existing application service states were verified. Nginx, exporter and control
service PIDs/restart counts were unchanged; no remote mutation was performed.

The Windows `Home Energy Reporter - Dummy` task remains `Enabled=False`/Disabled.
No new schedule was registered and no app automation was changed. Global Codex
settings and the prior private trial's snapshot/result hashes were reconfirmed.
No Git commit or push was performed.

## Repeatable no-model commands

Run from the repository using the already installed Python; do not download or
install anything. Each invocation rechecks pins. An unexpected write from a probe
must be preserved for review; the launcher must not delete it or broaden access.

```powershell
& 'C:\Users\anest\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -I -B server/insights_production_launcher.py --check
& 'C:\Users\anest\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -I -B server/insights_production_launcher.py --integration-check
& 'C:\Users\anest\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -I -B server/insights_production_launcher.py --preflight
& 'C:\Users\anest\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -I -B server/insights_production_launcher.py --os-check
```

These commands do not enable a future run. Do not connect this candidate to the
disabled task, open the server gate, alter report-state ownership or run a model
without the next explicit authorization.

## Reviewed hashes

| Input | SHA-256 |
| --- | --- |
| Production launcher | `c68e4a57d5a0bf39d3efd881f2d9fba6a61976f70ea56fdc1de11becf7406edd` |
| Production connection guard | `425af214b37a661ebdc94202d8fa1e612b69412274ba284fec831365a49dd5aa` |
| Production OS probe | `a9146223041cf653194160826d3647d4de693b975e1b86cdf8f80b3f9abb7c75` |
| New tests | `e797937eb730a0eb26eb8329a4fad6099aa2bf069c6c59030d2a1e07c9f1d2a3` |
| Private production binding (no secret values) | `285abf34a087b2b364abd0224174e720cd6162647ba8555d98b6b3883ee0106a` |
| Production host pin | `c9eac033de68d2fca971c5e77bb9cfd72343a7703ca4a266d0cb3f03c265ddfe` |
| Existing global Codex configuration | `66eb0479fc51a914aae18782cda79de5d107a57fa7ac88f20d938703f6ac3905` |
| Original private snapshot | `42b72f63085b348f7b3a9251917881d9ad4c5c6d96a60346d8e404aa8eb673a1` |
| Original private result | `dc6f29cc0b15bcbff44ff42ffd343baa42fa0b34dff90b9eb1ee92287e81c9fd` |

The remaining imported-source/canary pins are explicit in the launcher. A later
execution supervisor must itself be reviewed/pinned before activation; these
hashes are evidence, not protection against the administrator changing this source.

## Next approval boundary

Prepare a bounded, explicitly armed one-report execution path and its private
audit/receipt handling; then separately activate the server writer/gate and run
one authorized public report with preservation checks. Keep the six-hour schedule
disabled until that controlled result has been validated and scheduling is
explicitly approved. No public trial is implied by this preparation approval.
