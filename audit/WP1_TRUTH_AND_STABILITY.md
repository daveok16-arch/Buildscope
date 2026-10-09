# WP1 — Truth & Stability: implementation report

Branch `wp1-truth-stability`. Scope: make every public headline number honest, sourced from one
stored snapshot, and stop presenting unverified dates/values as facts. No fabricated values; no
classification, provenance or eligibility logic was simplified.

Full test suite: **929 passed** (`PYTHONPATH="vendor/python:src" python -m pytest tests/ -q`).

## Commits

| Commit | Title |
| --- | --- |
| `996751f` | docs(ops): require a persistent Render disk for stable counts |
| `78752c4` | feat(stats): one stored snapshot for every public headline figure |
| `5349a59` | feat(trends,changes): honest windows, cumulative labels, real change total |
| `6182532` | feat(dates,how-it-works): label future filing dates, publish the ingest funnel |

## Item 4 — Semantic date validation (future filing dates)

**Defect.** Two permits/projects carry a filing date in the future relative to the audit day
(2026-10-09): permit `25-04076` (Plano, `2026-12-19`) and permit `25-005249` (Celina,
`2026-12-02`). A future date cannot describe a filing that has already happened, yet the card,
dossier and permit table rendered it as an ordinary filing date.

**Change.**
* `src/oppintel/dates.py` — added `parse_iso_date()` and `occurrence_is_future()` (read-only
  helpers; a missing/malformed value is *not* treated as future).
* `src/oppintel/service.py` — `decorate()` sets `permit_date_is_future` and never alters the
  stored value (`service.py:502`).
* `src/oppintel/app/main.py` — new `filing_date` Jinja filter renders a future value as
  `"<date> (date unverified — after today)"` and absent as `Not verified`
  (`main.py:365`).
* Templates switched from `nice_date` to `filing_date`: opportunity card, dossier filing date
  and permit table, company permit table.

**Tests.** `tests/test_dates.py` (+2), `tests/test_quality.py` (+2). The value is preserved
exactly; only the label changes. No record is dropped or rewritten.

## Item 5 — Ingest funnel shown on `/how-it-works`

The pipeline page claimed Collect → Normalize → Assemble with no accountable number.
`stat_snapshot.compute_metrics` already computed a funnel (`permits_landed`, `permits_linked`,
`permits_dropped`, `public_projects`, `by_source`); it is now rendered on `/how-it-works` from the
same stored snapshot every headline uses. Funnel rows resolve a publisher *name* (not a config
key); a source id with no config entry keeps its id so it stays visible.

Live copy (copy of `data/oppintel.db`): landed 4,190 → linked 1,904 → dropped 2,286 →
public projects 133. `linked + dropped == landed` is asserted.

**Tests.** `tests/test_stat_snapshot.py` (+2).

## Item 6 — Duplicate permit analysis (no code change)

Grouping over `(source_id, permit_number, address_key, permit_date)`:

* 7 groups are true duplicates — **10 excess permit rows** (e.g. `25-005873` at 540 W Frontier
  Pkwy, Prosper appears twice with different `natural_key`).
* 218 address+date groups of "multiple permits at one address on one day" are mostly legitimate
  (one project, many trade permits), not duplicates.

Recommendation (WP2, not done here): de-duplicate on `(source_id, natural_key)` already prevents
exact re-ingest; the residual 10 rows come from a source publishing two natural keys for one
permit. Handle by folding `(source_id, permit_number, address_key, permit_date)` at normalize
time, or record a `duplicate_permit` quality finding — a decision for the Director.

## Data findings (audit copy, `SELECT` only)

* Public projects 133 of 1,261 assembled; mechanical evidence 8; public linked records 236.
* Owner present on 1,193/1,261; **GC and architect are null on every project** —
  `project_party.role` is 100% `owner` (1,830 rows). No GC/architect data is ingested at all;
  the Companies page can only ever show Owner. This is a source-coverage gap, not a UI bug.
* `project_type` null: 0. `estimated_project_value` null: 63. `square_footage` null: 139.
* Projects with zero evidence: 0. Orphan permits (no project): 2,286 (the 2,286 "dropped").
* 1,117 of 1,261 project names are ALL CAPS; 0 are truncated with an ellipsis.
* Records per city: Fort Worth 1,944 · Plano 511 · Frisco 382 · McKinney 354 · Dallas 322 ·
  Allen 172 · Wylie 93 · Celina 82 · Prosper 62 · Melissa 57 · Richardson 47 · Murphy 34 ·
  Princeton 31 · Anna 24 · Farmersville 23 · Lavon 14 · Royse City 11 · Lucas 8 · **Nevada 6** ·
  Fairview 6 · Josephine 5 · St Paul 1 · **Mckinney 1**.
* City normalization defects: `Nevada` is out-of-market and is excluded from the public
  jurisdiction count (reported, not hidden); `McKinney`/`Mckinney` are two spellings of one city.
* Permit date range 2025-11-07 → 2026-12-19; `first_observed` 2026-10-09.

## Root cause of the original stat drift

The live site's headline totals differed because each surface ran its own live `COUNT(*)`
against a dataset the refresh loop was mutating, and the Render Free plan has **no persistent
disk**, so every deploy rebuilt the database — so a screenshot and a live fetch were answers
about two different datasets. Fixes: one stored snapshot (`market_stat_snapshot`, written once
per refresh, read by every surface) and a documented requirement that counts are only stable on
a paid plan with the disk mounted (`render.yaml`, `docs/deployment.md`).

## Determinism

`compute_metrics` is byte-identical across two consecutive calls on the same database
(verified). `write_snapshot` upserts one row per `(market_id, trade_id)`.
