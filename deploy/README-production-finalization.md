# Disabled production preparation: receipt finalization

The administrator ran the expiry recovery successfully on 14/10/2026. Its output
confirmed expiry day 1 and all original partial disabled-state checks passed.
No completion receipt was created by that narrowly approved recovery. The owner
then separately approved preparing receipt finalization with publishing,
scheduling and SSH access still disabled.

`finalize-report-preparation.py` loads the previously staged recovery source by
its fixed SHA-256, without importing cached bytecode or invoking recovery/main.
That loader in turn verifies the original preparation source and manifest. No
earlier helper, wrapper, evidence or release file is modified.

## Exact scope

Before any write it requires the original UID/GID, home, shell, sole primary
group, locked password and expiry exactly 1. The recovery's retained-state
checks verify the original root-owned evidence, closed gate, empty report state,
absent receipt/key/SSH policy and protected file/service baseline. The original
partial disabled-state verification then runs again. Account/shadow fields are
compared only in memory; neither passwords nor hashes are printed or saved.

`--check` stops after those read-only checks. `--finalize` creates only:

`/opt/homeenergy-reports/production-preparation-receipt.json`

It uses the original receipt format, root ownership, mode 0600 and exclusive
creation. It then fsyncs the parent directory, runs original full verification,
and checks the receipt bytes, account and evidence again. The receipt refers to
the existing `homeenergy-production-backup.9oe5dgb5`; no replacement baseline or
new evidence directory is created.

An existing receipt is refused, including symlinks or directories. A partial or
unverified receipt is never overwritten, deleted or treated as success. If any
post-write check fails, retain the receipt for administrator review. Progress
messages distinguish the pre-write checks from creation/full verification.

No account command, expiry change, service reload/restart, key/SSH installation,
database creation, model call, publication, schedule activation or Git operation
is part of finalization. Root-only execution remains pending until the owner
runs the separately staged command and returns its output.

After confirmed success, the original helper's read-only `--verify-disabled`
mode can validate the completed preparation. This receipt does not authorize or
certify future production SSH access, a public model run or unattended scheduling.

## Staging and validation evidence (14/10/2026)

- The finalizer and regression tests received an independent agent review.
- Finalizer suite: 36 tests, 35 passed and 1 Windows symlink-privilege skip.
- Combined production suite: 158 tests, 157 passed and the same 1 skip.
- Complete Python insights suite: 918 tests, 899 passed and 19 skipped. These
  are local tests, not certification of privileged server execution.
- The existing Windows task `Home Energy Reporter - Dummy` remains disabled
  (`Enabled=False`). No model test or public publication was performed.

Staged helper:

`/home/shini/homeenergy-preparation-finalize.JFDO36Uw/finalize-report-preparation.py`

SHA-256:

`a21a7ad2d5d1a078eea57842703bbe476b796c1a710ba69cfc5dbbf2c3e311f5`

The new directory is `shini:shini` mode 0700; the single-link helper is
`shini:shini` mode 0600. Uploaded bytes match the local source. A source-only
read-only Linux inspection verified the pinned loader chain, original bundle,
public account identity, manifest-protected file hashes, service baseline,
closed gate, installed wrapper and absence of activation resources/receipt.

An unprivileged attempt to inspect the complete preserved snapshot could not
read `/etc/ssh/sshd_config.d/50-cloud-init.conf`. This is a privilege boundary,
not a passed check: the original full SSH include, shadow and private evidence
checks remain pending for the administrator invocation below. No checks in the
finalizer were relaxed or bypassed.

```sh
sudo /usr/bin/python3 -I -B /home/shini/homeenergy-preparation-finalize.JFDO36Uw/finalize-report-preparation.py --finalize
```

This command has been provided for administrator execution, not run by the
assistant. Return its output before proceeding. On failure, preserve every
resource and do not rerun provisioning or remove a possibly created receipt.
