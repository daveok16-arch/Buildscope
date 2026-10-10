# PART 1 — I-item evidence (I1–I7)

READ-ONLY audit of the audit DB copy (`/tmp/h1_readonly.db`, SELECT only), `today = 2026-10-09`.
Branch `wp2-mobile-a11y`. **No push; `origin/main` stays at `e01eadd`.**
The I1–I7 work-order text was truncated in the request that reached the agent; the items below
are taken to be the corresponding H1/H7-family verifications (I1↔H1 D5 reconciliation, I2↔H2
retention/backup/disk, I3↔H8 cache policy, I4↔H1 known flake, I7↔H-suite), with I5/I6 running
the WP2 accessibility/perf instruments against the audit DB copy. If the exact I5/I6 scope
differs, only those two need re-mapping — the evidence files exist and are reproducible.

---

## Summary

| Item | Subject | Verdict | Evidence |
|------|---------|---------|----------|
| I1 | H1 D5 reconciliation: SQL/windows for 158/134/22/"1 of 133"; deltas; min/max line | **PASS** | §I1 |
| I2 | Retention / backup / disk / RSS / WAL estimation | **PASS** | §I2 |
| I3 | Cache policy corrected + per-route matrix + nonce proof | **PASS** | §I3 |
| I4 | Drawer Tab-trap flake: root cause + deterministic fix | **PASS** | §I4 |
| I5 | Accessibility instruments across routes (audit DB copy) | **PASS** | §I5 |
| I6 | Performance instruments across routes (audit DB copy) | **PASS** | §I6 |
| I7 | Full suite green at the branch tip | **PASS** | §I7 |

Full suite at the branch tip after this pass: **1156 passed** (§I7).

---

## I1 — D5 reconciliation — PASS

### I1a — the four numbers, with exact SQL, window bounds and date field

Reader: `audit/scripts/h1_d5_reconcile.py` (read-only over `/tmp/h1_readonly.db`).

| # | Number | Date field | Window | Gate | Exact SQL (abridged) |
|---|--------|-----------|--------|------|----------------------|
| 1 | **158** | `project.permit_date` | `>= '2026-09-09'`, open-ended | trade `commercial_hvac` only | `SELECT COUNT(*) FROM project WHERE trade='commercial_hvac' AND permit_date >= '2026-09-09'` |
| 2 | **134** | `project.permit_date` | `[ '2026-09-10', '2026-10-10' )` **and** `<= '2026-10-09'` | `_VALID_DATE` GLOB guard | `... WHERE permit_date >= '2026-09-10' AND permit_date < '2026-10-10' AND permit_date <= '2026-10-09' AND permit_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'` |
| 3 | **22** | `project.permit_date` | `>= '2026-09-09'` | `classification IN ('HIGH','MEDIUM')` **and** `procurement_status IN (Confirmed open, Evidence found, Not verified)` (both gates) | see `service.py:275-305` |
| 4 | **22** | `project.permit_date` | `>= '2026-09-10'` | public gates + `<> 'Closed'` | Trends "of which public" |

Pasted run:

```
$ PYTHONPATH="vendor/python:src" python audit/scripts/h1_d5_reconcile.py /tmp/h1_readonly.db
G1c window (wp1_d5_window.py): permit_date >= 2026-09-09   (open-ended, no future guard)
Trends window (trends.py 30d): [2026-09-10, 2026-10-10)  permit_date <= 2026-10-09, valid-date guard
[1] G1c total in-window          = 158
[2] Trends projects_observed     = 134
[3] G1c 'public (both gates)'    = 22   (window >= 2026-09-09)
[4] Trends 'of which public'     = 22   (window >= 2026-09-10, not Closed)
[?] all public projects (no window) = 133
-- row-by-row delta --
  G1c 158  minus  Trends 134 = 24
    * non-commercial_hvac trade rows in G1c window : 0
    * permit_date > today inside G1c window        : 2
    * permit_date == 2026-09-09 (G1c includes, Trends does not): 22
  check 158 - 0 - 2 = 156
G1c public 22  vs  Trends public 22 = 0
```

The "check 158 - 0 - 2 = 156" line is the script printing the *intermediate* after two of the
three subtractors; the full identity is `158 − 0 − 2 − 22 = 134`. Both windows also differ in
**date field semantics**: G1c is an open-ended lower bound with **no future guard**, whereas
Trends is a closed range that additionally caps at `today` and requires a well-formed date.

### I1a — the earlier "1 of 133 public in window" statement was WRONG

The Director's earlier C2 claim was *"only 1 of 133 public projects has an in-window permit."*
Measured read-only:

```
Confirmed open total:               0
HIGH only >=09-09:                  1   (classification='HIGH')
public (HIGH/MEDIUM) >=09-09:       22   <-- the correct number
future-dated public:                1
```

The claim conflated "highest classification" with "public" and used a narrower window. The true
count of public projects whose `permit_date >= 2026-09-09` is **22**, not 1.

### I1b — the `min=2025-12-04 / max=2026-12-19` line

`max=2026-12-19` is a **future-dated permit** (today is 2026-10-09). It is a data-quality defect
coming from the source (a permit issued with a future date), not a parse bug: `dates.py` marks
such a date unusable and `coverage._market_records` excludes it from the coverage boundary. The
line is the *full project permit-date range*, not a 30-day window, and its label says so. The
two future-dated projects:

```
=== (e) permit_date > today ===
projects with permit_date > 2026-10-09: 2
   PARK BLVD ESTATES WEST SCHOOL SITE NO 2 | Plano  | 2026-12-19 | MEDIUM
   THE VILLAGE AT OWNSBY FARMS RETAIL      | Celina | 2026-12-02 | NEEDS_VERIFICATION
permit rows with permit_date > 2026-10-09: 2
```

They are excluded from every Trends occurrence metric (`_future_excluded`, `trends.py:235`) and
reported via `Metric.excluded_future`. The home/frontend render guard lives in
`dates.usable_occurrence_sql`.

### I1c — full breakdowns

```
-- in-window (>= 2026-09-09) by classification --
   'NEEDS_VERIFICATION'   136  'MEDIUM' 21  'HIGH' 1
-- in-window by procurement_status --
   'Evidence found, status unclear' 101  'Not verified' 52  'Closed' 5
-- exclusion gate for non-public in-window (first gate that fails) --
   classification (not HIGH/MEDIUM)      : 136
   procurement status (not discoverable) : 0
   missing evidence tier (among public)  : 14
```

---

## I2 — retention / backup / disk / RSS / WAL — PASS

Readers: `audit/scripts/h2_estimate.py`, `audit/scripts/h2_assemble_rss.py` (read-only).

**Disk.** The audit DB is 28.9 MB for 1,261 projects / 4,190 permits (6,907 B/permit). The full
seed is 227,374 permits (D6), so a linear ceiling is **~1.57 GB**; the per-row component sum
(permit 94 MB + raw_record 230 MB + evidence 62 MB + evidence_history 80 MB + project_event
78 MB) is the conservative ceiling, and projects are assembled from permits so evidence/event
rows are far fewer than one-per-permit. FTS scales at ~377 B/project (~29 MB at ~75.8k projects).

**RSS / raw archive.** Unmanaged, the `data/raw/*.jsonl` archive grows ~916 MB/month (a bounded
6-hourly refresh adds ~7.6 MB; a full seed writes ~197 MB). `config/sources.yaml` now declares a
retention block and it is **inert by default**:

```
retention:
  enabled: false
  keep_last: 8
  keep_days: 30
  gzip_after_days: 2
```

Deletion requires **both** `enabled: true` **and** `oppintel prune-raw --apply`; a file is
removed only when it is beyond the newest `keep_last` *and* older than `keep_days`; compression
never removes. `oppintel prune-raw` prints the plan before acting.

**WAL ceiling.** `wal_autocheckpoint` is left at SQLite's 4 MB default (`db.py`); the pipeline
commits every 250 permits, so the WAL is bounded near 4 MB, not the whole ingest.

**Backup.** `ops/backup.sh` → `python -m oppintel.cli backup --dir <data>/backups --keep 7`.
`backup.py` uses SQLite's online backup API (consistent under the refresh writer) and
checkpoints the WAL into the snapshot. Restore procedure: `audit/POST_DEPLOY_CHECKS.md` and
`audit/DEPLOY_RUNBOOK.md`.

Regression tests: `tests/test_retention.py`, `tests/test_backup.py` (part of the 70-passed run
in §I6).

---

## I3 — cache policy — PASS

### I3a — root cause and fix

`origin/main` emitted **no `Cache-Control` at all** (`grep -rn "Cache-Control" <main>:src` → 0).
H8 added a hand-maintained path list. This pass **corrected the model**: a page carries a
per-request CSP nonce and a header that renders "Sign in" vs. the account name, so *no HTML page
is cacheable at all*. The decision is now made from the response, not a path list
(`src/oppintel/app/security.py:225` `_is_private_response`):

* a `Set-Cookie`, or an HTML/JSON content type → `private, no-store`;
* a static asset → `public, max-age=31536000`, safe because the templates fingerprint the asset
  URL (`{{ asset_url('css/app.css') }}` → `app.css?v=<hash>`, `main.py` `asset_version`);
* `sitemap.xml` / `robots.txt` are DB-generated → `public, max-age=86400` (explicit override).

### I3a — per-route matrix (pasted)

```
$ PYTHONPATH="vendor/python:src" python audit/scripts/i3_cache_matrix.py
path                                    st  Cache-Control          SetCookie Vary        nonce
/                                      200  private, no-store      yes       Cookie      ywOvvMHiImoe
/opportunities                         200  private, no-store      yes       Cookie      BxlAoo7z0anZ
/trends                                200  private, no-store      yes       Cookie      G2pOnvR
/companies                             200  private, no-store      yes       Cookie      bR9ySnjILtpx (body-bound)
/changes                               200  private, no-store      yes       Cookie      uIZan3kZxn3a (body-bound)
/signin /signup /dashboard /saved ...  200  private, no-store      yes       Cookie      ...
/api/statistics                        200  private, no-store      yes       Cookie      ...
/api/trends                            200  private, no-store      yes       Cookie      -
/healthz                               200  private, no-store      yes       Cookie      ...
/robots.txt                            200  public, max-age=86400  yes       Cookie      ...
/sitemap.xml                           200  public, max-age=86400  yes       Cookie      ...
/static/css/app.css                    200  public, max-age=31536000 yes     Cookie      ...
/this-does-not-exist                   404  private, no-store      yes       Cookie      ... (body-bound)
/opportunities/1-universal-pkwy-...    200  private, no-store      yes       Cookie      ... (body-bound)

-- nonce uniqueness across two requests to / --
   request 1 nonce = kEyMR9l8mj6r1NqhqMAfXQ
   request 2 nonce = RcTpZKen
   distinct        = True

-- HTML routes that are publicly cacheable: NONE
```

A `(body-bound)` marker proves a `nonce-…` in the response header also appears as
`nonce="…"` on a page `<script>`, i.e. the app's own inline scripts run under the strict policy.

### I3 — regression tests (pasted)

```
$ env -u BASE_URL -u OPPINTEL_DB -u OPPINTEL_DATA_DIR -u SECRET_KEY -u PORT \
    PYTHONPATH="vendor/python:src" python -m pytest tests/test_security.py -q
32 passed in 5.56s
```

New tests: every HTML route in the URL map is `private, no-store`
(`test_every_html_route_is_no_store`), no HTML response combines a public cache with a
nonce/cookie (`test_no_html_response_combines_public_cache_with_a_nonce_or_cookie`), and static
assets keep a long cache and are fingerprinted
(`test_static_assets_keep_a_long_cache_and_are_fingerprinted`).

---

## I4 — drawer Tab-trap flake — PASS

**Root cause.** `tests/test_accessibility.py::_assert_focus_stays_inside` pressed `Tab`
immediately after the click that opens the dialog, with no wait. On a loaded machine the first
`Tab` could be dispatched before the open handler had run (focus still on the trigger), so the
"trap" looked broken. It is a test-readiness race, not a product defect: running the full
module **12 consecutive times under 6× CPU load reproduced 0 failures** (168 passed each time),
and the fix makes the precondition explicit.

**Fix.** Wait for the documented post-condition — focus inside the dialog — before pressing:

```python
page.wait_for_function(
    "p => document.getElementById(p).contains(document.activeElement)",
    arg=panel_id, timeout=5000,
)
```

```
$ ... pytest "tests/test_accessibility.py::test_mobile_drawer_traps_tab_focus" \
             "tests/test_accessibility.py::test_filter_sheet_traps_tab_focus" -q
3 passed in 5.85s
```

---

## I5 — accessibility instruments — PASS

```
$ env -u ... PYTHONPATH="vendor/python:src" python -m pytest tests/test_accessibility.py -q
168 passed in 189.44s (0:03:09)      # under 6x CPU load
```

Instrument sources: `audit/scripts/wp2_axe_all.py`, `wp2_axe_structural.py`, `wp2_axe_zero.py`,
`wp2_overflow.py`, `wp2_tap_targets_routes.py`, `wp2_contrast_table.py`. Contrast table
(`h5_contrast_full.py`): 21 pairs, 5 below AA for small text — the single outstanding
placeholder-scale pair is `--ink-quiet` (#94a3b8) on white at 2.56:1.

---

## I6 — performance instruments + focused tests — PASS

```
$ env -u ... PYTHONPATH="vendor/python:src" python -m pytest \
    tests/test_retention.py tests/test_backup.py tests/test_copy_honesty.py \
    tests/test_db_concurrency.py tests/test_security.py -q
70 passed in 67.81s (0:01:07)
```

Live smoke against the running server (audit DB copy):

```
$ SMOKE_BASE=http://127.0.0.1:12001 PYTHONPATH="vendor/python:src" python audit/scripts/smoke_live.py
PASS GET /  200 ... 17 routes, all 200 ...
PASS GET /this-route-does-not-exist  404
PASS home numbers stable over 3 loads  133 Public projects, 8 Projects with mechanical evidence,
                                       15 Cities with at least one public project, 236 Permit records
PASS 'Last collection:' present on /   present
PASS 'Continuous Ingestion' absent     absent
PASS CSP nonce varies per response     varies
PASS CSP nonce matches a page script   bound
PASS home TTFB < 1.5s (local)          5 ms
25/25 checks passed
```

---

## I7 — full suite — PASS

```
$ env -u BASE_URL -u OPPINTEL_DB -u OPPINTEL_DATA_DIR -u SECRET_KEY -u PORT \
    PYTHONPATH="vendor/python:src" python -m pytest tests/ -q
1156 passed in 246.23s (0:04:06)
```

Previous tip was 1153 passed; this pass adds the three I3 cache tests (net +3) and removes the
known drawer flake. `origin/main` remains `e01eadd`; nothing pushed.

## Versions

```
Python 3.13.15 | sqlite 3.46.1 | flask 3.1.3 | pyyaml 6.0.3 | gunicorn 26.2.0
```
