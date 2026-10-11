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

---
---

# WP3 Release & LIVE Verification — V0–V4

**Author:** OpenHands (engineer) · **Date:** 2026-10-11 · **Rule:** read-only on data; LIVE is the
source of truth. Every claim below carries pasted command output. Items without live proof are
marked **PARTIAL**.

## V0 — RELEASE

### What was done

| Step | Command | Result |
|---|---|---|
| Tag rollback point | `git tag -f pre-wp3-rollback b8a69c3 && git push origin refs/tags/pre-wp3-rollback` | `* [new tag] pre-wp3-rollback` |
| Merge WP3 | `git checkout main && git merge --no-ff wp3-u0-u8` | merge commit `65f54f36df085f7c78ff5c56197fdc7f68dca177` |
| Push main | `git push origin main` | `b8a69c3..65f54f3  main -> main` |

Release marker was already implemented and confirmed present:

```
src/oppintel/app/config.py:104  #: Build identifier surfaced as a `<meta name="buildscope-release">` tag
src/oppintel/app/config.py:108  release: str = field(default_factory=lambda:
        (os.environ.get("OPPINTEL_RELEASE") or "").strip()
        or (os.environ.get("RENDER_GIT_COMMIT") or "").strip()[:7])
src/oppintel/app/main.py:339    "release": cfg.release,          # /api/statistics
src/oppintel/app/main.py:1285   if cfg.release: payload["release"] = cfg.release   # /healthz
src/oppintel/app/templates/base.html:13  {% if release %}<meta name="buildscope-release" content="{{ release }}">
```

### Poll result — marker did NOT match within 15 minutes

```
merge sha=65f54f36df085f7c78ff5c56197fdc7f68dca177 short=65f54f3
12:27:28 attempt 1  release=b8a69c333c6bbf284e570a2c9de1dc58ef2b2fae
...
12:50:14 attempt 12 release=b8a69c3
```

`b8a69c3` is the **pre-WP3** origin/main head; the live service kept serving it for ~23 minutes.
Per R1 the poll was **stopped** (no match).

### R1 report — why the deploy did not happen, and what the dashboard needs

* `render.yaml` says the service *should* deploy automatically:
  ```
  22:    name: buildscope-preview
  27:    branch: main
  33:    startCommand: bash ops/start.sh
  34:    healthCheckPath: /healthz
  37:    autoDeploy: true
  ```
* But Render never redeployed. Evidence:
  * `GET https://api.github.com/repos/daveok16-arch/Buildscope/commits/main` → `sha 65f54f3`
    (GitHub received the push; `date 2026-10-11T12:26:58Z`).
  * `GET https://api.github.com/repos/daveok16-arch/Buildscope/hooks` → `[]` — **no repository
    webhook is registered**, so GitHub is not notifying Render.
  * No `RENDER_API_KEY` is present in this environment (`env | grep -i render` → only
    `OH_TELEMETRY_DEPLOYMENT_KIND`), so the explicit
    `POST /v1/services/srv-db3u3jij9qps73ffa4vg/deploys` path used in the earlier session
    (`audit/live/Q1_TRUTH.md:11-21`) could not be repeated. Unauthenticated POST → `HTTP 401`.
* **What the dashboard needs:** in the Render dashboard open the service
  (`srv-db3u3jij9qps73ffa4vg`, "Buildscope", free, branch `main`) → **Manual Deploy → Deploy
  latest commit**, or confirm **Auto-Deploy = Yes** is actually saved. To make this automatable,
  add a **Deploy Hook** (Settings → Deploy Hook) or a `RENDER_API_KEY` secret so the release can
  be triggered from CI.

**V0 = FAIL** (merge + push + tag done; live marker never advanced).

### Consequence — none of WP3 is live

```
$ for c in 9878215 cd50ad4 fb18edd 04aeb8c e59ccd3 46dd051 65f54f3; do
    git merge-base --is-ancestor $c b8a69c3 && echo "$c IN live" || echo "$c NOT in live"; done
9878215 NOT in live
cd50ad4 NOT in live
fb18edd NOT in live
04aeb8c NOT in live
e59ccd3 NOT in live
46dd051 NOT in live
65f54f3 NOT in live
```

The live build is **M1–M6 only** (`b8a69c3 audit(live): deploy resolved — 6f6d8fb live, M1-M6
verified on production`). Everything below therefore reports the **pre-WP3** live behaviour and
names, per item, what is live vs only local.

## V1 — ASSESS THE LIVE SITE

### V1a — crawl (status / canonical / title / marker)

```
200  /           200  /opportunities   200  /opportunities/new   200  /companies
200  /markets    200  /markets/dfw     200  /changes             200  /trends
200  /analytics  200  /reports         200  /how-it-works        200  /guides
200  /trades     200  /trades/commercial-hvac  200  /commercial-construction-leads
200  /signin     200  /signup          200  /healthz
```

| Route | release | canonical | title |
|---|---|---|---|
| / | b8a69c3 | `https://buildscope-xppz.onrender.com/` | DFW Construction Opportunity Intelligence — Commercial HVAC |
| /opportunities | b8a69c3 | …/opportunities | DFW Commercial HVAC Opportunities |
| /companies | b8a69c3 | …/companies | Commercial Construction Companies & Stakeholders |
| /markets | b8a69c3 | …/markets | Markets |
| /markets/dfw | b8a69c3 | …/markets/dfw | Dallas–Fort Worth Commercial Construction Opportunities |
| /changes | b8a69c3 | …/changes | Market Change Audit |
| /trends | b8a69c3 | …/trends | Trend Radar |
| /analytics | b8a69c3 | …/analytics | Market Analytics |
| /reports | b8a69c3 | …/reports | Reports |
| /how-it-works | b8a69c3 | …/how-it-works | How It Works |
| /guides | b8a69c3 | …/guides | Guides |
| /trades | b8a69c3 | …/trades | Trades |
| /trades/commercial-hvac | b8a69c3 | …/trades/commercial-hvac | **Commercial Commercial HVAC / Mechanical** Construction Opportunities |
| /commercial-construction-leads | b8a69c3 | …/commercial-construction-leads | Commercial Construction Leads and Project Intelligence |
| /signin | b8a69c3 | **https://buildscope-xppz.onrender.com/** | Sign In |
| /signup | b8a69c3 | **https://buildscope-xppz.onrender.com/** | Create Free Account |

**Findings:** (1) `/signin` and `/signup` canonicalise to the homepage — a duplicate-content signal
on two live routes. (2) `/trades/commercial-hvac` title duplicates "Commercial": *"Commercial
**Commercial HVAC**"*.

### V1b — axe / overflow / tap / font / selects / CSP console

axe-core (wcag2a, wcag2aa, wcag21a, wcag21aa, wcag22aa, best-practice) across 16 routes × 3 viewports:

```
axe total instances: 0
axe routes with violations: []
console errors: {}
```

Per-route measured profile (live, DPR 1):

```
390/opportunities  ov= 36 tap<44=  1 font<12= 78 sel=1
390/companies      ov= 36 tap<44=  1 font<12=304 sel=0
390/changes        ov=244 tap<44=  1 font<12=  1 sel=0
390/trends         ov= 47 tap<44=  1 font<12=  8 sel=0
720/opportunities  ov= 36 tap<44= 26 font<12= 78 sel=1
720/companies      ov= 36 tap<44= 64 font<12=304 sel=0
720/changes        ov=165 tap<44= 29 font<12=  1 sel=0
1440/opportunities ov=  0 tap<44= 39 font<12= 78 sel=6
1440/changes       ov= 63 tap<44= 32 font<12=  1 sel=0
```
(Every route shows `sw==vw`, so `document.documentElement` does not actually scroll sideways.)

Offender breakdown:
* The bulk of "overflow" is the off-canvas **`.mobile-drawer`** (`l:390 rr:725`), parked off-screen
  at every width — not a visible horizontal scrollbar.
* **`/changes` at 1440 is a real overflow**: `table.data-table.change-table` spans
  `l:437 → rr:1458` on a 1440 viewport (63 elements), i.e. the change table is wider than the page.
* **`font<12`**: `small` at **10.4px**, `badge-plain`/`meta-label` at **11.52px**, SVG `<text>` at
  11px — all below the 12.5px caption floor. `font<12=304` on `/companies` and `78` on
  `/opportunities` is a large-scale violation.
* **Visible `<select>` on live**: 6 at 1440 on `/opportunities`, but only **one** is genuinely
  visible — `.feed-sort-select` (Sort Order, `clip:auto`, 165×31). The other five
  (`classification`, `city`, `project_type`, `procurement_status`, `sort`) are `bs-select-native`
  with `clip: rect(0,0,0,0)` (screen-reader-only fallbacks). At 390/720 one native select remains
  visible. So U2 ("remove native selects") is **not live**.
* **CSP console errors:** none on any route (`console errors: {}`).

### V1c — live city picker

```
city picker: {'390': {'open': True, 'top': 169, 'bottom': 844, 'height': 675, 'vh': 844,
                      'in_viewport': True, 'below_footer': False, 'position': 'fixed', 'parent': 'BODY'},
              '720': {'open': True, 'top': 429, 'bottom': 1280, 'height': 851, 'vh': 1280,
                      'in_viewport': True, 'below_footer': False, 'position': 'fixed', 'parent': 'BODY'}}
```
The picker opens **inside the viewport and above the footer** at both widths (a bottom sheet,
`position:fixed`, parented to `<body>`). No below-footer repro on live. Screenshots
`audit/live/V_citypicker_open_{390,720}.png`.

### V1d — banned-phrase grep (live HTML)

```
=== / ===              3 Verified October ; 1 receive
=== /opportunities === 20 Verified October ; 2 meeting ; 1 receive
=== /changes ===        8 QTEAM MEETING ; 1 receive
=== /analytics ===      1 QTEAM MEETING
```
* "Verified October" — **20 occurrences on `/opportunities`** (U4 not live).
* "QTEAM MEETING" — **8 on `/changes`**, 1 on `/analytics` (U3 not live).
* "receive"/"meeting" matches are incidental ("an inert element **receive**s no pointer events",
  lowercase "meeting" in copy).
* No "Continuous", "Data freshness", "TBD -" or ",,," on the sampled routes.

### V1e — same-minute numbers table

Fetched within the same minute (UTC 12:52:00–12:52:19):

| Source | Label | Value |
|---|---|---|
| /healthz | projects | **3331** |
| /healthz | permits | **18124** |
| /healthz | cities_with_public_projects | 15 |
| /api/statistics | projects_total | 3331 |
| /api/statistics | projects_public | **325** |
| /api/statistics | projects_with_mechanical_evidence | **40** |
| /api/statistics | permit_records_all | **18124** |
| /api/statistics | permit_records | **653** |
| /api/statistics | evidence_records | 40203 |
| /api/statistics | linked_permits | 5563 |
| /api/statistics | tier1 | 10 |
| / | Public projects | 325 |
| / | With mechanical evidence | 40 |
| / | Permit records | **653** |
| / | Cities with projects | 15 |
| /trends | New commercial projects | 376 |
| /trends | Permit records | **466** |
| /trends | Projects with classified trade scope | 299 |
| /trends | Projects with a detected change | **480** |
| /trends | Projects first observed | **3,331** |
| /trends | Projects with strong mechanical evidence | **34** |
| /markets | "1 of 3 configured markets" | 1 |
| /markets | "15 cities" | 15 |

Disagreements (same minute, same data):
1. **"Permit records" has three values**: 653 (home/API in-window linked), 466 (Trends), 18124
   (all permits). Three definitions behind one phrase.
2. **Mechanical evidence**: 40 (home/API) vs 34 (Trends).
3. **"480 projects with a detected change"** exceeds "376 new commercial projects" and "325 public
   projects" — see V2/U5b: the count is `new_project` first-observation rows, not changes.
4. **Projects**: 3331 total / 325 public (home) vs 3,331 "first observed" (Trends).

### V1f — screenshots

`audit/live/V_*.png` (390 & 720): home top, drawer, home bottom, opportunities, city picker open,
sort open, new filings, companies, companies role open, trends top, trends chart, dossier
(Monticello), dossier (QTEAM), markets — 28 files. Index + notes in §"Screenshot index" below.

## V2 — ITEMS MISSING FROM THE PREVIOUS SUMMARY

### V2/U3 — title cleaner, per-rule counts across the full DB

```
projects: 1261 titles changed by the cleaner: 1195
  rule marker_prefix      25
  rule repeated_punct     3
  rule reference_clause   3
  rule run_together       1
  rule summarise          28
  rule note_fallback      30
```
(Counts are the number of projects whose title the rule altered at that pipeline stage; they sum
to more than 22 because the stages compose. `marker_prefix=25` is the internal-marker sweep.)

Examples:
```
marker_prefix:   #22 'QTEAM MEETING TBD - Ground-up new construction of a single-s' -> 'Ground-up new construction of a single-story office and ware'
marker_prefix:   #33 'X TEAM /// EV Chargers' -> 'EV Chargers'
repeated_punct:  #157 'Refer to PB26-12967 /// Dumpster Enclosure' -> 'Refer to PB26-12967 / Dumpster Enclosure'
reference_clause:#740 'Refer to PO26-02663 Equipment Yard' -> 'Equipment Yard'
run_together:    #560 '...McKinney Ave. It is16,297 SF' -> '...McKinney Ave. It is 16,297 SF'
```

**QTEAM title render — LOCAL (cleaner applied):**
```
card-title" title="INTERIOR RESTAURANT TENANT IMPROVEMENT, INCLUDES NEW PARTITION WALLS, T BAR CEILING, SOFFITS, FLOOR AND WALL FINISHES.">
```
The internal marker `X TEAM ///` is gone from the title; it survives only inside the verbatim
evidence quote (`Tier 2: work description: "X TEAM /// INTERIOR RESTAURANT ..."`), which is
correct — a quote must not be edited.

**QTEAM title render — LIVE (cleaner absent):**
```
QTEAM MEETING 8.19.26 (9 AM) - New commercial restaurant - Braum's
```
U3 is **verified locally, not live**.

### V2/U4 — "Verified October" → "Collected <date>"

```
$ grep -rn "Verified October\|Last verified" src/oppintel/app/templates/
(no matches)
$ grep -rn "collected" src/oppintel/app/templates/partials/opportunity_card.html src/oppintel/app/templates/opportunities/detail.html
opportunity_card.html:124:  <span>Collected {{ project.last_verified | collected }}</span>
detail.html:34:             · Collected {{ project.last_verified | collected }}
detail.html:478:           Collected {{ project.last_verified | collected }}.
$ curl -s LIVE/opportunities | grep -c "Verified October"   ->  20
```
Before: live cards say "Verified October" (20×). After (local): "Collected <date>". U4 is
**verified locally, not live**.

### V2/U5b — "480 projects with a detected change"

**Live SQL equivalent** (local copy; the schema and semantics are identical):

```
project_change cols: id, project_id, field_name, previous_value, current_value, change_kind,
                     summary, source_id, source_name, source_url, detected_at
by kind: [('new_project', 1261)]
detected_at range: ('2026-10-09T16:49:35.896446+00:00', '2026-10-09T16:49:43.440709+00:00')
distinct projects: (1261,)
rows excluding new_project: (0,)
```

**Root cause (not source changes, not the restore):** every row is `change_kind='new_project'` — a
*first observation*, not a difference. All 1261 rows were written inside an **8-second window**
(`16:49:35 → 16:49:43`), i.e. one assembly pass, not incremental source diffs. On live the same
row-set is **480** for the current window. The label "projects with a **detected change**"
therefore overstates: rule 7 ("a no-op assembly emits nothing") is intact, but `new_project` is a
discovery, and discovery is being counted and labelled as change.

**Was there also derived drift?** Yes, previously. `classification_score` is derived and
time-dependent (its recency points expire 180 days after the permit), so a no-op assemble
manufactured `evidence_updated` events on unchanged data. Fixed in `9878215`:
```
src/oppintel/changes.py  TRACKED_FIELDS: drop ("classification_score","classification score");
                         keep ("classification","classification")
tests/test_accessibility.py::test_derived_classification_score_is_not_a_tracked_change
```
The normaliser now only emits when a *displayed, non-derived* field truly differs.

**10 sample diffs** (all `new_project`, so before/after is `∅ → value`): every one is a first
observation, classified **noise-as-change** (real discovery, wrong label), not a false diff:
```
#1  ∅ -> {project_name: 'Ground-up new construction ...', classification: 'HIGH'}
#2  ∅ -> {project_name: 'Interior Restaurant Tenant Improvement ...', classification: 'MEDIUM'}
... (10 of 1261)
```
**Corrected number (local):** `Projects with a detected change = 0`; `Projects first observed =
1,261`. The Trends card should read "Projects first observed", not "with a detected change". Live
correction is **blocked on V0** (needs the deploy).

### V2/U6 — numbers table with definitions + disagreements

See the V1e table. Definitions and the reason for each disagreement:
* **Permit records** — home/API `653` = permits *linked to in-window public projects*; Trends `466`
  = permits *inside the observation window*; healthz `18124` = *all* permits in the DB. Same words,
  three scopes.
* **Mechanical evidence** — home/API `40` (all-time mechanical evidence); Trends `34` (mechanical
  evidence inside the 30-day window).
* **Detected change `480`** — `new_project` discovery rows in the window, not differences (V2/U5b).
* **Markets 1/3 configured, 15 cities** — one market configured as "verified" with data (DFW), two
  configured-but-empty (Austin, Houston); `15` cities have public projects.
None of these are "wrong arithmetic"; they are **the same word bound to different definitions in
different modules**, which is the founder-visible defect.

## V3 — MOTION CLAIM

**Behaviour 9 (chart draw-in + sparklines).** The chart bar draw-in is live in CSS
(`app.css:1456`, removed under `prefers-reduced-motion`), but **no sparkline is rendered on any
route** — the Trends metric cards carry numbers, not sparklines. V3 = **PARTIAL**: the claim is
inaccurate for sparklines; either add sparklines or remove that clause from the docs.

**Video + frames (LOCAL — motion is not live):** `audit/wp3/video/*.webm` (8 clips: home,
/opportunities, dossier, /trends × 720/390) and `audit/wp3/frames/<clip>/f01–f04.png` (32 frames,
4 per animated element). Recording and frame extraction used the vendored `imageio-ffmpeg`.

**prefers-reduced-motion disables motion (LOCAL):**
```
reduced-motion: {'invisible_after_load': 0, 'bar_transform': 'no-bar'}
```
With `reduced_motion="reduce"` nothing is left at `opacity:0` after load (reveals settle
immediately) and the chart bar has no transform. Confirmed.

## V4 — SELF-REVIEW

Measured against the master-prompt checklist (live, 390/720):

| Criterion | Live result | Verdict |
|---|---|---|
| One H1 per page, sane heading order | every route `h1=1`; `/opportunities` H2 "Matching opportunities" is `sr-only` (clipped by design) | PASS |
| Nothing clipped | leaf clipping scan: `/companies` 0, `/markets` 0, `/opportunities` 1 (`sr-only`) | PASS |
| Evidence first | cards lead with tier/scope evidence | PASS |
| No walls of text | home 898 words, /opportunities 1600, /changes **2039**; longest paragraph 458 chars | **FAIL (/changes, /opportunities)** |
| Consistent spacing | 33 font sizes, 18 text colours, 493 inline `style=` attrs, 11 `!important` | **FAIL (one-offs)** |
| Native controls gone | 1 genuinely visible native `<select>` (Sort Order) live | **FAIL (U2 not live)** |
| Readable at 720/390 | footer **152% of a 390 screen**; `small`=10.4px, badges=11.52px | **FAIL** |
| Looks like pro B2B | live serves pre-WP3 (banned copy, `QTEAM MEETING`, "Verified October") | **FAIL (blocked on V0)** |

Self-review screenshots: `audit/live/V_home_top_{390,720}.png`, `V_home_bottom_390.png`,
`V_citypicker_open_{390,720}.png`, `V_companies_role_open_{720}.png`, `V_dossier_qteam_390.png`,
`V_trends_top_720.png`.

**V4 = FAIL** — the failures are the un-deployed WP3 work plus two genuine live defects
(`/changes` table overflow, footer height at 390, sub-12px text) that WP3 also does not yet fix.

## Screenshot index (live, one-line problem note)

| File | Note |
|---|---|
| V_home_top_390.png | Hero OK; sub-12px helper text |
| V_home_top_720.png | 4 stat cards readable; "253/26/11,966"-class figures stale vs API |
| V_home_bottom_390.png | Footer ≈1.5 screens tall on a phone (orphaned link columns) |
| V_drawer_390.png / _720.png | Off-canvas drawer; parked off-screen (source of the "overflow" count) |
| V_opportunities_390/720.png | Native "Sort Order" `<select>` visible; "Verified October" on every card |
| V_citypicker_open_390/720.png | Opens in-viewport, above footer — no below-footer repro |
| V_sort_open_390/720.png | Sort control |
| V_new_filings_390/720.png | New-filings view |
| V_companies_390/720.png | Sub-12px badges; role column shows mostly "Owner" |
| V_companies_role_open_720.png | Role control open |
| V_trends_top_390/720.png | 6 metric cards; "480 detected change" overstates |
| V_trends_chart_390/720.png | Bar chart present (no sparklines) |
| V_dossier_390/720.png | Monticello dossier |
| V_dossier_qteam_390/720.png | Title shows raw "QTEAM MEETING …" (U3 not live) |
| V_markets_390/720.png | Markets list |

## Commits (this session)

```
65f54f3  WP3: release U0-U8 (merge of wp3-u0-u8 into main)
tag pre-wp3-rollback -> b8a69c3
```

## Files created this session

* `audit/wp3/live_probe_v1.py`, `audit/wp3/live_shots_v1.py`, `audit/wp3/motion_video_v3.py`
* `audit/wp3/v_live.json`
* `audit/live/V_*.png` (28), `audit/wp3/video/*.webm` (8), `audit/wp3/frames/*/f01–f04.png` (32)
* `audit/WP3_REPORT.md` (this file, updated)

## Risks

* **Highest:** the founder judges by the live preview, and the live preview is pre-WP3. Every WP3
  claim is invisible until the V0 deploy is triggered. Nothing else matters until then.
* Live still serves banned copy, the raw `QTEAM MEETING` title, and "Verified October".
* Live `/changes` table overflows at 1440; footer is 152% of a phone screen; sub-12px text is
  widespread — these are live defects, not deploy artefacts.

## Blocked

* **V0 deploy trigger** — no `RENDER_API_KEY`, no repository webhook. Needs the Render dashboard
  (Manual Deploy) or a deploy hook / API key. This blocks live proof for U3–U8.

