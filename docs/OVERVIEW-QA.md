# Overview redesign verification (2026-09-27)

Approved design: retain the light theme, place the existing house illustration at
25% opacity, and connect source/receiver metric boxes directly with moving dots.
Only the overview frontend was deployed; backend calculations and history data
were not changed.

## Automated checks

- `node --check app.js` and `node --check history.js`: passed.
- `node server/test_overview.cjs`: 13 routing/battery-state assertions passed.
- In-app browser: 72 local combinations (320, 390, 430, 540, 768 and 1080 CSS-pixel
  widths; English, Russian and Greek; grid/waiting, solar/charging, solar+battery,
  battery-only) had no scene-card overlap, card overflow or horizontal overflow.
- Local synthetic endpoint: stale and failed data hid flow paths and showed the
  existing communication warning; fresh data restored routing.
- Live overview: received fresh readings, showed the grid-to-house path and
  waiting state, retained voltage/SOC, and hid zero solar current and idle battery
  watts. No relevant browser console errors were observed.
- Live history: four chart groups and period summaries loaded successfully.

## Visual review

Compared the approved concept with in-app-browser desktop/mobile captures:

| Area | Result |
| --- | --- |
| Palette | Light mint background, white cards, blue grid, amber solar/waiting, teal house |
| Illustration | Original code-native SVG retained, CSS opacity exactly 0.25 |
| Routing | Direct source-card to destination-card endpoints, no detour around the house |
| Typography | Compact readable values, preserved W/kW/V/A units and localized decimals |
| Cards | Four-column desktop / two-column phone summary, battery A omitted below |
| Translations | New waiting/discharge labels translated, longer labels wrap safely |
| Responsive controls | Phone header explicitly uses two rows to avoid stray wrapping |

Intentional differences from the raster concept: use the original SVG illustration
and live data rather than generated artwork/sample numbers; preserve the existing
active-source green tint; retain a compact battery-current reading in the scene
when power is active. There are no invented sparkline histories. The comparison
image combines desktop/mobile views, so each implementation viewport was checked
separately rather than matching the full comparison-board dimensions.

## UI refinement QA (02/10/2026)

This refinement retains the existing design system and code-native artwork;
it does not introduce a new generated concept. It supersedes the older notes
above about blue grid, always-visible summary cards and a fixed two-row header.

- In-app Chromium checks covered desktop, narrow phone layouts and EN/RU/EL.
- Greek charging at 360 CSS pixels had no overlapping diagram boxes or page overflow; Russian charging and expandable readings were checked too.
- History controls exercised: select/clear all, legacy voltage, day/week/month, Today, five-minute averaging, peaks on/off, chart jumps, keyboard inspection and recorded-time fitting.
- All four numeric charts showed the same inspected minute. Rounded positive/negative current ticks included zero. Dense synthetic mode changes opened a smaller shared time range without losing the original intervals.
- Node regressions cover routing, power-dependent flow speed, inverter/source distinction, energy residual bounds, cached snapshot boundaries, rounded axes, timeline grouping and fit bounds. All 15 existing Python backend tests pass.
- Production deployment is frontend-only: no collector, Home Assistant or inverter configuration changes. Public files must be hash-checked against the release and the public pages checked again after upload.

## Limitations

Routing indicates estimates, not independently measured branch power. Solar to
battery assumes grid charging is disabled. Waiting-to-charge is a presentation
rule using the owner's 40% return threshold, not a confirmed BMS status.
Synthetic solar/discharge scenarios test rendering, not physical inverter
behavior. Browser testing used Chromium through the in-app browser, not physical
iOS/Android devices. Reduced-motion handling is provided by the existing global
CSS animation override; it was not tested on a physical reduced-motion device.
