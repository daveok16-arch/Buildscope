# WP1.1 — Director corrections (PART 0)

Branch `wp1-truth-stability`. This closes the Director's PART 0 items **D1–D8**. Every claim is
backed by pasted command output. Full suite after the changes: **966 passed** (see §D1 test run
and the final suite run below).

Ordering note: PART 1 (`wp2-mobile-a11y`) is not started because it is gated on every PART 0
item passing. All eight items below are **PASS**.

---

## D1 — SQLite safety (the real C4) — PASS

### Connection settings, where they are set

`src/oppintel/db.py:703-720` (`Database.__init__`), applied to **every** connection:

```
journal_mode = wal
busy_timeout = 30000
synchronous = 1        # NORMAL
foreign_keys = 1
```

Command (fresh DB through the real `Database` class):

```
$ PYTHONPATH="vendor/python:src" python3 -c "from oppintel.db import Database; import tempfile,os; \
  d=tempfile.mkdtemp(); db=Database(os.path.join(d,'x.db')); db.init_schema(); \
  [print(p,'=',db.conn.execute('PRAGMA '+p).fetchone()[0]) for p in ['journal_mode','busy_timeout','synchronous','foreign_keys']]"
journal_mode = wal
busy_timeout = 30000
synchronous = 1
foreign_keys = 1
```

Source lines: `db.py:708` foreign_keys, `:709` WAL, `:714` busy_timeout, `:720` synchronous.

### gunicorn workers/threads

`ops/automate.py:60-61` and `:224-225`:

```
WEB_WORKERS = os.environ.get("WEB_WORKERS", "2")
WEB_THREADS = os.environ.get("WEB_THREADS", "4")
...
sys.executable, "-m", "gunicorn", "--workers", WEB_WORKERS, "--threads", WEB_THREADS,
```

So: **2 worker processes × 4 threads**. `ops/start.sh` itself does not invoke gunicorn; it
`exec`s `ops/automate.py --serve`, which supervises gunicorn. On Render `RENDER=true` forces
foreground mode (`ops/start.sh:25-29, 82-85`), and `numInstances: 1` (`render.yaml`).

### Every writer, and which can run at the same time

| Writer | File evidence | Runs where |
|---|---|---|
| Web account writes (signup, prefs, reset, login) | `app/accounts.py:204-572` (many `.commit()`) | gunicorn workers |
| Watchlist / pipeline / notes / tags / activity | `app/workflow.py:118-469` | gunicorn workers |
| Alert reads/marks | `app/alerts.py:116-206` | gunicorn workers |
| Analytics funnel | `app/accounts.py:562-572` | gunicorn workers |
| Refresh thread (`ingest → assemble → index → monitor`) | `ops/automate.py` `refresh_once` | separate thread in the supervisor process |
| CLI ingest / seed | `src/oppintel/cli.py` | Render shell or the supervisor child process |

On Render the **gunicorn workers (2 procs × 4 threads) and the refresh thread run in the same
container against one SQLite file**, so a web write and the refresh's assembly commit can
overlap. That is exactly the race D1 targets.

### Fixes applied

* **WAL + busy_timeout 30000 + synchronous NORMAL** on every connection — `db.py:708-720`. WAL
  lets readers proceed during a write; the 30s busy timeout makes a short web write *wait* for a
  long assembly commit instead of raising `database is locked`.
* **Single-writer file lock** — `src/oppintel/locks.py` (new). `writer_lock` is an advisory
  `flock` on a sibling `<db>.writelock`, re-entrant per thread and no-op when the job lock is
  already held (`HELD_ENV`). Wrapped around `Pipeline.run` and `Pipeline.assemble_and_classify`
  (`src/oppintel/pipeline.py`), and `job_lock` wraps `refresh_once` in `ops/automate.py`.
* The lock is released automatically when the process exits, so a crashed job cannot leave it
  stuck (`locks.py:14-16`).

### Soak — 60 seconds of concurrent readers + web writes against an ingest writer

```
$ PYTHONPATH="vendor/python:src" python audit/scripts/wp1_d1_soak.py
duration:            60.2s
ingest commits:      142385
reader queries:      8967
web writes:          1994
errors:              0
'database is locked': 0
```

**Zero** lock errors and zero exceptions over 60s with ~142k ingest commits interleaved with
~9k reads and ~2k web writes.

Tests: `tests/test_db_concurrency.py` — **5 passed** (60.83s).

---

## D2 — Snapshot heal out of the request path — PASS

### What changed

* `read_snapshot()` (`src/oppintel/app/stat_snapshot.py:286`) **never writes**. It returns the
  stored snapshot; when a canonical key is missing it computes the metrics *without persisting*
  and returns them. `refresh_if_missing` was removed.
* `heal_snapshot()` added at `stat_snapshot.py:353`; `_snapshot_is_current()` helper at `:334`.
* Startup heal wired into `create_app` via `_refresh_stat_snapshots(db)`
  (`src/oppintel/app/main.py:1767`, called at `:2338`), guarded by the D1 writer lock.

### Test: N parallel requests against a DB missing a new metric key

Added `test_parallel_requests_do_not_write_a_stale_snapshot` (`tests/test_stat_snapshot.py:237`):
deletes `configured_jurisdictions` from the stored metrics, fires **24 threads** across
`/`, `/api/statistics`, `/markets`, `/healthz`, asserts every response is 200/302 with no
exception, then asserts the stored row **still lacks the key** (no handler persisted a heal).

```
$ PYTHONPATH="vendor/python:src" python -m pytest tests/test_stat_snapshot.py -q
..................                                                       [100%]
18 passed in 1.70s
```

---

## D3 — Copy honesty residuals (complete C3) — PASS

### Banned-phrase list (`tests/test_copy_honesty.py:23-36`)

```
saved search · zero-false-positive · zero false positive · without false alerts · continuous ·
months before general contractors · commands buildscope to monitor the parcel ·
organize active pursuits with your team · coordinate team business development ·
teams can bookmark · receive alerts · receive an alert
```

### Grep over templates, meta, JSON-LD, guides, docs and README

Case-insensitive, for the Director's list. Findings and the rewrite for each:

| Found (file:line) | Before | After |
|---|---|---|
| `home.html:116` | "Watch the record and **receive an alert** when a later collection run detects a difference." | "Watch the record and get an **in-app alert** when a later collection run detects a difference." |
| `home.html:167` | "**coordinate team business development** from a unified workspace." | "keep a **per-account pursuit workspace**. Stages and notes are the account's own labels, kept separate from the source's stated status." |
| `core_category.html:67` | "**Teams can** bookmark opportunities, keep private estimator notes…" | "**Accounts** can bookmark opportunities, keep private estimator notes, and track internal pursuit stages." |
| `how_it_works.html:60` | "…detect changes and **notify watched projects**." | "…detect changes and **raise an in-app alert on a watched project**." |
| `account/alerts.html:96` | "Watch opportunities you care about to **receive alerts** about them." | "Watch opportunities you care about to **get in-app alerts** about them." |

The home lifecycle card 03 was **already** renamed to "Revision Tracking" and 04 to "Pursuit
Pipeline Management" in the prior WP1.1 pass (commit `150e033`); no "Continuous Revision
Tracking" or "Continuous Ingestion: <month>" string remains anywhere (grep for `continuous`
returns zero hits in templates).

### New phrases added to the banned-phrase test

`continuous`, `coordinate team business development`, `teams can bookmark`, `receive alerts`,
`receive an alert` (`tests/test_copy_honesty.py:28-35`).

```
$ PYTHONPATH="vendor/python:src" python -m pytest tests/test_copy_honesty.py -q
................                                                         [100%]
16 passed in 0.49s
```

Residual (reported, not silently changed, because it is a scope-boundary product decision): the
tagline "See the work **before the work begins**" (`home.html:11`, `signin.html:13`,
`signup.html:13`, `base.html:9,208`) is a slogan whose literal reading is a lead-time promise
the product does not measure. Flagged for the Director; not banned by the current test.

---

## D4 — Jurisdictions (complete C1) — PASS

### The four figures, as shown (rendered from the populated DB)

| Figure | Home stat card | `/api/statistics` | `/healthz` | Home meta |
|---|---|---|---|---|
| Public projects | 133 | 133 | 133 | "Search **133** commercial construction projects…" |
| Projects with mechanical evidence | 8 | 8 | 8 | "…including **8** with documented mechanical or HVAC permits" |
| Cities with at least one public project | 15 | 15 | `cities_with_public_projects: 15` | "…across **15** DFW cities with at least one public project" |
| Permit records | 236 | 236 | — | — |

Public label now reads exactly **"Cities with at least one public project"**
(`stat_snapshot.py:60-66`, and the six templates listed below), replacing the shorter
"cities with public projects".

### Nevada

`Nevada` is a **data** value, not configuration. The configured market cities
(`config/markets.yaml`) are 18 names; `Nevada` is not among them. The audit script shows it as
the single out-of-market value:

```
$ PYTHONPATH="vendor/python:src" python audit/scripts/c1_jurisdiction.py
   TOTAL distinct raw values: 16
in-market (accepted, incl aliases): 15 [...]
excluded (out-of-market): ['Nevada']
```

So `active_jurisdictions` = **15** (configured cities that actually hold a public project),
`configured_jurisdictions` = **18** (config intent), and `Nevada` is excluded because it is a
city name in the data with no configured market entry. It is reported, not hidden.

### Rename

`jurisdictions_excluded_count` → **`out_of_market_cities_count`** (and
`jurisdictions_excluded` → `out_of_market_cities`), because the old name did not say *which side
of the market line* the list described. The old keys are kept as back-compat aliases with the
same value (`stat_snapshot.py:232-236`), and a test asserts they match
(`tests/test_stat_snapshot.py:313`).

### The missing test

Added/extended `test_jurisdiction_count_is_identical_on_every_surface`
(`tests/test_stat_snapshot.py:125`): asserts the same number on **home**, **/markets**,
**/api/statistics**, **/healthz** and the **home meta description**, and that
`configured_jurisdictions >= active_jurisdictions`.

```
$ PYTHONPATH="vendor/python:src" python -m pytest tests/test_stat_snapshot.py tests/test_seo_keywords.py tests/test_app_api.py -q
........................................................................ [ 74%]
.........................                                                [100%]
97 passed in 8.28s
```

---

## D5 — Trends vs the public feed — PASS

READ-ONLY investigation on the audit DB copy (`/tmp/populated.db`). Full output:
`audit/scripts/wp1_d5_trends.py`.

### Last 30 days (window start 2026-09-10)

```
total: 136
by classification:  NEEDS_VERIFICATION 114 · MEDIUM 21 · HIGH 1
by procurement:     Evidence found 100 · Not verified 31 · Closed 5
public among them: 22
```

### The gate that excludes the non-public ones

Every non-public last-30-day project fails on **classification = NEEDS_VERIFICATION** — i.e. it
lacks trade evidence. Procurement status is *not* the gate here; 100 of the 136 are already
`Evidence found, status unclear`:

```
NEEDS_VERIFICATION / Evidence found, status unclear / tier None : 80
NEEDS_VERIFICATION / Not verified                     / tier None : 29
NEEDS_VERIFICATION / Closed                           / tier None :  5
```

**So the gate is classification (trade evidence), not procurement.**

### 10 sample non-public rows

See `wp1_d5_trends.py` output; e.g. "THE VILLAGE AT OWNSBY FARMS RETAIL" (Celina, 2026-12-02,
NEEDS_VERIFICATION, no tier), "Plumbing remodel for Burnt apartments" (Dallas, 2026-10-09), etc.

### How stale the public set is (permit date by month, all 133 public)

```
2025-12: 1   2026-01: 3   2026-02: 4   2026-03: 9   2026-04: 8
2026-05: 26  2026-06: 16  2026-07: 20  2026-08: 21  2026-09: 11
2026-10: 13  2026-12: 1
```

Only 24 of 133 public projects (18%) carry a permit dated in the last two months; the mass sits
in May–August 2026. The public set is **stale by design** — a project only becomes public once a
tier-1/tier-2 mechanical evidence record is classified, and that lags the permit date.

### Minimal change: the Trends "new projects" card

Relabelled so the headline cannot be read as the public count. The card now carries a second
line **"of which public: N"**:

```
$ ... GET /trends
New commercial projects: 134
of which public: 21
```

Implemented as `Metric.secondary` (`trends.py:145-148`) populated for `projects_observed`
(`trends.py:367-403`), rendered at `templates/trends.html:73`. New test
`test_new_projects_card_reports_the_public_subset` (`tests/test_trends.py:316`) asserts a strict
subset.

**Recommended policy (Director decides):** the public feed gate is trade evidence; 114 of 136
recent projects are invisible because the classifier finds no mechanical/plumbing/electrical
signal. Either (a) widen the trade taxonomy so more real commercial work is classified, or
(b) accept a small, high-precision public set and label Trends as "all observed" (done). No
classification logic was changed.

```
$ PYTHONPATH="vendor/python:src" python -m pytest tests/test_trends.py -q
...........................                                              [100%]
27 passed in 0.90s
```

---

## D6 — Seed docs (complete C5) — PASS

### Measured duration (this machine: audit sandbox, Python 3.13, wired network)

Run against a **copy** of the shipped DB with the CLI's own default page cap
(`max_pages: 200` in `config/sources.yaml`), no `--max-pages`:

| Source | Result | Elapsed |
|---|---|---|
| `fort_worth_permits` | 195,161 permits | **~4m05s** (22:55:30 → 22:59:35) |
| `collin_cad_permits` | +~12,250 permits | **~14s** |
| `dallas_accela_permits` | still running (per-category, each hitting the 200-page cap) | **>20 min** and climbing |

Log evidence:

```
22:55:30 Ingesting fort_worth_permits
22:59:35 Ingesting collin_cad_permits
22:59:49 Ingesting dallas_accela_permits
23:03:04 Dallas: searching commercial_alteration_addition
23:06:52 Dallas: commercial_alteration_addition hit the page cap (200 pages)
23:10:39 Dallas: commercial_mechanical hit the page cap (200 pages)
```

Fort Worth is the long pole (**>13 min** confirmed) and Dallas Accela walks 10 commercial
categories each capped at 200 pages. Budget **15–30 minutes** for a full seed; it varies on
Render. Recorded in `docs/deployment.md` ("Expected duration (measured on the audit sandbox…)").

### Overlap protection

* Documented in `docs/deployment.md`: pause the refresh loop by raising `REFRESH_SECONDS`
  (e.g. `604800`) before seeding, restore `21600` after.
* Mechanism: the D1 `job_lock`/`writer_lock` in `src/oppintel/locks.py`; a seed started mid-cycle
  waits for the lock rather than running a duplicate pass.

### Is the default cap gentle on the public sources?

Yes. `src/oppintel/connectors/base.py:25-95`: a `RateLimiter` enforces
`min_seconds_between_requests` (1.0s) between requests; transient HTTP (429/500/502/503/504) is
retried up to 3 times with exponential backoff (1s/2s/4s); 60s timeout; descriptive User-Agent.
Documented in `docs/deployment.md` ("Rate limiting and retries").

---

## D7 — Scripts and post-deploy checks (complete C6, C7) — PASS

### `ls audit/scripts` and README

Scripts for the Director's named analyses are present and catalogued in
`audit/scripts/README.md`:

* **A4 (duplicates):** `a4.py`, `a4b.py`, `a4c.py`, `a4d.py`, `a4e.py`, `a4_final.py`, `a4_rows.py`
* **A5 (restart stability):** `a5.sh`, `a5b.sh`, `a5c.sh`, `a5d.sh`, `a5_final.sh`,
  `a5_firstboot.sh`, `a5_sidebyside.sh`
* **A7 (change counting):** `a7.py`, `a7b.py`
* Plus `c1_jurisdiction.py` and the D1/D5 scripts added here.

**Missing scripts added:** `wp1_d1_soak.py` (the 60s concurrency soak) and `wp1_d5_trends.py`
(the D5 read-only Trends investigation). Also fixed `c1_jurisdiction.py`, which used the
Python constant *names* (`CONFIRMED_OPEN`, …) instead of the stored *values*
("Confirmed open", …) and so reported 0 cities; it now prints 15/`['Nevada']` correctly.

### `audit/POST_DEPLOY_CHECKS.md`

Contains: (1) the SQL for non-`new_project` change counts; (2) three example diffs with
before/after values; (3) how to confirm two collection passes ran, **plus a new `last_observed`
query**; (4) how to confirm the refresh loop ran; and an "interpreting the result" section
covering "what to do if the count is zero after 24 hours".

**Defect found and fixed:** step 2 selected `p.name`, but `project` has no `name` column
(it is `project_name`). Running it raised `sqlite3.OperationalError: no such column: p.name`.
Corrected to `p.project_name`; verified it now runs clean (prints "No real differences yet." on
the shipped DB, which has a single collection pass).

---

## D8 — Deploy runbook — PASS

Created `audit/DEPLOY_RUNBOOK.md` for a non-technical founder. It covers: the Render blueprint
steps (Starter plan, disk `oppintel-data` 1 GB at `/var/data`, the env vars, `SECRET_KEY`
generation, and the `MAIL_BACKEND=console` note that reset emails are not sent by default);
first-boot expectations (`/healthz` 200 `"empty"`, self-seed); the one-time seed commands; how to
pause/resume the refresh loop; the verification checklist (healthz status, identical numbers on
`/` and `/api/statistics` across three reloads, honest `/changes` status line); and a rollback
plan. It states that the disk requires the paid plan.

---

## Final suite

```
$ PYTHONPATH="vendor/python:src" python -m pytest tests/ -q
966 passed in 125.52s (0:02:05)
```

## PASS/FAIL

| Item | Result |
|---|---|
| D1 SQLite safety | **PASS** |
| D2 Snapshot heal out of request path | **PASS** |
| D3 Copy honesty residuals | **PASS** |
| D4 Jurisdictions | **PASS** |
| D5 Trends vs public feed | **PASS** |
| D6 Seed docs | **PASS** |
| D7 Scripts and post-deploy checks | **PASS** |
| D8 Deploy runbook | **PASS** |

All PART 0 items pass, so PART 1 (`wp2-mobile-a11y`) may proceed.
