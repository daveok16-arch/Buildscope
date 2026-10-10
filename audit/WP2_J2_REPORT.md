# PART J2 — Disk / seed-scope / backup sizing

Scope: **disk, one-time seed size and backup sizing**, with the prerequisite that the connector
date filters are applied **upstream**. Read-only against the audit DB copy
(`/tmp/h1_readonly.db`, opened `mode=ro`); all writes go to scratch copies under `/tmp`.
Today = 2026-10-09. Branch `wp2-mobile-a11y`; `origin/main` untouched at `e01eadd`; no push.

Evidence pastes: `audit/J2_PASTE.txt`, `audit/J2_SIZING_PASTE.txt`, `audit/J2_RSS_PASTE.txt`.

---

## Sub-item results

### J2a — Confirm connectors filter by date upstream — **PASS**

The pipeline passed `since=None` on a normal ingest (no `--since`, no config), so there was **no
window applied**; a one-time seed would fetch all history. The bound now resolves from config and
reaches each source's own filter:

| Source | Filter field | Mechanism | file:line |
|---|---|---|---|
| Fort Worth | `File_Date` | ArcGIS `where` | `connectors/fort_worth_permits.py:80-82` |
| Collin CAD | `permitissueddate` | Socrata `$where` | `connectors/collin_cad_permits.py:57-59` |
| Dallas Accela | `record_date` | portal search `start`/`end` | `connectors/dallas_accela_permits.py:416-417` |

Changes:
* `SourceConfig.since` / `SourceConfig.since_months` + `resolved_since()` — `config.py:41-46,58-73`.
* `Pipeline.ingest_source` resolves and passes it — `pipeline.py:117-123` (`effective_since`).
* `config/sources.yaml`: `since_months: 24` on all three current sources (`:32,:52,:75`).
* Test proof the bound reaches the ArcGIS `where`: `tests/test_source_window.py::test_the_bound_reaches_the_arcgis_where_clause`
  asserts `where == "File_Date >= timestamp '2024-10-09 00:00:00'"`.
* Test proof the pipeline applies config with no CLI `--since`:
  `tests/test_source_window.py::test_pipeline_applies_the_configured_window_when_no_since_is_given`.

CLI `--since YYYY-MM-DD` still overrides for one run (precedence in `pipeline.py:119`).

### J2b — Disk and seed sizing across scopes — **PASS**

Measured bytes/permit = **6,899.7 B** (28.9 MB live / 4,190 permits, `dbstat`). Raw JSONL
per-row from the shipped captures: Fort Worth 813 B/row, Collin 1,124 B/row, Dallas 1,034 B/row.

| Scope | Permits | DB | raw JSONL | 3 gz backups | Total | Recommended disk |
|---|---|---|---|---|---|---|
| 24-month (default) | 21,635 | 149 MB | 23 MB | 43 MB | 220 MB | **1 GB** |
| 12-month | 16,501 | 114 MB | 18 MB | 33 MB | 165 MB | 1 GB |
| all history | 232,213 | 1,602 MB | 197 MB | 461 MB | 2,281 MB | **5 GB** (2× headroom) |

The 24-month default is the shipped configuration (`since_months: 24`), so the baseline footprint
is **~220 MB / 1 GB disk**. All history is a deliberate `--full`/`since_months: null` choice.

### J2c — No raw duplication; provenance survives JSONL pruning — **PASS**

* `raw_record` holds landed payloads (4,239 rows / 4.29 MB in the audit DB); `data/raw/*.jsonl` is
  the append-only replay file. They are the same bytes, but the DB copy is what `document.raw_record_id`
  references, so **pruning JSONL cannot orphan anything**.
* `document.raw_record_id` is **100% populated (1901/1901)** and has **0 orphans**
  (`document → raw_record` LEFT JOIN).
* `retention.py` only ever `path.unlink()`s JSONL files (`retention.py:135,138`); it never deletes
  `raw_record` rows. Provenance is intact after raw pruning.

### J2d — Peak RSS for the largest seed — **PASS**

Replayed the shipped 200,000-row Fort Worth capture into a scratch DB (no network) and assembled:

```
ingest:   status=ok  permits=200,000   (38.0s)
assemble: projects_written=5,900       (67.6s)
permits=186,774  projects=5,900
peak RSS = 304,540 KiB = 297.4 MB
```

**~298 MB peak.** Recommendation: **512 MB RAM** for the default 24-month seed, **2 GB** for an
all-history `--full` backfill. (`audit/scripts/j2_rss.py`, `audit/J2_RSS_PASTE.txt`.)

### J2e — Backup sizing, retention and a low-disk guard — **PASS (with one added guard)**

* Snapshot of the default DB ≈149 MB; 3 kept ≈447 MB uncompressed / **≈43 MB gzipped** (measured
  gzip ratio 0.096).
* Default retention lowered from 7 → **3** (`backup.py:49`, `cli.py:349`, `ops/backup.sh:31`) so a
  month of backups fits a 1 GB disk. Tests `test_default_keep_is_three`,
  `test_backup_keeps_only_the_newest_n`.
* **Added:** `create_backup` now logs a warning when free space is below one snapshot + 200 MB
  (`backup.py:_warn_if_low_disk`, `DEFAULT_MIN_FREE_MB = 200`). It warns and **proceeds** rather
  than aborting — a stale backup is usually better than none. Test
  `test_low_disk_warns_but_still_writes`.
* Same-disk caveat documented: backups do **not** survive the volume's loss; copy off-disk.

### J2f — Raw-archive retention policy — **PASS (documented, inert by default)**

`retention.enabled: false` (`config/sources.yaml`), so files are kept and `prune-raw` is a no-op
plan. Unmanaged growth is **916 MB/month**; with retention on and `keep_last: 8` per source
(gzip after 2 days) the steady archive is **~17.6 MB gzipped**. Keep the policy inert unless the
archive growth is acceptable, or enable it and accept that replay history is bounded.

### J2g — Warm response time and HTML weight — **PASS**

gunicorn, 2 workers, real 28.9 MB DB, warm (3 samples each):

| Route | warm TTFB (s) | HTML bytes (no gzip) |
|---|---|---|
| `/` | 0.051 / 0.044 / 0.008 | 33,159 |
| `/opportunities` | 0.034 / 0.012 / 0.032 | 62,727 |
| `/trends` | 0.100 / 0.012 / 0.013 | 24,774 |
| `/companies` | 0.014 / 0.016 / 0.007 | 108,012 |
| `/changes` | 0.022 / 0.007 / 0.007 | — |

`/healthz` → 200. **No compression**: gunicorn is launched without a proxy and no `Content-Encoding`
is set, so `/companies` is 108 KB over the wire. Gaps to consider (not in J2 scope): gzip/Brotli,
and `Cache-Control: private, no-store` with no ETag means no conditional caching. Both are
frontend/perf items, logged here for the record.

---

## Changes made (branch `wp2-mobile-a11y`, no push)

| File | Change |
|---|---|
| `src/oppintel/config.py` | `SourceConfig.since`/`since_months`, `resolved_since()`, `_days_in_month` |
| `src/oppintel/pipeline.py` | resolve `effective_since` from config when no explicit `since` |
| `config/sources.yaml` | `since_months: 24` on three sources; header note |
| `src/oppintel/backup.py` | `keep=3` default; `_warn_if_low_disk` free-space warning |
| `src/oppintel/cli.py` | `backup --keep` default 7 → 3 |
| `ops/backup.sh` | `KEEP` default 7 → 3 |
| `docs/deployment.md` | sizing tables (24m/all), RSS, backup cop |
| `AGENTS.md` | seed-window note |
| `tests/test_source_window.py` | 7 new tests (new file) |
| `tests/test_backup.py` | +2 tests (keep=3, low-disk warn) |
| `audit/scripts/j2_sizing.py`, `audit/scripts/j2_rss.py` | new measurement scripts |

## Tests

`pytest tests/test_source_window.py tests/test_backup.py` → **13 passed**.
Full suite → **1167 passed** (was 1158).

## Open items / blockers

* Cold-start on Render (free tier sleep) was **not measured** — no network to the live Render
  service; the sandbox runs the app locally only. UNVERIFIED.
* Real all-history ingest time (>13 min for Fort Worth alone) is carried from the D6 note, not
  re-measured here (bounded offline replay used instead).
