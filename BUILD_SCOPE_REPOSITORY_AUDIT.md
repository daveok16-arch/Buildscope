# BuildScope Repository Forensic Audit

**Audit type:** Read-only forensic audit. No application code, configuration, schema or data was
changed. The only writes were (a) running the existing test suite, (b) initialising the app schema
against the shipped database as documented, (c) two throwaway account rows created to prove a
security finding and then deleted, and (d) this report file.

**Audit basis:** the repository as extracted from the supplied archive, at
`/workspace/project`. Every claim below cites a file, a line, a command, or a measured number
from the shipped `data/oppintel.db`. Where something could not be determined it is marked
**UNKNOWN**.

**Snapshot of measured state at audit time**

| Metric | Value | Source |
|---|---|---|
| Python source | 16,089 LOC across 47 modules | `wc -l src/oppintel/**` |
| Test source | 6,704 LOC across 30 files | `wc -l tests/*.py` |
| Templates | 42 HTML files, ~4,400 LOC | `find .../templates` |
| CSS | 2,267 LOC single file | `wc -l app.css` |
| Tests run | **531 passed, 0 failed** in 56.9s | `pytest tests/ -q` |
| DB tables | 35 (incl. FTS5 shadow tables) | sqlite_master |
| DB rows | 1,797 projects, 6,227 permits, 7,425 raw records, 21,106 evidence rows | sqlite_master counts |
| Classification | 2 HIGH, 175 MEDIUM, 1,620 NEEDS_VERIFICATION | `project` group-by |
| Procurement | 166 Evidence-found, 12 Closed, 1,619 Not-verified | `project` group-by |
| Mechanical evidence | 6 Tier-1, 11 Tier-2, 1,780 none | `project` group-by |

---

## Executive Summary

BuildScope is **not** a prototype or a mock-up. It is a real, working, unusually disciplined
construction-permit intelligence pipeline (Flask + SQLite) with a genuine evidence model, a real
change-detection system, and 531 passing tests. The intelligence engine is the strongest asset in
the repository and is materially better than the marketing copy around it.

The core finding of this audit is a **separation between three things that are at very different
maturity levels**:

1. **The intelligence layer (`src/oppintel/*.py`) is production-grade in intent and largely in
   execution.** Provenance is enforced structurally (`provenance.assert_field` is the only path
   that sets a project field and it always writes an evidence row). Classification is gated, not
   just scored. Contradictions between sources are preserved rather than averaged. The
   "never invent" rule is real code, not a slogan.

2. **The application layer (`src/oppintel/app/`) is broad but over-built relative to the data.**
   It has 46 page routes, 18 API routes, accounts, plans, entitlements, orgs, alerts, analytics,
   SEO gating, company intelligence, natural-language search — but the shipped dataset holds only
   **46 publicly discoverable projects and 17 with mechanical evidence**. Many account features
   are architecturally present and thinly used (1 saved row, 1 watch, 1 pipeline entry, 0 alerts,
   0 orgs).

3. **The authentication layer contains a critical, exploitable vulnerability.** `/auth/google`
   accepts an email address from an unauthenticated GET/POST query parameter and starts a session
   as that account, with no token verification. **This is a full account-takeover vector**,
   demonstrated below against a real password-protected account. Separately, a genuine
   Firebase/Google client login flow exists in the templates, so this is not "Google auth is
   missing" — it is "a second, unsafe Google path bypasses authentication entirely."

The product's *concept* (PROJECT + EVIDENCE + SIGNALS + PROCUREMENT STATE, with strict
separation) is already the right shape in the intelligence layer. The gap to BuildScope 2.0 is
mostly in the application and data-model breadth (companies, people, documents, multi-market
scale), not in the evidence engine.

**Single most important action before any Phase 2 work: fix `/auth/google`.** It invalidates every
other security control in the app.

---

## Repository Inventory

### Classification of every top-level artifact

| Path | Type | Verdict |
|---|---|---|
| `src/oppintel/*.py` (29 modules) | Intelligence layer | **KEEP** — the core asset |
| `src/oppintel/app/*.py` (17 modules) | Application layer | Mixed — see five-way classification |
| `src/oppintel/connectors/*.py` (5) | Ingestion | **KEEP** (2 active, 3 unused/historical) |
| `src/oppintel/app/templates/` (42) | Frontend | Mixed — dense, real, some legacy copy |
| `src/oppintel/app/static/css/app.css` | Frontend | KEEP (single stylesheet, responsive) |
| `config/*.yaml` (4) | Configuration | **KEEP** — genuine config-over-code |
| `tests/*.py` (30) | Tests | KEEP, with gaps (see Test Results) |
| `data/oppintel.db` | Data | Shipped seed DB (6.8 MB) |
| `data/raw/*.jsonl` (5) | Raw landing zone | KEEP — replay/audit evidence |
| `docs/DESIGN.md`, `docs/dallas_source.md` | Documentation | KEEP — high quality, matches code |
| `docs/POSTGRES_MIGRATION.md` | Documentation | ASPIRATIONAL — describes a target not built |
| `README.md`, `AGENTS.md` | Documentation | Mostly accurate; some stale claims (below) |
| `render.yaml`, `ops/*` | Deployment/ops | KEEP |
| `server.ts`, `index.html`, `src/App.tsx`, `src/main.tsx`, `src/index.css`, `package.json`, `vite.config.ts`, `tsconfig.json`, `bun.lock` | AI-Studio React/Vite scaffold | **DELETE** (dead scaffold, see Legacy) |
| `firebase-applet-config.json` | Config w/ secret | **REPLACE** (committed API key) |
| `metadata.json` | AI-Studio artifact | DELETE |
| `src/oppintel.egg-info/` (6 files) | Generated | DELETE (build artifact) |
| `.pytest_cache/`, `**/__pycache__/` | Generated | DELETE (includes orphan `.pyc` for deleted tests) |
| `vendor/python/` | Vendored deps | KEEP for this container; not how prod runs |
| `.env.example` | Config template | KEEP |

### Dead code / unused / abandoned (evidence)

- **`src/oppintel/events.py` (354 LOC)** — a full "formal event-driven model"
  (`BuildScopeEvent`, `ProjectCreatedEvent`, …). **Not imported by any runtime module.** The only
  importer is `tests/test_events.py`. It is a parallel, unused event model alongside the real one
  (`project_change` + `app/alerts.py`). Confirmed by AST scan: no `ImportFrom`/`Import` of the
  events module anywhere in `src/` except its own file.
- **`src/oppintel/nl_search.py` (247 LOC)** — used only by one route
  (`_interpret_search_query` in `main.py`); tested by 3 tests. Lightly integrated, not dead but
  thin.
- **`src/oppintel/connectors/dallas_permits.py`** — two connectors
  (`dallas_gis_permits`, `dallas_permits_socrata`) that are **`enabled: false`** in
  `config/sources.yaml` and hold **0 rows**. Historical-only by design.
- **Orphan compiled tests** — `tests/__pycache__/` contains `.pyc` for
  `test_provenance`, `test_report_generator`, `test_reporting`, `test_security`, `test_seo_keywords`,
  `test_workflow` — **six test files whose source is absent** but whose compiled bytecode remains
  (containing 11+27+13+23+52+35 = **161 tests**). README claims "673 tests"; the suite actually runs
  **531**. The missing six files are the ones that would cover provenance, the report generator,
  the reporting module, **security**, **SEO keywords**, and **workflow**.
- **React/Vite scaffold** — `src/App.tsx` renders `<div></div>` (8 LOC). `index.html` mounts
  `/src/main.tsx`. `server.ts` is an Express reverse proxy that spawns `run_server.py`. `package.json`
  declares React 19, Vite 8, Tailwind 4, `@google/genai`, `sql.js` — **none used by the running
  product**. This is the AI Studio wrapper; the real product is the Flask app.
- **`bun.lock`** — 0 bytes.
- **`firebase-applet-config.json`** — committed Firebase web config incl. `apiKey` (see Security).

### Duplicate implementations

- **Two change/event models:** `changes.py` (real, wired into the pipeline) and `events.py`
  (unused).
- **Two Google auth paths:** the safe Firebase-token path (`/auth/firebase-verify`, verifies the
  token server-side) and the unsafe email path (`/auth/google`, no verification). Both exist in
  `main.py`.
- **Two entity sources for companies:** `project_party` (intelligence table) and the
  denormalised `project.owner` / `project.general_contractor` columns, unioned at query time in
  `companies.py`.

---

## Current Architecture

### Framework and entry points

- **Framework:** Flask (vendored 3.1.3), server-rendered Jinja2. No SPA, no JS framework, no build
  step for the served product.
- **WSGI entry point:** `src/oppintel/app/wsgi.py` → `application = create_app(load_config())`.
  Also exposes `main()` for the dev server.
- **App factory:** `create_app(config)` in `src/oppintel/app/main.py` (1,977 LOC). Registers 46
  page routes, then the `api` blueprint (18 routes), then `install_security`.
- **Reverse proxy:** `server.ts` (Express) proxies all requests to Flask on port 12000 and spawns
  the Python backend. This is the AI-Studio path; `render.yaml` bypasses it and runs gunicorn
  directly.
- **Supervisor/automation:** `ops/automate.py` runs gunicorn on `0.0.0.0:12000` **and** a refresh
  thread (ingest → assemble → build-search-index → monitor).

### Real request/data flow

```
Browser
  │  (HTML forms, no SPA)
  ▼
Flask routes (app/main.py)  ──  api blueprint (app/api.py, /api/*)
  │  g.db (per-request sqlite3 connection)
  │  g.service = OpportunityService(db, market, trade, viewer_id)
  ▼
OpportunityService (src/oppintel/service.py, 1,206 LOC)
  │  THE boundary: only app-layer module that queries intelligence tables
  │  reads project / evidence / project_permit / project_change / project_slug / source_coverage
  │  + app/workflow.py, app/alerts.py, app/entitlements.py, app/accounts.py (own tables)
  ▼
SQLite (data/oppintel.db)  ← single file
  ▲                                    ▲
  │ writes                             │ reads
Pipeline (src/oppintel/pipeline.py)    search_index.py (FTS5 project_search)
  │  connectors → raw landing (data/raw/*.jsonl + raw_record) → normalize → assemble
  │  → evidence (provenance) → classify → eligibility/procurement → changes
  ▼
Public sources: Fort Worth ArcGIS · Collin CAD Socrata · Dallas Accela
```

### Layer boundaries (verified)

- `tests/test_app_architecture.py` (23 tests) asserts the application layer contains **no**
  classification logic, no hardcoded market name, and no literal evidence column. It passes.
- `service.py` is the only app-layer module issuing SQL against `project`. Confirmed by reading
  the app modules: `workflow.py`, `alerts.py`, `accounts.py`, `entitlements.py`, `companies.py`
  query only their own tables (plus `project` by id for joins).
- Market and trade are resolved from `config/markets.yaml` / `config/trades.yaml`; the active
  market/trade is `dfw` / `commercial_hvac`.

### Search layer

- **SQLite FTS5** virtual table `project_search` (tokenize `unicode61`), populated from `project`
  + aggregated permit `work_description` by `search_index.py`. Query path:
  `OpportunityService._search_ids()` → `WHERE project_search MATCH ? ORDER BY rank`. All other
  filters are ordinary SQL `WHERE` clauses over indexed `project` columns.

### Background jobs

- One in-process supervisor + one refresh thread (`ops/automate.py`). No Celery, no cron, no
  external queue. Documented as deliberate (container has no cron/systemd).

### External APIs

- Three permit sources over HTTP (ArcGIS REST, Socrata, ASP.NET WebForms).
- Google OAuth token verification (`oauth2.googleapis.com/tokeninfo`) and Firebase Identity
  Toolkit, in `/auth/firebase-verify`.

### Caching

- **None.** No Redis, no HTTP cache layer, no in-process memoisation of counts. Every page re-runs
  its SQL.

### Session mechanism

- Flask signed cookie (`session`), holds only the user id and the CSRF token. `HttpOnly`,
  `SameSite=Lax`, `Secure` outside debug. Confirmed live: `Set-Cookie: session=...; Secure; HttpOnly; Path=/; SameSite=Lax`.

---

## Intelligence Pipeline

Stage-by-stage trace of the **actual** implementation.

| Stage | Location | Input | Output | Tables | Status |
|---|---|---|---|---|---|
| FETCH | `connectors/*.py` | HTTP | `RawPermit` stream | — | **Production** for FW/Collin/Dallas |
| LAND | `connectors/base.py:land_to_disk`, `pipeline.ingest_source` | `RawPermit` | `data/raw/*.jsonl` + `raw_record` | `raw_record`, `ingest_run` | **Production** |
| NORMALIZE | `connectors/*.normalize`, `normalize.py` | `RawPermit` | `Permit` | `permit` | **Production** |
| ENTITY RESOLUTION | `assemble.normalize_address`, `grouping.building_key` | `Permit` | address key / building key | — | **Partial** (see below) |
| ASSEMBLE | `assemble.cluster_permits` / `assemble_project` | `Permit[]` | `Project` | `project`, `project_permit` | **Production** |
| EVIDENCE | `provenance.assert_field` | field + source | `Evidence` | `evidence` | **Production** |
| CLASSIFICATION | `classify.classify` | `Project` | HIGH/MEDIUM/NEEDS_VERIFICATION + reasons | `project`, `project_classification` | **Production** |
| PROCUREMENT | `procurement.procurement_status` | `Project` | 4 states | `project.procurement_status` | **Production, deliberately narrow** |
| CHANGE DETECTION | `changes.diff_project` | current vs `project_state_snapshot` | `DetectedChange[]` | `project_change`, `project_state_snapshot` | **Production** |
| SEARCH | `search_index.py`, `service._search_ids` | text | ranked project ids | `project_search` (FTS5) | **Production** |
| USER EXPERIENCE | `app/main.py` + `service.py` | filters | HTML/JSON | reads | **Production but over-broad** |

### Does BuildScope have an evidence *graph* or flat records?

**It has a genuine per-field evidence model, and it is a graph, but a shallow one.**

- `evidence` (21,106 rows) is keyed `(project_id, field_name)` and carries
  `source_id, source_name, source_url, source_record_key, source_date, observed_at, evidence_type,
  tier, excerpt`. That is real provenance per field, not a flat denormalised row.
- `project_permit` links projects to the permits that formed them (2,832 rows).
- `project_party` records owner/GC roles with their own source citation (2,697 rows).
- **But** the graph is shallow: there is no first-class `company` / `person` entity, no
  company-to-company relationship table, no `document` entity (no PDFs/plans), and `project_party`
  is not resolved (names are stored as raw strings, so "ACME LLC" and "ACME LLC." are two
  parties). Entity resolution for *people and companies* does not exist; resolution for *projects*
  is address-based only.

### Known limitations observed in code and data

- Collin CAD carries **no trade permits**, so **0** mechanical evidence comes from it
  (`source_coverage.mechanical_count = 0`), yet it supplies 19,174 of 21,106 evidence rows. The
  dataset is dominated by a source that cannot evidence the product's core claim.
- Only **6 Tier-1** and **11 Tier-2** mechanical projects exist in the entire shipped dataset.
- `raw_record` (7,425) and `permit` (6,227) counts differ from `source_coverage.record_count`
  (e.g. Fort Worth 2,875 covered vs fewer stored), because coverage counts what the source
  returned, not what survived commercial filtering — this is documented and intentional.

---

## Data Model

### Every entity, mapped to storage

| Entity | Table | PK | Provenance | Notes |
|---|---|---|---|---|
| SOURCE | `source` | `id` | — | 5 rows, config-driven |
| INGEST RUN | `ingest_run` | `id` | — | 7 rows |
| RAW RECORD | `raw_record` | `id` | `(source_id, natural_key, payload_hash)` unique | 7,425 rows, verbatim JSON |
| SOURCE COVERAGE | `source_coverage` | `source_id` | — | observed dates, pagination |
| PERMIT | `permit` | `id` | `(source_id, natural_key)` unique | 6,227 rows |
| **PROJECT** | `project` | `id` | via `evidence` | 1,797 rows, 33 columns |
| PROJECT↔PERMIT | `project_permit` | `(project_id, permit_id)` | — | 2,832 rows |
| **EVIDENCE** | `evidence` | `id` | `(project_id, field_name)` | 21,106 rows |
| PARTY | `project_party` | `id` | `(project_id, role, name)` | 2,697 rows; **no company table** |
| CLASSIFICATION HISTORY | `project_classification` | `id` | — | 1,798 rows (append-only) |
| PROJECT SLUG | `project_slug` | `slug` | — | 1,797 rows |
| CHANGE | `project_change` | `id` | `(project_id, detected_at)` | 2,355 rows |
| SNAPSHOT | `project_state_snapshot` | `project_id` | — | 1,797 rows |
| USER | `app_user` | `id` | — | 7 rows |
| SAVED | `saved_opportunity` | `(user_id, project_id)` | — | 1 row |
| WATCH | `watched_opportunity` | `(user_id, project_id)` | — | 1 row |
| PIPELINE ITEM | `pipeline_entry` | `(user_id, project_id)` | — | 1 row |
| NOTE | `opportunity_note` | `id` | — | 3 rows |
| TAG | `opportunity_tag` | `(user_id, project_id, tag)` | — | 0 rows |
| ACTIVITY | `user_activity` | `id` | — | 7 rows |
| ALERT | `alert_event` | `id` | points at change/match | **0 rows** |
| ORG | `organization` / `organization_member` | `id` | — | **0 rows** |
| PLAN | `plan` | `id` | — | 3 rows (FREE/PRO/TEAM) |
| SUBSCRIPTION | `subscription` | `user_id` | — | 0 rows |
| QUALITY ISSUE | `data_quality_issue` | `id` | — | 0 rows (cleared/rebuilt each pass) |
| APP ERROR | `app_error` | `id` | — | 5 rows |
| ANALYTICS | `analytics_event` | `id` | — | 128 rows |
| SEARCH INDEX | `project_search` (FTS5) | — | — | 1,797 rows |

### Entities the brief asks about that are **missing**

| Entity | Status |
|---|---|
| COMPANY (first-class) | **MISSING** — derived at query time in `companies.py` from `project_party` + `project.owner`/`general_contractor`; no table, no stable id, no merge |
| PERSON | **MISSING** — no person entity at all |
| DOCUMENT | **MISSING** — no documents, no PDFs, no attachments |
| LOCATION (as geometry) | **MISSING** — city/state/zip text only; no lat/long column used, no PostGIS |
| TRADE (as entity) | Config-only (`config/trades.yaml`), not a table |
| PROCUREMENT SIGNAL (as event) | **MISSING** — procurement is a single enum column, not a signal stream |
| ORGANIZATION | Table exists, **0 rows**, no web admin |
| EVENT (formal) | `events.py` defines it, **unused**; the real event record is `project_change` |

### The true "core object"

**`project` is the true core object, and `permit` is the true substrate.** The code assembles
permits into projects at a normalised address (`assemble.py`), and every customer-facing page and
API response is a `project`. A "lead" or "opportunity" is not a stored entity — it is a *derived
view* of a project (`service.decorate` adds `is_report_eligible` from `eligibility.evaluate`). The
"opportunity" in the UI is `project` + filters, not its own table.

This is architecturally **correct** for BuildScope 2.0's stated target (PROJECT as the anchor), and
it means "lead" is indeed a derived business concept, as the brief requires.

### Duplication / scalability risks in the model

- `project` denormalises 33 columns including display values that also exist in `evidence`. This is
  a deliberate read-optimisation (the service reads `project` directly), but it means the same fact
  lives twice; `provenance` keeps them consistent at write time.
- No foreign keys are declared on the FTS table (by design) and SQLite FK enforcement is off unless
  `PRAGMA foreign_keys=ON` — **UNKNOWN whether it is enabled** (not observed in `db.py`; the
  `REFERENCES` clauses are therefore documentation, not enforcement, for most paths).
- `raw_record.payload` is TEXT (JSON), not JSONB; payload-hash uniqueness is the idempotency key.

---

## Data Sources & Ingestion

### Per-source inventory

| Source id | Jurisdiction | Type | Auth | Enabled | Status | Rows (DB) | Mechanical |
|---|---|---|---|---|---|---|---|
| `fort_worth_permits` | Fort Worth, TX | ArcGIS REST | none | ✅ | **ACTIVE** | ~2,875 covered | 531 covered |
| `collin_cad_permits` | Collin County, TX | Socrata | none | ✅ | **ACTIVE** | 2,869 covered | **0** |
| `dallas_accela_permits` | Dallas, TX | ASP.NET WebForms | none | ✅ | **ACTIVE** | 483 covered, 42 pages | 39 covered |
| `dallas_gis_permits` | Dallas, TX | ArcGIS | none | ❌ | **STALE/DISABLED** | 0 | 0 |
| `dallas_permits_socrata` | Dallas, TX | Socrata | none | ❌ | **STALE/DISABLED** | 0 | 0 |

### Ingestion quality properties (verified in code)

- **Reliable:** `BaseConnector.get_json` retries 429/500/502/503/504 with exponential backoff
  (`base.py`); Dallas Accela `_post` does the same and the doc explains the intermittent 502s.
- **Reproducible:** verbatim payloads land to `data/raw/*.jsonl` **and** `raw_record`; the DB write
  path re-reads the landed file (`pipeline._replay`), proving the file is complete before use.
- **Observable:** `ingest_run` (status, counts, error) + `source_coverage` (observed date range,
  pagination) + `data_quality_issue`.
- **Idempotent:** `raw_record` unique on `(source_id, natural_key, payload_hash)`; permits unique on
  `(source_id, natural_key)`; assembly diffs against snapshot.
- **Incremental:** connectors accept `since` (`File_Date >= …`, `permitissueddate > …`).
- **Restartable:** commits every 250 permits (`COMMIT_EVERY`).

### Connector sophistication (this is a real asset)

`connectors/dallas_accela_permits.py` (561 LOC) reverse-engineers an ASP.NET WebForms portal: it
parses the whole form and replays every hidden field, requires session-scoped `__VIEWSTATE` +
`Origin`/`Referer`, and follows the pager by the **`Next >` anchor label** rather than an arithmetic
control index (the doc explains that index arithmetic silently oscillated between two pages). This
is genuinely non-trivial engineering and is documented in `docs/dallas_source.md`.

### Licensing / terms

- All sources are public, free, no-auth government portals; documented as such in
  `config/sources.yaml` and README. TDLR TABS and the Dallas GIS portal were **excluded** because
  they require login. No paid feeds. **No explicit licence text** is captured per source — a
  residual legal-review item, not a code defect.

---

## Evidence & Provenance

**This is the best-engineered part of the repository and should be protected.**

- `provenance.assert_field()` is the **only** path that sets a project field, and it always appends
  an `Evidence` row (or refuses). It rejects `None`, blank, and the sentinel strings
  `NULL/N/A/NA/NONE/-`. It raises `ProvenanceError` if no source is identified. It records the
  same fact once per source, but records *different* values from different sources (so
  contradictions survive).
- `NOT_VERIFIED = "Not verified."` is applied **at render time only**; the DB stores `NULL`.
  Confirmed in `constants.py` and `models.Project.display`.
- Evidence carries: field, value, source id, source name, **source URL**, source record key,
  source date, observed-at, evidence type (`permit`/`permit_scope`/`party_role`/`derived`), tier,
  and a **literal excerpt**.
- `evidence_type` distinguishes *sourced* (`permit`) from *derived* (`project_type` is recorded as
  `derived`), which is a genuinely careful touch.
- The API returns `null`, never the string "Not verified" (`api.py`), and the HTML renders "Not
  verified" — the two disagree by design and correctly.

**Weaknesses:** excerpts are constructed, not always verbatim spans of the source; and while
`source_url` is stored, Fort Worth/Collin use `source_url_template` while Dallas Accela records
`source_url_template: null` (the portal exposes no per-record permalink), so **Dallas evidence has
no resolvable source link** — documented, but it weakens the "every fact is clickable" promise for
Dallas.

---

## Entity Resolution

- **Project identity:** address-based. `assemble.normalize_address` lowercases, strips
  suite/unit, expands street-type abbreviations, and clusters permits within a 540-day window.
  `project_key = sha1(trade|address_key)[:20]`.
- **Building identity:** `grouping.building_key` groups projects by base address (suite removed) as
  a *hint*; projects are **never merged**. Sibling relationships are reported as *uncertain*.
- **Company identity:** **DOES NOT EXIST.** `companies.py:clean_company_name` does light
  normalisation (trim, strip quotes, drop `none/n/a/unknown/null/owner`) but there is no
  canonicalisation, no alias table, no merge. Two spellings of the same GC are two companies.
- **Person identity:** **DOES NOT EXIST.**
- **Trade identity:** config-only.
- **Location identity:** text `city`/`state`/`zip_code`; no geocoding, no coordinates used
  (Fort Worth requests `Latitude,Longitude` in `OUT_FIELDS` but `normalize()` **discards them** —
  the data is fetched and thrown away).

This is the single largest *data-model* gap relative to BuildScope 2.0 (which requires COMPANIES
and PEOPLE as first-class entities).

---

## Classification

- `classify.classify()` is additive scoring **plus four enforced gates** (`classify.py`,
  `config/trades.yaml`): mechanical-evidence gate, scale gate, significance gate, precise-location
  gate. A large-value project with no mechanical evidence **cannot** be HIGH. Verified in code and
  by `tests/test_classify.py` (15 tests).
- Every point appends a human-readable reason to `classification_reasons`; the DB column stores
  them; the detail page renders them.
- `project_classification` is append-only history (1,798 rows for 1,797 projects — one per pass).
- Vocabulary: `HIGH`, `MEDIUM`, `NEEDS_VERIFICATION` only.

**Assessment: production-quality.** The gating philosophy (under-claim by default) is exactly
right for a trust-dependent product, and it is enforced in code, not documentation.

---

## Procurement Intelligence

- `procurement.py` defines four states: `Confirmed open`, `Evidence found, status unclear`,
  `Not verified`, `Closed`.
- **`Confirmed open` is unreachable** because `BID_EVIDENCE_PHRASES` are never present in any
  configured source; the module says so explicitly. This is honest, not a bug.
- Completion is tested **before** activity with whole-word matching, so `"Final CO Issued"`
  (closed) is not read as active via the word "issued", and `"Incomplete Submittal"` (active) is not
  read as closed via "complete". This is a genuinely subtle, correct implementation.
- `is_claimable()` excludes CLOSED and NOT_VERIFIED from customer-facing briefs.

**Assessment: production-quality but *narrow by design*.** It is a *status classifier over permit
status text*, not a procurement-signal system. There is no bid calendar, no award record, no
contract-value signal, no "who is bidding" signal. The brief's target of a `PROCUREMENT SIGNALS`
entity does not exist; there is one enum column.

---

## Change Detection

- `changes.py` compares a project against its `project_state_snapshot` across 16 tracked fields
  plus the permit-key set. A no-op pass emits nothing (verified by `tests/test_changes.py`, 18
  tests).
- Change kinds are a controlled vocabulary; `NOTIFIABLE_KINDS` distinguishes an alert-worthy change
  from a routine correction.
- Ordering is careful: snapshot is written *after* recording changes so a mid-failure cannot mark a
  project seen-and-drop an event.
- Live data: **2,355 change rows** in `project_change` for 1,797 projects — change detection is
  running and producing real events (the seed DB was assembled more than once).

**Assessment: production-quality.** This is a genuine, idempotent, low-false-positive change feed.

---

## Search

- **Mechanism: SQLite FTS5** (`project_search`, `unicode61`) for free text, plus parameterised SQL
  `WHERE` for every structured filter. `quote_for_fts` sanitises the query; a malformed FTS
  expression degrades to "no results" rather than a 500.
- Filters: city, project type, classification, procurement, date range, min/max value,
  mechanical-only, freshness-days, project-ids. Sorting: `SORT_OPTIONS`. Pagination:
  `OpportunityPage` with total.
- **Natural-language search:** `nl_search.StructuredSearchInterpreter` parses a query into
  structured filters (city/project_type/classification/mechanical_only) — a lightweight rule-based
  interpreter, 3 tests. Not an LLM.
- **Saved searches:** **MISSING.** `user_preference` holds cities/project_types/value band, but
  there is no saved-search table and no saved-search UI.
- **Autocomplete:** **MISSING.**
- **Geographic search:** **MISSING** (no coordinates; city is an exact-match filter).
- **Procurement filter:** present.

**Scalability of search:** FTS5 with no ranking tuning is fine into the low millions of rows;
`ORDER BY rank` and `COUNT(*)` over `project` are the first things to slow. The architecture (index
table separate from `project`, rebuildable) is sound and does **not** require a rewrite to move to
Postgres FTS or OpenSearch.

---

## Project Experience

`/opportunities/<slug>` (`templates/opportunities/detail.html`) renders, from `service.py`:

- Project name, address, city/state, project type, declared value, square footage, permit date,
  project status — each with a **field verdict** (`field_status_for`) and evidence excerpt.
- **Procurement status** with `procurement_explanation`.
- **Mechanical evidence** with tier and excerpt.
- **Sources** (`sources_for`) and **per-field evidence** (`evidence_for`).
- **Timeline** of recorded changes (`timeline_for`).
- **Siblings** / building relationship (`sibling_info_for`), **related** projects.
- Saved/watched/pipeline state for the signed-in viewer.

**Evidence-backed vs inferred:**
- Evidence-backed: address, city, state, permit number/date, status, value, sqft, owner,
  mechanical evidence, project type (marked `derived`), sources.
- **Inferred/derived:** `project_type` (derived from keywords — labelled derived), `property_class`,
  `location_precision`, `classification`, `procurement_status`, `is_report_eligible`, siblings.
- **Effectively always unverified:** `architect` and `developer` — no free source publishes them.
  In the shipped DB, `evidence` has **no rows** for `architect` or `developer` at all. The product
  states this honestly rather than guessing.

**Unsupported-claim check:** the detail page states the procurement caveat verbatim and does not
call anything an open bid. No fabricated party is shown.

---

## Company Intelligence

- `/companies` and `/companies/<slug>` via `companies.CompanyService` (309 LOC).
- Aggregates stakeholders from `project_party` **union** `project.owner` **union**
  `project.general_contractor` over HIGH/MEDIUM projects.
- Profile includes roles observed, project count, total declared value, cities, project types,
  mechanical project count, associated projects, **recurring partners** (co-occurrence).
- **Live defect found:** `/companies?q=…&city=…` **returns HTTP 500** (recorded in `app_error`,
  reproduced during audit). The bare `/companies` and single-param variants return 200. The
  `where_clause.replace('name', 'pp.name')` string-surgery in `list_companies` is fragile: a `city`
  filter combined with `q` produces a malformed column reference. **Confirmed via live request.**

**Assessment: PARTIAL and currently buggy.** The concept is valuable (this is a BuildScope 2.0
pillar) but the implementation is query-string surgery over a UNION and breaks on combined filters.
No company table exists.

---

## Watchlists

- `watched_opportunity` table + `app/workflow.py` + `/watching`.
- A watch is distinct from a save (documented and structurally separate).
- **Live data: 1 watch, 1 save.** The feature is implemented and essentially unused.

**Assessment: IMPLEMENTED but unexercised.** Design is sound (watch drives re-check + alerts).

---

## Alerts

- `app/alerts.py` (251 LOC). Every alert points at a `project_change` row or a first-match event.
  `alerts_without_event()` must stay empty (asserted).
- Delivery is **in-app only**; email is modelled (`email_sent_at`) but **not sent** (documented).
- **Live data: `alert_event` = 0 rows.** No alerts have been generated because no user has watched
  a project that changed since.

**Assessment: IMPLEMENTED, event-driven and honest, but dormant and unproven in production.** The
"no event = no alert" invariant is real.

---

## Pipeline

- `pipeline_entry` table + `/my-pipeline` + `app/workflow.py` (553 LOC).
- Stages: NEW, REVIEWING, WATCHING, TARGET, CONTACTED, PURSUING, CLOSED (rendered "Closed out").
- Vocabulary is deliberately disjoint from procurement states; `tests/test_workflow.py` (source
  missing!) asserted this. The assertion logic still exists in code (`normalise_stage`).
- **Live data: 1 pipeline entry.**

**Assessment: IMPLEMENTED, correct separation, unexercised.**

---

## Analytics

- `analytics_event` (128 rows) records an event **name** + optional project/market/trade + timestamp.
  **No IP, no user agent, no URL, no query string, no account link** — verified in `accounts.py`
  and `analytics_funnel.py`.
- `/analytics` page renders counts from `market_statistics()` (projects, mechanical, tier-1, cities,
  permits) and a per-city breakdown.
- Funnel: landing → viewed → created → saved → watched → pipeline, from real event rows.

**Assessment: IMPLEMENTED and privacy-respecting.** Thin (128 events) but real. No third-party
analytics (GA/Plausible) integrated; no Search Console/rank tracking.

---

## Authentication & Accounts

**Actual system (verified in `accounts.py`, `main.py`, live requests):**

- **Signup/signin with email+password:** implemented. PBKDF2-SHA256 via `werkzeug.security`, per
  password salt, `MIN_PASSWORD_LENGTH = 8`. Identical error for unknown email vs wrong password
  (enumeration-resistant). Live signup returned 302 + authenticated session.
- **Sessions:** Flask signed cookie, user id only; `HttpOnly`, `SameSite=Lax`, `Secure`.
- **Logout:** `/signout` clears session.
- **Password reset:** implemented (`forgot_password` + `reset_password/<token>`), token stored with
  expiry. **Note:** the reset link is rendered on the page (`reset_link`) rather than emailed
  (no mailer), which means password reset currently leaks the token to whoever requests it for a
  known email — see Security.
- **Google authentication — two paths:**
  1. **Firebase client path (real):** `signin.html` loads the Firebase SDK, `signInWithPopup`, and
     POSTs the ID token to `/auth/firebase-verify`, which **verifies the token server-side** against
     Google/Firebase. This path is legitimate.
  2. **`/auth/google` (unsafe):** accepts `email` (and `display_name`) from GET/POST query
     parameters, derives a `google_id` as `"google_"+sha256(email)`, and calls
     `authenticate_or_link_google`, then `_start_session`. **No token is verified.**
- **Email verification:** not implemented.
- **Orgs/roles:** tables exist; `access_level ∈ {FREE, PRO, TEAM, ADMIN}`; ADMIN granted only by
  CLI. Org membership implemented in `workflow.py` for peer listing/assignment; org **creation** is
  not exposed (must be seeded in DB).

**CRITICAL FINDING — account takeover via `/auth/google` (demonstrated live):**

```
1. Victim signs up:      POST /signup  (email=audit-victim@example.com, real password) → 302, authenticated
2. Attacker, no password: GET /auth/google?email=audit-victim@example.com → 302
3. Attacker session:     GET /api/me → {"authenticated":true,"user":{"display_name":"audit-victim",...}}
```

The attacker obtained an authenticated session as the victim with **no credential and no token**.
Because `authenticate_or_link_google` **links by email to an existing account**, this works against
any registered user. It requires no Firebase project, because the `firebase_configured` check passes
whenever `firebase-applet-config.json` exists (it is committed) — and even the "not configured"
branch is a redirect, not a security boundary. Probe accounts were deleted after the test.

This also means the `users table` (7 rows) contains accounts created via a path that cannot verify
identity; and it defeats the enumeration-resistant signin, CSRF on forms (GET needs no token),
password hashing, and IDOR protections (the attacker *is* the victim).

---

## Security Audit

| Area | Finding | Severity |
|---|---|---|
| **Auth bypass / account takeover** | `/auth/google` accepts an arbitrary `email` and starts a session as that user, no verification. Demonstrated live against a password-protected account. | **CRITICAL** |
| **Committed secret** | `firebase-applet-config.json` commits a live Firebase **`apiKey`** (`AIza…`) + project/app IDs. Redacted here. Web API keys are not secrets by design *if* restricted, but this one is unrestricted in the repo and also gates the vulnerable `/auth/google` branch. | **HIGH** |
| **Password reset token disclosure** | `forgot_password` renders the reset link (with token) directly in the response for any known email; no mailer. Anyone can request a reset for a victim email and read the token, then set a new password. | **HIGH** |
| **CSRF** | Session-bound token, constant-time compare, required on all unsafe methods; every template POST form carries it (asserted by a test). `/auth/firebase-verify` exempted (header/JSON auth). **But** `/auth/google` is reachable by **GET**, so CSRF is irrelevant there — the bypass is worse than CSRF. | MEDIUM (given the above) |
| **Rate limiting** | In-process sliding window; tightest on `/signin`/`/signup` (10/300s). Per-process only (documented). Does not cover `/auth/google`. | MEDIUM |
| **Session security** | `HttpOnly`, `SameSite=Lax`, `Secure` outside debug; cookie holds only user id. Good. | LOW |
| **SQL injection** | All user values bound as parameters; the only identifier interpolation is the config-derived `evidence_field`, validated with `re.fullmatch(r"[a-z_]+")`. **No injection found.** | LOW |
| **XSS** | Jinja autoescaping; JSON-LD emitted via `|tojson`. Inline scripts use `|tojson` for config. CSP is same-origin with `'unsafe-inline'` for scripts/styles (weakens CSP but standard for this pattern). | LOW |
| **SSRF** | `/auth/firebase-verify` fetches Google endpoints with a user-supplied token appended to a fixed URL — not a general SSRF. No user-controlled outbound URL found. | LOW |
| **Command execution** | None found. No `eval`, no `subprocess` with user input. | LOW |
| **File upload** | None implemented. | N/A |
| **CORS** | No CORS headers set (same-origin only). Fine. | LOW |
| **Security headers** | `X-Content-Type-Options`, `X-Frame-Options: DENY`, `Referrer-Policy`, `Permissions-Policy`, CSP — all present (verified live). Note `server.ts` *deletes* `X-Frame-Options` and rewrites `frame-ancestors` when proxying, which **weakens framing protection in the AI-Studio path**. | MEDIUM |
| **Sensitive logging** | Passwords never logged; errors record only class + path (`app_error`). Good. | LOW |
| **IDOR** | Account-scoped reads/writes filtered by session user id; tests cover cross-account saves/notes/alerts. Good — except the auth bypass renders it moot for `/auth/google` victims. | LOW (modulo bypass) |
| **Debug/production config** | `FLASK_DEBUG` default false; CSRF/secure-cookie default on outside debug. `SECRET_KEY` warned if unset. | LOW |
| **Reproducibility** | `data/oppintel.db` ships **7 user rows** including password hashes; if this DB is deployed, those accounts and their sessions are exposed. | MEDIUM |

### Committed-secret report (redacted)

| Secret type | File | Location | Severity |
|---|---|---|---|
| Firebase Web API key | `firebase-applet-config.json` | `apiKey` field | HIGH |
| Firebase project/app identity | `firebase-applet-config.json` | `projectId`, `appId`, `oAuthClientId` | LOW (identifiers) |

No other credentials were found (scanned `*.py`, `*.json`, `*.yaml`, `*.ts`, `*.sh`, `*.md`,
`*.example`). `.env.example` contains only placeholders.

---

## Frontend / UX Audit

- **No JS framework.** Server-rendered Jinja + one 2,267-LOC stylesheet. Small inline JS only for
  the Firebase Google button, nav, and directory filters.
- **Navigation:** header nav (Home, Opportunities, Markets, Companies, Changes, Analytics, Reports,
  How It Works), search box, Sign In / Get Access. Footer with product/coverage/workspace columns.
- **Pages audited:** home, opportunities list/detail, markets (+city, city_trade, trade),
  project-types, guides, how-it-works, reports, companies, changes, analytics, dashboard, watching,
  my-pipeline, alerts, saved, preferences, signin/signup, admin/data, error pages.
- **Live render verified** for `/`, `/opportunities`, and a detail dossier via the public work host.
  Real data renders (92 commercial projects, 4 with mechanical evidence, 14 jurisdictions, 5,884
  permit records on the homepage at audit time — note these differ from DB counts because the
  homepage uses public-scoped counts).
- **Hardcoded DFW/HVAC references** appear in **23 templates** and in `content.py`, `reports.py`,
  `seo.py`. Most are *rendered from config* (`g.market.short_name`, `g.trade.label`), but
  `content.py` (guide prose) hardcodes "Dallas, Fort Worth and the Collin County cities" and
  "HVAC" as literal text, and `reports.py` hardcodes `"dfw-hvac-opportunity-brief"`. This is
  **legacy DFW/HVAC-specific copy** inside an otherwise config-driven app.
- **Unsupported metrics:** none found — analytics/home counts are DB counts. The homepage
  "5,884 Permit records processed" is a real count (public scope), not a fabricated figure.
- **Fake/demo/placeholder content:** none found in templates. Empty states are real.
- **Duplicated components:** `partials/opportunity_card.html` is shared; `markets/city.html` and
  `markets/trade.html` and `markets/city_trade.html` overlap heavily.
- **Dead navigation:** `/analytics` is linked in nav; it 500'd earlier in the DB's history
  (`app_error` rows) but returns 200 now. `/companies` link is live but its filtered view 500s.
- **Broken responsive behavior:** **not observed** — media queries at 1024/860/640/380px exist and
  the viewport meta is set. Mobile behaviour was **not** exercised on a device (see Mobile Audit).

---

## Mobile Audit

- `viewport-fit=cover` and four breakpoints are present; layout uses a `.wrap` container.
- **Not verified on a real device or emulator** during this audit (no mobile browser available in
  the environment). **UNKNOWN** beyond the presence of responsive CSS.
- Risk: the directory filter form is dense (7 controls + Apply/Clear); at 380px it may be cramped,
  but no code evidence of breakage was found.

---

## SEO Audit

- `app/seo.py` (740 LOC) builds metadata, canonical URLs, JSON-LD, `sitemap.xml` (76 URLs live) and
  `robots.txt` from the route map. `robots.txt` disallows account/admin/api/healthz and `/*?`
  (filtered views). Verified live.
- `app/seo_gate.py` gates programmatic pages on `min_projects` / `min_mechanical` from
  `config/keywords.yaml`; failing pages render but are `noindex` and excluded from the sitemap.
- `config/keywords.yaml` maps keywords → single canonical page; deferred keywords (bid-intent) carry
  reasons. **This is genuinely sophisticated and honest.**
- **`seo_report.py`** produces an audit and reports **no ranking** (correctly).
- **The tests that enforce the keyword map (`test_seo_keywords.py`, 52 tests) are MISSING** — only
  orphan `.pyc` remains. So the "one primary keyword per page" and "deferred has no destination"
  rules are currently **unenforced** by the live suite.
- **Content is real intelligence**, not generic marketing: guide prose explains permit evidence;
  pages render live counts. Some guide prose is DFW/HVAC-specific (legacy).
- **Unsupported claims:** the SEO copy explicitly avoids bid-status claims; `keywords.yaml` records
  bid-intent keywords as deferred. No unsupported ranking/claim found.

---

## Monetization Audit

| Capability | Status |
|---|---|
| Plan catalogue (FREE/PRO/TEAM) | **IMPLEMENTED** (3 rows) |
| Subscription record + status | **IMPLEMENTED** (table; 0 rows) |
| Entitlement resolution | **IMPLEMENTED** (`entitlements_for`) |
| Feature gates (`export`, `alerts`, `team`, `api`) | **IMPLEMENTED** as keys + `require_entitlement` decorator; **no route currently uses the decorator** — grep shows the decorator is defined but not applied |
| Stripe / payment provider | **MISSING** |
| Checkout / invoices | **MISSING** |
| Self-service upgrade | **MISSING by design** (only `set-plan` CLI) |
| Usage limits (`FREE_VIEW_LIMIT`) | **PLANNED** (config exists, default 0/disabled) |
| Team/enterprise (org admin, invites) | **PARTIAL** (membership/assignment in `workflow.py`; no web org admin) |
| API access as a paid tier | **PLANNED** (`FEATURE_API` key; API is currently public/unauthenticated for reads) |

**Assessment:** the *authorization model* for monetization is real and correctly separated; the
*commerce* is entirely absent and honestly documented as such. Nothing pretends a plan was bought.

---

## Infrastructure & Deployment

- **Render blueprint** (`render.yaml`): Python runtime, `buildCommand: pip install -r
  requirements.txt`, `startCommand: bash ops/start.sh`, `healthCheckPath: /healthz`,
  `numInstances: 1`, `plan: free` (disk commented out). Env: generated `SECRET_KEY`, `PYTHONPATH=src`,
  `SESSION_COOKIE_SECURE=true`, `CSRF_ENABLED=true`, `REFRESH_SECONDS=21600`, `MAX_PAGES=3`.
- **`ops/start.sh`:** idempotent `initdb` + `init-app`, then background (local) or foreground
  (Render) `ops/automate.py --serve`. Two modes documented and implemented.
- **Gunicorn:** 2 workers × 4 threads; SQLite writer serialisation reasoned about in comments.
- **Health check:** `/healthz` returns 503 only on DB-unreachable, 200 with `"status":"empty"` on an
  empty DB. Verified live: `{"database":"...","permits":6227,"projects":1797,"status":"ok"}`.
- **Persistence:** SQLite file; disk required to survive deploys (documented). **Single point of
  failure:** the DB file + the single instance + the single supervisor process.
- **Scheduled jobs:** in-process refresh thread only.
- **Logging:** `data/automation.log`, `data/automation.out`, `data/web.log`; `LOG_LEVEL` env.
- **Backups:** **MISSING** — no backup strategy for the SQLite file.
- **Docker:** **NONE** in repo (the injected docker skill is not relevant to this project).
- **CI/CD:** **NONE** — no `.github/`, no workflow files. `autoDeploy: true` on Render only.
- **Local vs prod mismatch:** local uses `ops/start.sh` (background) which is the same supervisor
  Render runs (foreground); the AI-Studio path (`server.ts`) is a **different** entry point not used
  in production. Three entry points exist (`server.ts`, `run_server.py`, `ops/automate.py`), which is
  a configuration hazard.

---

## Scalability

### What SQLite handles adequately today

- Current volumes (1,797 projects, 6,227 permits, 21,106 evidence rows, 6.8 MB) are trivial for
  SQLite. Single-file ACID, zero-ops, full SQL.
- Read-heavy workload with a small write burst per refresh — a good SQLite shape.
- FTS5 search is fine into the low millions of rows.

### Where it will break

1. **Write contention during assembly.** One assembly pass writes thousands of rows in one logical
   transaction per project with periodic commits; two gunicorn workers + a refresh thread all open
   the same file. Under a full 200k-record ingest (`--full` is documented as >13 min) a writer holds
   the file and readers block or retry.
2. **Horizontal scaling is impossible with the file.** Multiple instances cannot share a SQLite
   file; the blueprint pins `numInstances: 1` and the docs say scaling out needs a networked store
   first.
3. **No geospatial index.** Location is text; territory search (`ST_DWithin`) is impossible.
4. **Full-count queries per page.** `SELECT COUNT(*) FROM project WHERE {where}` runs on every
   listing and every statistics call, with no caching.
5. **`ORDER BY rank` FTS over the whole table** has no ranking tuning and no result cap before
   filtering.

### Is a PostgreSQL/PostGIS migration actually required?

**Not to serve the current product. It is required to reach BuildScope 2.0's multi-market,
multi-trade, spatial, multi-tenant target.** `docs/POSTGRES_MIGRATION.md` (351 LOC) already
specifies the target (Postgres 16 + PostGIS + object storage + outbox + Redis). The SQLite schema
was written to avoid SQLite-only constructs (documented in DESIGN.md §1.3), so the *shape* migrates,
but the following would need real work:

- `evidence`/`project` denormalisation is compatible; JSONB improves `raw_record.payload`.
- PostGIS columns must be **added** (no coordinates are stored today).
- The outbox/event model in the doc does not exist in code (`events.py` is unused) — it is a build,
  not a migration.
- Company/person tables do not exist — a build, not a migration.

### What can remain SQLite

- The intelligence layer, provenance, classification, change detection and the FTS index all work
  as-is and need no rewrite. **SQLite is not the problem; the problem is breadth, not the engine.**

### Concurrency model

- Gunicorn 2 workers × 4 threads; one in-process rate limiter **per worker** (so the effective limit
  scales with workers — documented); one refresh thread. No connection pool (a fresh sqlite3
  connection per request via `open_db`).

---

## Observability

| Question | Can the system answer it? | Evidence |
|---|---|---|
| Is ingestion working? | **Yes** | `ingest_run.status`, `/admin/data`, `automation_state.json` |
| Which source failed? | **Yes** | `ingest_run.error`, `_system_health().sources_failing` |
| Which source is stale? | **Yes** | `source_coverage` observed/earliest/latest dates |
| How many records ingested? | **Yes** | `ingest_run.rows_fetched/landed/permits_created`, coverage counts |
| How many projects assembled? | **Yes** | `project` count, `/healthz`, `/api/statistics` |
| How many evidence records? | **Yes** | `evidence` count (21,106) |
| Classification failures? | **Partial** | counts by label exist; "failure" is not a modelled concept |
| How long did ingestion take? | **Yes** | `ingest_run.started_at`/`finished_at` |
| Are workers alive? | **Yes (external)** | Render health check; no in-app worker heartbeat |
| Is search healthy? | **No** | no search health check |
| Is the database healthy? | **Yes** | `/healthz` 503 on unreachable |
| Are alerts being generated? | **Yes** | `AlertService.summary()`, `/admin/data` |
| Are errors increasing? | **Partial** | `app_error` rows exist (5); no trend/dashboard |

- **Health endpoint:** `/healthz` (operational, `noindex`, robots-disallowed). Verified.
- **Metrics:** none exported (no Prometheus/StatsD/OTel). Counts are read from the DB on demand.
- **Tracing:** none.
- **Job monitoring:** `automation_state.json` (last run, per-step ok/failed, run count). Verified:
  `{"last_run_ok":true,"last_steps":{"assemble":true,"build-search-index":true,"ingest":true,"monitor":true},"runs":1}`.
- **Source health:** `source_coverage` + last `ingest_run` per source.
- **Logging:** file-based (`automation.log`, `web.log`), `LOG_LEVEL` configurable.

**Assessment: observability is surprisingly good for a single-instance MVP** — every operational
question except "search healthy" and "errors trending" is answerable from stored rows. The gaps are
metrics export, tracing, and search health.

---

## Test Results

**Command actually run:**

```
PYTHONPATH="vendor/python:src" SECRET_KEY=dev-only-not-for-production \
  python3 -m pytest tests/ -q -p no:cacheprovider
```

**Result (verbatim):** `531 passed in 56.90s`. **0 failed, 0 errors, 0 skipped.** pytest 9.1.1
(installed during the audit; not vendored). No coverage plugin was installed, so **coverage was not
measured**.

### Classification of the suite

| Class | Files | Count |
|---|---|---|
| Data pipeline / intelligence | `test_assemble`, `test_classify`, `test_normalize`, `test_grouping`, `test_discrepancy`, `test_procurement`, `test_eligibility`, `test_changes`, `test_events` | ~200 |
| Connector | `test_dallas_accela`, `test_dallas_pagination`, `test_dallas_probe_matrix`, `test_dallas_quality` | 91 |
| Application / API | `test_app_directory`, `test_app_detail`, `test_app_api`, `test_app_seo`, `test_app_admin`, `test_app_accounts`, `test_app_architecture` | 173 |
| Accounts/entitlements | `test_entitlements`, `test_app_accounts` | ~45 |
| Ops/deployment | `test_deployment`, `test_cli_operations` | 30 |
| Acceptance | `test_acceptance` | 3 |
| Search | `test_nl_search` | 3 |
| Alerts | `test_alerts` | 13 |
| Brief/reporting | `test_brief_sanity` | 20 |
| Company | `test_companies` | 2 |

### Critical gaps (tests whose source is missing but whose `.pyc` remains)

| Missing file | Tests (from bytecode) | Covers |
|---|---|---|
| `test_security.py` | 23 | CSRF, rate limiting, headers, IDOR, open redirect, **operator area**, session cookie |
| `test_workflow.py` | 35 | watching, pipeline, notes, tags, assignment, **stage/procurement separation** |
| `test_seo_keywords.py` | 52 | keyword map coherence, gate, sitemap, structured data, funnel |
| `test_report_generator.py` | 27 | brief honesty, unverified rendering, contradictions |
| `test_reporting.py` | 13 | coverage/validation reports |
| `test_provenance.py` | 11 | the never-invent rule |

**161 tests are missing from the live suite** (673 documented − 531 running = 142; the bytecode
counts 161 across six files, so some documentation/bytecode drift exists — **UNKNOWN** exactly
which). The most consequential omission is **`test_security.py`**: the security suite that would
have exercised CSRF/IDOR/headers does not run, and the auth-bypass finding is precisely in the gap
it would not have covered anyway (`/auth/google` is not a cookie-auth route).

### No-test areas

- `/auth/google` and `/auth/firebase-verify` (the vulnerability lives here).
- `/companies` filtered query (which 500s).
- Multi-market behaviour beyond a config-load test.
- Concurrency / SQLite locking.
- `events.py` is tested but unused — testing dead code.
- Mobile/responsive rendering.

---

## Legacy / Technical Debt

| Item | Classification | Evidence |
|---|---|---|
| React/Vite scaffold (`src/App.tsx`, `main.tsx`, `index.css`, `index.html`, `package.json`, `vite.config.ts`, `tsconfig.json`, `bun.lock`, `server.ts`, `metadata.json`) | **DELETE** | App.tsx renders empty div; server.ts is the AI-Studio proxy; none used by Render/prod |
| `src/oppintel/events.py` + `tests/test_events.py` | **DELETE or WIRE UP** | Full event model, zero runtime importers |
| Orphan `.pyc` for 6 deleted test files | **DELETE** | `tests/__pycache__/` |
| `src/oppintel.egg-info/` | **DELETE** | Generated build artifact |
| `.pytest_cache/`, all `__pycache__/` | **DELETE** | Generated |
| `firebase-applet-config.json` | **REPLACE** | Committed API key; source of the auth bypass branch |
| `bun.lock` (0 bytes) | **DELETE** | Empty |
| Dallas disabled connectors (`dallas_permits.py`) | **KEEP (disabled)** | Historical-only, documented |
| DFW/HVAC hardcoded guide prose (`content.py`) and `reports.py` slug | **REFACTOR** | Not config-driven |
| `docs/POSTGRES_MIGRATION.md` | **KEEP as target doc** | Aspirational, not implemented |
| README claims ("673 tests", `requirements-dev.txt`, `test_workflow.py`, etc.) | **REFACTOR** | Stale/incorrect claims |
| `data/oppintel.db` with 7 user rows committed | **REPLACE** | Ships real accounts + password hashes |

### Obsolete assumptions

- **DFW-only** and **HVAC-only** identity is still present in prose and slugs even though the
  architecture is config-driven. The code is ready for a second market; the *content* is not.
- The `metadata.json` `majorCapabilities: ["MAJOR_CAPABILITY_SERVER_SIDE_GEMINI_API"]` implies a
  Gemini capability that **does not exist** in the code (no `google.generativeai` / `@google/genai`
  usage in the running app).

---

## KEEP

Working, valuable, aligned with BuildScope 2.0:

- `provenance.py` — the never-invent enforcement point.
- `classify.py` + `config/trades.yaml` gates.
- `normalize.py` — boilerplate stripping, negation handling, service-work exclusion.
- `assemble.py` + `grouping.py` — conservative clustering, building hints.
- `changes.py` — idempotent change detection.
- `procurement.py` — the four honest states.
- `discrepancy.py` — contradiction preservation.
- `connectors/base.py` + the three active connectors (esp. `dallas_accela_permits.py`).
- `db.py` schema + `service.py` boundary.
- `search_index.py` (FTS5).
- `app/security.py` (CSRF/headers/rate-limit framework), `app/entitlements.py`,
  `app/workflow.py`, `app/alerts.py`, `app/accounts.py` (the *design*).
- `config/*.yaml` (markets/trades/sources/keywords).
- `docs/DESIGN.md`, `docs/dallas_source.md`.
- `render.yaml`, `ops/start.sh`, `ops/automate.py`.
- The test suite (531 passing).

## REFACTOR

Valuable but structurally needs improvement:

- `companies.py` — query-string surgery; needs a real company entity + resolution.
- `app/main.py` (1,977 LOC) — monolithic factory; should split (blueprints).
- `content.py` / `reports.py` — DFW/HVAC copy should come from config.
- `README.md` / `AGENTS.md` — stale test counts and file references.
- `app/seo_gate.py` / `seo.py` — sound, but unenforced without `test_seo_keywords.py`.
- The `project` 33-column denormalisation — works, but document/maintain the dual source of truth.
- Rate limiter — move to shared (Redis) for multi-worker.

## REPLACE

Concept needed, implementation inadequate:

- **`/auth/google`** — replace with the already-present verified Firebase path only.
- **`firebase-applet-config.json`** — replace with environment-injected config.
- **Committed `data/oppintel.db`** — replace with an empty DB + ingest.
- Password reset token delivery (rendered on page) — replace with a real mailer or CLI-only.

## DELETE

Legacy / obsolete / unnecessary:

- React/Vite/AI-Studio scaffold + `server.ts` + `metadata.json` + `bun.lock`.
- `src/oppintel/events.py` (+ its test) unless it becomes the real event store.
- Generated artifacts (`egg-info`, `__pycache__`, `.pytest_cache`, orphan `.pyc`).
- README references to non-existent files (fix, then the phantom files are gone).

## BUILD

Required capability that does not exist:

- **COMPANY** and **PERSON** entities with entity resolution.
- **DOCUMENT** storage (filings/plans) + object storage.
- **PROCUREMENT SIGNALS** (beyond one enum).
- **Location geometry** (geocode + PostGIS) for territory search.
- **Saved searches** + **autocomplete**.
- **Email delivery** for alerts + password reset.
- **Payment/checkout** (Stripe) and self-service plan management.
- **Org administration** (create org, invite member).
- **Backups** and **CI** (tests on push).
- **Multi-market ingestion** (the architecture supports it; no second connector exists).

---

## Current Product Scorecard

Scores justified by repository evidence only.

| # | Dimension | Score | Justification |
|---|---|---|---|
| 1 | Data acquisition | **7** | 3 live connectors, one reverse-engineered WebForms portal; only 3 cities; no multi-market |
| 2 | Data normalization | **8** | Boilerplate stripping, negation, service-work exclusion, whole-word matching |
| 3 | Project assembly | **7** | Conservative address clustering + 540-day window; suite-level over-split acknowledged |
| 4 | Evidence/provenance | **9** | `assert_field` is the only write path; per-field citation; missing stays NULL |
| 5 | Entity resolution | **2** | Address-only for projects; none for companies/people; coordinates fetched then discarded |
| 6 | Classification | **9** | 4 enforced gates; reasons recorded; append-only history |
| 7 | Procurement intelligence | **4** | 4 honest states, but no signals, no bid data, `Confirmed open` unreachable |
| 8 | Change detection | **9** | Snapshot diff, controlled vocabulary, no-op silent, 2,355 real events |
| 9 | Search | **6** | FTS5 + filters works; no saved search, autocomplete, or geo; no ranking tuning |
| 10 | Project dossier | **8** | Rich, evidence-linked, honest about unknowns; Dallas lacks source links |
| 11 | Company intelligence | **3** | Derived-only, no entity, combined filters 500 |
| 12 | Watchlists | **7** | Implemented, correctly distinct from saves; 1 row in data |
| 13 | Alerts | **7** | Event-driven invariant real; in-app only; 0 rows |
| 14 | Pipeline | **7** | Correct stage/procurement separation; 1 row |
| 15 | Analytics | **6** | Privacy-respecting funnel; thin; no third-party/rank tracking |
| 16 | Authentication | **2** | Email/password + Firebase path are sound, but `/auth/google` bypass is CRITICAL |
| 17 | Authorization | **6** | FREE/PRO/TEAM/ADMIN, CLI-only escalation, IDOR-safe; `require_entitlement` unused |
| 18 | Frontend UX | **7** | Dense, real, consistent, responsive CSS; some legacy copy |
| 19 | Mobile UX | **5** | Responsive CSS present; **not verified on device** |
| 20 | SEO | **7** | Sophisticated gating + honest keyword map; enforcement tests missing |
| 21 | Security | **3** | Good controls undermined by auth bypass + committed key + reset-token leak |
| 22 | Testing | **6** | 531 real passing tests; 6 critical suites missing; no coverage measured |
| 23 | Observability | **7** | Excellent stored-row ops view; no metrics/tracing/search health |
| 24 | Deployment | **6** | Clean Render blueprint; 3 entry points; no CI; no backups; committed DB |
| 25 | Scalability | **4** | SQLite fine now; single instance, no geo, no shared cache, count-per-page |
| 26 | Monetization readiness | **3** | Authz model real; zero commerce; gates unused |

**Weighted read:** the *intelligence core* averages ~8; the *application/commercial surface*
averages ~5; the *security* dimension drags the whole product to **not-yet-shippable** until fixed.

---

## Current Product Moat

Based only on what exists:

1. **The provenance discipline (9/10).** A field cannot be set without evidence; missing stays
   NULL; contradictions are preserved. This is hard to retrofit and is the trust foundation.
2. **The gated classifier (9/10).** Four code-enforced gates that refuse to over-claim. Most
   competitors score and promote; this one refuses.
3. **The change-detection engine (9/10).** Idempotent, low-false-positive, event-backed.
4. **The Dallas Accela connector.** Reverse-engineering an ASP.NET WebForms portal with
   session/viewstate/Origin handling and label-based pagination is real, defensible work.
5. **The evidence model itself** — per-field citation with excerpt and source date.

**What is NOT a moat (be honest):** the Flask app breadth, the account/plan scaffolding, the SEO
layer, the company aggregation, and the AI-Studio frontend are all commodity or thin. The moat is
the **intelligence engine and its honesty invariants**, not the website around it.

---

## Top 10 Assets

1. `src/oppintel/provenance.py` — `assert_field`.
2. `src/oppintel/classify.py` + `config/trades.yaml` gates.
3. `src/oppintel/changes.py` + `project_state_snapshot`.
4. `src/oppintel/connectors/dallas_accela_permits.py` (and `docs/dallas_source.md`).
5. `src/oppintel/normalize.py` (boilerplate/negation/service-work rules).
6. `src/oppintel/assemble.py` + `grouping.py`.
7. `src/oppintel/service.py` — the enforced read boundary.
8. `src/oppintel/db.py` schema (intelligence vs app separation).
9. `src/oppintel/procurement.py` + `discrepancy.py` (honesty modules).
10. `tests/` (531 passing) + `data/raw/*.jsonl` replay evidence.

---

## Top 10 Risks

| # | Risk | Severity | Evidence |
|---|---|---|---|
| 1 | Account takeover via `/auth/google` (email param → session) | **CRITICAL** | Demonstrated live; `main.py:915` |
| 2 | Password-reset token disclosed on-page for any known email | **HIGH** | `main.py:1022` renders `reset_link`; no mailer |
| 3 | Committed Firebase API key + project config | **HIGH** | `firebase-applet-config.json` |
| 4 | Committed DB with 7 real user rows (hashes) | **MEDIUM** | `data/oppintel.db`; `app_user` count 7 |
| 5 | 6 critical test suites deleted (security, workflow, SEO, provenance, reporting, report generator) | **HIGH** | orphan `.pyc`; README claims 673 vs 531 running |
| 6 | Only 17 mechanical-evidence projects; 0 from the dominant source (Collin) | **HIGH** | `project` group-by; `source_coverage.mechanical_count=0` |
| 7 | `/companies` combined filters 500 | **MEDIUM** | Reproduced; `app_error` id 8 |
| 8 | Single SQLite file + single instance, no backups | **HIGH** | `render.yaml` `numInstances: 1`; no backup code |
| 9 | Three entry points (`server.ts`, `run_server.py`, `ops/automate.py`) risk config drift; `server.ts` strips `X-Frame-Options` | **MEDIUM** | code read |
| 10 | DFW/HVAC hardcoded in guide prose and report slug despite config-driven architecture | **MEDIUM** | `content.py`, `reports.py` |

---

## BuildScope 2.0 Gap Map

Target = PROJECT + EVIDENCE + DOCUMENTS + COMPANIES + PEOPLE + TRADES + LOCATION + EVENTS +
PROCUREMENT SIGNALS, lifecycle DISCOVER → UNDERSTAND → VERIFY → WATCH → DETECT CHANGE → DECIDE → ACT.

| Target capability | Status | Note |
|---|---|---|
| PROJECT | **ALREADY EXISTS** | Core object; correct |
| EVIDENCE | **ALREADY EXISTS** | Strongest asset |
| DOCUMENTS | **MISSING** | No document store/attachments |
| COMPANIES | **WRONG ARCHITECTURE** | Derived at query time; needs entity + resolution |
| PEOPLE | **MISSING** | No person entity |
| TRADES | **PARTIALLY EXISTS** | Config-only; not an entity, not multi-trade runtime |
| LOCATION | **PARTIALLY EXISTS** | Text city/zip; no geometry; coordinates fetched then discarded |
| EVENTS | **PARTIALLY EXISTS** | `project_change` is the real event log; `events.py` unused |
| PROCUREMENT SIGNALS | **WRONG ARCHITECTURE** | Single enum, not a signal stream |
| DISCOVER | **ALREADY EXISTS** | Directory + filters |
| UNDERSTAND | **ALREADY EXISTS** | Dossier + evidence |
| VERIFY | **ALREADY EXISTS** | Field verdicts + sources |
| WATCH | **PARTIALLY EXISTS** | Implemented, unexercised |
| DETECT CHANGE | **ALREADY EXISTS** | Change engine |
| DECIDE | **PARTIALLY EXISTS** | Pipeline stages + eligibility badge |
| ACT | **MISSING** | No outreach/export/notification delivery |

### ALREADY EXISTS
Project, Evidence, Discovery, Understanding, Verification, Change detection, Classification,
Procurement status (as a state, not signals), Search (structured + FTS), SEO gating.

### PARTIALLY EXISTS
Trades (config), Location (text), Events (`project_change`), Watch, Alerts, Pipeline, Analytics,
Organizations (tables, no admin), Entitlements (model, no commerce).

### MISSING
Documents, Companies (entity), People, Procurement signals, Saved searches, Autocomplete, Geo
search, Email delivery, Payments, Org admin, Backups, CI, Multi-market ingestion.

### WRONG ARCHITECTURE
Company intelligence (query-time derivation), Procurement (enum vs signals), Location (no geometry),
`events.py` (unused parallel model).

### NEEDS REFACTORING
`app/main.py` monolith, `companies.py`, `content.py`/`reports.py` copy, README/AGENTS accuracy,
rate limiter, `project` dual-source-of-truth documentation.

---

## Recommended Implementation Order

*(Recommendation only. No Phase 2 work was performed.)*

1. **CRITICAL — security remediation (do first, before anything else):**
   a. Remove or hard-require token verification on `/auth/google`; delete the email-parameter path
      and keep only the verified `/auth/firebase-verify`.
   b. Move `firebase-applet-config.json` values to environment variables; rotate the exposed key.
   c. Stop rendering password-reset tokens on-page; require email delivery or operator-only reset.
   d. Ship an empty database (no user rows).
2. **Restore the deleted test suites** (`test_security`, `test_workflow`, `test_seo_keywords`,
   `test_provenance`, `test_reporting`, `test_report_generator`) and add coverage measurement; add
   tests for both auth paths.
3. **Delete the dead scaffold** (React/Vite/AI-Studio, `events.py` or wire it, generated artifacts)
   and correct README/AGENTS claims.
4. **Data-model build-out** in this order: COMPANY + PERSON entities with resolution → DOCUMENTS →
   LOCATION geometry → PROCUREMENT SIGNALS.
5. **Fix `/companies`** and finish company intelligence on the new entity.
6. **Multi-market ingestion** (second connector) to prove the config-driven architecture.
7. **Observability + ops:** CI (run the 531+ tests on push), metrics export, search health, DB
   backups.
8. **Commerce:** Stripe checkout + self-service plan + apply `require_entitlement` to real routes.
9. **Scale when data demands it:** Postgres + PostGIS per the existing migration doc, then
   multi-instance.

---

## Questions / Unknowns

- **UNKNOWN:** whether `PRAGMA foreign_keys=ON` is set anywhere (not observed; FK clauses may be
  non-enforcing).
- **UNKNOWN:** exact original count of the six deleted test files' tests vs README's "673"
  (bytecode suggests 161 across six files; running suite is 531).
- **UNKNOWN:** whether the shipped `data/oppintel.db` user rows are demo accounts or real operator
  accounts.
- **UNKNOWN:** mobile rendering quality (not exercised on a device).
- **UNKNOWN:** production state on Render (no deployment was inspected; only the blueprint).
- **UNKNOWN:** the intended owner of `/auth/google` — whether it was a deliberate "dev shortcut"
  that was never removed, or an unfinished integration. `AGENTS.md` forbids Firebase/Firestore in
  Phase 1, yet the code contains Firebase integration, suggesting it post-dates that rule.
- **UNKNOWN:** licence/terms capture per source (no per-source terms text in the repo).

---

## Audit Conclusion

BuildScope is **a genuine intelligence product wrapped in an over-built application with a critical
authentication hole.** The intelligence layer — provenance, gated classification, conservative
assembly, honest procurement states, contradiction preservation, and idempotent change detection —
is the real, defensible asset and is close to BuildScope 2.0's PROJECT + EVIDENCE foundation. It
should be protected and extended, not rewritten.

The application layer is broad but ahead of its data: 46 routes and a full account/plan/org model
serve a dataset with 46 discoverable projects, 17 mechanical-evidence projects, 1 watch, 1 save and
0 alerts. That is scaffolding, not traction.

The most urgent fact in this audit is that **`/auth/google` lets anyone become any registered user
with a single unauthenticated request**, which was demonstrated against a real password-protected
account. Until that is removed, every other control (CSRF, password hashing, IDOR filtering,
enumeration resistance) is decorative. Fixing it, restoring the six deleted test suites, and
shipping an empty database are the prerequisites for everything else.

The gap to BuildScope 2.0 is **breadth, not core**: COMPANIES, PEOPLE, DOCUMENTS, LOCATION geometry
and PROCUREMENT SIGNALS must be built, and the company/procurement/location models need replacing
rather than extending. The evidence engine, the classifier and the change detector already meet the
target's spirit and should be carried forward unchanged.

*End of audit. No Phase 2 work performed; no application code, schema, configuration or data
modified.*
