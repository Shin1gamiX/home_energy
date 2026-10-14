# Production reporting: disabled preparation

This is a separate candidate from the successful private-history trial. It does
not change that trial's source, credentials, evidence, runtime pins or disabled
schedule. Preparing files is not permission to publish publicly.

## Current boundary

The new files are:

- `server/insights_production_publish.py`: fixed-command Linux wrapper, closed by
  a root-owned gate before report storage/history access or writer imports.
- `deploy/reports-production-sshd.conf.example`: candidate report-only SSH policy;
  staging this template does not install it.
- `deploy/prepare-report-production.py`: explicit, disabled-only administrator
  preparation and verification; no activation mode.
- `server/insights_production_mcp.py`: inert client library with an injected
  transport, not an executable live runner.

No new Windows account is required. A future dedicated production credential must
remain outside model-readable paths, separate from the existing staging key and
the administrator SSH key. No production Windows credential root or key is
created by this preparation.

## What the administrator preparation does

`--prepare-disabled` first checks the exact bundle and reviewed file/service
baseline. It then creates only:

1. A dedicated Linux `homeenergy-reporter` account/group, password locked and
   expired from creation, with a root-owned empty home at
   `/var/lib/homeenergy-reporter-home`.
2. `/opt/homeenergy-reports/insights_production_publish.py`, root-owned and not
   writable by the reporter.
3. `/opt/homeenergy-reports/production-gate.json`, root-owned with
   `public_publishing_enabled: false`.
4. A private root-owned preparation receipt and baseline evidence directory.

It leaves the existing report storage root-owned, the public feed empty, and
the existing analysis/publisher/translation module bytes untouched. It installs
no SSH key or policy, creates no database, requests no report, and reloads or
restarts no service or device. It does not access Windows scheduling.

Effective access checks run a fixed local probe after explicitly dropping UID,
GID and supplementary groups. Probe execution errors are not counted as denials.
The checks include protected-file write denial and private-directory traversal
denial. The setup also invokes the wrapper with the fixed `prepare` command
while its gate is false, and verifies that execution stops at the gate without
entering the writer/history code. It must return the exact generic refusal and
leave report storage unchanged. This does not prepare a report. Sudo denial must
match the exact C-locale denial message; either observed
exit code 0 or 1 is accepted only with that message and valid sudoers syntax.

`--check` is the read-only pre-install check. `--verify-disabled` verifies a
completed preparation against its private receipt. If a check fails, preserve
all output/evidence and request review; do not rerun or remove resources blindly.
The helper never overwrites pre-existing preparation resources. A changed
service PID or protected hash requires fresh review, not relaxing validation.

## Runtime contract, still inactive

The eventual SSH boundary accepts only the exact commands `prepare` and
`publish`, never caller-selected commands, paths, destinations or configuration.
The root-owned gate must be enabled in a separately approved activation.
The wrapper checks the dedicated identity, fixed paths and permissions, then
executes the three hash-pinned source modules directly. It does not load their
cached bytecode. Python must run with `-I -B`.

Responses retain their real provenance:

```json
{"scope":"production","synthetic":false,"result":{"analysis_id":"...","analysis":{}}}
```

Publication returns the same envelope with `result` containing `status`
(`published` or `already_published`) and the matching `id`. Real data is never
relabeled as synthetic/private to pass an old validator.

The client validates schema 2, the canonical analysis digest, its completed
24-hour window and preceding seven-day baseline, coverage and localized text.
It freezes one analysis, rejects calls/responses outside a 240-second session,
limits upstream sends to four, and allows at most two publish sends with identical
canonical payloads. A separately bounded transport/launcher must enforce actual
wall-clock termination; this library cannot interrupt a blocked transport. A
malformed server response permanently fails that client session. An uncertain
transport exception retains the pending payload, so the lower-level API can
permit one identical retry. The protocol stream itself closes on rejection;
automatic retry is not implemented here.

Server-authoritative analysis and publication retain existing limits:
30-minute snapshot validity, five-minute publication cooldown, 12 reports per
day, a bounded latest-report feed, and no replacement of an existing report.
Preparing an analysis writes a private server snapshot and is not a read-only
diagnostic operation. No analysis is prepared or report published in this phase;
only the closed-gate refusal is exercised by the administrator setup.

## Separate activation work still required

Before unattended publication, explicitly approve and verify:

1. A dedicated production key and host pin, outside model-readable paths.
2. Installation and effective verification of the production SSH policy, with
   real client tests for allowed commands and denied shells, forwarding, file
   transfer, unrelated writes and credential access.
3. Restricted report-state ownership and the root-owned publication gate.
4. A production launcher with source/config/runtime pins, secret-free evidence,
   restricted model tools, disabled inherited notifications/delegation, bounded
   output and fail-closed checks. The inert client library is not that launcher.
5. One explicitly authorized controlled public report, followed by feed and
   preservation verification.
6. A separately authorized six-hour schedule activation.

The existing `check-report-isolation.py` targets the earlier direct-publisher
command and old sudo-denial semantics. It must not be used unchanged to certify
the new production wrapper; use a separately reviewed production audit.

Do not reuse the administrator key for the reporting process. Do not assume a
prompt, skill, working directory, or visible UI permission label establishes
runtime confinement. Existing private-trial evidence certifies that reviewed
trial, not these unactivated production components.

Application-level immutable reports do not protect against arbitrary code
already executing as the report-writer UID. The forced-command boundary, pinned
trusted code, filesystem permissions and restricted model runtime must work
together. Schema validation also cannot prove that every sentence of generated
prose is factually correct or privacy-safe; retain reporting instructions and
review the first explicitly authorized live result.

## Local verification

Run the three `test_insights_production_*.py` suites using the already installed
Python runtime, plus the existing insights regression tests. These tests use
temporary data, injected transports and mocked privileged operations. They do
not certify Linux ACLs, effective SSH configuration, real authentication, a model
run, or public publication; those require the later scoped checks above.

## Preparation evidence: 14/10/2026

- All 97 new production tests passed (43 preparation, 24 wrapper, 30 client).
- Full insights regression suite: 857 tests run, 839 passed, 18 platform skips.
  Printed provisioning/reload messages in that suite came from mocked fixtures,
  not server operations.
- Independent wrapper/client/preparation reviews found no blocking issue within
  the disabled-preparation scope. A disappearing live-exporter temporary file
  during an access audit can still cause a safe failure requiring review. Each
  batch is bounded, but a very large tree can make the full audit slow.
- Existing private-trial source and snapshot/result hashes, global Codex settings
  and the disabled Windows task were reconfirmed unchanged.
- The four-file bundle was uploaded to private mode-0700
  `/home/shini/homeenergy-production-release.GioFVQ8K`; each file is mode 0600.
  Server-side read-back verified bundle hashes, the exact protected-file/service
  baseline and the continued absence of production account/key/gate/SSH policy.
- No root preparation or activation was run. No model, public report, new key,
  schedule change, service/device restart, commit or push was performed.

Reviewed candidate hashes:

| File | SHA-256 |
| --- | --- |
| Preparation helper | `d0f3b66b5cdaa2aa80631fb14d8d28b21d1430a69614dac9d812567f6938c1a3` |
| Production wrapper | `2f0fe7714d4310eab60579e0c6a86007d70315f78912c1b62a23e2518d23745b` |
| SSH policy template | `84941a0f4ef89d7caf5f786b729c5cb82dda3407853d7a2293db3ecf17196b01` |
| Private release manifest | `8b2f07e63cd0385841bf42913d06c9345f499d8a085611ea0b402fe0175f0ffa` |
| Inert client adapter (local only) | `cb42392809b42112c87ca3722c9a0fa4a4a747b55f7960af06b02e62456377d7` |

Next administrator command, if proceeding with disabled preparation:

```sh
sudo /usr/bin/python3 -I -B /home/shini/homeenergy-production-release.GioFVQ8K/prepare-report-production.py --prepare-disabled
```

This creates the locked/expired Linux identity and closed-gate files described
above. Return its output for review. Do not follow it with activation, account
unlocking, key installation, public reporting or scheduler enablement.
