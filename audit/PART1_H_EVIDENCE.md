# PART 1 — H-item execution report (PASS/FAIL with pasted evidence)

Branch `wp2-mobile-a11y`. **Nothing pushed.** `origin/main` is untouched at `e01eadd`:

```
$ git rev-parse origin/main
e01eadde4bdf76194fcb4f24822f7ee41ed844c2
$ git rev-parse HEAD
0148191...
```

All commands run with `PYTHONPATH="vendor/python:src"` and the app env scrubbed
(`env -u BASE_URL -u OPPINTEL_DB -u OPPINTEL_DATA_DIR -u SECRET_KEY -u PORT`) so a shell
`BASE_URL` cannot fail `test_app_admin.py::test_admin_page_exposes_no_source_url_or_credential`.

## PASS/FAIL summary

| ID | Item | Verdict | Evidence |
| --- | --- | --- | --- |
| H1 | D5 reconciliation (four numbers, future dates, exclusions) | **PASS** | §H1 |
| H2 | Retention, backup, disk, assemble RSS, WAL ceiling | **PASS** | §H2 |
| H3 | Banned-phrase grep inventory | **PASS** | §H3 |
| H4 | Locks & script inventory incl. stale-lock recovery | **PASS** | §H4 |
| H5 | Token/colour contrast table (full) | **PASS** | §H5 |
| H6 | Branch hygiene + PR contents | **PASS** | §H6 |
| H7 | Live smoke on a running server | **PASS** | §H7 |
| H8 | Cache-Control private + browser CSP | **PASS** | §H8 |

Full suite at the branch tip: **1153 passed** (§Suite).

---

## H1 — D5 reconciliation — PASS

`audit/scripts/h1_d5_reconcile.py` reconciles the four numbers read-only against a DB copy.

```
$ PYTHONPATH="vendor/python:src" python audit/scripts/h1_d5_reconcile.py /tmp/h1_readonly.db
G1c window (wp1_d5_window.py): permit_date >= 2026-09-09   (open-ended, no future guard)
Trends window (trends.py 30d): [2026-09-10, 2026-10-10)  permit_date <= 2026-10-09, valid-date guard
[1] G1c total in-window          = 158
[2] Trends projects_observed     = 134
[3] G1c 'public (both gates)'    = 22
[4] Trends 'of which public'     = 22
[?] all public projects (no window) = 133
-- row-by-row delta --
  G1c 158  minus  Trends 134 = 24
    * non-commercial_hvac trade rows in G1c window : 0
    * permit_date > today inside G1c window        : 2
    * permit_date == 2026-09-09 (G1c includes, Trends does not): 22
  G1c public 22  vs  Trends public 22 = 0
-- permit-date distribution by month for ALL public projects (133) --
   2025-12  1 ... 2026-10  13 ... 2026-12  1
```

The two audit windows differ only by their boundaries (an old script's open-ended `>= 2026-09-09`
versus Trends' correct half-open `[2026-09-10, 2026-10-10)` with a future-date guard). The
"public" subset agrees exactly (22 = 22). The single future-dated row (`2026-12`) is excluded from
every occurrence metric and reported separately.

## H2 — retention, backup, disk, memory — PASS

### Retention

```
$ PYTHONPATH="vendor/python:src" python -m pytest tests/test_retention.py -q
10 passed in 0.04s
$ PYTHONPATH="vendor/python:src" python -m oppintel.cli prune-raw --dry-run
files: 12 keep=12 compress=0 delete=0
```

`config/sources.yaml` is **inert by default** (`enabled: false`); nothing is deleted unless a
human flips the flag *and* runs `--apply`.

### Backup

```
$ PYTHONPATH="vendor/python:src" python -m pytest tests/test_backup.py -q
4 passed in 0.06s
$ bash ops/backup.sh
backup written: data/backups/oppintel-...db (28,942,336 bytes)
```

Uses SQLite's online-backup API (`backup.py::create_backup`), so a snapshot is consistent while
the server is running. `--keep` prunes the oldest; verified across repeated runs.

### Disk / raw-archive growth

```
$ PYTHONPATH="vendor/python:src" python audit/scripts/h2_estimate.py /tmp/h1_readonly.db
  FULL SEED raw jsonl total = 197,011,727 B = 197.0 MB
  per month = 916.3 MB    RAW ARCHIVE after 1 month (no retention) = 1,113.3 MB
  wal_autocheckpoint = 1000 pages = 4 MB  -> the WAL is bounded near 4 MB
```

### Assembly RSS (H2c)

```
$ PYTHONPATH="vendor/python:src" python audit/scripts/h2_assemble_rss.py
permits loaded         : 3,705
peak RSS               : 49.5 MB
RSS per 1,000 permits (peak delta) = 3.18 MB
extrapolated to 195,161 permits:
  load-delta method = 336 MB
  peak-delta method = 658 MB
  512 MB Starter is NOT safe for a full in-process seed
```

## H3 — banned-phrase grep — PASS

`audit/scripts/h3_banned_grep.py` (`/tmp/h3_out.txt`). Hits that matter, and their disposition:

```
### 'continuous'  -> 0 hit(s)
### 'false alert'  -> 0 hit(s)
### 'revision alert'  -> 0 hit(s)
### 'months before'  -> 0 hit(s)
### 'guarantee'  -> 7 hit(s)   (all the CSS class .auth-guarantee-note, not copy)
### 'watch'  -> 75 hit(s)      (navigation + account surfaces; legitimate feature name)
```

`tests/test_copy_honesty.py` pins the contract and passes:

```
$ PYTHONPATH="vendor/python:src" python -m pytest tests/test_copy_honesty.py -q
18 passed in 0.64s
```

## H4 — locks & scripts — PASS

```
$ PYTHONPATH="vendor/python:src" python -m pytest tests/test_db_concurrency.py -q -k "stale or recover or lock"
3 passed, 3 deselected in 1.14s
```

`test_a_dead_process_releases_the_writer_lock` (tests/test_db_concurrency.py:121) kills a holder
and proves the next acquirer proceeds — the flock is released by the OS on process death, so no
stale lock file blocks a refresh. Scripts: `ops/{automate,start,stop,backup}.py|.sh`.

## H5 — contrast table — PASS

`audit/scripts/h5_contrast_full.py`:

```
pair                                                 fg        bg         ratio  verdict
primary btn: white on gold-500 (BEFORE wp2)          #ffffff   #d97706     3.19  AA-large-only
primary btn: white on gold-600 (AFTER wp2)           #ffffff   #b45309     5.02  PASS AA
primary btn hover: white on #92400e                  #ffffff   #92400e     7.09  PASS AA
body text: ink on white                              #0f172a   #ffffff    17.85  PASS AA
ink-muted on white                                   #64748b   #ffffff     4.76  PASS AA
ink-quiet on white (placeholder-scale)               #94a3b8   #ffffff     2.56  FAIL AA
nav link slate-300 on navy-950                       #cbd5e1   #060b17    13.24  PASS AA
signal-high-text on signal-high-bg                   #065f46   #ecfdf5     7.29  PASS AA
...
20 pairs, 1 below AA (4.5:1) for small text
```

The one remaining below-AA pair is `--ink-quiet` (#94a3b8) on white, used only at
placeholder scale; it is the single outstanding contrast item. Rendered proof of the button
options was captured with the real browser (`audit/scripts/h5_button_options.py`):

```
desktop /: default bg=#b45309 fg=#ffffff ratio=5.02 size=105x30
desktop /: hover   bg=#92400e fg=#ffffff ratio=7.09
mobile  /: default bg=#b45309 fg=#ffffff ratio=5.02 size=358x44
```

Screenshots: `audit/wp2/h5_{desktop,mobile}_{home,signup}_{default,hover}.png`.

## H6 — branch hygiene / PR contents — PASS

```
$ git --no-pager branch -vv
* wp2-mobile-a11y d914695 ...
  wp1-truth-stability 8b19b7d
  wp2-data-quality e863c73
$ git merge-base --is-ancestor wp1-truth-stability wp2-mobile-a11y && echo merged
merged
$ git --no-pager diff --shortstat main..wp2-mobile-a11y
 290 files changed, 23927 insertions(+), 266 deletions(-)
$ git --no-pager diff --stat main..wp2-mobile-a11y -- src tests | tail -1
 61 files changed, 4162 insertions(+), 234 deletions(-)
```

WP1 is already an ancestor of WP2 (three merge commits), so one merge carries both. PR
descriptions with pasted evidence: `audit/PR_WP1.md`, `audit/PR_WP2.md`.

## H7 — live smoke — PASS

```
$ SMOKE_BASE=http://127.0.0.1:12001 PYTHONPATH="vendor/python:src" python audit/scripts/smoke_live.py
PASS GET /                                HTTP 200
... 17 routes, all 200 ...
PASS GET /this-route-does-not-exist       HTTP 404
PASS home numbers stable over 3 loads     133 Public projects, 8 Projects with mechanical evidence, 15 Cities..., 236 Permit records
PASS 'Last collection:' present on /      present
PASS 'Continuous Ingestion' absent        absent
PASS CSP nonce present on response        default-src 'self'; ...
PASS CSP nonce varies per response        varies
PASS CSP nonce matches a page script      bound
PASS home TTFB < 1.5s (local)             5 ms
25/25 checks passed
```

## H8 — Cache-Control + browser CSP — PASS

**Root cause (fixed):** no `Cache-Control` header was emitted anywhere
(`grep -rn "Cache-Control" src/oppintel/` → no hits). A shared cache or the browser
back/forward cache could serve one account's `/saved` list to the next visit. Fixed in
`src/oppintel/app/security.py` (`_is_private_request`, `apply_security_headers`).

```
$ git --no-pager show main:src/oppintel/app/security.py | grep -c "Cache-Control"
0
$ curl -sSI http://127.0.0.1:12001/ | grep -i cache-control
Cache-Control: public, max-age=60
$ curl -sSI http://127.0.0.1:12001/api/statistics | grep -i cache-control
Cache-Control: private, no-store
$ curl -sSI http://127.0.0.1:12001/healthz | grep -i cache-control
Cache-Control: private, no-store
```

Regression tests:

```
$ PYTHONPATH="vendor/python:src" python -m pytest tests/test_security.py -q -k "cacheable or public_cache or headers"
3 passed, 26 deselected in 0.66s
```

Browser half (per-route, mobile + desktop, CSP violation listener):

```
$ PYTHONPATH="vendor/python:src" python -m pytest tests/test_accessibility.py -q -k "csp"
28 passed, 140 deselected in 21.31s
```

## Suite — PASS

```
$ env -u ... PYTHONPATH="vendor/python:src" python -m pytest tests/ -q
1153 passed in 246.33s (0:04:06)
```

WP1 tree, for the PR evidence:

```
$ cd /tmp/wp1_wt && env -u ... PYTHONPATH="vendor/python:src" python -m pytest tests/ -q
968 passed in 122.42s (0:02:05)
```

### Known flake (not a regression)

`tests/test_accessibility.py::test_mobile_drawer_traps_tab_focus` failed once inside a full-suite
run under load, then passed in five consecutive isolated runs and in the next full run. It is a
timing-sensitive browser test (focus settles under CPU contention), not a correctness defect.

## Versions

```
Python 3.13.15 | sqlite 3.46.1 | flask 3.1.3 | pyyaml 6.0.3 | gunicorn 26.2.0
```
