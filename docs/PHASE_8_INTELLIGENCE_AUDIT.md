# Phase 8 audit — Search Intelligence, Trend Radar, LinkedIn Content Intelligence

Written before any change, by reading the working tree at commit `daded68` (`main`) and the
live database at `data/oppintel.db`. It exists to establish what is already built, so the three
requested capabilities extend the platform rather than duplicate it.

## A. Architecture

* **Language / runtime**: Python 3.13 (`/.python-version`), Flask 3, gunicorn. A React/Vite
  scaffold is present but the shipped product is entirely server-rendered Jinja; `App.tsx`
  renders an empty `<div>` and `server.ts` only supervises the Flask process.
* **Entry points**: `ops/start.sh` (production, writes through `ops/automate.py --serve`),
  `run_server.py` (AI Studio / local, gunicorn → Flask → wsgiref fallback), `src/oppintel/app/wsgi.py`.
* **Layers**: `connectors/` → `pipeline.py` (FETCH→LAND→NORMALIZE→PERSIST→ASSEMBLE→CLASSIFY→
  **DERIVE**) → `assemble.py` / `classify.py` / `complexes` → `db.py` → `service.py`
  (`OpportunityService`) → `app/main.py` routes + `app/api.py` JSON → Jinja templates.
* **Blueprints**: `main` (HTML), `api` (`/api/*` JSON). Auth/authorisation in
  `app/accounts.py`, `app/security.py`, `app/entitlements.py`; admin gated by `_require_admin`.
* **Config-driven**, cached with `lru_cache`, `reset_config_cache()` clears all: `config/markets.yaml`,
  `trades.yaml`, `sources.yaml`, `keywords.yaml`, `search_vocabulary.yaml`, `trade_taxonomy.yaml`.

## B. Existing relevant features (do not rebuild)

| Capability | Where | State |
|---|---|---|
| Full-text search | `search_index.py` FTS5 `project_search`, `quote_for_fts`, `expand_terms` | Built: prefix match, synonym expansion, malformed query degrades to empty |
| Search vocabulary | `config/search_vocabulary.yaml`, `SearchVocabulary` | Built: global + per-trade synonym groups, conflict detection |
| Structured NL search | `nl_search.py` `StructuredSearchInterpreter` | Built: city/type/class/mechanical/freshness/value extraction, surfaced as badges |
| Filters + paging | `service.OpportunityFilters`, `list_opportunities` | Built: city, type, classification, procurement, dates, value band, mechanical, freshness, sort, paging |
| Match reasons | `app/matching.py` | Built: per-account "why relevant" reasons, all checkable |
| Search analytics | `db.analytics_event`, `app/search_analytics.py` | Built: `search_performed` with query_text/result_count/filter_summary/session/campaign; top/zero-result/filter reports |
| Coverage honesty | `coverage.py` | Built: 6 states configured→source_enabled→ingested→records_held→current→verified |
| Date semantics | `dates.py` | Built: `semantic_kind`, `validate_date`, `is_usable_occurrence` (future-date flagging) |
| Change detection | `changes.py`, `db.project_change`, `project_state_snapshot` | Built: snapshot diff; no change ⇒ no event |
| Durable events | `intel_events.py`, `db.project_event` | Built: digest-keyed, idempotent |
| Intelligence graph | `intelligence.py` (entities, trades, documents, locations) | Built, verified |
| Reports | `report_generator.py`, `reporting.py` | Built: internal report + customer brief, NOT VERIFIED carried through |
| Content (guides) | `app/content.py` | Built: owned editorial guides, no invented statistics |

## C. Schema and migration

* Single SQLite file; `db.init_schema()` creates base tables, `db.init_app_schema()` adds
  application tables then runs `_ensure_*` migrations (idempotent `ALTER TABLE` guarded by
  `PRAGMA table_info`). New columns are added *before* indexes that name them
  (`idx_analytics_query` is created post-migration for exactly this reason).
* Verified live counts: `project` 3062, `permit` 11966, `project_change` 5073,
  `project_event` 20647, `evidence_history` 37311, `analytics_event` 216.
* `project.trade` holds one trade today (`commercial_hvac`); `project_trade` holds 2480 rows
  across the taxonomy, so multi-trade data exists even though one trade profile is active.
* **No trend or editorial tables exist.**

## D. Test baseline (actually executed)

```
PYTHONPATH="vendor/python:src:tests" python -m pytest tests/ -q
844 passed in 138.18s
```

The mission's historical "605 passing" is **not** the current baseline: the repository is at
**844 passing, 0 failing**. One environment note: `pytest` was not installed in the container
and was installed (`pip install pytest`, 9.1.1) before the run; no test content was changed.
`tests/test_search_vocabulary.py` (14), `test_search_analytics.py` (8), `test_nl_search.py` (3)
already cover search; there are no trend or content tests.

## E. Evidence protections (must remain intact)

* A value with no supporting source is stored `NULL` and rendered `Not verified` / `NOT VERIFIED`.
* Classification gates: mechanical-evidence gate, scale floor, negation-aware normalisation,
  residential exclusion. HIGH is not granted for keyword presence alone.
* `matching.py` never asserts procurement; a closed project stays reachable by direct URL only.

## F. Gap analysis

* **Search Intelligence** — mostly present. Missing: transparent *per-result match reasons*
  (matched field/term), a normalisation layer for punctuation/whitespace/abbreviations beyond
  trade synonyms, and an explicit no-results analytics event. Vocabulary has only one project-type
  group and no location group.
* **Trend Radar** — absent. No metric definitions, no time-window comparison, no future-date
  exclusion from metric windows, no coverage caveats surfaced alongside a trend.
* **LinkedIn Content Intelligence** — absent. No candidate discovery, no formats, no drafts,
  no factual validation, no graphic brief.

## G. Implementation plan

1. **Search** — extend `search_vocabulary.yaml` (project-type + location groups, more trade
   shorthand, kept distinct); add `explain_match` to `search_index.py`; add a match-reason
   builder and `search_reasons` on `OpportunityService` items; add `search_no_results` and
   `search_result_opened` analytics events; render reasons in the list template.
2. **Trend Radar** — new `src/oppintel/trends.py` with explicitly documented metrics, project/
   permit/trade/observation counts kept separate, dedup by project identity, future-dated and
   invalid records excluded from windows with the exclusion stated, previous-period comparison
   only when history exists, coverage limits attached. New `/trends` route + template + nav.
3. **LinkedIn Content** — new `src/oppintel/linkedin.py`: deterministic candidate discovery,
   distinct formats, drafts split into verified / unverified / interpretation / editorial,
   a deterministic validation pass, and a graphic brief. New operator routes `/content/linkedin`
   and `/content/linkedin/<candidate>`; no external AI dependency.

## H. Regression risks

* `search_index.py` is imported by `service.py` and tested directly — extend, do not change
  signatures.
* `conftest_app.FIXTURE_PERMITS` is the shared synthetic dataset; new app tests must use it or
  build their own database, never edit shared fixtures.
* New tables must be created in an `_ensure_*`/app-schema path so an existing database upgrades
  without data loss, and index creation must follow column creation.
* Do not export `SECRET_KEY`/`BASE_URL` during pytest (breaks the admin no-URL test).

## I. Configuration / environment

No new mandatory variables. Optional, all defaulted: none required for the three features to
function offline; the app must start with the existing variables only
(`SECRET_KEY`, `OPPINTEL_DB`, `OPPINTEL_DATA_DIR`, `BASE_URL`, `PORT`, `HOST`).
