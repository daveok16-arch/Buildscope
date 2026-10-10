# M2 Release — home metric band

**Branch:** `wp3-visual` · **Commit:** `572df3b` · **Released to preview:** yes
**Preview:** https://work-1-bskoteqbanzlqswf.prod-runtime.all-hands.dev
**`origin/main`:** unchanged (`fc7b0e3`).

## What changed (visible)
- The home headline strip is now a labelled four-cell **metric band**: Public projects /
  With mechanical evidence / Permit records / Cities with projects.
- Figures **count up** when scrolled into view, then settle on the exact stored value.
- Each label states its exact population and carries the definition as a tooltip; a link goes
  to `/trends` ("See how they change over time").

## Why (audit items a–d)
The audit found the home numbers and `/trends` numbers disagreed. Root cause: more than one
population was being read under the same word, and `permit` vs linked `permit` were conflated.
The band now reads the single `market_stat_snapshot` row (`stat_snapshot.py`), so every page
that shows a headline figure reads the same snapshot. `/trends` remains a windowed view of the
same records, and its page states that plainly; the two are different populations by design,
not a disagreement.

## Evidence
| Check | Result |
|---|---|
| Full suite | 1254 passed |
| New tests | 4 passed (`tests/test_home_metric_band.py`) |
| axe all rules, `/` mobile+desktop | 0 violations |
| Overflow 390/820/1440 | none (scrollWidth == innerWidth) |
| Rendered == data-count | exact match for all 4 cells |
| Public preview | band present, values 133/8/236/15 |

## Screenshots (ignored; on disk)
`audit/releases/m2/home_{mobile,tablet,desktop}.png`

## Rollback
Re-push the previous preview commit `bf36e48`.
