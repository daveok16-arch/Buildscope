# WP1.1 — Truth & Stability closeout

Branch `wp1-truth-stability`. Closes the Director's Part 1 items A4–A7 and re-confirms A1–A3,
plus the Step 0 verdict and the `render.yaml` persistent-disk definition. Every number below is
copied from a command output; commands are in each section.

Full test suite: **933 passed** (`PYTHONPATH="vendor/python:src" python -m pytest tests/ -q`).

---

## Step 0 — root-cause verdict (original WP1 prompt, "write the verdict at the top")

> Hypothesis: public counts drift because Render's free plan has no persistent disk, so each
> deploy/restart rebuilds the SQLite DB from a partial ingest.

**Verdict: CONFIRMED.** Evidence:

1. The database is a single SQLite file. The web layer and the CLI both read `OPPINTEL_DB`
   (`src/oppintel/app/config.py:41`, `oppintel.cli.DEFAULT_DB`). Nothing points at a mounted disk
   in the tree as it stood (`render.yaml` had `plan: free` and the `disk` block commented out).
2. On an empty database at startup the process creates the schema and serves `200` with
   `"status":"empty"`; the in-process refresh loop (`ops/automate.py`) then ingests
   (`ingest → assemble → build-search-index → monitor`). A bounded refresh (`MAX_PAGES=3`) means a
   fresh instance re-collects only the newest pages, so the assembled dataset does not converge to
   the same counts as a longer-running instance.
3. The dataset is mutable while visitors read, because the refresh thread writes the same tables
   (and the snapshot) the routes read. Without a persistent disk every deploy starts from zero.

The fix (one stored snapshot + a persistent disk) is the subject of Items 1–5 and this closeout.

---

## A4 — the 61 → 7 duplicate reconciliation (no behavior change)

Two exact counts, then a category breakdown. Source: `/tmp/a4.py`, `/tmp/a4b.py`, `/tmp/a4d.py`,
`/tmp/a4e.py`, `/tmp/a4_rows.py` against the audit DB (`/tmp/populated.db`, 4,190 permit rows).

### SQL-61 — duplicate groups by `(source_id, permit_number)`

```sql
SELECT source_id, permit_number, COUNT(*) n
  FROM permit
 WHERE permit_number IS NOT NULL AND permit_number <> ''
 GROUP BY source_id, permit_number
HAVING COUNT(*)>1;
```

Result: **61 groups, all from `collin_cad_permits`** (0 from any other source).
Group-size distribution: `{2: 50, 3: 8, 20: 1, 4: 1, 6: 1}`.
(Including rows with a NULL `permit_number` changes nothing — the predicate above is the one the
original audit used, and every group already has a non-empty number.)

### SQL-7 — true-duplicate groups by `(source_id, permit_number, address_key, permit_date)`

```sql
SELECT source_id, permit_number, address_key, permit_date, COUNT(*) n
  FROM permit
 WHERE permit_number IS NOT NULL AND permit_number <> ''
   AND address_key IS NOT NULL AND permit_date IS NOT NULL
 GROUP BY source_id, permit_number, address_key, permit_date
HAVING COUNT(*)>1;
```

Result: **7 groups / 10 excess rows** — same permit number, same address, same filing date:

| permit_number | address_key | permit_date | rows |
|---|---|---|---|
| 25-004694 | 212 frontier parkway celina tx 75009 | 2026-05-19 | 2 |
| 25-005873 | 540 west frontier parkway prosper tx 75078 | 2026-03-27 | 2 |
| 26-1451 | 601 fm 544 murphy tx 75094 | 2026-08-28 | 2 |
| 26-PLAN | 2409 aurora avenue celina tx 75009 | 2026-07-08 | 4 |
| 26-PLANS | 1021 industrial drive royse city tx 75189 | 2026-04-02 | 2 |
| 26-PLANS | 610 east cook street josephine tx 75189 | 2026-05-20 | 2 |
| 26-PLOT | 2708 bachman mews celina tx 75009 | 2026-05-19 | 3 |

### Category breakdown of the 61 (two views; both sum to 61)

View 1 (`a4b`, grouped by address/date spread):

| Category | Groups | Excess rows |
|---|---|---|
| distinct_addresses | 50 | 79 |
| revision_same_address | 6 | 6 |
| identical_dup | 5 | 8 |
| **sum** | **61** | |

View 2 (`a4d`, the Director's four buckets):

| Category | Groups | Excess rows |
|---|---|---|
| identical_same_address_date | 5 | 8 |
| placeholder_number_distinct_addresses | 3 | 25 |
| real_number_distinct_addresses | 47 | 54 |
| same_address_multiple_dates (revision/inspection) | 6 | 6 |
| **sum** | **61** | |

### Reconciliation line

`61 = 50 distinct-address groups + 6 revision groups + 5 identical groups` (View 1)
`61 = 47 real-number distinct-address + 3 placeholder distinct-address + 6 revision + 5 identical` (View 2)

The **7 true-duplicate groups are drawn from two of these categories** (`/tmp/a4e.py`):

```
the 7 broken down by 61-category: {'identical_same_address_date': 5, 'placeholder_number_distinct_addresses': 2}
```

* 5 of the 7 (`25-004694`, `25-005873`, `26-1451`, `26-PLAN`, `26-PLOT`) come from the
  **identical/placeholder same-address-same-date** population — the same permit republished under
  a second `natural_key`.
* 2 of the 7 (`26-PLANS` at 1021 Industrial Dr and at 610 E Cook St) are `26-PLANS` placeholder
  numbers whose rows happen to share one address and date.
* The other 53 groups are **not duplicates**: they are many permits of different type at
  *different* addresses sharing a placeholder or reused number (e.g. `26-PERMIT` spans 20 rows at
  20 addresses across 5 dates — a genuine multi-family filing run), or the same number reused for
  revisions/status updates on one address across *different* dates.

### 5 examples per category (View 2)

**identical_same_address_date** (5 groups): `25-004694` n=2, `25-005873` n=2, `26-1451` n=2,
`26-PLAN` n=4, `26-PLOT` n=3.

**placeholder_number_distinct_addresses** (3 groups): `25-PERMIT` n=2 (2 addresses, 2 dates),
`26-PERMIT` n=20 (20 addresses, 5 dates), `26-PLANS` n=6 (4 addresses, 2 dates).

**real_number_distinct_addresses** (47 groups): `25-000015` n=2 (2 addr/2 dates), `25-0081` n=2,
`25-0082` n=2, `25-04525` n=2, `26-000001` n=3 (3 addr/3 dates).

**same_address_multiple_dates (revision/inspection)** (6 groups): `25-0120` n=2 (1 addr/2 dates),
`25-0134` n=2, `26-000015` n=2, `26-0029` n=2, `26-2309` n=2.

Raw rows for `26-PLOT` (shows the republish under three `natural_key`s):

```
id 2794 nk 1258701 type Pool            date 2026-05-19 desc LILYBROOK AT LEGACY HILLS SECTION 1
id 2795 nk 1258703 type Miscellaneous   date 2026-05-19 same address/description
id 2805 nk 1249525 type New Construction date 2026-05-19 same address/description
```

**No behavior change**: no dedupe logic was touched; WP2's `duplicate_permit` finding (detect and
report, never merge) is unchanged.

---

## A5 — stability across three fresh processes + Render checklist

Three separate processes, each against its own **copy** of the audit DB, started on its own port.
`/tmp/a5_sidebyside.sh`, `/tmp/a5_final.sh`.

| surface | run 1 | run 2 | run 3 |
|---|---|---|---|
| `/` Public projects | 133 | 133 | 133 |
| `/` Projects with mechanical evidence | 8 | 8 | 8 |
| `/` Active jurisdictions | 15 | 15 | 15 |
| `/` Permit records | 236 | 236 | 236 |
| `/healthz` | `{status: ok, projects: 1261, permits: 4190}` | same | same |
| `/api/statistics` statistics | `{public_projects:133, projects_with_mechanical_evidence:8, permit_records:236, linked_permits:1904, active_jurisdictions:15, projects_total:1261}` | same | same |
| `/trends` card values | `134, 167, 98, 0, 1261, 8` | same | same |

**Deterministic across fresh processes and restarts.**

### Render production checklist for the founder

| Item | Value |
|---|---|
| Plan | `starter` (smallest paid plan that can attach a disk) |
| Disk name | `oppintel-data` |
| Disk size | `1` GB |
| Mount path | `/var/data` |
| `OPPINTEL_DB` | `/var/data/oppintel.db` |
| `OPPINTEL_DATA_DIR` | `/var/data` |
| Start command | `bash ops/start.sh` (Render sets `RENDER=true`; start script runs gunicorn + refresh loop in the foreground on `$PORT`) |
| Health check | `/healthz` |
| Instances | `1` (a disk attaches to one instance; a second would run a second refresh loop) |

### First boot when the disk is empty vs already populated

Measured with `OPPINTEL_DB` and `OPPINTEL_DATA_DIR` both set to the mounted dir.

* **Empty disk** (`/tmp/a5_firstboot.sh`): the process **starts successfully**. `/healthz`
  returns **HTTP 200** with `{"status":"empty","projects":0,"permits":0}` and the home page shows
  `0`. It does not 503 — a first deploy starts empty on purpose, so a 503 would cause a restart
  loop. The refresh loop then self-seeds: one bounded pass
  (`ops/automate.py --once --max-pages 1`) took an empty DB to **2,110 permits / 696 projects** in
  ~50s, with a snapshot written (`{"public_projects": 98, ...}`).
* **Populated disk**: `/healthz` returns **HTTP 200** with `{"status":"ok","projects":1261,"permits":4190}`
  and every surface reads the stored snapshot (133 / 8 / 15 / 236).
* **Path configured but disk not attached** (`OPPINTEL_DATA_DIR=/proc/a5-none`): `ops/start.sh`
  exits `1` with a named error:

```
error: cannot write to OPPINTEL_DATA_DIR=/proc/a5-none
  On Render this means the disk is not attached at that path. Free instances have no
  disk, so the mount path is never created. Unset OPPINTEL_DATA_DIR to use the
  checkout, or attach a disk and point this at its mount path.
```

---

## A6 — Step 0 verdict + `render.yaml` / ops diff

The Step 0 verdict paragraph is reproduced at the top of this report (the WP1 prompt asked for it
"at the top of your report"; the earlier WP1 file carried the root-cause narrative under
"Root cause of the original stat drift", not a section literally titled "Step 0").

`render.yaml` had **no active persistent-disk definition** (plan `free`, disk commented out). It
now ships the disk enabled. Exact diff vs `main`:

```diff
diff --git a/render.yaml b/render.yaml
--- a/render.yaml
+++ b/render.yaml
@@ -4,17 +4,26 @@
-# This defaults to the Free plan, which has no persistent disk. ...
+# This ships with `plan: starter` and a persistent disk attached, because stable public counts
+# require the database to survive a deploy. ...
+# A Blueprint that declares a `disk` on a Free instance does not apply at all ...
 
 services:
   - type: web
     name: buildscope
     runtime: python
-    plan: free
+    plan: starter
     region: oregon
     branch: main
@@ -52,17 +61,16 @@ services:
       - key: MAX_PAGES
         value: "3"
-      # - key: OPPINTEL_DATA_DIR
-      #   value: /var/data
-      # - key: OPPINTEL_DB
-      #   value: /var/data/oppintel.db
-    # disk:
-    #   name: oppintel-data
-    #   mountPath: /var/data
-    #   sizeGB: 1
+      - key: OPPINTEL_DATA_DIR
+        value: /var/data
+      - key: OPPINTEL_DB
+        value: /var/data/oppintel.db
+    disk:
+      name: oppintel-data
+      mountPath: /var/data
+      sizeGB: 1
```

(Comment lines elided above for length; the file itself carries the full prose.)

Full command: `git --no-pager diff main -- render.yaml ops/ docs/deployment.md`
(`docs/deployment.md` now documents that the disk ships enabled; `ops/` was unchanged).
`python -c "import yaml; yaml.safe_load(open('render.yaml'))"` parses cleanly and the result has
`plan: starter`, `envVars.OPPINTEL_DB=/var/data/oppintel.db`, `disk.mountPath=/var/data`.

---

## A7 — what "133 Differences Detected" counts (label was misleading; renamed)

Live page before this change read "133 Differences Detected in Last 60 Days"
(`src/oppintel/app/templates/changes.html:46`). It is **not** the count of detected differences.

Exact SQL (the page headline count, `OpportunityService.recent_changes_count`,
`src/oppintel/service.py`):

```sql
SELECT COUNT(*) FROM project_change c
  JOIN project p ON p.id = c.project_id
 WHERE c.detected_at >= :cutoff              -- now - days, UTC ISO
   AND p.classification IN ('HIGH','MEDIUM')
   AND p.procurement_status IN ('CONFIRMED_OPEN','EVIDENCE_FOUND','NOT_VERIFIED');
```

Live DB facts (`/tmp/a7.py`, `/tmp/a7b.py`):

* `project_change` rows total: **1,261**.
* by `change_kind`: **`new_project` 1,261; every other kind 0**. There are **zero real
  differences** (no `status_changed`, `value_changed`, …) — the dataset is a single collection
  pass, so the only event produced is "first observation".
* So 133 counts **public, discoverable `project_change` rows** (each is a single `new_project`
  row), which happen to be one per project; it is **public-only** and **rows (≈ distinct
  projects)**. It is **not** the count of detected differences.

`new_project` is explicitly "Project first entered the system" (`src/oppintel/changes.py:158`) and
`diff_project` returns it only when `previous_values is None` (`changes.py:151`).

Fix: added `OpportunityService.recent_changes_breakdown` (`service.py:1411`) and changed the feed
to name what it measures. Measured output on this DB:

```
133 Change-feed Entries in Last 60 Days
133 are first observations (a record entering the system, not a change) and 0 are detected
differences between passes. Showing 25 of 133 (page 1 of 6).
```

The label no longer calls a first observation a "detected difference". Tests updated:
`tests/test_changes.py::test_changes_feed_headline_is_the_total_not_the_page_size` and the new
`test_changes_feed_separates_first_observations_from_differences`.

---

## PASS/FAIL — A1 through A7

| Item | Result | Basis |
|---|---|---|
| A1 | **PASS** | `/changes` shows a real total with pagination (`133 … page 1 of 6`) and test coverage |
| A2 | **PASS** | future-date guard `dates.occurrence_is_future` + `filing_date` filter; `tests/test_dates.py`, `tests/test_quality.py` green |
| A3 | **PASS** | `config/markets.yaml:31` alias `Mckinney`→`McKinney`; jurisdiction count excludes out-of-market `Nevada` |
| A4 | **PASS** | 61 and 7 reproduced with exact SQL; both category views sum to 61; 7 = 5 identical + 2 placeholder; no behavior change |
| A5 | **PASS** | 3 fresh processes identical on `/`, `/healthz`, `/api/statistics`, `/trends`; Render checklist + first-boot behaviour |
| A6 | **PASS** | Step 0 verdict above; `render.yaml` now ships the disk enabled and parses; ops/docs diff shown |
| A7 | **PASS** | exact SQL stated; 1,261/1,261 rows are `new_project`, 0 real diffs; label renamed to what it measures |

Full suite green: **933 passed in 142.33s**.
