# PR — wp2-mobile-a11y → main

> **Do not merge automatically.** Owner action. No push has been made; `origin/main` is
> untouched at `e01eadd`.

## Purpose

Mobile usability and accessibility hardening on top of the truth/stability work, plus the
H-series Director items: retention, backup, and verification evidence.

## Scope (real diff, `main..wp2-mobile-a11y`)

```
$ git --no-pager diff --shortstat main..wp2-mobile-a11y
 290 files changed, 23927 insertions(+), 266 deletions(-)

$ git --no-pager diff --stat main..wp2-mobile-a11y -- src tests | tail -1
 61 files changed, 4162 insertions(+), 234 deletions(-)
```

The 290-file total includes `audit/` reports and screenshots; the code surface is the 61 files
under `src/` and `tests/` (4,162 insertions).

## What changed

| Area | Change | Evidence |
| --- | --- | --- |
| Mobile filters | filter bottom sheet + date presets replace the empty dropdowns | `tests/test_accessibility.py` |
| Focus management | real focus trap for the filter sheet and nav drawer | `src/oppintel/app/templates/partials/dialog_a11y.html` |
| Overlap/overflow | `overflow-x` removal + responsive fixes; no horizontal scroll at 360–1440 | `audit/scripts/wp2_overflow.py` |
| Contrast | primary button white-on-gold-500 (3.19:1) → gold-600 (5.02:1); signal text uses `*-text` variants | `audit/scripts/h5_contrast_full.py` |
| CSP | per-request nonce; zero axe/CSP violations asserted | `tests/test_accessibility.py` |
| Retention | `retention.py` + `prune-raw`, inert by default | `tests/test_retention.py` |
| Backup | `backup.py` + `ops/backup.sh` + CLI `backup`, keep newest N | `tests/test_backup.py` |
| Lock recovery | killed holder releases the flock; no stale lock | `tests/test_db_concurrency.py` |

## Tests (real output, wp2 tree)

```
$ env -u BASE_URL -u OPPINTEL_DB -u OPPINTEL_DATA_DIR -u SECRET_KEY -u PORT \
    PYTHONPATH="vendor/python:src" python -m pytest tests/ -q
1151 passed in 246.72s (0:04:06)

$ ... python -m pytest tests/test_accessibility.py -q
168 passed in 125.42s (0:02:05)
```

The accessibility module needs Playwright's browser: `python -m playwright install chromium`.

## Screenshots (H5 button options)

`audit/wp2/h5_desktop_home_default.png` (gold-600, 5.02:1) vs `h5_desktop_home_hover.png`
(#92400e, 7.09:1); mobile pair `h5_mobile_home_default.png` / `h5_mobile_home_hover.png`.

## Risk / rollback

* The retention and backup paths are **inert by default** (`retention.enabled: false`); no data
  is deleted or written unless a human sets the flag and runs `--apply`.
* Rollback: revert the merge commit. No destructive migration.
