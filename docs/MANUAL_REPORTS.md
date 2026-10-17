# Manual Home Energy reviews

## Current state

Scheduled reporting is disabled. The manual client, analyzer, UI compatibility
update and activation helper are prepared locally. The server must be activated
before the new commands can be used. No public report was requested by this
conversion. Existing reports and all automation evidence are retained.

Release staged on 17/10/2026 at
`/home/shini/homeenergy-manual-release.n4gWcpxL`; sudo activation is pending.
The Windows task remains disabled and its definition is unchanged. Checks found
all 605 existing private PC evidence/workspace files unchanged. Windows checks:
71 tests passed, 3 platform-specific skips; Linux: all 14 synthetic manual tests
passed. UI contract tests and existing-card expansion/translation checks passed.
The optional skill YAML validator is unavailable (PyYAML is not installed);
frontmatter and installed/source equality were checked without installing it.

Ask in the current chat, for example:

- Review the last 6 hours against the previous 7 days.
- Review the last 50 hours against the previous 30 days and tell me what stands out.
- Publish that review to Energy insights.

The first two mean a chat response, not a public post. A request to review and
publish permits one post. No extra model session, API key, subscription, daemon,
scheduled task or expiring local grant is involved. The selected chat model does
the interpretation. Codex updates no longer need reporter hash-pin updates for
this path; ordinary connectivity/Python/SSH failures can still occur.

## Components

| Component | Purpose/location |
| --- | --- |
| `server/insights_manual_client.py` | PC: ordinary Python CLI using Windows SSH and the existing report-only key. No Codex imports or hashes. |
| `server/insights_manual.py` | Server: selectable-period analysis and optional private snapshot preparation. Reuses the existing reader/integration. |
| `server/insights_manual_entry.py` | Server: fixed SSH command dispatcher, installed under `/opt/homeenergy-reports`. |
| `server/insights_publish.py` | Existing server writer, unchanged: validated, translated, append-only reports. |
| `insights.js` | Existing Energy insights display with variable-period support; old reports remain compatible. |
| `docs/REPORTING_SKILL.md` | Instructions mirrored to the local `home-energy-report/SKILL.md`. |
| `deploy/activate-report-manual.py` | One-time, backup-first switch of the reporting SSH entrypoint and two frontend files. |

PC repository: `C:\Users\anest\Documents\New project\home-energy-repo`.
Existing key/host identity: `C:\ProgramData\HomeEnergyReporterProduction\id_ed25519`
and `known_hosts`. Never copy these into Git or print the private key.
Public feed: `/var/lib/homeenergy-reports/public/index.json`.
Private archive: `/var/lib/homeenergy-reports/reports.sqlite3`.

## Comparison method

Defaults are 24 hours and 7 preceding days. Integer hours 1–168 and days 1–90
are supported. An explicit end time permits historical reviews. Only complete
minutes up to that end are read; the baseline ends when the review starts.

For reviews up to 24 hours, compare equal-duration windows ending at the same
Athens clock time on previous dates. For longer reviews, use consecutive,
non-overlapping blocks of the same duration within the requested baseline.
Any incomplete oldest block is excluded. Thus 50 hours against 7 days uses
three comparable 50-hour blocks, not a comparison of a 50-hour total to a
seven-day total. Clock changes can affect boundary alignment; no seasonal or
weekday correction is claimed.

Each metric needs at least three baseline windows with 90% recording coverage.
The reference value is their median. Missing readings remain missing, not zero;
partial totals are not extrapolated. The private response includes hourly
review buckets to help identify when behaviour changed. These are not raw
device fault logs or weather observations. The `baseline_days` metric key is
retained for schema compatibility but counts qualifying windows; the new
`baseline.windows` field supplies the correct denominator.

## Commands and authority

From the repository:

```powershell
python -B server/insights_manual_client.py review --hours 12 --baseline-days 14
python -B server/insights_manual_client.py prepare --hours 12 --baseline-days 14
```

`review` reads evidence only. `prepare` stores a private snapshot for a requested
publication; it does not publish, prune old snapshots or change existing reports.
Publish the translated JSON on stdin with `publish --confirm-publication` only
when the owner explicitly requests publication. See the skill for its compact
submission contract. A saved snapshot lasts 30 minutes; an expired one needs
preparation again with the same requested review end and fresh evidence review.
Do not silently shift an already-discussed review to a new period.

SSH accepts only `manual-review`, `manual-prepare`, and `manual-publish`. Distinct
names ensure an unactivated old server rejects the request without running the
legacy preparation. The account still cannot open a shell, transfer arbitrary
files, forward ports, use sudo, edit the site/history or control equipment.
This does not sandbox the administrator chat itself. No absolute security or
narrative-accuracy guarantee is implied.

The unchanged writer validates JSON, required languages and plain text; derives
severity/numeric facts from its snapshot; refuses replacement reports; and
retains a five-minute publication cooldown and twelve-per-day limit. A network
timeout after publishing is uncertain, not permission to blindly retry. Inspect
the feed and stop. No rolling 30-day/120-attempt runner grant applies here.

## Activation and retained files

Stage only the helper, the two new server modules, `index.html`, and `insights.js`.
Run the helper with `--check` first, then `--activate` with sudo. It privately
backs up the changed files and the existing report database/feed. It switches
only the reporting account's fixed ForceCommand target, retaining all SSH
restrictions, and reloads SSH. No Nginx/application/device restart is needed.
The entrypoint change also makes the old automated runner's command names invalid.

After activation: verify read-only review, both public feeds, existing report
IDs/content, and the disabled Windows task. Do not create a test public report.
If activation fails, the helper restores the old SSH/frontend bytes; new unused
modules and all backup evidence are retained rather than deleted. A manual
rollback can use `--rollback BACKUP_DIRECTORY`.

Nothing is deleted by this conversion. The old task, Reporter workspace,
production/staging credentials, grants, exception records, run outputs, old
modules, old runbooks and deployment backups remain. Review a separate exact-path
cleanup list only after the manual workflow is working. Do not remove the whole
ProgramData directory: it contains the still-used reporting credential.

## Local checks

```powershell
python -B -m unittest discover -s server -p test_insights_manual.py
python -B -m unittest discover -s server -p test_insights_publish.py
python -B -m unittest discover -s server -p test_insights_analysis.py
node server/test_insights_ui.cjs
```

Tests use temporary synthetic state, not production reports or existing evidence.
