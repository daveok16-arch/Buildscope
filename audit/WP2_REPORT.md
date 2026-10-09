# WP2 — Mobile & Accessibility Report

**Branch:** `wp2-mobile-a11y`
**HEAD:** `5277457` — `docs(wp2): B1/B2/B3 report with pasted evidence + before/after screenshots`
**Code HEAD:** `3fb8e4d` — `fix(wp2): mobile filter bottom sheet + date presets + overflow fixes`
**Base:** `d052430` (merge of `wp1-truth-stability` into `wp2-mobile-a11y`)
**Date:** 2026-10-09
**Scope:** B1 horizontal overflow, B2 touch targets, B3 mobile filter bottom sheet.
**Result:** all three verified in a real browser (Playwright + Chromium 1243). Full suite: **1075 passed**.

---

## Summary of state

| Item | Status | Evidence |
|---|---|---|
| B1 — overflow at 360/390/430/820/1440, every public route | PASS | 70 parametrised cases, §B1 |
| B2 — touch targets ≥ 44px on 7 routes | PASS | 0 undersized, §B2 |
| B3 — mobile filter bottom sheet + date presets | PASS | 2 sheet cases + 1 preset case, §B3 |
| Full test suite | PASS | 1075 passed, §Suite |
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

## Suite

```bash
env -u BASE_URL -u OPPINTEL_DB -u OPPINTEL_DATA_DIR -u SECRET_KEY -u PORT \
  PYTHONPATH="vendor/python:src" python -m pytest tests/ -q -p no:cacheprovider
```

```
1075 passed in 231.51s (0:03:51)
```

Accessibility module alone: `109 passed in 75.81s`. The B1/B2/B3 subset:
`80 passed, 29 deselected in 46.76s`.

---

## Files changed in this branch (B1–B3)

| File | Change |
|---|---|
| `src/oppintel/app/main.py` | `_date_presets()` helper; `date_presets` + `active_filter_count` into the opportunities context |
| `src/oppintel/app/templates/opportunities/list.html` | `Filters (n)` button, backdrop, sheet open/close JS, date fieldset with presets |
| `src/oppintel/app/templates/companies/index.html` | Same sheet pattern for stakeholders |
| `src/oppintel/app/templates/trends.html` | Removed the inline `min-width:240px` card floor |
| `src/oppintel/app/static/css/app.css` | Bottom-sheet styles; 44px sheet controls; `.auth-grid` `minmax(0,…)`; `.auth-footer-links` wrap; `.stat-strip` `min(180px,100%)` |
| `tests/test_accessibility.py` | Overflow at 5 widths with named offenders; sheet open/close; date presets |
| `audit/scripts/wp2_before_after.py` | Before/after screenshot + scrollWidth harness |
| `audit/wp2/*.png` | Before/after screenshots |

CSS grew 52,365 → 54,646 bytes (+2,281, ~4%). No JS files added; the sheet logic is ~30 lines
inline per template.

## Versions

Python 3.13.15 · Flask 3.1.3 · Playwright + Chromium 1243 · pytest 9.1.1 · SQLite (WAL, `busy_timeout=30000`).

## Notes / open questions

* `/companies` has no date filter (stakeholders have no permit-date range), so the date presets
  are `/opportunities`-only. If a "first seen" date filter is wanted on `/companies`, the preset
  helper already returns the values and only the template needs the fieldset.
* The sheet traps focus by moving focus in and returning it on close; it does not yet implement a
  full focus trap (Tab can still leave the sheet into the page behind the backdrop). axe is clean
  and the practical behaviour is correct, but a true trap is a candidate follow-up.
