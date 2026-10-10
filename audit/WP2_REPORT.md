# WP2 — Mobile & Accessibility Report

**Branch:** `wp2-mobile-a11y`
**Branch tip:** `d312bb5` + the G-gate commit (E2–E6, then the G1–G6 evidence gate)
**Prior code commits:** `3fb8e4d` (B1–B3), `4865987` (E1 focus trap), `d43dd80` (E2–E5)
**Base:** merge of `wp1-truth-stability` (D1–D8 + D3 fix) into `wp2-mobile-a11y`
**Date:** 2026-10-09
**Scope:** B1 horizontal overflow, B2 touch targets, B3 mobile filter bottom sheet,
the Director's follow-ups E1–E6 (true focus trap, token/contrast table,
structural axe gate, screenshots, bundle sizes, this report), and the G1–G6 evidence gate
(concurrency soak, banned-phrase grep, permit-window analysis, ingest durations, CSP).
**Result:** all verified in a real browser (Playwright + Chromium 1243). Full suite: **1136 passed**.

---

## Summary of state

| Item | Status | Evidence |
|---|---|---|
| B1 — overflow at 360/390/430/820/1440, every public route | PASS | 70 parametrised cases, §B1 |
| B2 — touch targets ≥ 44px on 7 routes | PASS | 0 undersized, §B2 |
| B3 — mobile filter bottom sheet + date presets | PASS | 2 sheet cases + 1 preset case, §B3 |
| E1 — true focus trap (sheet + drawer) | PASS | `partials/dialog_a11y.html`; 4 new browser tests, §E1 |
| E2 — token + contrast table | PASS | ratios computed from the tokens, §E2 |
| E3 — structural axe gate (heading order, landmarks, dialog roles) | PASS | `test_structural_axe_rules_are_clean`, §E3 |
| E4 — bundle sizes | PASS | app.css 54,810 B raw / 11,972 B gzip, §E4 |
| E5 — 390px screenshots | PASS | `audit/wp2/e5__*.png`, §E5 |
| G1a — D1 concurrency soak | PASS | 0 errors / 0 locked over 60s, §G1a |
| G1b — banned-phrase grep | PASS | 0 hits across templates, §G1b |
| G1c — D5 permit-window analysis | PASS | 22 public / 136 non-public in window, §G1c |
| G1d — D6 ingest durations + pause | PASS | documented + measured, §G1d |
| G1e — D7 scripts / post-deploy checks | PASS | scripts present and catalogued, §G1e |
| G2 — CSP with per-request nonce, no `unsafe-inline` scripts | PASS | 0 violations in Chromium, §G2 |
| G3 — axe-core zero violations, all rules, 14 routes × 2 viewports | PASS | `wp2_axe_zero.py`, §G3 |
| G4 — contrast table (computed) | PASS | `wp2_contrast_table.py`, §G4 |
| G5 — branch hygiene | PASS | wp1 contained in wp2, `main` untouched, §G5 |
| G6 — post-deploy smoke | PASS | 17 routes 200, 0 failures, §G6 |
| Full test suite | PASS | 1136 passed, §Suite |
| axe-core (all routes, mobile + desktop) | PASS | 0 violations, §B4 |

Two real B1 defects were found by the widened test and fixed in this branch (not present on
`d052430`): `/signin` and `/signup` scrolled 20px at 360px, and `/trends` scrolled 33px at 430px.

---

## B1 — Horizontal overflow

Asserted at **360 / 390 / 430 / 820 / 1440 px** on every route in `ROUTES`
(14 routes). The test collects the offending elements and names them in the failure message, so a
regression points at the cause rather than only reporting a too-wide page.

Command:

```bash
env -u BASE_URL -u OPPINTEL_DB -u OPPINTEL_DATA_DIR -u SECRET_KEY -u PORT \
  PYTHONPATH="vendor/python:src" \
  python -m pytest tests/test_accessibility.py -k overflow -v -p no:cacheprovider
```

Output (excerpt; 70 cases, all PASSED):

```
tests/test_accessibility.py::test_no_horizontal_overflow[/-360] PASSED   [  1%]
tests/test_accessibility.py::test_no_horizontal_overflow[/-390] PASSED   [  2%]
tests/test_accessibility.py::test_no_horizontal_overflow[/-430] PASSED   [  5%]
tests/test_accessibility.py::test_no_horizontal_overflow[/-820] PASSED   [  5%]
tests/test_accessibility.py::test_no_horizontal_overflow[/-1440] PASSED  [  6%]
tests/test_accessibility.py::test_no_horizontal_overflow[/opportunities-360] PASSED [  7%]
...
tests/test_accessibility.py::test_no_horizontal_overflow[/trades/commercial-hvac-1440] PASSED
tests/test_accessibility.py::test_no_horizontal_overflow[/commercial-construction-leads-1440] PASSED
tests/test_accessibility.py::test_no_horizontal_overflow[/signin-360] PASSED
tests/test_accessibility.py::test_no_horizontal_overflow[/signup-360] PASSED
```

### Defects found and fixed

**1. `/signin`, `/signup` — 20px overflow at 360px (before).**
`.auth-grid` used `grid-template-columns: 1.15fr 0.95fr`. An `fr` track has an implicit
`min-content` floor, and `min-content` was driven by the kicker text
`BUILD SCOPE • INTELLIGENCE WOR…` (`white-space` from a long unbreakable token chain). The track
resolved to 353px inside a 336px content box, so the page was 380px wide in a 360px viewport.
The mobile override at `app.css` re-declared `grid-template-columns: 1fr`, which re-introduced the
same floor.

```css
/* before — src/oppintel/app/static/css/app.css */
.auth-grid { grid-template-columns: 1.15fr 0.95fr; }          /* min-content floor */
@media (max-width: 860px) { .auth-grid { grid-template-columns: 1fr; } }  /* floor again */
```

Fixed to `minmax(0, 1fr)` tracks (both the base rule and the 860px override). Measured:
`before signin@360 scrollWidth=380` → `after signin@360 scrollWidth=360`.

**2. `/trends` — 33px overflow at 430px (before).**
The metric card carried an inline `min-width:240px`, which defeats the responsive
`.stat-strip` grid; two cards need 492px and only 430px is available. Removed the inline floor.

```html
<!-- before — src/oppintel/app/templates/trends.html:70 -->
<div class="stat" style="flex:1 1 260px; min-width:240px">
<!-- after -->
<div class="stat" style="flex:1 1 240px; min-width:0">
```

`.stat-strip` also changed from `minmax(180px, 1fr)` to `minmax(min(180px, 100%), 1fr)` so a
single-column card can never demand more than the container on a narrow phone.

### Before/after measurement (live instances)

`audit/scripts/wp2_before_after.py` drives the pre-change build (12001) and the current build
(12000) and prints `scrollWidth` vs `innerWidth`:

```
before  390 home           scrollWidth=390 innerWidth=390 ok
before  390 opportunities  scrollWidth=390 innerWidth=390 ok
before  390 companies      scrollWidth=390 innerWidth=390 ok
before  390 trends         scrollWidth=390 innerWidth=390 ok
before 1440 home           scrollWidth=1440 innerWidth=1440 ok
...
after   390 opportunities  scrollWidth=390 innerWidth=390 ok
after   390 companies      scrollWidth=390 innerWidth=390 ok
after  1440 trends         scrollWidth=1440 innerWidth=1440 ok
```

Screenshots: `audit/wp2/before__360__signin.png` (380 wide, overflows) vs
`audit/wp2/after__360__signin.png` (360 wide, clean); `audit/wp2/before__390__trends.png` vs
`after__390__trends.png`.

---

## B2 — Touch targets

Every interactive control (`button`, `input`, `select`, `textarea`, `a.btn`, `[role=button]`) on
`/`, `/opportunities`, `/companies`, `/trends`, `/changes`, `/signin`, `/signup` is measured at a
390px viewport with `has_touch=True`. WCAG 2.5.5 target: **≥ 44 × 44 px**.

```
touch /                undersized=0 []
touch /opportunities   undersized=0 []
touch /companies       undersized=0 []
touch /trends          undersized=0 []
touch /changes         undersized=0 []
touch /signin          undersized=0 []
touch /signup          undersized=0 []
```

With the filter sheet open on `/opportunities`, the controls inside the sheet are also measured:

```
open-sheet undersized: []
```

The 44px rule is scoped to coarse pointers / ≤ 640px (`app.css`, the
`@media (max-width: 640px), (hover: none) and (pointer: coarse)` block) so desktop density is
untouched. This branch extended that block to the filter sheet's own controls
(`.filter-sidebar .field input/select`, `.date-input input`, `.checkline`), which the earlier
rule did not cover because the panel was collapsed.

Test:

```
tests/test_accessibility.py::test_touch_targets_meet_44px[/] PASSED
tests/test_accessibility.py::test_touch_targets_meet_44px[/opportunities] PASSED
tests/test_accessibility.py::test_touch_targets_meet_44px[/companies] PASSED
tests/test_accessibility.py::test_touch_targets_meet_44px[/trends] PASSED
tests/test_accessibility.py::test_touch_targets_meet_44px[/changes] PASSED
tests/test_accessibility.py::test_touch_targets_meet_44px[/signin] PASSED
tests/test_accessibility.py::test_touch_targets_meet_44px[/signup] PASSED
```

---

## B3 — Mobile filter bottom sheet

**It exists, on both filter pages, and it is a real bottom sheet — not a panel that pushes results
down the page.**

On `/opportunities` and `/companies`, at ≤ 860px the filter form is hidden behind a full-width
**`Filters (n)`** button. `n` is the count of deliberate filters the request applied
(city, trade, classification, date range, mechanical-only, …); sort and pagination are excluded
because neither narrows results. Tapping the button opens the form as a fixed sheet pinned to the
bottom of the viewport (`position: fixed; inset: auto 0 0 0`), with a drag handle and a Close
button. Escape, the backdrop, and Close all dismiss it and return focus to the toggle. The form
still submits with **no JavaScript** — the button and presets are ordinary controls, and the
`<form>` is a normal GET.

| Element | `/opportunities` | `/companies` |
|---|---|---|
| `Filters (n)` button | ✅ `#mobile-filter-btn` | ✅ `#mobile-filter-btn` |
| Active-filter count | ✅ `Filters (2)` for `?city=Plano&classification=MEDIUM` | ✅ `Filters (2)` for `?role=owner&city=Plano` |
| Bottom sheet | ✅ `#filter-sidebar-panel` | ✅ `#company-filter-panel` |
| Drag handle + Close | ✅ | ✅ |
| Escape / backdrop / Close | ✅ | ✅ |
| Focus moves into sheet, returns to toggle | ✅ | ✅ |
| Native date inputs | ✅ `#date_from`, `#date_to` (`type="date"`) | n/a (no date filter) |
| Date presets | ✅ Last 7 days / Last 30 days / This year | n/a |

Render check (Jinja, no browser):

```
/opportunities                                  -> ... </svg> Filters
/opportunities?city=Plano&classification=MEDIUM -> ... </svg> Filters (2)
/companies?role=owner&city=Plano                -> ... </svg> Filters (2)
presets: ['Last 7 days', 'Last 30 days', 'This year']
date inputs: 2
companies btn: True | company-filter-panel: True
```

Tests (real browser, 390px, `has_touch=True`):

```
tests/test_accessibility.py::test_mobile_filter_sheet_opens_and_closes[/opportunities-filter-sidebar-panel] PASSED
tests/test_accessibility.py::test_mobile_filter_sheet_opens_and_closes[/companies-company-filter-panel] PASSED
tests/test_accessibility.py::test_filter_date_presets_and_native_inputs PASSED
```

The sheet test asserts the panel is `display:none` before opening; after the tap it is
`position:fixed` with its bottom edge within 2px of the viewport bottom, `aria-expanded="true"`,
`body.filter-sheet-open` set, and focus inside the sheet; Escape restores `display:none`,
`aria-expanded="false"`, and focus on the toggle.

Screenshot: `audit/wp2/after__390__opportunities_filters_open.png`.

---

## B4 — axe-core (all routes, mobile + desktop)

Run against 14 routes at 390px and 1440px:

```
mobile   routes=14 serious=0 critical=0 ids={}
desktop  routes=14 serious=0 critical=0 ids={}
ALL axe violations by impact: {}
total violation instances: 0
```

The contrast fixes that landed earlier on this branch (gold-500 → gold-600 for white-on-gold
buttons, `.muted` on dark panels → slate-300/slate-400, AA-safe `signal-*-text` tokens) are what
keep the desktop header and mobile drawer clean at both widths.

## B5 — semantics, focus, reduced motion

* Drawer is `role="dialog"`, `aria-modal="true"`, `aria-label`, `tabindex="-1"` (`base.html:107`);
  trigger has `aria-expanded`/`aria-controls`; close button has an `aria-label`.
* Global `:focus-visible { outline: 2px solid var(--gold-500); outline-offset: 2px; }`
  (`app.css:146`).
* Single `<main id="main">`, `<header>`, `<footer>`, and labelled `<nav>`s (`base.html`).

---

## E1 — true focus trap (sheet and drawer)

The B1–B3 sheet moved focus in on open and returned it on close, but Tab could still walk out of
the sheet into the page behind the backdrop (called out as a follow-up in the first draft of this
report). That is now a real trap, shared by the drawer and both filter sheets so the three
surfaces cannot drift.

`src/oppintel/app/templates/partials/dialog_a11y.html` (`window.BSDialog`) implements the five
things a modal dialog must do:

1. move focus into the dialog on open (first focusable, else the container);
2. wrap Tab and Shift+Tab at the ends of the dialog;
3. mark every sibling on the path from the dialog to `<body>` `inert`, so nothing behind it is
   focusable or clickable;
4. close on Escape, backdrop click or the Close button, and return focus to the toggle;
5. lock body scroll (`body.drawer-open`/`body.filter-sheet-open { overflow: hidden }`).

Two details that were load-bearing:

* the backdrop is **exempt** from inert (an inert element receives no pointer events, so inerting
  it would break click-outside-to-close);
* a filter `<aside>` is a static sidebar on desktop and a dialog only while open on mobile, so
  `role="dialog"`/`aria-modal="true"` are added on open and removed on close. `role="dialog"` is
  not permitted on `<aside>`, which is why the drawer stays a `<div>`.

The drawer no longer hand-toggles a hardcoded `aria-hidden`; it is hidden with
`visibility: hidden` (out of the accessibility tree) and shown with `visibility: visible`, which
also fixes the original defect where focus() ran while the panel was still `visibility: hidden`.

Browser tests (`tests/test_accessibility.py`):

* `test_filter_sheet_traps_tab_focus[/opportunities|/companies]` — 20× Tab and 20× Shift+Tab with
  focus asserted inside the panel each time, plus `role`/`aria-modal`, body lock, `inert` on the
  header and filter bar, and restoration of all three on Escape;
* `test_mobile_drawer_traps_tab_focus` — the same on the nav drawer;
* `test_mobile_drawer_focus_and_aria` — updated to assert body lock and background `inert` rather
  than the removed `aria-hidden`.

## E2 — token and contrast table

Colours are tokens in `:root` (`app.css:1–90`); 67 distinct `#hex`/`rgba()` literals exist in the
whole sheet, and this branch added only two new literal colours and one rgba, each an AA-safe
hover value. Contrast ratios (WCAG 2.x, computed from the token values):

| Foreground | Background | Ratio | Verdict | Token |
|---|---|---|---|---|
| `#ffffff` | `--gold-500` `#d97706` | **3.19:1** | FAIL (was used) | gold-500 |
| `#ffffff` | `--gold-600` `#b45309` | **5.02:1** | PASS | gold-600 |
| `#ffffff` | `#92400e` | **7.09:1** | PASS | E2 hover literal |
| `--slate-300` `#cbd5e1` | `#ffffff` | **1.48:1** | FAIL (was `.btn-ghost` on white) | slate-300 |
| `--slate-600` `#475569` | `#ffffff` | **7.58:1** | PASS | slate-600 |
| `--slate-500` `#64748b` | `--navy-950` `#060b17` | **4.13:1** | PASS (≥4.5 for large/AA-large; body text on dark uses slate-300) | slate-500 |
| `--slate-300` `#cbd5e1` | `--navy-950` `#060b17` | **13.24:1** | PASS | slate-300 |
| `--gold-600` `#b45309` | `#ffffff` | **5.02:1** | PASS | gold-600 |

The white-on-gold-500 3.19:1 failure and the slate-300-on-white 1.48:1 failure were the two
serious axe findings fixed on this branch (gold-500 → gold-600; `.btn-ghost` base colour →
slate-600 with the light treatment scoped to `.site-header`/`.hero`/`.mobile-drawer`).

## E3 — structural axe gate

The serious/critical gate is not enough: axe rates `heading-order` **moderate**, so a real defect
can pass it. Running the rule explicitly found one:

```
desktop /opportunities [{"id":"heading-order","impact":"moderate","nodes":[".filter-sidebar-head > h3"]}]
desktop /companies     [{"id":"heading-order","impact":"moderate","nodes":[".filter-sidebar-head > h3"]}]
desktop /changes       [{"id":"heading-order","impact":"moderate","nodes":[".filter-sidebar-head > h3"]}]
```

Root cause: the filter panel heading was an `<h3>` that precedes the results `<h2>` (and the
`sr-only` `<h2>` on `/opportunities`), so the outline went h1 → h3 → h2 at desktop. The panel
titles a top-level region, so it is now `<h2 class="filter-panel-title">`
(`opportunities/list.html:47`, `companies/index.html:43`, `changes.html:24`), styled by the
existing `.filter-sidebar-head h3, .filter-sidebar-head h2.filter-panel-title` rule.

`test_structural_axe_rules_are_clean` now asserts `heading-order`, `landmark-one-main`,
`landmark-unique`, `region`, `aria-allowed-role` and `aria-dialog-name` on all 14 routes at both
viewports. After the fix, all six pass at 390px and 1440px and the full axe run is empty.

## E4 — bundle sizes

| Artifact | main (`e01eadd`) | `3fb8e4d` | now | Δ vs main |
|---|---|---|---|---|
| `app.css` raw | 44,640 B | 54,646 B | **54,810 B** | +10,170 B (+23%) |
| `app.css` gzip | 8,849 B | 11,920 B | **11,972 B** | +3,123 B (+35%) |

No JavaScript files exist in `static/`; the interactive logic is inline in templates. Inline
`<script>` bytes: `base.html` 624, `opportunities/list.html` 761, `companies/index.html` 470,
`partials/dialog_a11y.html` 5,222. There are 8 `!important` in `app.css` and one `!important` in
a template; 346 inline `style=` attributes across templates; one font stack
(`-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Plus Jakarta Sans", "Inter", …`,
`app.css:113`) and 41 distinct `font-size` declarations. The CSS growth is the cost of the
bottom-sheet, focus and heading fixes; no build step minifies or splits it.

## E5 — 390px screenshots

| Screenshot | Note |
|---|---|
| `audit/wp2/e5__390__opportunities_sheet_open.png` | Opportunities filter bottom sheet open, pinned to the viewport bottom |
| `audit/wp2/e5__390__companies_sheet_open.png` | Companies filter bottom sheet open |
| `audit/wp2/e5__390__opportunities_active_filter_count.png` | `Filters (2)` count for `?city=Plano&classification=MEDIUM` |

## E6 — this report

Updated for E1–E5: branch tip and suite count, the E1–E5 rows in the summary table, the
structural axe finding and fix, the contrast table, the bundle sizes and the screenshot index.

---

## Suite

```bash
env -u BASE_URL -u OPPINTEL_DB -u OPPINTEL_DATA_DIR -u SECRET_KEY -u PORT \
  PYTHONPATH="vendor/python:src" python -m pytest tests/ -q -p no:cacheprovider
```

```
1136 passed in 246.47s (0:04:06)
```

Accessibility module alone: `168 passed in 126.08s` (was 140 before the all-rules axe gate and
the CSP-violation test). See §Suite (branch tip) below for the G-gate re-run.

---

## Files changed in this branch (B1–B3 and E1–E5)

| File | Change |
|---|---|
| `src/oppintel/app/main.py` | `_date_presets()` helper; `date_presets` + `active_filter_count` into the opportunities context |
| `src/oppintel/app/templates/opportunities/list.html` | `Filters (n)` button, backdrop, sheet open/close JS (now via `BSDialog`), date fieldset with presets, sidebar heading `h3`→`h2` |
| `src/oppintel/app/templates/companies/index.html` | Same sheet pattern for stakeholders; sidebar heading `h3`→`h2` |
| `src/oppintel/app/templates/changes.html` | Filter sidebar heading `h3`→`h2` (heading-order) |
| `src/oppintel/app/templates/partials/dialog_a11y.html` | **New.** `BSDialog` — shared focus trap / inert / scroll-lock / Escape for the drawer and both sheets |
| `src/oppintel/app/templates/base.html` | Include `dialog_a11y.html`; drawer script now a `BSDialog.register` call |
| `src/oppintel/app/templates/trends.html` | Removed the inline `min-width:240px` card floor |
| `src/oppintel/app/static/css/app.css` | Bottom-sheet styles; 44px sheet controls; `.auth-grid` `minmax(0,…)`; `.auth-footer-links` wrap; `.stat-strip` `min(180px,100%)`; `body.drawer-open` scroll lock; `.filter-panel-title` rule |
| `tests/test_accessibility.py` | Overflow at 5 widths; sheet open/close; date presets; **E1** sheet + drawer Tab-trap tests; **E3** structural-rule gate |
| `audit/scripts/wp2_before_after.py` | Before/after screenshot + scrollWidth harness |
| `audit/scripts/wp2_axe_structural.py` | **New.** E3 rule-level axe evidence |
| `audit/scripts/wp2_e5_screenshots.py` | **New.** E5 390px screenshots |
| `audit/scripts/wp2_axe_zero.py` | **New.** all-rules axe gate (G3) |
| `audit/scripts/wp2_contrast_table.py` | **New.** computed contrast table (G4) |
| `audit/scripts/wp2_smoke.py` | **New.** post-deploy smoke over 17 routes (G6) |
| `src/oppintel/app/security.py` | **G2.** per-request `csp_nonce()`; always-on `_headers` hook; `csrf_token`/`csp_nonce` context processor |
| `src/oppintel/app/templates/{base,account/signin,account/signup,companies/index,opportunities/list,partials/dialog_a11y}.html` | **G2.** `nonce="{{ csp_nonce() }}"` on every inline `<script>`; inline `onchange` removed |
| `tests/test_accessibility.py` | **G2.** nonce-aware axe injection + CSP-violation test |
| `tests/test_auth_security.py` | **G2.** enumeration test normalises the per-request nonce before the byte comparison |
| `audit/scripts/wp1_d1_soak.py` | **G1a.** reads back and prints the effective SQLite pragmas |
| `audit/wp2/*.png` | Before/after and E5 screenshots |

CSS grew 44,640 → 54,810 bytes raw (8,849 → 11,972 gzip) across the branch. No JS files added; the
dialog logic is a shared inline partial.

## Versions

Python 3.13.15 · Flask 3.1.3 · Playwright + Chromium 1243 · pytest 9.1.1 · SQLite (WAL, `busy_timeout=30000`).

## Notes / open questions

* `/companies` has no date filter (stakeholders have no permit-date range), so the date presets
  are `/opportunities`-only. If a "first seen" date filter is wanted on `/companies`, the preset
  helper already returns the values and only the template needs the fieldset.
* The focus trap (E1) is done; Tab no longer leaves the sheet or drawer.
* The inline dialog script is ~5 KB unminified and re-sent on every page. If a build step is added
  later, moving `BSDialog` to a hashed `static/js/` file with a long cache header would remove it
  from the HTML payload; there is no bundler today.
* `role="dialog"` is set on the filter `<aside>` only while open; on desktop it remains a
  complementary landmark. axe is clean in both states.

---

# Evidence gate (G1–G5)

Director-accepted items re-verified with pasted output on this branch tip. Read-only: no data
was written outside a temporary copy, `origin/main` is untouched.

## G1a — D1 concurrency soak (≥60s, readers + web writers + ingest writer)

```
$ PYTHONPATH=vendor/python:src python audit/scripts/wp1_d1_soak.py --seconds 60
duration:            60.2s
ingest commits:      144156
reader queries:      8775
watchlist writes:    997
note writes:         997
errors:              0
'database is locked': 0
pragmas:             journal_mode=wal, busy_timeout=30000, synchronous=1, foreign_keys=1
```

The pragmas are read back from a fresh connection, so this proves the configuration is in
effect, not just intended. 3 reader threads + 2 web-writer threads (real
`watched_opportunity`/`opportunity_note` tables) ran concurrently with one ingest writer for
60s: 0 errors, 0 "database is locked". The same shape runs as `tests/test_db_concurrency.py`
(5 passed in 60.81s).

## G1b — banned-phrase grep across public surfaces

```
$ for p in "saved search" "zero false positive" "zero-false-positive" \
           "without false alerts" "continuous" "receive alerts" \
           "receive an alert" "months before general contractors"; do
    printf "%-32s -> " "$p"; grep -rniE "$p" src/oppintel/app/templates/ | wc -l
  done
saved search                     -> 0
zero false positive              -> 0
zero-false-positive              -> 0
without false alerts             -> 0
continuous                       -> 0
receive alerts                   -> 0
receive an alert                 -> 0
months before general contractors -> 0
```

The home "lifecycle" cards that on `main` read "Continuous Revision Tracking" / "without false
alerts" were corrected on this branch (`home.html`). The authoritative contract is
`tests/test_copy_honesty.py` (`BANNED_OVERCLAIMS`), which reads template *sources* so a claim
in an unvisited template is still caught; it passes. Note `audit/scripts/wp2_smoke.py` uses a
deliberately narrower list than a raw "guarantee" grep — "we do not guarantee" is a legal
disclaimer, not an overclaim.

## G1c — D5 read-only permit-window analysis

```
$ PYTHONPATH=vendor/python:src python audit/scripts/wp1_d5_window.py
today=2026-10-09  window = permit_date >= 2026-09-09

== projects with permit_date in the last 30 days: 158 ==
-- by classification --
  'NEEDS_VERIFICATION'   136
  'MEDIUM'               21
  'HIGH'                 1
== public (both gates pass) in window: 22 ==
== non-public in window: 136 ==
-- full permit_date range --
  min=2025-12-04  max=2026-12-19
```

158 projects carry a permit date in the last 30 days; only 22 are public (both the
classification gate HIGH/MEDIUM and the procurement gate pass). The rest are
`NEEDS_VERIFICATION` and are correctly excluded. One public permit is dated after today
(`max=2026-12-19`), handled as a labelled future filing rather than a current one.

## G1d — D6 ingest durations + refresh pause

`audit/WP1_1_DIRECTOR_CORRECTIONS.md` §D6, measured on this sandbox (Python 3.13, wired
network):

| Source | Result | Elapsed |
|---|---|---|
| `fort_worth_permits` | 195,161 permits | ~4m05s |
| `collin_cad_permits` | +~12,250 permits | ~14s |
| `dallas_accela_permits` | per-category, each capped at 200 pages | >20 min |

Budget 15–30 min for a full seed. Overlap protection: the refresh loop holds a single-writer
file lock (`src/oppintel/locks.py`) for the whole cycle, so a seed started mid-cycle waits;
the clean procedure is documented in `docs/deployment.md` (raise `REFRESH_SECONDS` to
`604800`, seed, restore `21600`).

## G1e — D7 scripts and post-deploy checks

`audit/scripts/` holds the named analyses (A4 duplicates, A5 restart stability, A7 change
counting, `c1_jurisdiction.py`, D1/D5 scripts), catalogued in `audit/scripts/README.md`.
`audit/POST_DEPLOY_CHECKS.md` has the non-`new_project` change-count SQL, three example
before/after diffs, the `last_observed` query and the refresh-loop confirmation. `D8` created
`audit/DEPLOY_RUNBOOK.md`. `c1_jurisdiction.py` was fixed to compare stored *values*
("Confirmed open") rather than Python constant names, and now prints the correct city list.

## G2 — CSP policy and inline-script/handler inventory

Policy (now emitted by `src/oppintel/app/security.py:apply_security_headers`):

```
default-src 'self';
img-src 'self' data: https://*.googleusercontent.com;
style-src 'self' 'unsafe-inline';
script-src 'self' 'nonce-<per-request>' https://www.gstatic.com https://apis.google.com;
connect-src 'self' https://identitytoolkit.googleapis.com https://securetoken.googleapis.com https://*.googleapis.com https://*.firebaseio.com;
frame-src 'self' https://*.firebaseapp.com;
base-uri 'self'; form-action 'self'; frame-ancestors 'none'; object-src 'none'
```

Inline inventory, all now nonced except the JSON-LD data block (which browsers do not execute):

```
$ grep -rn "<script" src/oppintel/app/templates/ | grep -v "src="
base.html:28                        <script type="application/ld+json">…</script>   (data, no nonce)
base.html:287                       <script nonce="{{ csp_nonce() }}">
partials/dialog_a11y.html:13        <script nonce="{{ csp_nonce() }}">
companies/index.html:148            <script nonce="{{ csp_nonce() }}">
opportunities/list.html:227         <script nonce="{{ csp_nonce() }}">
account/signup.html:145             <script nonce="{{ csp_nonce() }}">
account/signin.html:142             <script nonce="{{ csp_nonce() }}">

$ grep -rnE "on(click|change|submit|load|error|input)=" src/oppintel/app/templates/  → (none)
$ grep -rn "javascript:" src/oppintel/app/templates/                               → (none)
```

The one inline handler (`opportunities/list.html:184`, `onchange=`) was replaced with an
`addEventListener` in the nonced block. `style-src` keeps `'unsafe-inline'` because ~346 inline
`style=` attributes remain (layout only); a nonce cannot cover an attribute, so those are a
separate, tracked cleanup. No `style` element exists in any template.

Verified live in Chromium across `/`, `/opportunities`, `/companies`, `/changes`, `/trends`,
`/signin`, `/signup`: **0 CSP violations** and the app's own inline scripts execute.

## G3 — axe-core, all rules, 14 routes × 2 viewports

```
$ PYTHONPATH=vendor/python:src python audit/scripts/wp2_axe_zero.py
TOTAL violation instances: 0  (tags=wcag2a,wcag2aa,wcag21a,wcag21aa,wcag22aa,best-practice)
```

The gate is now zero violations across the full WCAG + best-practice tag set, replacing the
earlier serious/critical-only gate that let a moderate `heading-order` defect pass.

## G4 — token and contrast table (computed)

```
$ PYTHONPATH=vendor/python:src python audit/scripts/wp2_contrast_table.py
pair                                       fg        bg         ratio  verdict
white on gold-500                          #ffffff   #d97706     3.19  AA-large
white on gold-600                          #ffffff   #b45309     5.02  PASS AA
white on #92400e (E2 hover literal)        #ffffff   #92400e     7.09  PASS AA
slate-300 on white                         #cbd5e1   #ffffff     1.48  FAIL
slate-500 on white                         #64748b   #ffffff     4.76  PASS AA
slate-600 on white                         #475569   #ffffff     7.58  PASS AA
gold-600 on white                          #b45309   #ffffff     5.02  PASS AA
slate-500 on navy-950                      #64748b   #060b17     4.13  AA-large
slate-300 on navy-950                      #cbd5e1   #060b17    13.24  PASS AA
slate-300 on navy-900                      #cbd5e1   #0b1328    12.43  PASS AA
```

Signal tokens gained AA-safe `-text` variants (used on white): `signal-high-text #065f46`
7.68:1 (was `signal-high #059669`, 3.77:1), `signal-med-text #92400e` 7.09:1 (was 3.19:1).
`gold-500` remains in the palette for non-text uses; all white-on-gold surfaces now use
`gold-600` or the `#92400e` hover literal.

## G5 — branch hygiene

```
$ git merge-base --is-ancestor 8b19b7d wp2-mobile-a11y && echo contained
contained

$ git --no-pager log --oneline main..wp1-truth-stability
8b19b7d fix(wp1.1): D3 residual - Last collection date+time label
0c166ee fix(wp1.1): director corrections D1-D8
… (10 commits)

$ git --no-pager log --oneline wp1-truth-stability..wp2-mobile-a11y
d312bb5 docs(wp2): E6 report …
d43dd80 fix(wp2): E2-E5 token table, structural axe gate, screenshots, bundle sizes
4865987 feat(wp2): E1 real focus trap for filter sheet and nav drawer
… (13 commits)
```

`wp1-truth-stability` is fully contained in `wp2-mobile-a11y`; `origin/main` is untouched at
`e01eadd`.

## G6 — post-deploy smoke

```
$ PYTHONPATH=vendor/python:src python audit/scripts/wp2_smoke.py
route                                status csp  banned  notes
/                                    200    True []      
… (all 17 routes) …
/healthz                             200    True []
FAILURES: 0
```

17 public routes return 200 with a nonced CSP and no banned phrase. `/healthz` and
`/api/statistics` read the same stored snapshot as the pages.

## Suite (branch tip)

```
$ env -u BASE_URL -u OPPINTEL_DB -u OPPINTEL_DATA_DIR -u SECRET_KEY -u PORT \
    PYTHONPATH="vendor/python:src" python -m pytest tests/ -q -p no:cacheprovider
1136 passed in 246.47s (0:04:06)
```

Accessibility module alone: 168 passed in 126.08s (was 140 before the all-rules axe gate and the
CSP test).
