---
name: home-energy-report
description: Review Home Energy history over a user-selected period, compare it with earlier patterns, and optionally publish to Energy insights when explicitly requested. Not for equipment control or deployment.
---

# Manual Home Energy reviews

Work in the current conversation with its selected model. Do not start another
model, enable a schedule, use the old recurring runner, renew grants, or check
Codex/OS executable hashes. Scheduling is disabled; existing reports stay visible.

## What the user wants

- "Review the last 12 hours against the previous 14 days" means analyze and
  explain in chat, not publish.
- "Review and publish" authorizes one public report. "Publish that review"
  authorizes publication of the discussed evidence, not an unrelated new period.
- Defaults when omitted: last 24 completed hours against the 7 days immediately
  before that review. State the periods used. Support 1–168 integer hours and
  1–90 comparison days; explain that larger requests need a tool extension.
- Dates/times are Europe/Athens; show day/month/year. Comparisons exclude the
  reviewed period. Requests about an earlier period need an explicit end time.

## Tools

Repository: `C:\Users\anest\Documents\New project\home-energy-repo`.
Client: `server/insights_manual_client.py`, ordinary Python 3, standard library.
Run from the repository, not the old isolated Reporter workspace:

```powershell
python -B server/insights_manual_client.py review --hours 12 --baseline-days 14
```

Optional `--end 2026-10-17T12:00:00+03:00` uses an explicit offset at a whole minute.
The command prints evidence; it does not save files or database snapshots.

The server activation for the manual protocol must have completed. The new
`manual-*` SSH commands are deliberately rejected by the old server without
starting its legacy preparation. If unavailable, explain the blocker; never fall
back to the old runner, administrator SSH, direct database/feed edits, or device
control. This skill does not authorize installation, activation or repair.

The client uses the existing report-only SSH key and known-hosts file in
`C:\ProgramData\HomeEnergyReporterProduction`. Never read or print key contents.
SSH verifies the server identity; the server forces fixed report operations and
retains the append-only writer. This is a report permission boundary, not a claim
that the whole administrator chat is sandboxed.

## Review

Check the actual window, freshness, coverage and available comparison windows
before interpreting numbers. Read the hourly context to find when changes happen,
not just whether a whole-period threshold fired. Give a short bottom line, the
material observations with figures/times, uncertainty, and proportionate next steps.
No fixed number of findings is required. A routine review can simply say no clear
unusual pattern was found in the available evidence.

Up to 24 hours, baseline windows have the same duration and ending Athens clock
time on earlier dates. Longer periods use non-overlapping equal-duration blocks
within the baseline; an incomplete oldest block is excluded. At least three
90%-covered windows are required for a metric's median comparison. The
`baseline_days` metric field is historical naming: it counts qualifying windows;
`baseline.windows` is the number considered, not the requested calendar days.

Missing readings are not zero. Do not extrapolate partial totals. Daily timing,
weekday/season differences, daylight-saving boundaries and limited baselines can
limit conclusions. Battery supplied is discharge, not energy charged; solar totals
need not equal solar-to-house plus battery discharge within the same period.

Separate observations from possible explanations. Low solar does not prove bad
weather, shading or a fault. Do not infer occupancy, specific appliances, battery
capacity loss or an electrical diagnosis from these readings alone. If weather
or logs are needed to establish a cause, say so; do not invent them. A threshold
not being exceeded is not proof that equipment is healthy.

## Publish only when requested

1. Run `prepare` with the same hours/baseline/end options. This stores one private
   analysis snapshot and leaves the public feed unchanged. Use its returned
   analysis ID and facts. If the discussed review is now older, preserve its
   exact end instead of silently moving the window.
2. Compose this JSON (plain text, no HTML/links/control characters):
   `{"analysis_id":"...","summary":{"en":"...","el":"...","ru":"..."},"notes":[]}`.
   Summary: 1–600 characters per language. Optional notes: up to six unique
   existing finding IDs, each shaped as
   `{"finding_id":"...","commentary":{"en":"...","el":"...","ru":"..."}}`,
   with 1–400 characters per language. All translations must convey the same
   facts and uncertainty. Maximum UTF-8 payload 32768 bytes.
3. Pass that JSON on stdin to
   `python -B server/insights_manual_client.py publish --confirm-publication`.
   A local submission file may be created only when authorized; preserve it if
   used. Do not include credentials, private addresses, serials or logs.
4. Confirm the returned matching ID and published/already_published status, then
   verify the new ID in both public report feeds. If a timeout or failure leaves
   the outcome uncertain, inspect the public feed and stop; do not automatically
   retry or create another report.

The server fixes the numeric evidence and severity, requires all three languages,
and refuses overwrites. It does not prove that prose is true; check the narrative
against the evidence. Existing short server limits (five-minute publication
cooldown and twelve per day) remain as duplicate/spam protection, not a schedule
or a renewable authorization. No report is requested merely by installing or
testing this workflow.
