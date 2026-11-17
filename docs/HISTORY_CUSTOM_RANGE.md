# Custom History dates

Status: deployed and verified on both public HTTPS hostnames on 17 November 2026.
Only Nginx was reloaded; Overview, reports and historical records were preserved.

## Behaviour

- Custom sits alongside Day, Week, Month and All time. Start and end are date-only,
  entered as DD/MM/YYYY or chosen with the existing keyboard-accessible calendar.
- Both dates are included in Europe/Athens time. Internally the range ends at the
  next day's midnight, so daylight-saving days retain their actual 23 or 25 hours.
- The end cannot precede the start; equal dates are valid. Future and malformed
  dates are rejected. Invalid input never swaps dates or replaces the loaded data.
- Opening Custom and editing dates do not load readings. Apply loads the selected
  range using the existing cancellable cache and eight-request limit. Switching
  presets closes the editor; returning to Custom restores its draft.
- Calendar dots still indicate available readings. Range shading and endpoints
  are separate, and an end picker disables dates earlier than the start.
- Energy totals use the original minute records and snapshot cutoff. Missing
  readings remain gaps, not zero. Today's selection includes only recorded time.
- Auto chart averaging is one minute for 1–2 calendar days, 15 minutes for 3–10,
  one hour for 11–90, and six hours for longer spans. Zoom and manual averaging
  remain available. There is no new hard range limit; only indexed recorded days
  are requested. Long selections still transfer and retain their minute records,
  so coarser charts do not make their network or memory costs disappear.
- English, Greek and Russian are supported in light and dark mode. On small
  phones the date fields stack and the five period buttons use two rows.

## Files and verification

`history-custom-range.js` owns draft/apply state, validation, translations and
custom bounds. `history-custom-range.css` contains History-only styling.
`history.html` loads them; `history.js` integrates the range with existing data
and rendering. `history-calendar.js` adds optional lower bounds and range
highlighting, with separate accessible IDs for each calendar instance.

`server/test_history_custom_range.cjs` covers invalid/equal/future dates, inclusive
bounds, leap years, year boundaries, both Athens DST transitions, empty/partial
data, unchanged raw totals, draft/apply separation and translations. The retained
calendar test also covers minimum dates, keyboard bounds, highlights and IDs.
An older HTML page can still load the updated History script without the new
custom controls; the deployment compatibility regression covers that case.

Twelve relevant Node test scripts and syntax/diff checks passed. Browser checks
covered six widths (320, 393, 600, 768, 1024 and 1440px), three languages and both
themes, plus twelve calendar combinations at phone/tablet widths. No input
clipping, control overlap, page overflow or browser errors were observed. Presets,
all five chart views, unavailable days, partial today and keyboard dismissal were
also exercised. These are desktop-browser responsive checks, not physical-device
certification.

Production checks exercised both `home.shin1gamix.com` and `home.shin1gamix.gr`
at 393, 768 and 1440px in all three languages and both themes (36 combinations),
plus 24 phone/tablet calendar checks. Reversed/future dates were blocked, equal
dates worked, calendar choices stayed drafts until Apply, and preset switching
retained drafts. Screenshots were visually inspected; no control overlap, clipping,
page overflow or browser errors were observed. This used existing headless Chromium
through bundled Playwright because in-app Browser controls were unavailable.

A local synthetic 108-day selection contained 75 recorded days and 106,560 rows.
It finished in approximately 1.8 seconds, rendered 303 six-hour points and reached
at most eight simultaneous requests. This used mixed cache state and is not a
production-speed claim. No production requests or data writes were needed.

Preview: `http://127.0.0.1:8766/history.html`, served by the existing synthetic
history preview. Screenshots and browser results are kept outside the repository
under `C:\Users\anest\Documents\ChatGPT\Home Energy\custom-range-preview-20261117`.

The deployment added only two exact Nginx asset routes and installed the
five History files. It kept Overview, reports, reporting access and history data
unchanged. See [the deployment procedure](../deploy/README-history-custom-range.md).
