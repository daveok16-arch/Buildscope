# BuildScope 2.0 — Current Repository Audit & Next-Step Recommendation

**Type:** READ-ONLY audit and planning exercise. No application code, schema, data, configuration,
or template was modified. Nothing was deleted or renamed.
**Date:** 2026-10-16. **Auditor:** OpenHands agent (AI), read-only.
**Method:** static code reading of the working tree at the audited commit, a locally rebuilt
throwaway SQLite database, a real bounded ingestion of one public source, live HTTP probes of a
local gunicorn instance, and two full runs of the test suite. Every number below is copied from a
command output; commands are in §11. Anything not verifiable is marked **UNVERIFIED** with the
reason. No environment-variable value or secret is printed.

---

## 1. Executive summary

BuildScope is a genuine, well-layered evidence-first intelligence product, not a prototype. The
intelligence core (ingest → normalize → assemble → provenance → classify → procurement → changes →
events → alerts) is implemented, tested and works end-to-end on live public data: a bounded
one-source ingest produced 965 raw records → 940 permits → 39 linked → 22 assembled projects, and
the full suite is **1206 passed, 1 skipped** on a clean tree.

The product's discipline is its strongest asset and it is enforced by tests, not convention: a
missing value stays `NULL` and renders "Not verified" (never the string in JSON), an alert cannot
exist without an underlying event, change detection records only real differences, and no web route
grants privilege. The layered architecture is real and machine-checked
(`tests/test_app_architecture.py`).

The gaps are **not** in the engine. They are at the product's *edges*, and they cluster into three
themes:

1. **Second verticals are not actually reachable.** The architecture is config-driven and the
   trade taxonomy already defines nine trades, but the product ships one active market (DFW) and one
   active trade (commercial HVAC). More importantly, the **company-intelligence layer is empty**:
   100% of `project_party` rows are `owner`; the pipeline projects a contractor only when a source
   publishes one, and none of the enabled sources do. For a mechanical contractor the
   general contractor on a job is often the buyer, so this is a real product gap, not a cosmetic one.
2. **The runtime baseline is environment-sensitive.** The declared "no model configured" test path
   is a live-API call whenever an analyst provider key is present in the environment, which makes
   one test non-deterministic and makes the suite take ~30 s longer. This is a test-isolation
   defect with a security dimension (a test process reaches the network with a real credential).
3. **"Professional SaaS" surfaces are absent by design.** No CI, no in-repo error monitoring, no
   pricing route, no export, and the operator-only AI analyst is wired and works.

**Recommended immediate next phase: Phase 9 — Company intelligence (contractor/GC resolution and
exposure).** It is the highest-value capability that (a) the evidence engine already has the
machinery for, (b) is verifiably empty today, and (c) directly serves the target user. A small,
test-isolation fix to the analyst suite should be folded in as a prerequisite.

---

## 2. Verified repository and commit state

| Fact | Value | Evidence |
|---|---|---|
| Repository root | `/workspace/project/Buildscope` | `pwd` |
| Branch | `main` (tracks `origin/main`) | `git branch -a` |
| HEAD commit | `4344ace53ddf596f1e5853df2cbd4cc77829a357` (short `4344ace`) | `git rev-parse HEAD` |
| Clone type | **shallow** (`is-shallow-repository` → `true`), `git rev-list --count HEAD` → `1` | `git rev-parse --is-shallow-repository` |
| Commit date / subject | `2026-10-15 23:33:01 +0000` — "feat(markets): chart projects by city, showing where the evidence is thin" | `git log -1 --format` |
| Working tree | **clean**, `git status --porcelain` → empty (before and after the audit) | `git status --porcelain=v1` |
| Uncommitted changes | **none** to preserve | `git status --porcelain=v1` |
| Tracked files | 638 | `git ls-files \| wc -l` |
| Python | 3.13.15 | `python -V` |
| Declared deps | `requests`, `PyYAML`, `Flask`, `gunicorn` (four) | `requirements.txt` |

**History caveat.** The clone is grafted/shallow, so "recent commits" beyond HEAD cannot be
inspected here and historical blame/merge-base is unavailable. The commit message body and the
`audit/` records are the available evidence for what the latest work did. `AGENTS.md` and the
`docs/engineering/README.md` describe `docs/engineering/*` as **point-in-time historical records,
not current documentation** — so code was read as the source of truth, not those docs.

### Documentation read

`README.md`; `AGENTS.md` (34 KB, the operating manual); `docs/{README,architecture,testing,
security,deployment,data-sources,evidence-and-classification,api,operations,getting-started}.md`;
`docs/engineering/{README,DESIGN,PHASE_8_INTELLIGENCE_AUDIT,BUILDSCOPE_PLATFORM_AUDIT,
POSTGRES_MIGRATION,PHASE_1A_AUTH_REMEDIATION,BUILD_SCOPE_REPOSITORY_AUDIT,dallas_source}.md`;
`audit/{BUILDSCOPE_AUDIT_2026-10-09,BUILDSCOPE_AUDIT,WP3_REPORT,RELEASE_NOTES_PORTABLE,
AI_ANALYST_AUDIT,DEPLOY_RUNBOOK}.md`.

### Legacy prototype artifacts — present but **not used** by the served product

| Artifact | State | Served? |
|---|---|---|
| `src/App.tsx` | renders an empty `<div>` | No |
| `src/main.tsx`, `src/index.css` | React entry, imports the empty App | No |
| `index.html` | Vite entry mounting `#root` | No |
| `package.json` | React/Vite/Express/Tailwind/sql.js deps | No (server-rendered Jinja is the product) |
| `server.ts` | Express supervisor | No (gunicorn + `ops/automate.py` is the product) |
| `vite.config.ts`, `tsconfig.json`, `bun.lock` (0 bytes) | Vite/TS config | No |
| `metadata.json` | AI Studio applet metadata | No |
| `firebase-applet-config.example.json` | shape only (no real key) | No |

These are AI Studio / Bolt-style prototype residue. They are **unused**: the running app is
`oppintel.app.wsgi:application`, templates are Jinja, and `Dockerfile` copies `server.ts` and
`package.json` but never runs them. `docs/architecture.md` and `audit/PHASE_8_INTELLIGENCE_AUDIT.md`
state the same. **Disposition: DELETE candidate, separately approved** (see §8) — safe to remove,
but removal is out of scope for this audit.

---

## 3. Current architecture and data-flow map

### 3.1 Runtime shape

```
ops/start.sh  ──►  ops/automate.py --serve  ──►  gunicorn (WSGI: oppintel.app.wsgi:application)
     │                     │
     │                     └─ refresh thread every REFRESH_SECONDS (default 21600):
     │                        ingest → assemble → build-search-index → monitor
     │                        guarded by a cross-process single-writer flock (locks.py)
     └─ initdb + init-app (idempotent) on every boot
```

Verified live: `gunicorn --workers 1 --bind 127.0.0.1:12000 oppintel.app.wsgi:application` booted
cleanly, `/healthz` returned HTTP 200 with `"status":"empty"` on an empty database, and every
public route returned 200 while every gated route returned 302 (see §4.3).

### 3.2 Layers (verified by reading, not by trusting docs)

| Layer | Modules | Boundary |
|---|---|---|
| Connectors | `connectors/{fort_worth_permits,collin_cad_permits,dallas_accela_permits,dallas_permits,base}.py` | Fetch + per-source normalize |
| Pipeline | `pipeline.py` (`FETCH→LAND→NORMALIZE→PERSIST→ASSEMBLE→CLASSIFY→DERIVE`) | Orchestration |
| Intelligence | `normalize.py`, `assemble.py`, `grouping.py`, `provenance.py`, `classify.py`, `eligibility.py`, `procurement.py`, `discrepancy.py`, `changes.py`, `quality.py`, `intelligence.py`, `identity.py`, `events.py`, `intel_events.py`, `coverage.py`, `dates.py` | Decides meaning; never rendered |
| Storage | `db.py` (two schema strings: `SCHEMA`, `APP_SCHEMA`; `_migrate_app_tables`, `_ensure_*`) | Single SQLite file |
| Read boundary | `service.py` (`OpportunityService`), `companies.py` (`CompanyService`) | **The only** app-layer SQL over intelligence tables |
| Application | `app/main.py` (Flask routes, 2,728 lines), `app/api.py` (JSON blueprint `/api`) | Reads only |
| App services | `app/{accounts,workflow,alerts,entitlements,matching,security,seo,seo_gate,seo_report,stat_snapshot,content,reports,display,analytics_funnel,search_analytics,mailer,compression}.py` | Accounts/SEO/ops |
| Config | `config/{markets,trades,sources,keywords,search_vocabulary,trade_taxonomy}.yaml` via `config.py` | No hardcoded market/trade |

### 3.3 Routes (verified by grep + live probe)

- **HTML (`app/main.py`, 39 routes):** `/`, `/opportunities`, `/opportunities/<slug>`,
  `/opportunities/new`, `/companies`, `/companies/<slug>`, `/markets`, `/markets/<market>`,
  `/markets/<market>/<city>`, `/markets/<market>/<city>/<trade>`, `/trades`, `/trades/<trade>`,
  `/project-types`, `/project-types/<type>`, `/guides`, `/guides/<slug>`, `/how-it-works`,
  `/reports`, `/reports/<slug>`, `/trends`, `/changes`, `/analytics`, `/commercial-construction-leads`,
  `/dashboard`, `/watching`, `/watching/<id>`, `/my-pipeline`, `/pipeline/<id>`, `/alerts`,
  `/alerts/<id>/read`, `/alerts/read-all`, `/saved`, `/saved/<id>`, `/notes/<id>`, `/notes/<id>/delete`,
  `/tags/<id>`, `/preferences`, `/signin`, `/signup`, `/signout`, `/forgot-password`,
  `/reset-password/<token>`, `/auth/google`, `/auth/firebase-verify`, `/admin/data`, `/ai-analyst`,
  `/content/linkedin`, `/sitemap.xml`, `/robots.txt`, `/healthz`.
- **JSON (`app/api.py`, blueprint `api`, prefix `/api`):** `/api/opportunities`,
  `/api/opportunities/<slug>`, `/api/markets`, `/api/trades`, `/api/trends`, `/api/statistics`,
  `/api/me`, `/api/saved`, `/api/saved/<id>`, `/api/watching`, `/api/watching/<id>`,
  `/api/pipeline`, `/api/pipeline/<id>`, `/api/notes/<id>`, `/api/alerts`,
  `/api/alerts/<id>/read`, `/api/project/<id>/analyst` (ADMIN). There is no `/api/companies`
  endpoint — company reads are HTML-only (`/companies`).
- **No `/pricing` route.** Plans exist in the DB (`entitlements.py`: `FREE`/`PRO`/`TEAM` +
  `OPERATOR`) but are unreachable from the product.

### 3.4 Authentication, sessions, authorization

- `app/accounts.py` (`AccountService`): PBKDF2-SHA256 password hashing, signup, signin, reset
  tokens; `is_active` is set immediately at signup — **no email verification**.
- `app/security.py` (`install_security`): session-bound CSRF token (constant-time compare, required
  on POST/PUT/PATCH/DELETE), per-process sliding-window rate limiter, and hardening headers
  (`nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: same-origin`, `Permissions-Policy`,
  per-request-nonce CSP). Live headers confirmed in §6.
- `app/entitlements.py`: plan / subscription / entitlement kept separate; **no web route grants a
  plan** — only the CLI `set-plan`. `ADMIN` is CLI-only (`grant-admin`).
- Google/Firebase sign-in via `_load_firebase_config` (injected config, never committed).

### 3.5 Database schema and migrations

Verified by querying the live schema: **39 tables**, including FTS5 `project_search` (+ its shadow
tables) and the intelligence graph tables. Intelligence tables include `raw_record`, `permit`,
`project`, `project_permit`, `project_party`, `project_trade`, `evidence`, `evidence_history`,
`project_event`, `project_change`, `project_state_snapshot`, `entity`, `entity_name`,
`entity_project`, `document`, `project_location`, `project_classification`, `ingest_run`,
`source`, `source_coverage`, `data_quality_issue`. Application tables include `app_user`,
`organization`, `organization_member`, `plan`, `subscription`, `saved_opportunity`,
`watched_opportunity`, `pipeline_entry`, `opportunity_note`, `opportunity_tag`, `alert_event`,
`user_activity`, `user_preference`, `analytics_event`, `market_stat_snapshot`, `app_error`.

Migrations are idempotent `ALTER TABLE` guarded by `PRAGMA table_info`, run by
`_migrate_app_tables` after `APP_SCHEMA`; indexes over migrated columns are created in a
post-migration step (`_ensure_analytics_indexes`) — the "schema-order invariant" in `AGENTS.md` is
real and consistent with the code.

### 3.6 Ingestion, normalization, assembly, evidence

- Connectors order newest-first; `since_months: 24` is set on the three active sources
  (`fort_worth_permits`, `collin_cad_permits`, `dallas_accela_permits`); two Dallas sources are
  present but **disabled** (stale/too-old data, documented in `source_coverage.pagination_notes`).
- Verified live: `ingest --max-pages 1 --source fort_worth_permits` → **965 `raw_record`, 940
  `permit`**; `assemble` → **22 projects** (`MEDIUM 3`, `NEEDS_VERIFICATION 19`); `build-search-index`
  → 22 indexed + 22 slugs; `integrity` → `"healthy": true`.
- Provenance: every material field is set only through `provenance.py`; a field no source publishes
  stays `NULL`. Verified: **0 projects with zero evidence**, and the API returns `null` (not
  "Not verified") for absent fields.

### 3.7 Classification, eligibility, procurement

`classify.py` awards HIGH/MEDIUM/NEEDS_VERIFICATION with recorded reasons and four gates
(mechanical-evidence gate, scale floor, negation-aware normalization, residential exclusion);
`eligibility.py` holds the customer-brief criteria; `procurement.py` holds four states and **never**
claims a bid is open. Verified: procurement on real data was uniformly
`"Evidence found, status unclear"` — i.e. the product does not overclaim.

### 3.8 Search

`search_index.py` (FTS5 `project_search`, `quote_for_fts`, `expand_terms`, `explain_matches`) →
`service.OpportunityService._search_ids` → `list_opportunities`. Synonym/abbreviation expansion is
config-driven (`config/search_vocabulary.yaml`). Verified live: `q=restaurant` → 1 result,
`q=hvac` → 0, `q=roof` → 0, `q=zzzznothing` → 0 (consistent with the small bounded dataset and the
"no evidence ⇒ no claim" rules; `hvac` returns 0 because no Fort Worth page-1 record carries HVAC
scope). Match reasons are whole-word only by design (`test_explain_matches_whole_words_only`).

### 3.9 Change detection, events, alerts, workflow

`changes.py` diffs each project against its snapshot inside `assemble_and_classify`; only real
differences produce a `project_change`; `intel_events.py` writes idempotent digest-keyed
`project_event` rows; `app/alerts.py` turns a recorded change (or a first-match) into an in-app
alert — `AlertService.alerts_without_event()` must stay empty. `app/workflow.py` holds per-user
watching, pipeline stages, notes, tags, activity. Verified on real data: `project_event 142`,
`project_change 22` (all `new_project` first-observations — a fresh DB, so no genuine diffs yet).

### 3.10 Background jobs, caching, observability

`ops/automate.py` supervises gunicorn and runs the refresh on a thread; `locks.py` provides a
cross-process single-writer `flock` (job lock + re-entrant writer lock, `OPPINTEL_LOCK_HELD`
inheritance) so a refresh cannot overlap a manual seed. Headline stats are frozen per refresh in
`market_stat_snapshot` (`app/stat_snapshot.py`) — this is the remediation of the earlier "volatile
headline count" finding. Observability is limited: an `app_error` table surfaced at `/admin/data`;
no external monitoring.

### 3.11 Deployment and persistence

`render.yaml` (free preview, no disk, `autoDeploy: true`, one instance, `PREVIEW_MODE=1`);
`render.production.example.yaml` (paid + 1 GB disk); `Dockerfile` (non-root, `FOREGROUND=1`);
`ops/start.sh` (foreground/background modes, `BASE_URL` precedence, writable-dir checks);
`ops/backup.sh` + `oppintel backup` (keeps newest 3, same-disk warning). Sizing documented from
measured runs (24-month seed ≈149 MB DB; peak RSS ≈298 MB).

---

## 4. Test and runtime baseline

### 4.1 Environment setup required

The container shipped **bare**: `python -m pytest` → "No module named pytest"; `vendor/python/` was
absent (git-ignored); `data/` held only `.gitkeep` (no seeded DB). The four declared dependencies
plus `pytest` were installed from PyPI to establish the baseline. `vendor/python` is not committed
by design (`requirements.txt` is the install path), so this matches the documented host flow.

### 4.2 Test results

Command:

```bash
env -u SECRET_KEY -u BASE_URL -u OPPINTEL_DB -u OPPINTEL_DATA_DIR -u PORT \
  PYTHONPATH=src python -m pytest tests/ -q
```

| Run | Result |
|---|---|
| Run 1 (analyst key present) | **1206 passed, 1 skipped** in 144.98s |
| Run 2 (analyst key present) | **1 failed, 1205 passed, 1 skipped** in 174.78s |
| Failing test alone, key present | **1 failed** in 8.67s |
| Failing test alone, `Gemini_API_Token` unset | **1 passed** in 0.31s |

- The one **skip** is `tests/test_accessibility.py:29` — "Playwright is a developer-only tool for
  a11y tests" (environmental, not a defect).
- The one **failure** is `tests/test_ai_analyst_app.py::test_ai_analyst_page_renders_for_an_operator`.
  **Root cause (verified, not inferred):** the test asserts the page contains "No model is
  configured" or "UNAVAILABLE", which is true only when *no* analyst provider key is in the
  environment. This sandbox injects `Gemini_API_Token` (present, 53 chars), so `resolve_provider()`
  returns `gemini`, the view makes a **live network call**, and the rendered page contains neither
  string. Unsetting the variable makes the same test pass in 0.31 s. **The failure is
  environmental, not a regression in product code** — but it is a genuine test-isolation defect: the
  suite is non-deterministic and reaches the network (with a real credential) whenever a key is
  present. `docs/deployment.md` and `AGENTS.md` warn not to export `SECRET_KEY`/`BASE_URL` during
  pytest; the same class of warning applies to the analyst keys and is not enforced by a fixture.
- No other test is network-dependent: `tests/fixtures/` is empty and `conftest.py` says payloads are
  "copied from live responses captured during Phase 1 reconnaissance" (inline in the test modules).

### 4.3 Build, migration, startup, endpoints

| Check | Result |
|---|---|
| `oppintel.cli initdb` | OK — "Initialized database at …/data/oppintel.db" |
| `flask … init-app` | OK — "Application schema ready" |
| `gunicorn … oppintel.app.wsgi:application` | Booted, `Listening at: http://127.0.0.1:12000` |
| `/healthz` (empty DB) | 200, `"status":"empty"` (as designed — 503 only if the DB is unreachable) |
| All 34 probed routes | public → 200; gated (`/dashboard`, `/watching`, `/my-pipeline`, `/alerts`, `/saved`, `/preferences`, `/admin/data`, `/ai-analyst`, `/content/linkedin`) → 302 |
| `ingest → assemble → build-search-index → integrity` | OK; `integrity` → `"healthy": true` |
| `seo-report` | OK — 12 mapped pages, 45 keywords, 0 duplicate primary keywords, 3 deferred |
| `analyst-project 1 --json` | OK — `status: AVAILABLE`, `model: gemini-2.5-flash` (live call) |

### 4.4 The two investigated reports

- **`/companies` filtered queries.** **Remediated, verified.** `CompanyService.list_companies`
  (`companies.py:115`) builds each union branch's SQL and its bindings *together* (the docstring
  documents the old string-substitution bug that caused a binding-count error). Regression tests
  `tests/test_companies.py::test_each_single_filter_returns_a_subset_without_error`,
  `test_companies_route_with_filters_does_not_return_500`, `test_combined_filters_apply_together`,
  `test_role_filter_does_not_leak_other_roles` **pass**. Live: `/companies?role=general_contractor`
  and `/companies?q=test` both return 200.
- **Password-reset delivery.** **Implemented and safe; delivery is deliberately off by default.**
  `app/mailer.py` is the single delivery boundary with three backends (`console` default, `smtp`,
  `null`). The token is never rendered, logged or returned (`_send_console` withholds it even at
  debug). The `/forgot-password` route generates a token only for a real account and shows an
  identical neutral message either way (no enumeration oracle). Verified live end-to-end with the
  correct CSRF field (`_csrf_token`): POST → "reset instructions have been generated"; the console
  backend sent nothing and logged nothing sensitive. **The real gap is that no deployment
  configures `MAIL_BACKEND=smtp`** — `render.yaml` and `render.production.example.yaml` set neither
  `MAIL_BACKEND` nor `SMTP_*`, and `SMTP_*` appears in no deployment file. So in production a reset
  request is recorded and silently not delivered. This is a configuration gap, not a code defect,
  and `RELEASE_NOTES_PORTABLE.md` documents it honestly.

---

## 5. Intelligence capability matrix

Evidence = the module plus what was observed on the freshly built database (22 projects / 940 permits
from one Fort Worth page). A one-source, one-page dataset is small; conclusions about *volume* are
limited by it, but conclusions about *machinery* are not.

| Capability | State | Evidence | Notes |
|---|---|---|---|
| Stable project identity | **Implemented** | `project.project_key`, `project_slug`, `assemble.py`, `slugs.py`; 22 slugs created | `project_key` is the stable identity |
| Source-record retention | **Implemented** | `raw_record` retains payloads verbatim; 965 raw rows for 940 permits | Keyed by content hash (idempotent re-ingest) |
| Provenance / evidence per field | **Implemented** | `provenance.py`, `evidence`, `evidence_history`; 269 evidence rows; 0 projects without evidence | The strongest asset |
| Explicit unknowns | **Implemented** | API returns `null` for absent fields; HTML renders "Not verified" | Verified in the JSON contract |
| Duplicate detection / merging | **Partial** | `grouping.py` (sibling detection, duplicate statistics); earlier audit found 61 duplicate `(source, permit_number)` groups | Address-key clustering; no cross-source entity merge |
| Company identity resolution + aliases | **Implemented (engine), empty (data)** | `identity.py`, `entity`/`entity_name`/`entity_project`; 18 entities from 22 projects | Legal-form folding, `&`=`and`, no fuzzy matching by design |
| Project timelines / historical change | **Implemented** | `changes.py`, `project_change`, `project_state_snapshot`, `project_event` (142 rows) | Fresh DB ⇒ only first-observations, no real diffs yet |
| Trade / scope classification | **Implemented** | `config/trade_taxonomy.yaml` (9 trades), `config.classify_trade`; `project_trade` populated (10 rows: general_construction 6, concrete/electrical/roofing/other_specialty 1 each) | Permit *type* consulted before description |
| Procurement without bidding claims | **Implemented** | `procurement.py`; all 22 records `"Evidence found, status unclear"` | Never claims a bid is open |
| Search with meaningful filters | **Implemented** | `search_index.py`, `service.OpportunityFilters`, `nl_search.py`; live filters on `/opportunities` | Filters: city, type, classification, procurement, value, freshness, mechanical, sort, paging |
| Source freshness / coverage / health | **Implemented** | `coverage.py` (six states), `source_coverage`, `/healthz` `coverage` block; `seo-report` | Distinguishes configured vs enabled vs ingested vs current vs verified |
| Reproducible classification | **Implemented** | `classify.py` records reasons per point; `project.classification_reasons` | Config weights, not guesses |
| **Company/participant depth (GC, architect, developer)** | **Missing in practice** | `project_party` roles on real data: **`owner` only**; `general_contractor`/`architect`/`developer` all `NULL` | `assemble.py:176-188` projects a contractor **only if `permit.contractor` is set**; none of the three enabled sources publish one (`fort_worth` and `dallas_accela` set `contractor=None`; `collin_cad` sets `contractor=None`) |
| Cross-source contradiction surfacing | **Implemented** | `discrepancy.py`; `project.discrepancies`, `disputed_fields` | Preserved and flagged, not resolved |

**Verdict.** The intelligence *engine* is genuinely reliable and should be preserved. The one
capability that is implemented as machinery but **verifiably empty as data** is company
intelligence for roles other than owner — and that is exactly the information a commercial
mechanical contractor uses to decide whether to pursue a job.

---

## 6. Product and UX findings

Verified by reading templates/routes and by live probes of a locally seeded instance (22 projects).
Because the dataset is small, this section states *structure*, and marks visual claims UNVERIFIED.

**Strengths (verified).**
- Server-rendered Jinja, **zero JS bundle** on the audited routes; Motion One is vendored locally
  (no CDN) and is progressive enhancement. CSS ≈84 KB, JS ≈24 KB on disk.
- Evidence is visible on the detail page; every claim carries source attribution; absent values read
  "Not verified".
- Display-layer title-casing works: the raw API returns `"X TEAM /// Interior plans for 42,120 sq
  ft…"` while the rendered card shows a cleaned title (`display.py:clean_title`,
  `_titlecase_shouting`). The stored value is not mutated.
- Honesty is test-enforced: `tests/test_copy_honesty.py` bans overclaims ("saved searches",
  "continuous", "zero false positives", …) in template sources.

**Findings.**

| # | Finding | Evidence | Impact | Confidence |
|---|---|---|---|---|
| U1 | **No company/GC information is visible** — `/companies` is effectively an owners directory | `project_party` = owner only; `/companies` body shows "Owner" repeatedly | A contractor cannot see who is building the job | Verified |
| U2 | **Home `<title>` is market+trade specific**: "DFW Construction Opportunity Intelligence — Commercial HVAC" while the homepage is a broad product page | live `<title>`; `/markets/dfw` title is the distinct market page | Homepage competes with the market page for one intent; contradicts the broader BuildScope positioning | Verified |
| U3 | **~364 inline `style="…"` attributes** across 49 templates | `grep -ro 'style="' templates \| wc -l` → 364 | Maintainability; the CSP keeps `style-src 'unsafe-inline'` because of them (documented in `security.py`) | Verified |
| U4 | **No export (CSV/Excel/PDF)** and **no pricing page** | no `export`/`csv` route in `main.py`/`api.py`; no `/pricing`; `entitlements.py` has `FEATURE_EXPORT` | Estimators expect export; plans are unreachable | Verified |
| U5 | **Legacy DFW/HVAC copy is mostly in configuration and legitimately market-scoped**, but the *homepage* is the one place it reads as a narrow product | `home.html` names "Dallas, Fort Worth, and Collin County"; `markets.yaml`/`trades.yaml` carry DFW/HVAC by design | Positioning mismatch at the front door | Verified (structure); visual read UNVERIFIED |
| U6 | **Raw ALL-CAPS source names surface in API and operator surfaces** | `analyst-project 1` summary began "A commercial project named X TEAM /// THE 4 STORY ADDITION…"; list card `title=` attributes are ALL-CAPS | Detail/list display is cleaned; API and operator surfaces are not | Verified |
| U7 | Empty/loading/error/stale states: empty state is honest ("empty database"), `PREVIEW_MODE` banner exists; **mobile/a11y polish UNVERIFIED this session** (Playwright skipped) | `healthz` empty state; prior `audit/BUILDSCOPE_AUDIT_2026-10-09.md` found contrast/heading/tap-target issues | Prior audit is a point-in-time record; re-verification needs Playwright | UNVERIFIED |

**Explicitly not recommending a cosmetic redesign.** No user problem in this audit requires one.
The one UX item with a concrete, evidenced user impact is U1 (no GC/participant information).

---

## 7. Security and operational risks

Verified by reading `app/security.py`, `app/accounts.py`, `app/mailer.py`, `app/entitlements.py` and
by probing live headers. No destructive tests were run.

| ID | Sev | Area | Finding | Evidence | Confidence |
|---|---|---|---|---|---|
| S-1 | **Medium** | Test isolation / credential use | `tests/test_ai_analyst_app.py::test_ai_analyst_page_renders_for_an_operator` performs a **live network call with a real provider credential** when `Gemini_API_Token`/`GEMINI_API_KEY`/`XAI_API_KEY` is present, making the suite non-deterministic (proven: pass with the var unset, fail with it set) | §4.2 | Verified |
| S-2 | **Medium** | Email / account lifecycle | **No email verification**: `create_account` sets `is_active` immediately; a reset is undeliverable in production because no deployment sets `MAIL_BACKEND=smtp`/`SMTP_*` | `accounts.py`; `render*.yaml` (no mail vars); `mailer.py` default `console` | Verified |
| S-3 | Low | Headers | **No `Strict-Transport-Security`** header anywhere in code | `grep -rn "Strict-Transport-Security" src/` → none | Verified |
| S-4 | Low | Rate limiting | The limiter is **per-process**, so behind N workers a client gets N allowances; documented as needing a proxy limiter | `security.py` docstring; `docs/security.md` | Verified |
| S-5 | Low | CSP | `style-src` keeps `'unsafe-inline'` because of ~364 inline style attributes | `security.py` comment; U3 | Verified |
| S-6 | Low | Dependency vulns | **UNVERIFIED** — `pip-audit` is not installed and was not added (adding a tool was out of scope) | `python -m pip_audit` → not found | UNVERIFIED |
| S-7 | Info | Secrets | No secret committed: `git grep` for `AIza…`/`sk-…`/`xai-…` in tracked files → clean; Firebase config is injected and git-ignored; a test fails if it is tracked | `git grep`; `tests/test_firebase_config.py` | Verified |
| S-8 | Info | Positive controls | CSRF (constant-time), CSP with per-request nonce, `HttpOnly`/`SameSite=Lax`/`Secure` session cookie (live `Set-Cookie` confirmed), no privilege-granting web route, IDOR filtering, open-redirect guard, PBKDF2, enumeration-resistant errors, generic 500 | live headers; `security.py`; `accounts.py`; `docs/security.md` | Verified |

**Operational risks.**

| ID | Sev | Finding | Evidence | Confidence |
|---|---|---|---|---|
| O-1 | **Medium** | **No CI**: `.github/` does not exist; nothing runs the suite on push/PR | `ls .github` → not found | Verified |
| O-2 | Medium | **Free preview has no persistent disk**, so the dataset resets on every deploy and public counts change between visits; documented and mitigated by `PREVIEW_MODE` banner + production example | `render.yaml`; `docs/deployment.md` | Verified |
| O-3 | Low | **Backups are same-disk only** (keeps newest 3) and do not survive volume loss | `ops/backup.sh`; `docs/deployment.md` | Verified |
| O-4 | Low | **No external error monitoring/alerting**; errors land in the `app_error` table only | `main.py` `record_app_error`; `/admin/data` | Verified |
| O-5 | Info | Background-job duplication is **guarded** by a cross-process `flock` + re-entrancy + `OPPINTEL_LOCK_HELD` | `locks.py`; `ops/automate.py:194` | Verified |

---

## 8. Component classification

Timing: **now** = this next phase; **soon** = following phase; **later** = enhancement; **separate** =
its own approved task.

| Component | Path(s) | Disposition | Reason / evidence | Risk if changed | Timing |
|---|---|---|---|---|---|
| Intelligence engine | `normalize,assemble,grouping,provenance,classify,eligibility,procurement,discrepancy,changes,quality,intelligence,identity,events,intel_events,coverage,dates` | **KEEP** | Tested, works end-to-end; the product's core value | High — must not be simplified | — |
| Read boundary | `service.py`, `companies.py` | **KEEP** | The enforced boundary; `test_app_architecture.py` | High | — |
| Schema + migrations | `db.py` | **KEEP** | Two schema strings, idempotent migrations, order invariant honoured | High | — |
| Config-driven market/trade | `config/*.yaml`, `config.py` | **KEEP** | Second market/trade load by config; `test_app_architecture.py` | High | — |
| Search | `search_index.py`, `nl_search.py`, `config/search_vocabulary.yaml` | **KEEP** | FTS5 + expansion + whole-word reasons; layered | Medium | — |
| Change/events/alerts | `changes.py`, `events.py`, `intel_events.py`, `app/alerts.py` | **KEEP** | No change ⇒ no event ⇒ no alert; integrity check clean | High | — |
| Accounts/entitlements/security | `app/{accounts,entitlements,security}.py` | **KEEP** | Phase 1A remediation verified; no web privilege grant | High | — |
| Stat snapshot | `app/stat_snapshot.py` | **KEEP** | Fixes volatile headline counts | Medium | — |
| SEO layer | `app/{seo,seo_gate,seo_report}.py`, `config/keywords.yaml` | **KEEP** | Gate and sitemap agree; one primary keyword per page | Medium | — |
| AI analyst | `ai_analyst.py`, `/ai-analyst`, `/api/project/<id>/analyst` | **REFACTOR (test isolation only)** | Works live; but the suite is credential-sensitive and network-dependent (S-1) | Low | now |
| Mailer | `app/mailer.py` | **REFACTOR (configuration)** | Correct boundary; production sends nothing because no deployment sets SMTP (S-2) | Low | soon |
| Company/participant exposure | `assemble.py`, `companies.py`, `service.py`, `identity.py` | **REFACTOR + BUILD** | Machinery exists; data is owner-only (U1) | Medium — must not invent a GC | **now** |
| Home positioning | `templates/home.html`, `app/seo.py` | **REFACTOR** | Home title is market+trade specific (U2) | Low | soon |
| Inline styles | 49 templates (~364 attrs) | **REFACTOR** | Maintainability + CSP relaxation (U3/S-5) | Low | later |
| Export / pricing | (absent) | **BUILD** | Estimator expectation; plans unreachable (U4) | Low | later |
| Email verification | `app/accounts.py` | **BUILD** | Signup active without ownership proof (S-2) | Medium | soon |
| CI | `.github/workflows/` (absent) | **BUILD** | Nothing runs the suite (O-1) | Low | soon |
| Legacy React/Vite/AI Studio | `src/App.tsx`, `src/main.tsx`, `src/index.css`, `index.html`, `package.json`, `server.ts`, `vite.config.ts`, `tsconfig.json`, `bun.lock`, `metadata.json`, `firebase-applet-config.example.json` | **DELETE** | Confirmed unused by the served product | Low, but touch deployment files | separate |
| `audit/scripts/*` (57 files) | `audit/scripts/` | **INVESTIGATE** | One-off audit tooling; value unknown without reading each | None if kept | later |
| Dependency vulnerabilities | `requirements.txt` | **INVESTIGATE** | `pip-audit` unavailable (S-6) | — | soon |

---

## 9. Scalability and commercial readiness

**Immediate blockers (in the way of the next vertical or a paid deployment):**
1. **Company/participant data** is owner-only (U1) — the core gap for a trade beyond HVAC and for
   usefulness to a contractor.
2. **Analyst test isolation** (S-1) — the suite must be deterministic and offline for CI.
3. **No CI** (O-1) — nothing protects the suite on change.

**Needed to add a market or trade (verified by architecture):**
- A market is a `config/markets.yaml` entry + connectors; a trade is a `config/trades.yaml` entry
  declaring its evidence field/values. Both are exercised by `test_app_architecture.py` with a
  synthetic second market. **No code change is required for the *mechanism*.**
- What a second trade needs that config cannot supply: an **evidence source that publishes that
  trade's scope** (the Collin feed carries no trade text at all — documented in `trades.yaml`), and
  a way to show that trade's participants.

**Needed for more users/teams:** `organization`/`organization_member` tables exist; the earlier
audit found no multi-user route exposes membership (`test_copy_honesty.py` bans the claim). Team
features are **BUILD**, not present.

**Needed for paid plans:** plans/entitlements exist and no web route grants one; a **pricing route**
and a billing integration are absent. Payment is explicitly not implemented and not pretended.

**Needed for API/data-feed access:** a read-mostly JSON API exists; `FEATURE_API` is defined but no
key-authenticated API path is implemented (`_api_client_uses_header_auth` is a stub that returns
False unless the header is present).

**Explicitly not recommended:** PostgreSQL/PostGIS, a separate search engine, or microservices.
`docs/engineering/POSTGRES_MIGRATION.md` is a **target that is not implemented**, and no measured
constraint in this audit justifies the migration. SQLite is adequate for the current volume;
the real limit (single writer, one instance) is already documented and is a deployment property,
not a reason to re-platform now.

---

## 10. Prioritized roadmap

Each phase is scoped so it can be shipped and verified on its own.

### Phase 9 (IMMEDIATE) — Company intelligence: resolve and expose contractors, GCs and participants

1. **Problem.** The product shows an owner and nothing else. `project_party` on a real dataset is
   100% `owner`; `general_contractor`, `architect`, `developer` are `NULL` for every project.
2. **Why it matters.** A commercial mechanical contractor decides whether to pursue a job by
   knowing who is building it. Without a GC/participant, `/companies` and the project detail page
   cannot answer the user's second question.
3. **Current evidence for the gap.** `assemble.py:176-188` projects `permit.contractor` into a
   `project_party`/`general_contractor` only when it is set; the three enabled sources set
   `contractor=None` (`fort_worth_permits.py:163`, `dallas_accela_permits.py:557`,
   `collin_cad_permits.py:125`). Verified `project_party` roles = `owner` only; 0/940 permits have a
   contractor.
4. **Dependencies/prerequisites.** (a) An evidence source that publishes a contractor/GC field —
   the two **disabled** Dallas sources populate `contractor` (`dallas_permits.py:112,198`) and are
   the only in-repo evidence path; (b) a decision on whether to enable them for
   contractor-attribution only (their permit dates are old, so they must not create "new
   opportunity" rows); (c) `identity.py` already resolves aliases.
5. **Scope / files likely affected.** `connectors/dallas_permits.py` (field mapping),
   `config/sources.yaml` (enable-for-attribution semantics), `assemble.py` (party roles),
   `identity.py` (alias folding), `companies.py` + `service.py` (read), `app/display.py` (role
   labels), `templates/companies/*`, `templates/opportunities/detail.html`, `app/api.py` (JSON).
   **Do not** invent a GC when no source publishes one — absence must stay `NULL`.
6. **Acceptance criteria (objectively testable).**
   - A regression test proves a project with a source-published contractor yields a
     `project_party(role='general_contractor')` row with evidence, and a project without one yields
     **no** row (never a guessed value).
   - `/companies` filters (`role`, `q`, `city`) return subsets with no binding error (extend
     `test_companies.py`).
   - `/api/opportunities` returns `null` (not "Not verified") where a participant is unknown.
   - `intelligence.integrity_report()` stays `healthy: true`.
   - The full suite passes **with the analyst keys unset and with them set** (ties to the S-1 fix).
7. **Regression risks / protection.** Risk: widening the directory must not widen a claim. Protect
   with the existing mechanical-evidence gate and `trade_signal_label`; a test must assert a
   participant is only shown when an evidence row supports it (`reporting.field_verdict`).
8. **Classification:** **High priority** (a blocker for the multi-trade/multi-market product
   promise and for day-one contractor usefulness). Not an infrastructure blocker.

**Fold-in prerequisite (small, same phase):** make the analyst app tests deterministic and offline
by clearing provider keys in a fixture / monkeypatching the client, so the suite passes identically
with or without a key (S-1). This is required for the CI phase and is a security improvement.

### Phase 10 (SOON) — Make the suite the gate: CI + test isolation + mail configuration

1. **Problem.** Nothing runs the suite automatically; the suite is credential-sensitive; reset mail
   is undeliverable in production.
2. **Why it matters.** A change cannot be trusted without a gate; an account cannot recover without
   mail.
3. **Evidence.** No `.github/`; S-1; `render*.yaml` set no `MAIL_BACKEND`/`SMTP_*`.
4. **Scope.** Add a GitHub Actions workflow (`pip install -r requirements.txt pytest` → `pytest`);
   add the key-clearing fixture; document/set `MAIL_BACKEND=smtp` + `SMTP_*` in the production
   example (not the free preview).
5. **Acceptance criteria.** CI runs green on a clean checkout; the suite passes with the analyst
   key both present and absent; `MAIL_BACKEND=smtp` without `SMTP_HOST` raises `DeliveryError`
   (already tested) and a configured backend is documented for production.
6. **Classification:** High priority (protects every later phase).

### Phase 11 (SOON) — Broadened positioning and commercial surface

Home title/positioning (U2), `/pricing` from `entitlements.plans()` (U4), CSV export for the current
filter set (`FEATURE_EXPORT`). Acceptance: home `<title>` is a brand+category title distinct from
`/markets/dfw`; `/pricing` renders the real plan catalogue with no purchase path; a CSV endpoint
returns exactly the filtered set. **Classification:** later enhancement.

### Phase 12 (LATER) — Depth and scale

Team features (organizations), API keys, additional markets/trades where a source publishes the
trade's scope, external monitoring, off-box backups. **Classification:** later enhancement, each
gated on a real user or operational need.

---

## 11. Recommended immediate next phase (single choice)

**Phase 9 — Company intelligence: resolve and expose contractors, GCs and participants** (with the
small analyst test-isolation fix folded in as a prerequisite).

**Why this one, and not another.** It is the only gap that is simultaneously (a) verified present,
(b) directly on the target user's critical path, (c) served by machinery that already exists
(`identity.py`, `project_party`, `companies.py`, evidence rows), and (d) aligned with the stated
product direction (multi-trade, multi-market) without a rewrite. The UI-polish items are cosmetic
and the audit found no user problem that requires them; the scaling items (PostgreSQL, new search
engine) are not justified by any measured constraint; pricing/export are commercial surfaces that
depend on users arriving first. Phase 9 improves *what the product knows and shows*, which is where
this product's whole differentiation lives.

**Acceptance criteria for the phase** (copy of §10 Phase 9 item 6, restated as the gate):
1. A source-published contractor yields a `project_party(role='general_contractor')` row **with
   evidence**; an absent contractor yields **no** row and `null` in JSON.
2. `/companies` `role`/`q`/`city` filters each return a subset without error; existing
   `test_companies.py` cases still pass.
3. `intelligence.integrity_report()` → `healthy: true`; `alerts_without_event()` empty.
4. The suite passes **1206+/0 failing** both with and without `Gemini_API_Token` set.
5. No intelligence-engine behaviour is simplified; `test_app_architecture.py` and the evidence
   protections still pass.

---

## 12. Open questions and unverified items

1. **Is the app served from a live deployment, and at what revision?** No deployed URL is reachable
   from this sandbox and no `RENDER_GIT_COMMIT`/release marker is set locally, so live status and
   the live dataset size are **UNVERIFIED**. (`render.yaml` is the declared blueprint.)
2. **Which "record" number is canonical?** `stat_snapshot.py` now defines one metric per word, but
   whether the *published* deployment uses the snapshot path could not be checked live.
3. **Was the earlier "716 tests" baseline wrong or has the suite grown?** The audit's own history
   records 605, then 716, then 844, and the current tree is **1206 passed, 1 skipped**. All four
   figures are from point-in-time records; the 1206 figure is the only one measured here.
4. **Mobile/accessibility/perf**: not re-measured (Playwright is a developer-only extra and was
   skipped). The 2026-10-09 audit's contrast/heading/tap-target findings are **point-in-time and
   UNVERIFIED** against the current commit.
5. **Dependency vulnerabilities**: `pip-audit` unavailable (S-6).
6. **`audit/scripts/*`**: 57 one-off scripts; their continued value is **UNVERIFIED**.
7. **Do the disabled Dallas sources have a licence/quality basis for contractor attribution?** They
   are public, but their permit dates are old; using them for *attribution* (not for "new
   opportunity") is a product decision the owner must make.
8. **Email verification scope**: whether signup should require ownership proof before alerts is a
   product decision (S-2).

---

## 13. Appendix — commands actually run (abridged)

```bash
# orientation
git rev-parse --is-shallow-repository; git branch -a; git rev-parse HEAD
git status --porcelain=v1; git --no-pager log -1 --format='%H%n%ci%n%s%n%b'
git ls-files | wc -l; ls -la; find . -maxdepth 2 -type d

# dependencies (the container shipped bare; vendor/python is git-ignored)
python -m pip install --quiet -r requirements.txt pytest

# tests (full, twice)
env -u SECRET_KEY -u BASE_URL -u OPPINTEL_DB -u OPPINTEL_DATA_DIR -u PORT \
  PYTHONPATH=src python -m pytest tests/ -q
# → 1206 passed, 1 skipped / then 1 failed, 1205 passed, 1 skipped
env -u SECRET_KEY -u BASE_URL PYTHONPATH=src python -m pytest tests/test_ai_analyst_app.py -q
env -u SECRET_KEY -u BASE_URL -u Gemini_API_Token \
  PYTHONPATH=src python -m pytest "tests/test_ai_analyst_app.py::test_ai_analyst_page_renders_for_an_operator" -q

# schema + runtime
export SECRET_KEY=audit-only PYTHONPATH=src OPPINTEL_DATA_DIR=$PWD/data OPPINTEL_DB=$PWD/data/oppintel.db
python -m oppintel.cli initdb
python -m flask --app oppintel.app.wsgi init-app
python -m gunicorn --workers 1 --bind 127.0.0.1:12000 oppintel.app.wsgi:application &
curl -s http://127.0.0.1:12000/healthz
for p in / /opportunities /companies "/companies?role=general_contractor" /markets /trades /trends \
         /dashboard /admin/data /ai-analyst /sitemap.xml /api/statistics …; do
  curl -s -o /dev/null -w "%{http_code}  $p\n" "http://127.0.0.1:12000$p"; done

# real bounded ingestion + pipeline
python -m oppintel.cli ingest --max-pages 1 --source fort_worth_permits   # 965 raw / 940 permit
python -m oppintel.cli assemble                                            # 22 projects
python -m flask --app oppintel.app.wsgi build-search-index
python -m oppintel.cli integrity                                           # healthy: true
python -m flask --app oppintel.app.wsgi seo-report
python -m flask --app oppintel.app.wsgi analyst-project 1 --json           # status: AVAILABLE

# foundation queries (throwaway DB)
python - <<'PY'
import sqlite3; c=sqlite3.connect('data/oppintel.db')
print(c.execute("SELECT role,COUNT(*) FROM project_party GROUP BY role").fetchall())
print(c.execute("SELECT COUNT(*) FROM permit WHERE contractor IS NOT NULL AND TRIM(contractor)<>''").fetchone())
PY

# cleanup (restores the initial state; the DB path is git-ignored)
pkill -f "gunicorn --workers 1 --bind 127.0.0.1:12000"
rm -rf data/oppintel.db* data/raw data/automation* data/web.log .pytest_cache
git status --porcelain=v1   # → empty
```

### Audit artifacts

- This report: `audit/BUILDSCOPE_2.0_AUDIT_2026-10-16.md` (the only file created).
- Runtime artifacts created during the audit (throwaway SQLite DB, `data/raw/*.jsonl`, logs,
  `.pytest_cache`, `__pycache__`) were **deleted**; the working tree is clean
  (`git status --porcelain` → empty).
