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
  log.

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

## Firebase web config is injected, never committed

* `firebase-applet-config.json` holds a Firebase web `apiKey` (an `AIza...` value). It is public
  client configuration, but an unrestricted key in a public repository is still a finding, so the
  file is git-ignored and untracked. `_load_firebase_config` reads, in order:
  `FIREBASE_CONFIG_JSON` (inline JSON) -> `FIREBASE_CONFIG_PATH` (file) -> a local
  `firebase-applet-config.json` (dev only). `firebase-applet-config.example.json` shows the shape.
* `tests/test_firebase_config.py` fails if the file is tracked again, if any committed file
  contains an `AIza...` key, or if the injection precedence regresses. If you must rotate the
  key: Firebase Console -> Project settings -> Web API key. Do not rotate `oAuthClientId` without
  updating Firebase Authorized Domains, or Google sign-in breaks.
* The key already appears in this repository's history. Removing it from the tree does not remove
  it from history; rotate it in the Firebase console and, if the history must be scrubbed, filter
  the repository separately.


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
