# Targeted disabled-reporter expiry recovery

On 14/10/2026 the disabled production preparation stopped in `account_disabled`.
The administrator's secret-free diagnostic reported password locked, expiry day
`-1`, inactivity `-1`. Thus the expected account expiry was missing; the warning
about Python 3.12 `spwd` was not the failed condition. The installed `useradd`
manual describes system accounts as created without aging information. This is
consistent with the observed state but is not an independently traced cause.

The owner approved only setting the existing account's expiry to 02/01/1970,
preserving its identity/evidence and repeating read-only verification. The
original preparation helper, release, manifest, wrapper and evidence remain
unchanged. Do not rerun the original `--prepare-disabled` command.

`recover-report-expiry.py` is scoped to:

- Original release `homeenergy-production-release.GioFVQ8K`, with pinned helper
  and manifest hashes, loaded from verified source bytes only.
- Original root-owned evidence `homeenergy-production-backup.9oe5dgb5`.
- Existing Linux account `homeenergy-reporter`, UID 996, GID 986, reviewed home
  and shell, no supplementary group and a still-locked password.
- A closed root-owned publishing gate, absent production key/SSH policy/receipt,
  empty report storage and the original protected-file/service baseline.

`--check` performs only the prerequisite inspections. `--recover` runs the single
fixed mutation `/usr/bin/chage --expiredate 1 homeenergy-reporter` if the account
is still unexpired. The installed `chage` manual accepts a numeric number of
days since 01/01/1970, so `1` is 02/01/1970. Already-correct expiry skips mutation
but does not bypass verification. Any other expiry state requires review.

The helper checks that expiry becomes exactly 1 and every other shadow/account
field remains unchanged; password data is compared only in memory and never
printed or saved. It then invokes the original partial `verify_disabled` against
the retained baseline. It does not create the missing completion receipt.

Failure leaves the account expired if the expiry change already succeeded. It
prints the original static validation message, not shadow records or subprocess
output. Do not remove evidence, refresh the baseline or relax a subsequent
failed check. Unrelated service drift or a transient exporter file can still
cause a legitimate later verification failure.

No new SSH access, model call, public report, schedule change, service/device
restart, source replacement or production activation is included. Completing
the preparation receipt and activating report-only access remain separate steps.

The recovery tests use mocked identity, shadow and subprocess calls. They never
run `chage` or provision accounts. Linux execution remains unverified until the
administrator runs the staged helper and supplies its result.

## Staged recovery: 14/10/2026

- 25 new recovery tests passed; all 122 production tests passed. Full insights
  regression suite: 882 tests run, 864 passed, 18 platform skips. Provisioning
  messages printed by the tests are mocked fixture output, not server actions.
- Recovery helper staged alone in private mode-0700 directory
  `/home/shini/homeenergy-expiry-recovery.fBPvTfCy`, with mode 0600 on the file.
- SHA-256 `fa1defe9bf19058bfa1b135b59463b6ba4557f37957cdb1ea7e15016a35b6851`
  matched the server copy. Non-root Linux inspection verified source-only loading,
  original bundle pins, account identity, protected file/service baseline and
  closed gate. Root-only preflight/recovery has not been run.
- Original helper/wrapper and private-trial snapshot/result hashes are unchanged.
  The Windows reporting schedule remains disabled. No Git commit/push was made.

Next administrator command:

```sh
sudo /usr/bin/python3 -I -B /home/shini/homeenergy-expiry-recovery.fBPvTfCy/recover-report-expiry.py --recover
```

Return the result for review. If a remaining check fails, do not rerun the
original provisioning command or undo the expiry correction.
