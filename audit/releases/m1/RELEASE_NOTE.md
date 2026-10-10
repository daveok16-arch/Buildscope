# M1 Release — motion system + custom filter controls

**Branch:** `wp3-visual` · **Commit:** `bf36e48` · **Released to preview:** yes
**Preview:** https://work-1-bskoteqbanzlqswf.prod-runtime.all-hands.dev
**`origin/main`:** unchanged (`fc7b0e3`) — nothing pushed to main.

## What changed (visible)

- Every filter `<select>` on **/opportunities** and **/companies** is now a branded
  listbox/combobox instead of the native control. On Android the native popup is the black
  system sheet the user flagged; it is now gone — replaced by a white, rounded, searchable
  panel with a check on the selected row.
- On mobile the picker opens as a **bottom sheet** (drag handle, rounded top, type-to-filter,
  Apply/Clear) instead of a full-screen system list.
- Filters now show **applied-filter chips** with one-tap removal (Nike pattern).
- Added the motion foundation: scroll reveal, hero count-up, sticky-header compaction.
- A `<noscript>` fallback keeps the mobile filter panel reachable with JS disabled.

## Evidence (all reproducible)

| Check | Result | Evidence |
|---|---|---|
| Existing suite | 1246 passed | `pytest tests/ -q` |
| New M1 tests | 5 passed | `tests/test_controls.py` |
| Comboboxes built (desktop) | 5 / 5, native hidden | `audit/live/M1_probe.txt` |
| Keyboard type "plan" → selects | `Plano` | `audit/live/M1_probe.txt` |
| Mobile sheet geometry | `position:fixed`, bottom 0, radius 20px | `audit/live/M1_probe.txt` |
| Option tap targets | min 44px, max 44px (n=17) | `audit/live/M1_gates.txt` |
| No-JS mobile filter reachable | visible + submits `city=Plano` | `audit/live/M1_gates.txt` |
| axe WCAG 2.1 AA | 0 violations · 16 routes + open combobox | `audit/axe_results.json` |
| Console/CSP errors | none | `audit/live/M1_probe.txt` |
| JS budget | 15.1 KB gzip total | see below |

### Byte budget
```
vendor/motion/motion.min.js   raw 24607  gzip 9773
js/motion.js                  raw  6699  gzip 2470
js/controls.js                raw 10521  gzip 3468
TOTAL JS                               gzip 15065  (cap 35000)
css/app.css                   raw 63870  gzip 14338
```

## Public-preview verification (phone)
5 comboboxes present, mobile sheet `fixed/20px`, zero console errors on
`/opportunities` from the public host.

## Screenshots (ignored in git; on disk)
`audit/releases/m1/*_390.png` — home, opportunities, companies, trends, new_filings
`audit/live/M1_public_mobile_picker.png` — the branded picker live on the public host.

## Rollback
`git reset --hard pre-wp-rollback` restores `e01eadd`; or re-push the previous preview
commit `a6eb637`.
