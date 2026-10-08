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

## Render deployment

* `render.yaml` is the blueprint for the persistent, always-on deployment. It runs the app and
  its refresh loop in one service — no separate cron, worker or scheduler.
* **A disk is what keeps the data, and it needs a paid instance type.** SQLite is a file and the
  assembled dataset is the app's value, and Render's container filesystem is ephemeral. With a
  disk mounted at `/var/data` and `OPPINTEL_DATA_DIR` pointing at it, a deploy replaces the code,
  not the data. Without one — the Free plan has none — every deploy starts from an empty database
  and the refresh loop has to refill it.
* **Never hardcode the mount path as a default.** The blueprint ships `plan: free` with the disk
  block commented out, because a Blueprint that declares a disk on a Free instance does not apply
  at all. `ops/start.sh` therefore defaults `OPPINTEL_DATA_DIR` to the checkout's `data/`, which
  is always writable, and fails with a named error if an explicitly configured path is not.
* **One instance only.** A Render disk attaches to a single instance, and a second instance
  would run a second refresh loop against the same file. Scaling out means moving the database to
  a networked store first, not adding instances.
* `ops/start.sh` has two modes. Locally it backgrounds the daemon and writes a PID file; on a
  container host (`$RENDER`, or `FOREGROUND=1`) it `exec`s the supervisor in the foreground and
  binds the platform's `$PORT`. Render requires the foreground mode — a backgrounded process
  looks like a crashed service.
* `/healthz` is the platform's health check. It returns 503 **only** when the database is
  unreachable. An empty database is `200` with `"status": "empty"` on purpose: a first deploy
  starts empty and the refresh loop fills it, so a 503 there would only cause a restart loop.
* `OPPINTEL_DB` is read by the CLI (`oppintel.cli.DEFAULT_DB`), so the pipeline honours a mounted
  disk without every command repeating `--db`. The web layer reads `OPPINTEL_DB` through
  `AppConfig.database_path`. Point both at the same file.
* `BASE_URL` is the public origin for canonical tags and the sitemap. `ops/start.sh` resolves it
  as `BASE_URL` → `RENDER_EXTERNAL_URL` → loopback, in that order. Never let the loopback default
  reach a deployment: it puts `http://127.0.0.1:<port>` in every canonical tag and every sitemap
  entry, and because the script always sets a value, the app's own relative-path fallback is never
  reached. `AppConfig.base_url` is empty only when nothing sets it.
* `/healthz` is operational, not content: it is in the robots disallow list (`app/seo.py`) and
  marked `noindex`. Add any new operational route there, not to a sitemap.

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
* `tests/test_seo_keywords.py` encodes an internal-linking contract (README ~line 361): the
  category page must link to `/opportunities`, `/markets/dfw`, `/trades/commercial-hvac` and
  `/guides`, and the footer must link the category page on every public page. The shipped
  `templates/base.html` had neither, so those two links were added to the footer Product column.

## Deployment (Render)

* `render.yaml` is the blueprint. Build `pip install -r requirements.txt`, start `bash ops/start.sh`,
  health check `/healthz`, branch `main`. `ops/start.sh` detects `RENDER=true` and runs the server
  plus the refresh loop in the foreground on `$PORT`.
* The data sources are public and unauthenticated, so a fresh instance self-seeds: the first
  refresh (`ingest → assemble → build-search-index → monitor`) took a fresh empty data dir to ~1,780
  projects in ~90s. `/healthz` reports `"status":"empty"` (HTTP 200) while filling, then `"ok"`.
* The Free plan has no disk, so the database is rebuilt on each deploy; the dataset only persists on
  a paid plan with the disk block uncommented. Leave `OPPINTEL_DATA_DIR` unset without a disk —
  pointing it at `/var/data` with no disk attached is the one way to break the start.
* Python is pinned with `.python-version` (`3.13`) rather than a `PYTHON_VERSION` env var: Render
  accepts an unqualified minor version in the file but requires a fully-qualified patch in the var.
* The repository is committed on branch `main` (163 files, ~1.8 MB of source). `vendor/python/` and
  `data/` runtime state are git-ignored — the host installs from `requirements.txt` and regenerates
  the database. The GitHub App token in this sandbox cannot create repositories; the owner must
  create the remote and push (see `docs/RENDER_DEPLOY.md`).

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
