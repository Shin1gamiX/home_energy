# Overview seasonal themes

Seasonal themes are separate from the shared light/dark theme and from energy,
History, reports and equipment controls. Overview includes one module loader;
History and the 404 page do not include it.

| File | Responsibility |
| --- | --- |
| `themes/schedule.js` | Annual date windows in `Europe/Athens`, theme IDs and versioned asset URLs |
| `themes/theme-loader.js` | Load the active theme, recheck the date, handle failures and remove expired themes |
| `themes/christmas/theme.css` | Christmas palette, tree placement, snow and alternating bulb animations |
| `themes/christmas/theme.js` | Mount decorations and translated effects controls; remove nodes, observers and listeners on cleanup |

Christmas runs **every year from 1 December 00:00 to 1 January 00:00, Athens
time**. The end is exclusive: all of 31 December is included. The browser uses
its clock with the explicit Athens timezone, irrespective of the visitor's
timezone. An inaccurate device clock can therefore affect activation.

Open, visible pages check just after each minute boundary. Returning to a tab,
waking the device or restoring a page checks immediately. Background polling is
paused. Sleeping/offline devices cannot update until they resume and have the
required assets. A device must load the updated Overview once; no cookie or
cache clearing is required. Versioned script/style URLs avoid stale assets.

Outside the window only the small loader and schedule modules are requested;
Christmas CSS and JavaScript load on demand. The ordinary page remains usable
if those assets fail. Late downloads cannot activate an expired theme. Theme
removal stops effects, disconnects listeners/observers, removes decorative nodes
and the stylesheet, and restores the original appearance.

The theme keeps the existing readings, units, source colors and light/dark
selection. Its **Christmas effects** button appears in the Overview footer and
pauses both snowfall and bulb animation. The control and footer copy support
English, Greek and Russian. Reduced motion hides snow and uses static bulb
colors. Hidden/off-screen effects are paused. Nothing is saved in browser
storage, and no monitoring/control requests are made by theme code.

## Adding another theme

1. Add a folder under `themes/` with its stylesheet and JavaScript module.
2. Export `mount()` from the module; it must return a cleanup function. Keep DOM
   changes owned by that theme, and scope all styles/variables to its active
   class. Keep the shared `data-theme` light/dark setting unchanged.
3. Add a schedule entry with `start` and exclusive `end` month/day values. Ranges
   across New Year are supported. If windows overlap, the first entry wins.
4. Bump the affected asset versions and the schedule/loader versions referenced
   by their parent files. Add only the exact new Nginx asset routes.
5. Check date boundaries, cleanup, narrow screens, both appearances and all
   languages before deployment. Preview/date overrides belong in local tooling,
   never in production query parameters or browser storage.

Run `node --test server/test_seasonal_themes.mjs` for boundary, recovery, lifecycle
and page-scope tests. Deployment uses `deploy/activate-seasonal-theme.py`; see
`deploy/README-seasonal-theme.md` for scope, verification and rollback.
