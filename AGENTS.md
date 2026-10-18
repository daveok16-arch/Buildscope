# AGENTS.md — repository guide

Persistent notes for working in this repository. Read this before making changes.

## What this is

DFW commercial construction opportunity intelligence for HVAC/mechanical contractors. Two
layers, deliberately separated:

* **Intelligence layer** (`src/oppintel/*.py`): connectors → normalize → assemble → evidence →
  classify → eligibility → procurement → changes → quality.
* **Application layer** (`src/oppintel/app/`): a Flask site that *reads* the intelligence data
  through `OpportunityService`. It re-implements nothing.

## Commands

```bash
export PYTHONPATH=src
export SECRET_KEY=dev-only-not-for-production

# Database and schema
python -m oppintel.cli initdb                 # intelligence schema + sources
flask --app oppintel.app.wsgi init-app        # application schema + migrations + plans

# Ingestion
python -m oppintel.cli ingest [--max-pages N] [--source ID]
python -m oppintel.cli assemble
flask --app oppintel.app.wsgi build-search-index
flask --app oppintel.app.wsgi monitor         # raise alerts from detected changes

# Inspect
python -m oppintel.cli stats
python -m oppintel.cli projects [--classification HIGH]
flask --app oppintel.app.wsgi report-quality
flask --app oppintel.app.wsgi seo-report            # SEO audit; --json for machine output

# Operations
flask --app oppintel.app.wsgi grant-admin EMAIL
flask --app oppintel.app.wsgi set-plan EMAIL PRO [--status ACTIVE|TRIALING|PAST_DUE|CANCELED]

# Tests
python -m pytest tests/ -q

# Run the app and automate the online-search pipeline (no cron/systemd here)
ops/start.sh                 # gunicorn on 0.0.0.0:12000 + scheduled refresh
ops/stop.sh
PYTHONPATH=src python ops/automate.py --once --max-pages 2   # one bounded refresh
```

## Non-negotiable rules

These are enforced by tests, not by convention. Breaking one fails the suite.

1. **Never invent a value.** A missing field stays `None` in the database and renders as
   "Not verified" (HTML) or `null` (JSON). Never the string "Not verified" in JSON.
2. **The application layer holds no intelligence logic.** No classification, scoring, evidence
   field names, or `FROM permit` queries in `app/`. `tests/test_app_architecture.py` asserts it.
3. **No hardcoded market or trade.** Resolve from `config/markets.yaml` and `config/trades.yaml`.
   The evidence field name comes from `TradeConfig.discovery`.
4. **Relevance is not procurement.** A project relevant to HVAC is not an open bid. Only a
   source stating bid language yields `Confirmed open`, which today never happens.
5. **User workflow is not source status.** Pipeline stages are the account's labels, kept
   disjoint from `procurement.py` states. A test asserts the vocabularies do not overlap.
6. **Every alert has an underlying event.** An alert points at a `project_change` row or a
   first-match event. `AlertService.alerts_without_event()` must stay empty.
7. **Change detection records real differences only.** A no-op assembly pass emits nothing.
8. **No web route grants privilege.** ADMIN only via CLI; plans only via `set-plan`.
9. **Ingestion stays off the web surface.** `/pipeline` is a reserved path in the admin tests;
   the account workflow route is `/my-pipeline`.
10. **Preserve the intelligence engine.** Do not simplify classification, provenance or
    eligibility to make the web layer easier.

## Layout notes

* `service.py` — the only module in the app layer that queries intelligence tables. Add a new
  read here, not in a route.
* `changes.py` / `quality.py` — intelligence layer. `changes.py` is diffed inside
  `Pipeline.assemble_and_classify`, which is why monitoring cannot be skipped.
* `app/workflow.py` — watching, pipeline, notes, tags, activity, org peers. Per-user rows only.
* `app/alerts.py` — event-driven, in-app. Email is modelled (`email_sent_at`) but not sent.
* `app/entitlements.py` — plan / subscription / entitlement. No payment code.
* `app/security.py` — CSRF, rate limiting, headers. Installed in `create_app`.
* `app/seo_gate.py` — programmatic-page quality gate. Thresholds live in `config/keywords.yaml`,
  not in code. The sitemap re-evaluates the same gate so the two signals agree.
* `app/seo_report.py` — the SEO audit. Computed from the map and the database; reports no ranking.
* `app/analytics_funnel.py` — landing events. Records a page *kind*, never a URL or identity.
* `config/keywords.yaml` — keyword-to-page map. One primary keyword per page (asserted by test).
  Curated `landing_pages` cities are indexable; city x trade combinations are gated.
* `config/search_vocabulary.yaml` — search synonyms and abbreviations. A group is a set of
  equivalent terms (`ahu` = `air handling unit`); a query naming any one expands to the whole
  group, so a contractor's shorthand finds the sources' spelled-out text. Per-trade groups live
  under `trades:`. Expansion only ever widens a search — the literal term is always retained —
  and a term claimed by two groups is rejected by test, so the map stays deterministic. Adding
  a trade's equipment is a config edit, not a code edit.
* Schema lives in two strings in `db.py`: `SCHEMA` (intelligence) and `APP_SCHEMA`
  (application). `alert_event` is created by `_ensure_alert_event` so a legacy table can be
  rebuilt first. Column additions go through `_migrate_app_tables`.

## Testing conventions

* No mocks. Intelligence tests use real captured payloads; app tests use `tests/conftest_app.py`
  fixtures over a real database.
* `app_db` / `client` / `session_client` / `admin_client` run with protections off (debug).
* `secured_client` runs with CSRF and rate limiting **on** — use it for security tests.
* `csrf_from(client, path)` extracts a token as a browser form post would carry it.
* **The suite must pass with a provider key in the environment.** An autouse fixture
  (`_no_provider_keys`) in `conftest_app.py` scrubs `GEMINI_API_KEY`/`Gemini_API_Token` and
  `XAI_API_KEY`/`Grok_API_Token`, so app-surface tests never make a live model call. An autouse
  fixture in one test *file* does not apply to another file, so this belongs in the shared conftest.
  CI exports a dummy key on purpose to prove it.
* `requirements.txt` is **pinned** (`==`), not ranged, so a deploy builds the dependency set CI
  tested. Bump a pin deliberately, with the suite run against the new version.

## Automation

* `ops/automate.py` is the only automation entry point. It supervises gunicorn **and** runs
  the refresh pipeline on a separate thread, so a long `ingest` never blocks server restart.
* This container environment has no cron or systemd daemon. Do not add a crontab
  or unit file; schedule in-process instead.
* Connectors order newest-first and default to 200 pages/source. An unbounded `ingest`
  (Fort Worth ArcGIS alone) is 200k+ records and takes >13 min, so a recurring refresh is
  bounded (`MAX_PAGES`, default 3); use `--full` only for an initial backfill.
* Every source carries `since_months: 24` in `config/sources.yaml`. `SourceConfig.resolved_since`
  turns that into a date and `Pipeline.ingest_source` passes it to `fetch_raw`, so the bound
  reaches the source's own date filter (ArcGIS `where`, Socrata `$where`, Accela search dates) —
  it cuts the download, not just the stored rows. CLI `--since` overrides it for one run; a 24-month
  seed is ~21.6k permits and a ~149 MB / 1 GB-disk footprint, versus ~232k permits / ~1.6 GB for all
  history. Peak RSS for a 200k-row ingest+assemble measured at ~298 MB.
  Tests: `tests/test_source_window.py`.
* `ops/start.sh` redirects the daemon's stdout to `data/automation.out`, **not**
  `data/automation.log`: the daemon owns that log file itself and a second writer interleaves
  and truncates lines.
* Runtime state is git-ignored: `data/*.log`, `data/*.out`, `data/*.pid`,
  `data/automation_state.json`.

## No static publishing

* The site is served by the running app, not published as a static mirror. There is no GitHub
  Pages workflow and no static export step: `render.yaml` is the deployment.
* The site must not be frozen into a separate build. The public pages read assembled projects
  through `OpportunityService`, so a snapshot would be a second copy of the data to keep current
  — and a stale one. Serving from the app keeps one source of truth.
* `robots.txt` and `sitemap.xml` are generated by `app/seo.py` from the app's own route map, so
  they describe the live site with no extra tooling.

## Deployment

* `render.yaml` is a ready blueprint for a persistent, always-on deployment on Render, but the app
  is hosting-neutral: nothing in the code depends on Render. It runs the app and its refresh loop
  in one service — no separate cron, worker or scheduler.
* `ops/start.sh` has two modes. Locally it backgrounds the daemon and writes a PID file; on a
  supervised host (`FOREGROUND=1`, or a platform that sets `$RENDER`) it `exec`s the supervisor in
  the foreground and binds the platform's `$PORT`. A container host requires the foreground mode —
  a backgrounded process looks like a crashed service.
* **One instance only.** SQLite is a single-writer database, and a second instance would run a
  second refresh loop against the same file. Scaling out means moving the database to a networked
  store first, not adding instances.
* `/healthz` is the platform's health check. It returns 503 **only** when the database is
  unreachable. An empty database is `200` with `"status": "empty"` on purpose: a first boot starts
  empty and the refresh loop fills it, so a 503 there would only cause a restart loop.
* `OPPINTEL_DB` is read by the CLI (`oppintel.cli.DEFAULT_DB`), so the pipeline honours persistent
  storage without every command repeating `--db`. The web layer reads `OPPINTEL_DB` through
  `AppConfig.database_path`. Point both at the same file.
* `BASE_URL` is the public origin for canonical tags and the sitemap. `ops/start.sh` resolves it
  as `BASE_URL` → the host's own external URL (`RENDER_EXTERNAL_URL`) → loopback, in that order.
  Never let the loopback default reach a deployment: it puts `http://127.0.0.1:<port>` in every
  canonical tag and every sitemap entry, and because the script always sets a value, the app's own
  relative-path fallback is never reached. `AppConfig.base_url` is empty only when nothing sets it.
* `/healthz` is operational, not content: it is in the robots disallow list (`app/seo.py`) and
  marked `noindex`. Add any new operational route there, not to a sitemap.

### Hosting requirements

BuildScope needs a real application host, not a static file host:

* a **long-running Python process** (gunicorn) behind a reverse proxy that terminates TLS;
* a **writable directory on storage that survives restarts and deploys**, for the SQLite database
  and the `data/raw/*.jsonl` archive;
* **HTTPS** with an origin the app can advertise (`BASE_URL`);
* environment variables (`SECRET_KEY`, `OPPINTEL_DATA_DIR`/`OPPINTEL_DB`, `BASE_URL`, and the
  refresh settings — see `docs/deployment.md`);
* the ability to run a **long-lived refresh loop** (the app supervises it) or a **scheduled job**
  that runs `ingest → assemble → build-search-index → monitor`;
* **one instance only** — SQLite is a single writer, so two instances must not share the file;
* at least **2 GB RAM** (the D6 full-seed measurement; see `docs/deployment.md`).

Plain shared hosting (PHP-style, a static-file host, or any platform that cannot keep a process
running and cannot give a writable persistent directory) is **usually unsuitable**.

### Persistent storage requirement

* The database is a single SQLite file and the assembled dataset is the app's value. Point
  `OPPINTEL_DATA_DIR` (and `OPPINTEL_DB`) at storage that **survives a restart and a deploy**.
* **With persistent storage:** a deploy replaces the code, not the data. The first boot finds the
  file and reuses it; a deploy or restart shows the same numbers.
* **Without persistent storage:** the filesystem is ephemeral, so the database is rebuilt from a
  bounded, partial ingest on every boot. A headline count read before and after a deploy then
  describes two different datasets — this is why public numbers can differ between visits.
* **First boot on an empty path:** the schema is created, the refresh loop runs its first pass,
  `/healthz` reports `"status": "empty"` (HTTP 200) while filling and `"ok"` once projects are
  assembled.
* **First boot on a populated path:** the existing database is reused as-is; nothing is wiped.
* `ops/start.sh` defaults `OPPINTEL_DATA_DIR` to the checkout's `data/` (always writable) and
  fails with a named error only if an **explicitly configured** path is not writable. It never
  refuses to start merely because a host has no disk.

## Gotcha list

* A dict key named `items` collides with `dict.items` in Jinja. Use another name
  (`pipeline.html` uses `entries`).
* `record_new_match` must commit; it is a public entry point outside the generation pass.
* `is_paid` excludes the operator plan deliberately.
* Session cookies only appear in `Set-Cookie` when the session changes.
* The application `data/oppintel.db` is git-ignored. Ingest before expecting data.

## Local run notes (this container)

* All Python dependencies are vendored under `vendor/python` (Flask, Jinja2, Werkzeug, gunicorn,
  PyYAML, requests). Nothing is installed system-wide, so every Python command needs both paths:
  `PYTHONPATH="vendor/python:src"` (run_server.py and ops/automate.py add them automatically).
* `vendor/python/bin` holds the `flask` and `gunicorn` console scripts; prepend it to `PATH` if you
  call `flask` directly rather than `python -m flask`.
* A seeded `data/oppintel.db` (658 projects, 1,129 permits) and two `data/raw/*.jsonl` captures ship
  in the archive, so the site renders real content without a live ingest.
* Live run in this container: `HOST=0.0.0.0 PORT=12000 PYTHONPATH="vendor/python:src" bash ops/start.sh`
  starts gunicorn + the refresh daemon in the background on port 12000.
* The React/Vite frontend (`src/App.tsx`) is an empty stub; the served site is the Flask app.

## Test-source recovery (Phase 1B)

* Six test modules shipped as orphan bytecode only (`tests/__pycache__/test_{provenance,security,
  workflow,reporting,report_generator,seo_keywords}.cpython-311-pytest-9.1.1.pyc`); their `.py`
  sources were deleted before archiving. `.pytest_cache/v/cache/nodeids` records the authoritative
  nodeids (698 total; 165 for those six files).
* Recover from the code objects, not from decompiled prose: `pycdc` drops helper bodies
  (`_pipeline`, `_add`, `_build`, `_count_events`, `_sitemap_locs`) and mangles parametrize.
  Read `co_consts`/`co_names`/`co_varnames` and the `pycdas` disassembly for the exact values.
* `_sitemap_locs` returns a list of URL strings (`[el.text for el in root.iter(f"{NS}loc")]`),
  not Elements. A parametrize id is the `str()` form of the value: a source literal of two
  backslashes is recorded with four, so match the recorded nodeid exactly.
* Two recorded nodeids are stale names in shipped files and are out of scope for recovery:
  `test_app_api.py::test_list_excludes_records_without_trade_evidence` (now
  `test_list_marks_records_without_trade_evidence`) and
  `test_app_directory.py::test_plumbing_only_record_is_not_listed_as_hvac` (now
  `test_plumbing_only_record_is_listed_but_never_as_hvac`).
* `tests/test_seo_keywords.py` encodes an internal-linking contract (see `docs/testing.md`): the
  category page must link to `/opportunities`, `/markets/dfw`, `/trades/commercial-hvac` and
  `/guides`, and the footer must link the category page on every public page. The shipped
  `templates/base.html` had neither, so those two links were added to the footer Product column.

## Deployment (Render blueprint)

* `render.yaml` is a ready blueprint. Build `pip install -r requirements.txt`, start `bash ops/start.sh`,
  health check `/healthz`, branch `main`. `ops/start.sh` detects a foreground host and runs the server
  plus the refresh loop in the foreground on `$PORT`.
* The data sources are public and unauthenticated, so a fresh instance self-seeds: the first
  refresh (`ingest → assemble → build-search-index → monitor`) took a fresh empty data dir to ~1,780
  projects in ~90s. `/healthz` reports `"status":"empty"` (HTTP 200) while filling, then `"ok"`.
* The blueprint ships `plan: free` with the disk block commented out, so a Blueprint always applies.
  Enable the tool's own persistent storage (a disk on Render, or any durable directory on another
  host) and set `OPPINTEL_DATA_DIR` to its path to keep the dataset across deploys — see the
  "Persistent storage requirement" above.
* Python is pinned with `.python-version` (`3.13`) rather than a `PYTHON_VERSION` env var: Render
  accepts an unqualified minor version in the file but requires a fully-qualified patch in the var.
* The repository is committed on branch `main` (163 files, ~1.8 MB of source). `vendor/python/` and
  `data/` runtime state are git-ignored — the host installs from `requirements.txt` and regenerates
  the database. The GitHub App token in this sandbox cannot create repositories; the owner must
  create the remote and push (see `docs/deployment.md`).

## Local live run (sandbox)

* Run: `PYTHONPATH="vendor/python:src" SECRET_KEY=dev OPPINTEL_DB=$PWD/data/oppintel.db python -m gunicorn --workers 2 --bind 0.0.0.0:12000 oppintel.app.wsgi:application`.
  Port 12000 is the sandbox's exposed host (work-1). The shipped `data/oppintel.db` is already
  populated (~3,000 projects), so the site serves real data without an ingestion pass.
* `BASE_URL` must be set for a live run (canonical/OG tags). Do NOT export it when running pytest:
  `tests/test_app_admin.py::test_admin_page_exposes_no_source_url_or_credential` scans the admin
  HTML for `https?://`, so a `BASE_URL` in the shell makes that test fail for an environmental
  reason, not a code one. Same for `OPPINTEL_DATA_DIR`/`OPPINTEL_DB`/`SECRET_KEY`/`PORT`.
* `init_app_schema` runs on every request (`before_request` -> `open_db`), so a broken `APP_SCHEMA`
  surfaces as a 500 on every page, including `/healthz`.

## Schema-order invariant

* `APP_SCHEMA` is executed before `_migrate_app_tables()`. A `CREATE INDEX` in `APP_SCHEMA` that
  names a column added by the migration (e.g. `analytics_event.query_text`) aborts the whole
  script on any pre-existing database. Indexes over migrated columns belong in a post-migration
  step (`_ensure_analytics_indexes`), not in `APP_SCHEMA`.

## Coverage honesty

* `coverage._market_records` excludes permits dated after the observation date from
  `latest_record_date`. A future-dated permit is a quality defect (`quality.py` flags it), and
  taking `MAX(permit_date)` over it would report a future date as the newest record and keep a
  stale market looking current. Tests: `test_future_permit_does_not_set_the_coverage_boundary`,
  `test_future_only_market_has_no_latest_record_date`.

## Parameterized SQL

* Never build one filter fragment and string-substitute it into several branches of a query
  (`str.replace` on a `WHERE` clause). Each placeholder needs exactly one binding, so a fragment
  reused N times needs N copies of its bindings — the mismatch is a runtime
  `sqlite3.ProgrammingError` that surfaces as HTTP 500. Build each branch's SQL and its bindings
  together instead. Regression: `CompanyService.list_companies` and
  `tests/test_companies.py::test_each_single_filter_returns_a_subset_without_error`.

## Password-reset delivery

* `app/mailer.py` is the single reset-delivery boundary. Backends: `console` (default; logs that
  a request happened, never the token), `smtp` (requires `SMTP_HOST`; raises `DeliveryError` when
  unconfigured rather than falling back), `null` (delivers nothing). Chosen by `MAIL_BACKEND`
  (`AppConfig.mail_backend`). A token is generated only for a real account, so delivery cannot
  become an enumeration oracle; the visitor-facing message is identical either way. Tests in
  `tests/test_auth_security.py` (section L) assert the token never reaches a page, response or
  log. Section M exercises the `smtp` transport against a minimal stdlib SMTP server
  (`_SmtpStub`) — plain and STARTTLS — so delivery is proven end to end with no dependency and no
  egress; the STARTTLS case mints a throwaway cert with the `openssl` CLI and skips if it is
  absent.
* **The page must not promise an email the deployment cannot send.** `ResetMailer.delivers` is
  true only for a fully configured `smtp` relay; `/forgot-password` reads it, so when delivery is
  off the page shows a notice and the success message does not claim a message was sent. The
  condition is configuration-only (never account-dependent), so the registered and unregistered
  responses stay byte-identical and the enumeration guarantee holds. No deployment file sets
  `MAIL_BACKEND`/`SMTP_*`, so delivery is off until an operator opts in
  (`render.production.example.yaml` documents the variables; see `docs/deployment.md`).

## AI Studio export (self-contained bundle)

* A Google AI Studio export of this project is a *flat* layout: `src/` holds both the Python
  package (`src/oppintel/`) and the React stub (`App.tsx`, `main.tsx`), with vendored wheels in
  `vendor/python/` and a pre-populated `data/oppintel.db`. It is Phase 1 plus the frontend stub,
  not the Phase 2 work in this checkout.
* Run it with `python run_server.py` (env `HOST`, `PORT`, `OPPINTEL_DATA_DIR`, `OPPINTEL_DB`,
  `SECRET_KEY`). It prefers gunicorn, falls back to Flask's server, then `wsgiref`.
* The frontend is a stub: `App.tsx` renders an empty `<div>` and `server.ts` (Express) only
  supervises the Flask process. The served product is entirely server-rendered Jinja; the Vite
  entry point contributes nothing. Do not read the React files as the app.

## Intelligence graph (Phase 2)

* The graph is derived data, not a second source of truth. `intelligence.derive_for_project` reads
  the stored permits, projects, evidence and detected changes and writes relationships. It never
  invents a value: no evidence -> no scope event, no resolvable name -> no entity, no street
  number -> null building key, no geocoder -> null coordinates.
* `identity.py` resolves entities deterministically — legal-form folding, `&`=`and`, last/first
  reordering for people — with no fuzzy matching. A false merge is worse than a duplicate, so a
  distinguishing word keeps entities apart (`test_identity.py`).
* `evidence_history` is append-only. `upsert_project` rebuilds the current `evidence` set every
  pass but archives each observation by fingerprint; a changed value appends a new row, a
  re-observation only refreshes `last_seen_at`. `Pipeline._derive_intelligence` does this per
  project; `run_backfill` (CLI `backfill-intelligence`) does it for existing projects.
* Events are keyed by a stable `event_uid` and inserted with `INSERT OR IGNORE`, so re-deriving
  unchanged facts adds nothing. `PROJECT_DISCOVERED`/`PERMIT_RECORDED`/`COMPANY_IDENTIFIED`/
  `DOCUMENT_ADDED` are always evidence-backed; `SCOPE_IDENTIFIED` requires mechanical evidence;
  `PROJECT_REVISED` comes only from a recorded `project_change`.
* Trades come from `config/trade_taxonomy.yaml` via `config.classify_trade`. The permit *type*
  is consulted before the description, so a plumbing permit is a plumbing record whatever the
  description says. A record naming no trade gets an empty trade list, not a default.
* Reached only through `OpportunityService` (`get_project`, `get_project_events`,
  `get_project_companies`, `get_project_trades`, `search_companies`, `search_projects`, ...).
  The `/admin/data` operations view surfaces `intelligence.integrity_report`; the CLI `integrity`
  command runs the same audit. Tests: `tests/test_intelligence.py`,
  `tests/test_intelligence_service.py`.

## Company directory aggregates canonically (Phase 9)

* `CompanyService.list_companies` groups stakeholders by the **same** canonical key the entity
  graph uses. `Database.__init__` registers `entity_key()` as a SQL function
  (`db._entity_key_for_sql` -> `identity.entity_key`), so a directory query and the graph can
  never disagree about whether two spellings are one company. A name that does not resolve is
  `NULL` and is excluded, never its own group.
* Two defects are pinned by tests in `tests/test_companies.py`. (1) A project reached the union
  through both the `project_party` branch and a direct project-column branch, so
  `mechanical_project_count` and `total_declared_value` were **doubled**; the union is now
  de-duplicated per `(company_key, project_id)` before summing. (2) Grouping by the raw spelling
  split `ACME HEALTH LLC` and `ACME HEALTH, L.L.C.` into two directory rows although
  `identity.py` merges them; the row is now keyed canonically.
* The `q` filter selects **companies**, not raw rows: it is applied once to the canonical keys
  (a name search for either spelling returns the merged company's full project total). Applying
  it to raw spellings would let one spelling report a smaller total than the same company shows
  unfiltered. Role and city filters stay on the row (they describe the observation, not the
  company).
* `role_counts` counts distinct canonical keys, so a role count equals the length of the list it
  filters to. `get_company_profile` resolves by key, so a slug, the display name, or any observed
  variant reach one dossier; `roles_observed` is read from the stored parties/columns, so a role
  no source published stays absent. `recurring_partners` are merged by key and exclude the
  company itself. Participant *data* is still owner-only until a source publishes a contractor
  (`assemble.py` projects `permit.contractor` only when set; the enabled sources set it `None`).

## Historical ingestion integrity (Phase 12)

* **A permit the source already dated before the freshness window must never be presented or
  alerted on as newly filed.** `changes.classify_added_permit` classifies an added permit by its
  own source date: inside the window → `new_permit` (alertable), older or undated →
  `HISTORICAL_PERMIT` (a timeline entry, deliberately **absent** from `NOTIFIABLE_KINDS`). An
  undated permit cannot be shown to be fresh, so it is treated as historical — issuance is never
  inferred from ingestion time. The window is `trade.scoring.recent_permit_days` (default 180, the
  same value the classifier uses), with `changes.DEFAULT_RECENT_PERMIT_DAYS` as fallback.
  `Pipeline._monitor_project` passes it in. The *first* pass over a backfill is already safe: with
  no prior snapshot the only change is `new_project`, which is not alertable. Tests:
  `tests/test_historical_ingestion.py`.
* **A connector's `since` bound must reach the source's own filter.** `connectors/base.coerce_since`
  turns a `date`/ISO string into a bound (a malformed value returns `None` so the request widens
  rather than filtering on a guess); `connectors/base.within_since` returns True/False/None — the
  `None` ("no usable date") case must be **kept**, never silently dropped. `dallas_gis_permits`
  previously hard-coded `where=1=1` and ignored `since`; `dallas_permits_socrata`'s predicate was
  dead code (the pipeline passes `None` for a source with no `since_months`) and used an exclusive
  `>`. Both now filter server-side with a client-side backstop. Tests:
  `tests/test_connector_date_filtering.py`.
* **Attribution-only enrichment is safe on the permit path, blocked off it.** Adding owner/contractor
  attribution to a permit the system already holds updates the same project in place (identity is
  `sha1(trade|address_key)`), records provenance via `assert_field`, and converges because
  `upsert_project` rebuilds evidence/parties each pass. A **non-permit** attribution overlay would be
  wiped by that rebuild: it needs either routing through the connector/Permit path (no schema change)
  or one `origin` column. Not built — no current source needs it. Tests:
  `tests/test_attribution_only.py`.
* Report: `audit/PHASE_12_HISTORICAL_INGESTION_INTEGRITY_2026-10-17.md`. Phase 11 decision C
  (no participant source qualifies) unchanged.

## Search intelligence (Phase 8, capability 1)

* Search infrastructure is layered and must stay layered: `search_vocabulary.yaml` (configured
  synonym groups, per-trade + global) → `search_index.quote_for_fts` (prefix-matched FTS5 OR
  groups) → `OpportunityService._search_ids` (expansion) → `list_opportunities`.
* Match reasons are generated by `search_index.explain_matches` from the words the user typed,
  matched as whole words against displayable fields, never from the widened FTS expression. A
  prefix coincidence must not become a displayed claim (`test_explain_matches_whole_words_only`).
  Reasons are attached by `OpportunityService.attach_search_reasons`, which reads descriptions
  from the index in one batched query. Account match reasons (`matching.py`) and search reasons
  are distinct blocks in the card.
* `config/search_vocabulary.yaml` groups widen recall only; a group is never merged across work
  the user treats as different (renovation != build-out; electrical != plumbing; the individual
  city names are never interchangeable). Keep `conflicts()` empty.
* A zero-result search writes a distinct `search_no_results` analytics event (plus the existing
  `search_performed`), only when a query was actually typed. Tests: `tests/test_search_intelligence.py`.

## Trend Radar (Phase 8, capability 2)

* `src/oppintel/trends.py`. Every metric is defined in one place and its definition travels with
  its value (page, JSON, and post). Projects, permits, trade-evidence and changes are four
  different counts and are never summed into one "opportunity" total.
* A count measures what BuildScope observed: a permit-date window ("new commercial projects") and
  an ingestion window ("projects first observed") are reported separately, because a deeper
  collection pass is not a busier market.
* A future-dated permit is excluded from every occurrence metric and reported via
  `Metric.excluded_future` (dataset-wide, since a window ending tomorrow cannot contain a future
  date). A malformed date is excluded and counted. A previous-period comparison appears only when
  `created_at` predates the previous period; otherwise the report says so rather than printing 0.
* `new_project` in `project_change` is a first observation, not a change, and is excluded from
  `projects_changed`. Surface: `/trends` (HTML) and `/api/trends` (JSON). Tests: `tests/test_trends.py`.

## LinkedIn content intelligence (Phase 8, capability 3)

* `src/oppintel/linkedin.py`. Deterministic and offline: no external model is called. A draft is a
  transformation of stored rows, so it is reproducible and reviewable.
* A draft is split into `verified` (field confirmed by `reporting.field_verdict`), `unverified`
  (absent, or present but unsupported - named, never invented), and `interpretation` (one labelled
  reading that states its comparison). Every post carries `PERMIT_EVIDENCE_NOTICE`.
* `validate_post` re-derives the project's supported figures and dates and flags any fact-shaped
  token in a `verified` line that is not among them - a drift guard, not a generator.
* Operator-only surface `/content/linkedin` (ADMIN; 403 for a signed-in non-admin), rendered by
  `content_linkedin.html`. `discover_candidates` ranks by stored signals and lists every reason;
  closed projects are never proposed. Tests: `tests/test_linkedin.py`.

## Sign-in is email/password only

* The Google/Firebase sign-in integration was removed. Gone with it: the `/auth/google` and
  `/auth/firebase-verify` routes, `_verify_firebase_id_token` / `_load_firebase_config`, the
  Firebase JS SDK in `signin.html` / `signup.html`, `firebase_config` template injection, the
  `google_id` column on `app_user`, `AccountService.authenticate_or_link_google`, and the
  `firebase-applet-config.example.json` file. The CSP no longer allowlists any Google or Firebase
  origin (`script-src` is `'self'` + nonce; `connect-src` is `'self'`), and `CSRF_EXEMPT_PREFIXES`
  is now empty — `/auth/firebase-verify` was its only entry, so every state-changing request
  requires a valid CSRF token.
* `tests/test_firebase_config.py` was deleted with the integration. `tests/test_auth_security.py`
  keeps sections H (logout) and I-L (password reset); its Google/Firebase sections (A-G) went with
  the routes. The `/auth/google` account-takeover bypass cannot recur because the route is not
  registered, not because it refuses to act.
* A Firebase web `apiKey` (`AIza...`) still exists in this repository's git history from the
  earlier integration. Removing the code does not un-expose a historical value; if that Firebase
  project is still in use, rotate the key (Firebase Console -> Project settings -> Web API key).
  Scrubbing history is a separate, filter-repo operation.

## Release trigger (Render preview)

* The preview service (`buildscope-preview`, `srv-db3u3jij9qps73ffa4vg`, free, branch `main`) is
  declared `autoDeploy: true` in `render.yaml`, but a `git push` to `main` did **not** trigger a
  redeploy (verified 2026-10-11: pushed `65f54f3`, live stayed on `b8a69c3` for >23 min; GitHub has
  no repository webhook — `GET /repos/.../hooks` → `[]`). Trigger the deploy from the Render
  dashboard (**Manual Deploy → Deploy latest commit**) or add a Deploy Hook / `RENDER_API_KEY` so CI
  can `POST /v1/services/<id>/deploys`. Verify with the release marker, not the git log:
  `curl -s $URL/healthz | jq -r .release` must equal the short sha you pushed.
* The marker is `RENDER_GIT_COMMIT[:7]` (`config.py`), surfaced in `/healthz`, `/api/statistics` and
  `<meta name="buildscope-release">`. A local run has no `RENDER_GIT_COMMIT`, so no marker.


## AI analyst

* `src/oppintel/ai_analyst.py`. An LLM writes prose about the evidence; it is never the source of
  a fact. The flow inverts the usual one: `collect_facts` emits a fact only for a field a stored
  evidence row supports (`reporting.field_verdict`), an unsupported field becomes an explicit
  unknown, `assemble_dossier` serialises the facts into a numbered dossier, and the model is
  constrained to a JSON schema (`BRIEF_SCHEMA`) at temperature 0.
* **Two providers, one contract.** Gemini (`GeminiClient`, `generateContent`) and xAI's Grok
  (`GrokClient`, the OpenAI-compatible `chat/completions`). `resolve_provider` picks one: an
  explicit `AI_ANALYST_PROVIDER` (`gemini`/`xai`) wins, else Gemini when its key is present, else
  xAI, else `""`. `default_client` builds the matching transport. `BRIEF_SCHEMA` is written in
  Gemini's dialect (upper-case types); `gemini_schema_to_json_schema` converts it to standard JSON
  Schema for xAI, changing only the type spelling — field names, `required` and enums are carried
  across, so both providers are constrained to the *same* contract. `build_brief` reads the model
  id off whichever client ran, so a Grok brief records a Grok model.
* **Verification before publication.** `validate_brief` re-derives support from the fact list and
  flags any fact-shaped token in the model's free text that is not among the supported values: a
  number not in the dossier (scale shorthand such as `2.5M` of a stored `2,500,000` is accepted;
  any other figure is not), a date not stored, a unit attached to a figure the evidence does not
  mark with it, a restated `Not verified` marker, a URL, bid
  language the notice forbids, or a field verdict the model re-graded. A violation sets
  `status=UNVERIFIED`, floors confidence, and **discards every piece of model prose** — narrative,
  `why_it_matters`, field assessments, questions and limitations are all replaced with
  evidence-derived text, so an offending sentence cannot survive in the page or the JSON. The
  check is provider-blind: it runs on the payload, not on the transport, so a Grok payload is
  verified exactly like a Gemini one.
* **The bid-language check is clause-scoped and negation-aware.** The caveat is itself a negation
  ("does not by itself confirm that the package is currently available for bid"), so a model
  restating it is honest, not claiming. `_negated` excuses a forbidden phrase only when *every*
  occurrence sits in a clause containing a negation; a claim in its own clause still flags. The
  negation list holds whole words only, so a bare "No." does not excuse the next sentence.
* **A figure carries its unit, and the unit is verified.** `_display` renders a project value as
  `$30,918` and an area as `685 sq ft` (identical to `report_generator`), because a dossier of
  bare numbers lets a model write "30,918 and 685 square feet" — every figure stored, the sentence
  still wrong. `validate_brief` reads the figure-to-unit association (`_marked_units_in`, a unit
  adjacent after or before the figure, not merely present in the sentence) and flags a figure
  marked with a unit the evidence does not give it. A unit the evidence never states (a `%` on a
  dollar figure) is flagged the same way. A bare figure with no unit claim passes, because naming
  a stored number asserts nothing extra.
* **Errors are sentences, not payloads.** `_http_error_message(exc, provider_label)` reduces an
  HTTP failure to the status and the error's first sentence, because the raw body is a JSON
  document with a documentation URL that would otherwise be pasted into a limitation (and leak a
  URL into a brief). Both providers nest the reason as `error.message`, so one reader serves both.
* **Graceful absence.** No key for either provider (or any transport/decoding error) yields
  `status=UNAVAILABLE` with the evidence intact; a page never fails and never pretends an analysis
  happened. The page distinguishes "no model configured" from "the model could not be reached".
  Keys are read from `GEMINI_API_KEY`/`Gemini_API_Token` and `XAI_API_KEY`/`Grok_API_Token`; never
  committed.
* **Free-tier quota.** `gemini-2.5-flash` on a free key returns `429` under sustained use, which
  surfaces as `UNAVAILABLE` — the design already covers it, so a quota wall degrades to the
  evidence-only summary rather than an error page.
* **Response cache (`CachingClient`).** A page view must not pay for a model call the platform has
  already made, so the provider's *raw response* is stored in `analyst_response_cache`, addressed
  by `cache_key(provider, model, prompt_version, system, dossier)` — a sha256 of exactly what the
  model was shown, with a unit separator between fields so `("ab","c")` cannot collide with
  `("a","bc")`. **Evidence is the invalidator:** the dossier is built from the stored facts, so a
  changed fact is a different key and the old row is unreachable. There is no TTL and no
  invalidation hook to forget. The brief is never cached — facts, verdicts, status and
  `validate_brief` are re-derived on every read — so a hit can only skip the network call, never
  bypass a grounding check; a response that fails verification fails it identically from storage.
  Only the *production* transport is wrapped: a caller-supplied client (a test double, the
  no-model sentinel) is passed through untouched, which is what keeps `tests/test_ai_analyst.py`
  honest about call counts. Measured live: 5.67–7.09s cold → 0.00s warm, identical prose. A
  missing table or a corrupt row is a miss, not an error (the cache is an optimisation). Tests:
  `test_cache_hit_skips_the_second_call`, `test_a_changed_value_invalidates_the_cached_response`,
  `test_a_cached_response_is_still_verified_on_read`, `test_cache_stores_the_payload_not_a_brief`.
* **The prompt contract is in the key, and it is hashed, not remembered.**
  `prompt_contract_digest(system, schema)` folds the system instruction *and* `BRIEF_SCHEMA` into
  the address. The schema is a separate request field, not prose in the dossier, so a key built
  from `(system, dossier)` alone would let a schema edit reuse a payload produced under the old
  shape — a stale answer served as a current one. Because the digest is computed, nobody has to
  remember to bump `PROMPT_VERSION` when they add a field; the key moves on its own. `PROMPT_VERSION`
  stays for a *semantic* change (a reworded instruction) and travels on the brief. Tests:
  `test_cache_key_changes_when_the_response_schema_changes`,
  `test_a_schema_edit_makes_a_stored_payload_unreachable`.
* **The cache is bounded (`ANALYST_CACHE_MAX_ROWS`, 5000).** Evidence-keyed caching means a
  corrected value writes a *new* row and orphans the old one, so the table would otherwise gain a
  row per distinct dossier forever — a slow leak on the persistent disk this deployment is meant
  to have. `store_analyst_payload` prunes to the newest N, with a 1.2x hysteresis band so the
  prune is off the write path for almost every insert. Dropping a row costs a model call; it can
  never serve a wrong answer, because the key is content-addressed. A key-scheme change (as in the
  bullet above) orphans every existing row at once; those are harmless — unreachable, never read —
  and age out through the same prune.
* **`generated_at` is the assembly time, not the model time.** A cache hit stamps the brief when
  the page is built, so the prose may be older than the timestamp that reports it. It is never
  evidence about the project and must not be read as one. `project_id` on a cache row is
  informational (the key is what addresses it); a row written before that column was wired reads
  `NULL`.
* **The key travels in a header.** `GeminiClient` sends `x-goog-api-key`, never `?key=` in the
  URL: a query string is written to proxy access logs and quoted in error traces. Verified live
  against `gemini-2.5-flash` and pinned by
  `test_gemini_client_sends_the_key_in_a_header_not_the_url`.
* **The suite runs with a key present, by construction.** `tests/conftest_app.py` has an autouse
  fixture (`_no_provider_keys`) that scrubs both documented names for both providers. Without it,
  `test_ai_analyst_page_renders_for_an_operator` made a real, paid Gemini call and then *failed*,
  because a reachable model renders a brief instead of the "no model configured" text the test
  asserts — a green suite that depended on the absence of a credential. CI
  (`.github/workflows/ci.yml`) exports a dummy `GEMINI_API_KEY` so this cannot regress silently.
* **No SDK.** Both clients are standard-library JSON POSTs (`urllib.request`), so the analyst adds
  no dependency (the repo ships four). The transport is an `AnalystClient` Protocol, so the
  evidence pipeline and the verification pass are tested with no network; each client is tested
  against a real local HTTP stub. The Gemini path was verified live against `gemini-2.5-flash`;
  **the Grok path is unit-verified against the documented xAI request/response shape but has not
  been exercised against a live xAI key in this environment** (no `XAI_API_KEY`/`Grok_API_Token`
  was available) — do not read its test coverage as a live confirmation.
* Surfaces: `/ai-analyst` (HTML, ADMIN), `/api/project/<id>/analyst` (JSON, ADMIN: 401/403/404),
  `flask ... analyst-project ID [--json] [--no-model]` (CLI). `/ai-analyst` joins `/admin` in the
  robots disallow list and is `noindex`. Tests: `tests/test_ai_analyst.py`,
  `tests/test_ai_analyst_app.py`. Audit: `audit/AI_ANALYST_AUDIT.md`.

## WP3 display layer + motion system (M1+)

* The site stays **server-rendered Flask + Jinja**. Interactive pieces are small vanilla
  "islands" under `src/oppintel/app/static/js/`, loaded with a CSP nonce and `defer`:
  `motion.js` (named animation behaviours) and `controls.js` (custom filter controls).
* **Motion One** (the vanilla "motion" library) is vendored at
  `static/vendor/motion/motion.min.js` (pinned 10.18.0, MIT, `VERSION.txt`, `LICENSE`). No CDN.
  It provides `animate/inView/stagger/spring`; `motion.js` wires the behaviours (scroll reveal,
  count-up, sticky-header compaction, segmented indicator, disclosure height, View Transitions).
  JS budget: whole-site <= 35 KB gzip (currently ~15 KB). Motion is progressive enhancement:
  every animated element works with JS off and respects `prefers-reduced-motion`; only
  transform/opacity animate, resting states are set in CSS so CLS stays 0.
* **Custom filter controls** (`controls.js`) replace the native `<select>` popup (which renders
  as a black Android system sheet) with a branded listbox + combobox. The native `<select>`
  stays in the DOM inside `.bs-combobox` as the form field of record, so a no-JS submit still
  filters. The island only upgrades selects inside a `[data-bs-control]` scope (the filter
  panels on `/opportunities` and `/companies`). On mobile the popup is a bottom sheet via
  `window.BSDialog`. Do not put a `[data-bs-control]` wrapper on a select whose value triggers
  JS navigation (the inline sort select in the toolbar) — the island sets the value directly.
* **No-JS fallback:** the mobile filter sidebar is an off-canvas sheet opened by JS, so a
  `<noscript>` block in `base.html` reveals it inline and hides the inoperable toggle. Keep it
  when changing the filter layout.
* Tests: `tests/test_controls.py` (markup contract), `audit/m1_probe.py` (browser: keyboard,
  bottom sheet, no console errors), `audit/live/` for release screenshots.

## Share card and brand assets

* `ops/make_brand_assets.py` draws the static brand files from the same tokens as `app.css`:
  `og-image.png` (the Open Graph / Twitter share card), `favicon.svg`, `favicon.ico`,
  `apple-touch-icon.png` and `logo.png` (the square mark for Organization JSON-LD). Pillow is a
  dev-only tool for regenerating them — the built files are committed, so Pillow is not a
  runtime dependency. Regenerate with `pip install Pillow && python ops/make_brand_assets.py`.
* The card is **1200x627**, LinkedIn's Post Inspector recommendation. That size lives once, as
  `OG_IMAGE_WIDTH`/`OG_IMAGE_HEIGHT` in `seo.py`; `base.html` declares it and the generator draws
  it from the same constants, so the advertised size cannot drift from the file (a mismatch makes
  a scraper letterbox or reject the image). `Seo.og_image_url` derives the origin from the
  canonical, so the image URL is absolute on a deploy and relative when no `BASE_URL` is known —
  never a guessed host. Tests: the share-card section of `tests/test_app_seo.py`.
