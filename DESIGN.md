---
name: Home Energy
description: Quiet home-energy workspace for present readings and recorded history.
colors:
  theme-accent: "#166453"
  theme-accent-dark: "#7dd9bb"
  theme-on-accent: "#fff"
  theme-on-accent-dark: "#10241c"
  theme-page: "#f3f6f3"
  theme-page-dark: "#0e1c1a"
  theme-surface: "#ffffff"
  theme-surface-dark: "#142623"
  theme-elevated: "#edf3ef"
  theme-elevated-dark: "#1b302c"
  theme-text: "#163833"
  theme-text-dark: "#e8f1ee"
  theme-muted: "#526b65"
  theme-muted-dark: "#b0c4bd"
  theme-border: "#d7e2dc"
  theme-border-dark: "#344c45"
  theme-hover: "#e2ece6"
  theme-hover-dark: "#294139"
  theme-focus: "#32826c"
  theme-focus-dark: "#a2e9ce"
typography:
  display:
    fontFamily: 'Inter, ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif'
    fontSize: "clamp(29px, 3.2vw, 44px)"
    fontWeight: 650
    lineHeight: 1.15
    letterSpacing: "-.035em"
  headline:
    fontSize: "24px"
    fontWeight: 650
    letterSpacing: "-.025em"
  title:
    fontSize: "23px"
    fontWeight: 600
    lineHeight: 1.3
    letterSpacing: "-.025em"
  body:
    fontFamily: 'Inter, ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif'
    fontSize: "14px"
    lineHeight: 1.7
  label:
    fontSize: "13px"
  measurement:
    fontSize: "29px"
    lineHeight: 1.25
rounded:
  compact: "6px"
  control: "8px"
  field: "9px"
  surface: "12px"
  menu: "16px"
  calendar: "20px"
spacing:
  xs: "4px"
  sm: "8px"
  md: "12px"
  lg: "16px"
  xl: "20px"
  surface: "24px"
  page: "32px"
components:
  action-secondary:
    backgroundColor: "transparent"
    textColor: "{colors.theme-text}"
    rounded: "{rounded.control}"
    padding: "8px 12px"
    height: "44px"
  date-field:
    backgroundColor: "transparent"
    textColor: "{colors.theme-text}"
    rounded: "{rounded.field}"
    padding: "10px"
    height: "44px"
    width: "128px"
  navigation-current:
    backgroundColor: "transparent"
    textColor: "{colors.theme-text}"
    padding: "16px 0"
  series-toggle:
    backgroundColor: "transparent"
    typography: "{typography.label}"
    rounded: "{rounded.control}"
    padding: "10px 12px"
    height: "44px"
  insights-surface:
    backgroundColor: "{colors.theme-surface}"
    textColor: "{colors.theme-text}"
    rounded: "{rounded.surface}"
    padding: "{spacing.surface}"
---

# Design System: Home Energy

## Overview

**Creative North Star: "Quiet home-energy workspace"**

A calm operating interface makes the home's present readings and recorded history easy to inspect. Mineral off-white, deep evergreen, opaque surfaces, fine outlines, and restrained system typography give measurements priority. Source colors and the existing brand remain recognizable.

This reference captures the implemented, user-approved Overview B and History concepts. Their page compositions are specific to those tasks; future pages should reuse the visual language without automatically inheriting a house diagram, an insights column, or a chart-first layout.

**Status:** deployed on 18/10/2026 to `home.shin1gamix.com` and `home.shin1gamix.gr`, with public-browser checks completed. The earlier design finish review disposition remains **SHIP — LOCAL PREVIEW**. Production checks covered Overview/History rendering, live telemetry availability, source popups, report links, themes, English/Greek/Russian labels, desktop/mobile layouts, asset delivery and preserved report bytes. This verifies the interface and delivery, not independent electrical-measurement accuracy or device-control operation; no restart or report publication was requested.

**Key Characteristics:**

- Opaque mineral and evergreen surfaces with quiet outlines.
- Measurements, units, coverage, and freshness remain visible.
- Purposeful page hierarchy with equivalent access in English, Greek, and Russian.

Source order matters: `interface.css` follows the inherited styles and owns the current Overview/History composition. Consult `styles.css`, `theme.css`, `history.css`, and `insights.css` for inherited controls; `app.js`, `history.js`, and `insights.js` own data semantics and interaction behavior. The approved direction is recorded in [implementation-brief.md](../../ChatGPT/Home%20Energy/design-concepts/2026-10-18/implementation-brief.md).

## Colors

Primary interaction color is evergreen in light mode and mint in dark mode. The `theme-*` frontmatter names correspond to the CSS custom properties; a `-dark` entry records the value selected by `data-theme="dark"`, not a second simultaneous CSS property.

Neutral page, surface, elevated, text, muted, and border roles separate content without decorative gradients. Hover and focus roles remain distinct from the selected accent. Use the complete theme pairing, including on-accent text.

Source colors are semantic, not a second branding palette: grid purple, solar gold, house teal, and battery direction/state colors are retained. Overview's light solar route/label uses its contrast-adjusted local override. History has its own metric and dark-palette mapping in `history.js`; do not substitute the interface accent for those series colors.

**The State and Label Rule.** Pair source and status color with text, units, selection state, or a visible mark; color alone must not carry the meaning.

## Typography

The existing Inter/system stack is used without a new font dependency. Inter is an optional local face; the system fallbacks carry the interface when it is absent. There is no decorative display-font pairing.

The display role is the Overview title; History uses its fixed desktop title (34px), and both use the mobile title (29px). The headline role describes the flow heading; the title role describes the latest review heading. The body role is editorial review prose, not a universal body-size override. Labels and metadata become smaller in dense chart regions, while primary readings retain tabular numerals and distinct unit text.

**The Measurement Rule.** Keep W/kW, V, A, %, and kWh attached to their actual quantities. Power formatting changes to kW at an absolute magnitude of 1000 W; voltage and current retain their own units.

## Layout

The shared container is centered with a maximum width (1500px). Horizontal gutters step from the page spacing token to surface spacing at 1200px, then to large spacing at 700px. Header navigation uses an underline; on mobile it occupies a complete row below the brand and utilities.

| Surface | Desktop composition | Responsive behavior |
| --- | --- | --- |
| Overview | Flow and insights columns use 1.9fr / 1fr, with a 330px minimum insights column and a 16px gap. | At 1200px the ratio becomes 1.7fr / 1fr with a 310px minimum; at 1000px it stacks. |
| History | Title and period controls share a band. Six totals form one strip above the dominant chart and compact mode timeline. | Title/controls stack at 1200px; totals become three columns at 1000px and two at 700px. |
| Chart workspace | Shared controls, one selected measurement view, and the mode timeline. Chart SVG height is 285px. | At 700px controls wrap and chart height becomes 250px. Monthly totals retain their accessible horizontal scroll region. |

Spacing names describe repeated implementation values, not an imposed universal grid. Mobile source cards continue to show PV1/PV2 power, voltage, and current, plus the available grid, house, and battery measurements.

**The Surface Scope Rule.** Preserve the approved compositions on Overview and History without turning their structure into a requirement for every future surface.

## Elevation & Depth

Main flow, history, summary, and insights surfaces are flat, opaque, and defined by borders. The current source cards have no shadow or backdrop blur. Menus and native dialogs retain their inherited shadows so temporary layers remain legible; their exact light/dark shadow values are in the sidecar.

Language-menu state transitions are brief (120–160ms). The flow animation follows the existing data/state logic; it is not evidence of fresh telemetry. Reduced-motion preferences disable CSS transitions/animations, and the JavaScript flow renderer also observes that preference. Retain the existing stale/offline flow suppression.

## Shapes

Primary surfaces and source cards use the surface radius. Controls use the control radius; selected chart-view buttons are slightly tighter, and date fields keep the field radius. Menus and the calendar retain their larger inherited corners.

Use fine borders for separation, small circles for semantic keys, and a narrow underline for the current page. A selected series includes an inset mark; a hidden series also has struck-through text. These are functional state cues.

## Components

- **Actions:** quiet outlined secondary buttons; insights actions have a minimum height (44px). Preserve the inherited hover, disabled, and keyboard-focus states.
- **Fields:** the date field accepts the displayed date format and works beside the native-dialog calendar. It is not a substitute for validation in the existing date logic. The averaging selector keeps its visible label and explicit focus outline.
- **Navigation and selection:** page links use `aria-current`; periods, chart views, and series use `aria-pressed`. Current page uses an underline; selected chart view uses accent/on-accent fill. Preserve keyboard inspection, zoom/reset, peaks, and the calendar.
- **Overview readings:** source cards open the existing last-12-hours power view. Retain PV1/PV2 power, voltage, and current; estimated grid power and voltage; house power/current; battery SOC, signed power, measured voltage, average current, and state. Positive battery power means charging; negative means discharging. Battery voltage uses the measured public-feed field and remains unavailable when that field is missing.
- **History summary and charts:** retain all six energy totals, including Battery charged. Show recorded coverage; missing readings break chart paths and do not become zero. Future time is labeled Later today or Upcoming, separately from recording gaps.
- **Insights:** the latest saved review leads an editorial column. Native disclosures retain the review window, limitations, provenance, evidence links, and access to older reports. Saved reviews are not live alerts.
- **Illustration:** `assets/solar-home.webp` is decorative, with empty alternative text and explicit dimensions. Its built-in-generation provenance is in `assets/solar-home.provenance.json`. It is a conceptual home, not a depiction of the actual installation.

**The Data Truth Rule.** Missing measurements remain unavailable; do not invent values, infer unsupported battery voltage, or present simulated preview readings as live telemetry.

The sidecar contains five compact, framework-free examples of actions, date entry, navigation, series selection, and the insights disclosure. Their CSS uses the source theme custom properties; the examples are a visual reference, not replacements for the application modules.

## Do's and Don'ts

### Do

- Do preserve all available electrical measurements, their units, signed battery direction, coverage, and freshness.
- Do keep English, Greek, and Russian translations, light/dark themes, keyboard access, and reduced-motion behavior.
- Do retain source-specific chart colors and visible selection cues.
- Do retain the illustration provenance and label simulated preview data clearly.

### Don't

- Don't convert missing measurements into zero or infer battery voltage from power/current. Older history without the measurement stays unavailable.
- Don't hide A, W/kW, or V readings to simplify a small-screen layout.
- Don't present future time as a recording gap or a saved review as a live alert.
- Don't treat Overview or History composition as a universal page template.
