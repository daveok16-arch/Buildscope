# WP3 Report — Visual/UX work package (U0–U8)

**Author:** OpenHands (engineer) · **Branch:** `wp3-u0-u8` · **Date:** 2026-10-09
**Rule:** read-only on data; every claim below has pasted output or a file path.
**Live preview:** local gunicorn on `127.0.0.1:12000` (shipped `data/oppintel.db`,
`/healthz` → `"status":"ok"`, `projects: 1261`, `permits: 4190`).
Render preview `https://buildscope-xppz.onrender.com` is referenced only where noted.

Every item is **PASS / PARTIAL / FAIL**, and PASS requires output for every sub-item.

---

## U0 — Device viewport truth + font floor — **PASS**

* 720×1600 DPR 1 is now a primary test viewport alongside 360/390/430/600/768/820/1440:
  `tests/test_accessibility.py:63` `OVERFLOW_VIEWPORTS = [360, 390, 430, 600, 720, 768, 820, 1440]`
  and `:70` `"tablet": {"width": 720, "height": 1600}`.
* Overflow re-run this session, `document.documentElement.scrollWidth > innerWidth + 1`
  across 17 public routes:
  ```
  390x844:  overflow routes=NONE
  720x1280: overflow routes=NONE
  1440x900: overflow routes=NONE
  ```
* Font floor script `audit/wp3/font_sizes.py` (flags every computed size < 12px):
  ```
  TOTAL elements under 12px: 0
  ```
  The only element that sat below the 12.5px caption floor was the narrow-screen footer
  legal notice (0.78rem = 12.48px); raised to 0.8rem = 12.8px (`app.css`). Verified
  `legal px: 12.8px` at 390px.
* Mobile patterns below 900px (filters in a bottom sheet, drawer nav, compact footer):
  confirmed live (`#mobile-filter-btn` opens `.filter-sidebar` as a sheet; `#mobile-menu-open-btn`
  opens the drawer; footer compacted — see U8). Two card columns only in the 640–900 band
  (`.card-grid` min 320px track, single column ≤ 640px, `app.css`).
* Screenshots: `audit/wp3/u0_signin_{390,720}.png`, `u0_signup_{390,720}.png`.

Residual: tap-target audit still reports sub-44px controls at 1440 on dense pages
(`audit/wp3/gate_probe.py`: `/opportunities` 45, `/companies` 66). These are inline text
links exempt under WCAG 2.5.5 and desktop-only; not a mobile gate failure. Tracked in
WP4 candidates.

## U1 — City picker below the footer — **PASS**

**Root cause (file:line).** `src/oppintel/app/static/js/controls.js:106–145`. On open the
combobox did `document.body.appendChild(pop)` for *all* widths and relied on a CSS-scoped
sheet. When the sheet styles did not apply, the popover stayed a child of `<body>` and
flowed in normal document order — i.e. after the footer. The fix keeps the popover inside
its `position:relative` wrapper at ≥ 900px (`controls.js:135–141`) and only reparents to
`<body>` for the `< 900px` bottom sheet (`controls.js:123–132`).

Evidence (`audit/wp3/repro_u1.py`, `probe_u1_open.py`):
```
city pop closed: {'hidden': True, 'display': 'none', 'position': 'absolute',
                  'top': 0, 'h': 0, 'width': 0, 'inViewport': True, 'zIndex': '60',
                  'parent': 'DIV.bs-combobox'}
390 open: hidden=False top=169 bottom=844  inViewport=True belowFooter=False position=fixed  parent=BODY
720 open: hidden=False top=369 bottom=1280 inViewport=True belowFooter=False position=fixed  parent=BODY
1440 open: hidden=False top=689 bottom=1029 inViewport=False belowFooter=False position=absolute parent=DIV
```
* Closed by default and fully hidden when closed (parent is the field, not the footer).
* Open: popover under the field at ≥ 900px; branded bottom sheet < 900px (fixed, backdrop,
  focus trap via `partials/dialog_a11y.html`, inert background, Escape + backdrop dismiss,
  focus return, applied chips + active-filter bar).
* Screenshots: `audit/wp3/u1_open_{390,720,1440}.png`.

## U2 — No native controls left — **PASS**

`audit/wp3/count_selects.py` (visible `<select>` per route, expected 0):
```
/opportunities   visible <select>: 0
/companies       visible <select>: 0
/changes         visible <select>: 0
/analytics       visible <select>: 0
/reports         visible <select>: 0
/signin          visible <select>: 0
/signup          visible <select>: 0
TOTAL visible selects: 0
```
Sort Order is a segmented control, City/Type/Status/Signal are comboboxes, dates are native
`input[type=date]` at ≥ 44px plus preset chips. The native `<select>` is retained in the DOM
as `select.bs-select-native` (visually hidden) so the no-JS path still works — proven by the
no-JS probe (U7) showing `native_selects_visible: 6` with JS off.
Screenshots: `audit/wp3/live_{390,720}_sort.png`, `live_{390,720}_city_picker.png`.

## U3 — Titles — **PASS**

Display-layer cleaner `src/oppintel/app/display.py` (`clean_title`, `_looks_like_a_note`,
`_fallback_title`). Sweep over the whole database (`audit/wp3/title_sweep.py`, read-only):
```
projects: 1261
names with an internal marker: 22
  cleaned to a real headline:  22
  still a note (need fallback): 0
```
Rules strip a leading `<token> MEETING <date|TBD> (<time>) -`, `<token> ,,,`, `X TEAM ///`,
repeated punctuation and spacing artifacts; a residual note falls back to work description →
permit type → address. Cards clamp to 3 lines; full title in tooltip + dossier.
Examples: `#132 'QTEAM MEETING TBD - QTEAM CONSTRUCT …' -> 'Construct (1) New 4-STORY …'`,
`#482 'X TEAM /// Aquatic Bldg 1/// …' -> 'Aquatic Bldg 1 / the Project Consists of …'`.
Screenshots: `audit/wp3/u3_list_{390,720}.png`, `u3_detail_*_{390,720}.png`.

## U4 — "Verified" wording — **PASS**

Grep of the live HTML for the banned phrases returns nothing:
```
$ curl -s :12000/ | grep -oiE "verified october|last verified|collected [a-z]+ 2026" | sort | uniq -c
(no output)
```
Templates now read `Collected {{ project.last_verified | collected }}`:
`templates/partials/opportunity_card.html:124`, `templates/opportunities/detail.html:34,478`.
The old phrases are in the banned-phrase test (`tests/test_copy_honesty.py`).
Screenshots: `audit/wp3/u4_detail_{390,720}.png`.

## U5 — Trends — **PASS**

* Cards: number, label, one-line basis; full definition inside a collapsed `<details>`;
  equal heights; 2×2 grid on narrow screens (verified in `audit/wp3/capture_u5.py`).
* Change investigation (`audit/wp3/u5_changes_probe.py`, DB copy):
  ```
  project_change by kind: [('new_project', 1261)]
  distinct projects changed: 0
  total change rows: 1261
  ```
  `new_project` is a first observation, not a change, and is excluded from "projects with a
  detected change" — so the corrected count is **0** (the earlier "533"/"442" came from the
  snapshot-restore diff; the normaliser was tightened in U5/commit `9878215`). Live Trends now
  shows `Projects with a detected change = 0`.
* Window toggles with no distinct data are disabled with a one-line reason.
* Chart: month labels, value labels, axis, text-alternative table, draw-in (`.bar-chart-bar`
  `scaleY` transition, `app.css:1456–1465`).
  Screenshots: `audit/wp3/u5_trends_{390,720,1440}.png`, `live_{390,720}_trends_{top,chart}.png`.

## U6 — Numbers and labels — **PASS**

One snapshot, same minute (`/api/statistics` + `/healthz`), with definitions:

| Surface | Metric | Value | Definition |
|---|---|---|---|
| home | Public projects | 133 | project rows with a public permit link |
| home | With mechanical evidence | 8 | `mechanical_hvac_evidence` set |
| home | Permit records | 236 | linked permits for public projects |
| home | Cities with projects | 15 | cities with ≥1 public project |
| /api/statistics | projects_public | 133 | same as home (one source) |
| /api/statistics | projects_total | 1261 | all assembled projects |
| /api/statistics | projects_with_mechanical_evidence | 8 | same |
| /api/statistics | permit_records | 236 | same |
| /api/statistics | permit_records_all | 4190 | all collected permit rows |
| /api/statistics | evidence_records | 14564 | evidence rows |
| /api/statistics | active/configured jurisdictions | 15 / 18 | cities serving / configured |
| /healthz | projects / permits | 1261 / 4190 | database totals |

Home and `/api/statistics` now agree because both read the one snapshot
(`market_stat_snapshot`). Trends reports windowed definitions (e.g. "Permit records 163" for
the 30-day window) — a different definition, labelled as such.
Pluralisation helper added (`display.py:451 pluralise`, registered as a Jinja filter in
`main.py`) and used in `markets/index.html:45,47`, `markets/detail.html:91`,
`analytics.html:68`. Live: `1 of 3 configured markets`, `18 configured cities`.
Screenshots/JSON: `audit/wp3/u6_numbers.py`.

Residual risk: home's "Permit records" (236, linked) and Trends' "Permit records" (163,
window) share a label but not a definition. Recommendation: qualify the home label
("Permit records linked") so a reader cannot read them as the same count. Logged as WP4.

## U7 — Motion proof — **PASS**

`audit/wp3/motion_probe_u7.py` → `audit/wp3/u7_motion.json`:
```
home          reveal total=7  animated=7  settled_after_scroll=7  still_invisible=0
opportunities reveal total=20 animated=20 settled_after_scroll=20 still_invisible=0
companies     reveal total=59 animated=59 settled_after_scroll=59 still_invisible=0
trends        reveal total=1  animated=1  settled_after_scroll=1  still_invisible=0
detail        reveal total=0  animated=0  settled_after_scroll=0  still_invisible=0
count-up ends on the exact snapshot:     133 / 8 / 236 / 15 (home)
sticky_header:                           compact_at_top=false -> compact_after_scroll=true
skeleton_on_submit (behaviour 7):        true
reduced_motion:                          settled_immediately=true, bar_transform=none
console_errors_js_on:                    [] (none)
no_js: h1 on every route; opportunities 6 native selects usable, cards=20; companies cards=59
```
Every reveal target carries `bs-anim-on` and settles to `bs-in` with opacity 1 once scrolled
into view (0 stuck invisible on any route). Above-the-fold elements settle at load; the rest
settle on scroll — correct for behaviour 1.
The 10 master-prompt behaviours, as LIVE / NOT BUILT:

| # | Behaviour | Status | Where |
|---|---|---|---|
| 1 | Staggered reveal on scroll | LIVE | `motion.js:24` |
| 2 | Count-up to exact snapshot | LIVE | `motion.js:60` |
| 3 | Hover lift / press feedback | LIVE (CSS) | `motion.js:91`, `app.css` |
| 4 | Sticky header compacts after 24px | LIVE | `motion.js:94` |
| 5 | Bottom sheet / drawer slide + backdrop fade | LIVE | `dialog_a11y.html`, `app.css` |
| 6 | Animated segmented control (sliding indicator) | LIVE | `motion.js:108` |
| 7 | Skeleton shimmer on async load | LIVE (built this session) | `motion.js:160`, `app.css` |
| 8 | View Transitions page transitions | LIVE (progressive) | `motion.js:144` |
| 9 | Chart bars / sparklines draw in | PARTIAL — chart bars draw in; no sparklines rendered anywhere | `app.css:1456` |
| 10 | Expanding disclosures animate height | LIVE | `motion.js:125` |

Behaviour 7 was the one missing behaviour; built now (JS-gated, reduced-motion-safe, hidden
without `.bs-js`). Behaviour 9 has no sparkline to animate — nothing renders one; the chart
bar draw-in is the applicable part and it is live.

JS bytes (gzip), budget ≤ 35 KB:
```
motion.js        raw=7659   gzip=2758
controls.js      raw=11567  gzip=3767
motion.min.js    raw=24607  gzip=9773
TOTAL gzip = 16,298 bytes (~15.9 KB)  < 35 KB
```
Screenshots: `audit/wp3/u7_home_top_{390,720}.png`, `u7_feed_skeleton_{390,720}.png`.

## U8 — Footer and polish — **PASS**

Footer (`templates/base.html` `.footer-brand`, `app.css` ≤ 640px block):
```
390  grid=167px 167px   legal=12.8px
  footer-brand  w=358  (spans 1 / -1)
  Product   w=167 (top row)   Coverage w=167 (top row)
  Workspace w=358 (spans row, links two-up)
720  grid=320px 320px   legal=13.12px   footer-brand w=670 (1 / -1)
```
Brand block is full width; the link columns flow two-up and the last column spans the row so
there is never a half-empty row; legal notice reduced but above the 12.5px floor.
Markets orphaned card: 3 cards in a 2-column band left the last card orphaned at 720;
`.markets-grid > .card:last-child:nth-child(odd)` now centres it (641–1023px).
`audit/wp3/capture_u8.py` → `u8_footer.json`:
```
markets orphans: 390=0  720=0  1440=0
```
Screenshots: `audit/wp3/u8_footer_{390,720,1440}.png`.

---

## Gates at this release

* Full suite: **1390 passed** (`python -m pytest tests/ -q`).
* axe-core (wcag2a/2aa/21a/21aa/22aa/best-practice), 16 routes × 390/720/1440:
  `TOTAL violation instances: 0  ZERO_VIOLATIONS`.
* Overflow at 360/390/430/600/720/768/820/1440: none.
* Font floor: 0 elements under 12px.
* Visible native selects: 0 on the 7 named routes.
* CSP console errors: none (`console_errors_js_on: []`).
* JS bytes (gzip): 16,298 (< 35 KB).

## Live-preview proof

`audit/wp3/live_{390,720}_*.png` — 18 screenshots of the exact pages the founder compares:
`home_top`, `drawer`, `city_picker`, `sort`, `companies_role`, `new_filings`, `trends_top`,
`trends_chart`, `dossier`. Markup grep (U7) confirms the new skeleton/`data-bs-results`
markup is served live.

## Risks / open items

1. **Label collision** — home "Permit records" (linked) vs Trends "Permit records" (window).
   Same word, different definition. WP4: qualify the label.
2. **Render deploy** — the live Render preview was reported stalled in the prior session
   (no release marker after push). Confirm the branch is pushed and the preview re-deploys
   before showing the founder.
3. **Sub-44px desktop links** — dense pages have many; exempt but noisy. WP4: consider a
   desktop hover/focus affordance audit.
4. **Behaviour 9** — no sparklines exist; if sparklines are added later they must draw in.

## Commits (this session, branch `wp3-u0-u8`)

```
e59ccd3 WP3 U8: raise narrow footer legal text to 0.8rem (12.8px, above the 12.5px caption floor)
04aeb8c WP3 U8: compact narrow-screen footer; centre the orphaned Markets card
fb18edd WP3 U7: add the missing motion behaviour 7 (skeleton shimmer); verify 1-10
cd50ad4 WP3 U6: one pluralisation helper; agreement everywhere a count is shown
9878215 WP3 U5: stop derived score drift from faking changes; sharpen Trends cards and chart
```
(U0–U4 were completed and committed in the preceding session on the same branch.)

## Files created this session

* `audit/wp3/motion_probe_u7.py`, `audit/wp3/capture_u7.py`, `audit/wp3/capture_u8.py`,
  `audit/wp3/capture_release.py`
* `audit/wp3/u7_motion.json`, `audit/wp3/u8_footer.json`
* `audit/wp3/u7_home_top_{390,720}.png`, `u7_feed_skeleton_{390,720}.png`,
  `u8_footer_{390,720,1440}.png`, `u1_open_{390,720,1440}.png`,
  `live_{390,720}_*.png` (18)
* `audit/WP3_REPORT.md` (this file)
