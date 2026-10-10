# PR — wp1-truth-stability → main

> **Do not merge automatically.** Owner action. No push has been made; `origin/main` is
> untouched at `e01eadd`.

## Purpose

Make every public headline figure come from one stored snapshot, give every window an honest
definition, and stop future-dated permits from leaking into "current" views.

## Scope (real diff, `main..wp1-truth-stability`)

```
$ git --no-pager diff --stat main..wp1-truth-stability -- src tests | tail -1
 43 files changed, 2298 insertions(+), 115 deletions(-)

$ git --no-pager log --oneline main..wp1-truth-stability
8b19b7d fix(wp1.1): D3 residual - Last collection date+time label
0c166ee fix(wp1.1): director corrections D1-D8
150e033 fix(wp1.1): C3 copy honesty, C5 seed docs, C6 script archive, C7 post-deploy checks
94985a1 fix(wp1.1): C1/C2/C4 corrections and future-date coverage guard
3c449cb WP1.1 closeout final
a45d938 fix(wp1): close remaining future-date leaks; honest jurisdiction count
c8a3a76 docs(wp1): Director-facing implementation report for Truth & Stability
6182532 feat(dates,how-it-works): label future filing dates, publish the ingest funnel
5349a59 feat(trends,changes): honest windows, cumulative labels, real change total
78752c4 feat(stats): one stored snapshot for every public headline figure
996751f docs(ops): require a persistent Render disk for stable counts
```

## What changed

| Area | Change | Evidence |
| --- | --- | --- |
| One source of truth | `app/stat_snapshot.py`: a stored snapshot every public headline reads | `src/oppintel/app/stat_snapshot.py` |
| Honest windows | `trends.py` half-open `[start,end)` windows; future dates excluded and counted | `src/oppintel/trends.py` |
| Date handling | `dates.py`: `is_future`, parse/validate; future permits labelled, not dropped silently | `src/oppintel/dates.py` |
| Coverage guard | `coverage.py` excludes future-dated permits from `latest_record_date` | `tests/test_coverage.py` |
| Copy honesty | banned-overclaim grep as a test | `tests/test_copy_honesty.py` |
| Concurrency | single-writer `locks.py` | `src/oppintel/locks.py`, `tests/test_db_concurrency.py` |

## Tests (real output)

```
$ cd /tmp/wp1_wt && env -u BASE_URL -u OPPINTEL_DB -u OPPINTEL_DATA_DIR -u SECRET_KEY -u PORT \
    PYTHONPATH="vendor/python:src" python -m pytest tests/ -q
968 passed in 122.42s (0:02:05)
```

## Risk / rollback

* Low risk to the web layer: the snapshot is additive; routes read it through
  `OpportunityService`.
* Rollback: revert the merge commit. No schema migration is destructive; `_migrate_app_tables`
  is additive.

## Merge order

`wp1-truth-stability` is **already an ancestor** of `wp2-mobile-a11y`
(`git merge-base --is-ancestor wp1-truth-stability wp2-mobile-a11y` → exit 0), so merging
`wp2-mobile-a11y` carries WP1 with it. Merge `wp2-mobile-a11y` if merging once; merge `wp1`
first only if a separate review of the truth work is required.
