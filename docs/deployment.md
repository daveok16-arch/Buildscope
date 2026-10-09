# Deployment

BuildScope is a standard Flask app served by gunicorn. SQLite is a file, so the two things that
matter in production are where that file lives and how it is kept fresh.

## Environment variables

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `SECRET_KEY` | **Yes in production** | random per process | Session signing. Without it, sessions do not survive a restart |
| `OPPINTEL_DB` | No | `data/oppintel.db` | Database path. The CLI reads it, so a host can point the pipeline at a mounted disk |
| `OPPINTEL_DATA_DIR` | No | `data/` | Where the database, logs and refresh state live. Set it to the mount path on a host with a disk — a path that does not exist (`/var/data` with no disk) fails the start |
| `BASE_URL` | No on Render | empty | Public origin for canonical URLs, Open Graph and the sitemap. Falls back to `RENDER_EXTERNAL_URL` on Render |
| `SESSION_COOKIE_SECURE` | No | on unless debug | Secure cookie flag |
| `FLASK_DEBUG` | No | `false` | Debug mode. Never enable in production |
| `CSRF_ENABLED` | No | on unless debug | CSRF, rate limiting and response headers |
| `FREE_VIEW_LIMIT` | No | `0` (disabled) | Distinct opportunities a free account may view |
| `LOG_LEVEL` | No | `INFO` | Log verbosity |
| `REFRESH_SECONDS` | No | `21600` (6h) | Seconds between automatic refresh passes |
| `MAX_PAGES` | No | `3` | Pages fetched per source on a recurring refresh |
| `WEB_WORKERS`, `WEB_THREADS` | No | `2`, `4` | WSGI worker and thread counts. SQLite serialises writers, so a fractional CPU wants one worker |
| `HOST`, `PORT` | No | `127.0.0.1`, `5000` | Development server bind address. A hosting platform sets `PORT` |

`CSRF_ENABLED` follows the same convention as the secure-cookie flag: it defaults on, and off only
when `FLASK_DEBUG` is set, so a real deployment is protected unless someone deliberately disables
it. See `.env.example`.

For Firebase Google Sign-In configuration (`FIREBASE_CONFIG_JSON`, `FIREBASE_CONFIG_PATH`,
`FIREBASE_PROJECT_ID`), see [security](security.md).

## Persistent storage (required for stable public counts)

The database is a single SQLite file. On a host with no persistent disk the container filesystem
is ephemeral, so the database is rebuilt from a bounded, partial ingest after every deploy. Two
consequences follow:

* A headline count read before a deploy and again after it describes two different datasets. That
  is the observed cause of the home page showing different "commercial project" totals minutes
  apart (see `audit/WP1_REPORT.md`).
* The refresh loop is bounded (`MAX_PAGES`), so a fresh instance re-collects only the newest
  pages; the assembled dataset never converges to the same numbers as a longer-running instance.

The fix is a Render disk, which **requires a paid instance type**. `render.yaml` ships with this
enabled: `plan: starter`, the `disk` block attached at `mountPath: /var/data`, and both
`OPPINTEL_DATA_DIR=/var/data` and `OPPINTEL_DB=/var/data/oppintel.db` set. `ops/start.sh` honours
both variables and fails with a named error if an explicitly configured path is not writable.

A Blueprint that declares a disk on a Free instance does not apply at all, so the disk and the
paid plan go together. To run without a disk, set `plan: free`, remove the `disk` block and
remove both `OPPINTEL_*` variables — otherwise the Blueprint is rejected.

**Do not point `OPPINTEL_DATA_DIR` at `/var/data` without a disk attached** — that path does not
exist on a Free instance and the start is the one way to break the service.

## Deploy

1. **Install and build.**
   ```bash
   python -m venv .venv && source .venv/bin/activate
   pip install -r requirements.txt gunicorn
   ```

2. **Configure the environment.** Set at minimum `SECRET_KEY`. Set `BASE_URL` to your public
   origin, or leave it unset on Render, which uses `RENDER_EXTERNAL_URL`. Leave `FLASK_DEBUG` unset.

3. **Initialise the database.**
   ```bash
   PYTHONPATH=src python -m oppintel.cli initdb
   ```

4. **Ingest and build the index.**
   ```bash
   PYTHONPATH=src python -m oppintel.cli ingest
   PYTHONPATH=src python -m oppintel.cli assemble
   PYTHONPATH=src flask --app oppintel.app.wsgi init-app
   PYTHONPATH=src flask --app oppintel.app.wsgi build-search-index
   PYTHONPATH=src flask --app oppintel.app.wsgi monitor
   ```

   `init-app` creates the application tables, migrates any older application schema forward and
   seeds the plan catalogue. `monitor` raises alerts for changes detected during the assembly
   step, and is safe to re-run: a second call with no new data creates nothing. Change detection
   itself runs inside `assemble`, so monitoring is never a separate opportunity to miss.

5. **Run under a WSGI server.**
   ```bash
   PYTHONPATH=src gunicorn --workers 2 --threads 4 --bind 0.0.0.0:8000 \
     --access-logfile - --error-logfile - oppintel.app.wsgi:application
   ```

   Terminate TLS and apply a shared rate limit at the proxy in front of this process. The
   in-process limiter is per worker, so behind several workers a hostile client gets several
   allowances; the proxy is where the single enforceable limit belongs.

6. **Create an operator account for `/admin/data`.** The page requires an account whose access
   level is `ADMIN`. Create the account through `/signup`, then promote it on the host that owns
   the database:
   ```bash
   PYTHONPATH=src flask --app oppintel.app.wsgi grant-admin ops@example.com
   ```
   An unauthenticated request is redirected to sign-in, and a signed-in non-operator receives
   `403`. The level is granted only by this command: no web route can set it, so no request can
   escalate its own privileges. Revoke with `revoke-admin`.

**One-time full initial seed (Render shell).** The refresh loop is bounded (`MAX_PAGES`,
default 3), so a fresh instance re-collects only the newest pages and the assembled dataset
stays small. To fill it once, after the first boot, open a Render shell on the running service
and run a deeper backfill against the mounted database. Run the steps **in this order** — each
consumes what the previous one produced:

```bash
# 0. Confirm the shell sees the same database the web process serves.
export PYTHONPATH=src
export OPPINTEL_DB="${OPPINTEL_DB:-/var/data/oppintel.db}"
ls -la "$OPPINTEL_DB"

# 1. Ingest. Omit --max-pages to use each connector's own default cap (200 pages/source),
#    rather than the automation's MAX_PAGES=3. This is the deeper seed.
PYTHONPATH=src python -m oppintel.cli ingest

# 2. Assemble permits into projects, classify, and diff against the prior snapshot.
PYTHONPATH=src python -m oppintel.cli assemble

# 3. Rebuild the search index (this also refreshes the stored stat snapshot).
PYTHONPATH=src flask --app oppintel.app.wsgi build-search-index

# 4. Raise alerts from any detected changes.
PYTHONPATH=src flask --app oppintel.app.wsgi monitor

# 5. Confirm the snapshot actually moved: read the same figures the pages read.
PYTHONPATH=src python -m oppintel.cli stats
curl -s "$BASE_URL/api/statistics" | python -m json.tool | head -20
```

Expected duration: an unbounded `ingest` (no `--max-pages`) reaches each connector's 200-page
ceiling; Fort Worth ArcGIS alone is 200k+ records and takes more than 13 minutes, so budget
15–30 minutes for the full seed. The pipeline commits every 250 permits, so an interrupted run
leaves a consistent partial dataset and re-running is idempotent (raw records are keyed by
content hash). Do not schedule an unbounded `ingest`; leave the recurring refresh bounded.

**Confirm the snapshot refreshed.** The stored snapshot (`market_stat_snapshot`) is the single
source every headline figure reads. A successful seed changes two things in `/api/statistics`:
the counts (`public_projects`, `permit_records`) rise, and `statistics.computed_at` advances to
the time step 3 ran. If `computed_at` is unchanged, `build-search-index` did not run against the
same `OPPINTEL_DB` — check step 0. `/healthz` should report `"status":"ok"`, and the home page
should show the larger counts.

**Schema migration.** The application tables are created with `IF NOT EXISTS` statements, so
`init-app` is additive and idempotent against a database that already holds ingested data. It
never alters or drops an intelligence table.

**Scheduled ingestion.** Run `ingest` then `assemble` then `build-search-index` on a daily
schedule. A partial ingestion is safe: the pipeline commits every 250 permits, and re-running is
idempotent because raw records are keyed by content hash. On Render the service runs its own
refresh loop, so there is no separate cron job to configure (see [operations](operations.md)).

## Deploying to Render

`render.yaml` is a ready blueprint. In the Render dashboard choose **New → Blueprint**, point it
at the repository, and it reads the file. The service runs the app *and* its own refresh loop, so
there is no separate cron job, worker or scheduler to configure.

| Setting | Value |
|---|---|
| Runtime | Python |
| Build | `pip install -r requirements.txt` |
| Start | `bash ops/start.sh` |
| Health check | `/healthz` |
| Plan | `free` (default in the blueprint) |

Render generates `SECRET_KEY` once and keeps it, so logins survive a redeploy. `PYTHONPATH=src` is
set for you. No other secrets are required: the data sources are public and unauthenticated.

On first boot the service starts empty and the refresh loop fills it. `/healthz` returns `200` with
`"status":"empty"` while the database is still filling, and `"status":"ok"` once projects are
assembled. An empty database is deliberately not a failure, so the probe never produces a restart
loop during the first fill.

### Keeping the dataset across deploys

The default Free plan has **no persistent disk**, so the container filesystem — and the database in
it — is replaced on every deploy. That is fine for a first look. To keep the assembled dataset:

1. Change `plan` in `render.yaml` from `free` to `starter` (or higher).
2. Uncomment the `OPPINTEL_DATA_DIR` and `OPPINTEL_DB` variables and set them to `/var/data`.
3. Uncomment the `disk:` block at the bottom of `render.yaml`.

Keep `numInstances: 1`: a Render disk attaches to a single instance, and two instances would run two
refresh loops against one SQLite file. Scaling out needs the database moved to a networked store
first, not more instances.

Without a disk, leave `OPPINTEL_DATA_DIR` unset. The app then writes to the checkout's `data/`
directory, which always exists. Pointing it at `/var/data` with no disk attached is the one way to
break the start, because that path is never created.

### Custom domain and canonical URLs

`BASE_URL` overrides the public origin used in canonical links, Open Graph tags and the sitemap. On
Render you usually do not set it: the app falls back to `RENDER_EXTERNAL_URL`, the service's own
`onrender.com` URL. Set `BASE_URL` only to override that, e.g. for a custom domain. Leaving both
unset is not a failure — pages then emit relative canonical paths — but a deployed service should
end up with one or the other so the sitemap carries absolute URLs.

### Verify after deploy

```bash
curl -s https://<service>.onrender.com/healthz
curl -s -o /dev/null -w '%{http_code}\n' https://<service>.onrender.com/
curl -s -o /dev/null -w '%{http_code}\n' https://<service>.onrender.com/opportunities
```

### Manual path (no blueprint)

Create a **Web Service**, connect the repository, set the build command to
`pip install -r requirements.txt`, the start command to `bash ops/start.sh`, and the health check
path to `/healthz`, and add the environment variables from the `envVars` block of `render.yaml`
(`PYTHONPATH=src`, `SECRET_KEY` generated, `SESSION_COOKIE_SECURE=true`, `CSRF_ENABLED=true`).

## The React/Vite scaffold

The repository contains a React/Vite scaffold (`package.json`, `vite.config.ts`, `src/*.tsx`,
`server.ts`). It is not the shipped product: the product is entirely server-rendered Jinja served
by Flask. The scaffold is retained from the AI Studio export and is not part of the deployment.
