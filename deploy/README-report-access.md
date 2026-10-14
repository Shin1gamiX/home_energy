# Production report-only SSH access, publishing disabled

This stage follows successful disabled preparation and receipt finalization.
The administrator's 14/10/2026 finalization output confirmed all original
checks passed. That receipt and its original evidence must remain untouched.

The owner approved preparation of dedicated production SSH access and tests,
with public publishing and both scheduling mechanisms still disabled. This is
not approval for a model run, report generation, public publication or scheduled
activation. No original staging/private-review source or runtime pin is changed.

## Windows credential

`prepare-production-key.ps1` creates only a new dedicated credential directory
at `C:\ProgramData\HomeEnergyReporterProduction` and an Ed25519 keypair. Directory
creation is atomic with an explicit DACL: current Windows user, SYSTEM and
Administrators only. Inheritance is disabled. Key files are checked for reparse
points, unexpected ownership/rules, extra hardlinks and unreasonable sizes using
metadata-only handles. Existing directories/keys are never replaced.

No new Windows account is used. The empty-passphrase key is protected by its DACL
and the future restricted model sandbox, not by encryption or an OS boundary
against other applications running as the same Windows user. A later production
launcher must establish that sandbox and deny the entire credential root.

The actual key was generated and verified on 14/10/2026. Only its public key may
be staged or installed on the server. Private key contents are never read into
assistant output, copied into the repository, or uploaded. Public fingerprint:

`SHA256:g71Kp7ghTZuR5okkCZL8QpDt+SJsMKpjn5ClhR07njw`

Public-key file SHA-256:

`54f417401aa4cda931ece3cc30173787b268cab7e420168a2beb761b6200926d`

`-VerifyOnly` checks the existing key without generating or modifying one. The
credential helper does not connect to a server, invoke a model or change tasks.

## Administrator activation boundary

The new `activate-report-access.py` loads the hash-pinned finalizer, recovery
and original preparation source directly from verified bytes, not bytecode. It
does not call any of their provisioning/recovery/finalization entry points.
It takes exactly the pinned production public key and original pinned policy
template; there are no caller-selected accounts, commands or target paths.

`--check` is read-only. `--activate-transport` performs:

1. Full original disabled-preparation verification, then a candidate SSH policy
   check in anonymous memory, preserving the original Include ordering. Both
   administrator and existing staging-account effective settings must match.
2. A fresh snapshot must equal the **original receipt's baseline**; changed
   services/includes/files are not adopted into a new approved baseline.
3. New private root-owned access evidence. Original evidence is not rewritten.
4. Exclusive installation of only
   `/etc/ssh/sshd_config.d/00-homeenergy-reporter-production.conf` and
   `/etc/ssh/authorized_keys/homeenergy-reporter`, both root:root mode 0644.
   The key has `restrict` plus the same forced command as the Match policy.
5. Full effective-policy, no-sudo, protected-filesystem and closed-gate checks
   while the account remains expired. Only SSH is reloaded.
6. Remove only the account expiry as the last enabling mutation. The password
   stays locked, identity/groups unchanged. Repeat the installed-state audit,
   preserve all other shadow fields in memory, and write completion evidence.

The forced command uses a 45-second timeout with a five-second kill grace and
isolated Python (`-I -B`). Shell commands, file transfer, TTY, forwarding, agent
forwarding, tunnels and user rc hooks are not permitted. The complete effective
policy, allowed environment and absence of a global SSH rc hook are checked.

The existing root-owned publication gate remains false. **Both `prepare` and
`publish` return the exact generic refusal.** An allowed SSH authentication is
not approval to read live history, create a private analysis snapshot or publish.
Storage remains root-owned and empty; no database or publication lock is created.

## Failure and rollback

Files use exclusive creation. The helper records each created file's device and
inode; rollback can remove only files created by that invocation which still
have the expected ownership, mode and complete bytes. An existing, changed or
partially written file is retained for review, never overwritten or deleted.

Rollback first tries to expire the account again, and independently attempts
revocation of its own key. The restrictive include is removed only when expiry
and key absence are both confirmed; otherwise it is retained. Exact new-file
removal is followed by SSH syntax validation/reload. Existing SSH sessions are
not terminated by account expiry, so the forced-command execution bound remains
important. All private evidence is retained even after rollback.

Do not rerun activation after failure. Keep the administrator SSH session open
until a fresh administrator connection and the separate real client tests pass.

## Remaining certification

Local tests and `sshd -T` do not establish successful client authentication. After
the administrator runs the reviewed command, test the dedicated key with pinned
host verification: exact authenticated gate refusal for `prepare` and `publish`,
and explicit rejection of harmless shell sentinels, file-transfer subsystems,
TTY and forwarding. Network/authentication errors are not proof of command denial.
Never probe by reading real secrets or issuing destructive commands.

The production launcher, model restriction preflight, public report trial and
six-hour schedule activation remain separate approval stages. This stage does
not modify global Codex settings, the existing Windows task or app automation.

## Validation and staged command (14/10/2026)

- 56 new server access-helper tests passed, including phase-failure injection,
  owned-inode rollback, retained restrictive policy on ambiguous rollback,
  original-baseline preservation and memory-only candidate configuration.
- 16 credential-helper tests passed. Metadata/DACL tests used synthetic files;
  the main flow was mocked. Separately, actual approved key generation and
  `-VerifyOnly` both succeeded without displaying private-key contents.
- Combined production suite: 230 tests, 229 passed, 1 existing Windows
  symlink-privilege skip. Full insights suite: 990 tests, 971 passed, 19 skipped.
  Provisioning/reload messages printed by these tests came from mocks.
- Independent review found and resolved baseline-refresh and rollback ownership
  issues; final re-review found no remaining blocker for staging. This is not
  a substitute for root checks or real client denial tests.
- Original helper/wrapper/template, global Codex configuration and the earlier
  private-trial snapshot/result hashes were rechecked unchanged. The Windows
  reporting task remains disabled (`Enabled=False`). No app automation was
  modified, no model run was performed, and no Git commit/push was made.

The staged directory is `shini:shini` mode 0700:

`/home/shini/homeenergy-report-access.zduokPzZ`

It contains only `activate-report-access.py` and `reporter-key.pub`, both mode
0600. The helper SHA-256 is:

`850600758a3ef63c290fb42aea5360d6000257f5600294c9dacfb7ee2a536cef`

The credential helper SHA-256 is:

`72d799ea72c24d6678d635e1c8713cd85bdf4ddd8698f23bc0e4a12ffd4c9763`

Source-only, unprivileged server inspection verified the uploaded helper/key,
prior pinned loader chain, original bundle/wrapper, manifest-protected hashes
and application service baseline. The root-owned completion receipt exists;
the gate is false; production key/policy/activation/database/lock are absent.
Root-only private evidence, shadow, effective-policy and permission checks are
pending in the administrator command below, which was **not run** by the agent:

```sh
sudo /usr/bin/python3 -I -B /home/shini/homeenergy-report-access.zduokPzZ/activate-report-access.py --activate-transport
```

Keep the administrator session open and return all output. Do not close it or
rerun the command if activation/rollback fails. The newly generated private key
has not yet been used for production authentication. Real client tests follow
only after successful administrator activation output.
