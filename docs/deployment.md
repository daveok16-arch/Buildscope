# Deployment

BuildScope is a standard Flask app served by gunicorn. SQLite is a file, so the two things that
matter in production are where that file lives and how it is kept fresh.

## Environment variables

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `SECRET_KEY` | **Yes in production** | random per process | Session signing. Without it, sessions do not survive a restart |
| `OPPINTEL_DB` | No | `data/oppintel.db` | Database path. The CLI reads it, so a host can point the pipeline at persistent storage |
| `OPPINTEL_DATA_DIR` | No | `data/` | Where the database, logs and refresh state live. Set it to a persistent path on a host that has one — a path that is **not writable** fails the start |
| `BASE_URL` | No | empty | Public origin for canonical URLs, Open Graph and the sitemap. Falls back to the host's own external URL, if it injects one |
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

## Deployment model and hosting requirements

BuildScope is a standard Flask app served by gunicorn. SQLite is a file, so the two things that
matter in production are where that file lives and how it is kept fresh. The app is hosting-neutral:
nothing in the code depends on a particular platform.

Hosting requirements:

* a **long-running Python process** (gunicorn) behind a reverse proxy that terminates TLS;
* a **writable directory on storage that survives restarts and deploys** for the SQLite database
  and the `data/raw/*.jsonl` archive;
* **HTTPS** with an origin the app can advertise (`BASE_URL`);
* environment variables (`SECRET_KEY`, `OPPINTEL_DATA_DIR`/`OPPINTEL_DB`, `BASE_URL`, the refresh
  settings — see the table above);
* the ability to run a **long-lived refresh loop** (the app supervises it) or a **scheduled job**
  that runs `ingest → assemble → build-search-index → monitor`;
* **one instance only** — SQLite is a single writer, so two instances must not share the file;
* at least **512 MB RAM and a 1 GB persistent disk** for the default 24-month seed, or **2 GB RAM
  and 5 GB disk** for an all-history backfill (see the sizing below).

Plain shared hosting (PHP-style, a static-file host, or any platform that cannot keep a process
running and cannot give a writable persistent directory) is **usually unsuitable**.

## Persistent storage (required for stable public counts)

The database is a single SQLite file. On a host with no persistent storage the container filesystem
is ephemeral, so the database is rebuilt from a bounded, partial ingest after every deploy. Two
consequences follow:

* A headline count read before a deploy and again after it describes two different datasets. That
  is the observed cause of the home page showing different "commercial project" totals minutes
  apart (see `audit/WP1_REPORT.md`).
* The refresh loop is bounded (`MAX_PAGES`), so a fresh instance re-collects only the newest
  pages; the assembled dataset never converges to the same numbers as a longer-running instance.

The fix is persistent storage: point `OPPINTEL_DATA_DIR` and `OPPINTEL_DB` at a directory that
survives a restart and a deploy. `ops/start.sh` honours both variables and fails with a named error
only if an explicitly configured path is **not writable**. It never refuses to start merely because
a host has no disk: with both variables unset it writes to the checkout's own `data/`, which always
exists.

**Sizing (from `audit/scripts/j2_sizing.py` and `audit/scripts/j2_rss.py`).** Two seed scopes are
supported. The default seed is bounded to a rolling **24-month** window (`since_months: 24` on
every source in `config/sources.yaml`, applied at the source's own date filter):

| Scope | Permits | DB | raw `data/raw/*.jsonl` | 3 gz backups | Total | Disk |
|---|---|---|---|---|---|---|
| 24-month (default) | ≈21.6k | ≈149 MB | ≈23 MB | ≈43 MB | ≈220 MB | **1 GB** |
| all history | ≈232k | ≈1.6 GB | ≈197 MB | ≈461 MB | ≈2.3 GB | **5 GB** |

Peak RSS during an ingest+assemble of the largest single source (200k Fort Worth rows) was
measured at **298 MB** (`j2_rss.py`), so **512 MB RAM** covers the default seed and **2 GB** covers
an unbounded `--full` backfill. The WAL is bounded near 4 MB by SQLite's autocheckpoint; raw files
grow ~916 MB/month only if the (inert-by-default) retention policy is left off.

**Backups.** `ops/backup.sh` / `oppintel backup` writes a consistent online snapshot and keeps the
newest **3** by default. Backups land on the same disk, so they do **not** survive the volume's
loss — copy them to object storage (Render disk snapshots, or an off-box `rsync`) if the dataset
must be recoverable. `create_backup` warns when free space is below one snapshot plus 200 MB
headroom; it warns and proceeds rather than aborting, because a stale backup is usually better
than none.

## Deploy

1. **Install and build.**
   ```bash
   python -m venv .venv && source .venv/bin/activate
   pip install -r requirements.txt gunicorn
   ```

2. **Configure the environment.** Set at minimum `SECRET_KEY`. Set `BASE_URL` to your public
   origin; a host that injects its own external URL is used when `BASE_URL` is unset. Leave
   `FLASK_DEBUG` unset.

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

**One-time full initial seed.** The refresh loop is bounded (`MAX_PAGES`,
default 3), so a fresh instance re-collects only the newest pages and the assembled dataset
stays small. To fill it once, after the first boot, open a shell on the running service
and run a deeper backfill against the persistent database.

**Pause the refresh loop first.** The seed and the refresh loop must not overlap. The refresh
job holds a single-writer file lock (`src/oppintel/locks.py`) for its whole cycle, so a seed
started mid-cycle waits rather than corrupting the file — but the clean way is to pause the
loop: set `REFRESH_SECONDS` to a large value (e.g. `604800`) and save; the current cycle
finishes, then the loop sleeps. Set it back to `21600` when the seed is done.

Run the steps **in this order** — each consumes what the previous one produced:

```bash
# 0. Confirm the shell sees the same database the web process serves.
export PYTHONPATH=src
export OPPINTEL_DB="${OPPINTEL_DB:-$OPPINTEL_DATA_DIR/oppintel.db}"
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

Expected duration (measured on the audit sandbox, Python 3.13, wired network — expect it to vary
by host): an unbounded `ingest` (no `--max-pages`) reaches each connector's 200-page ceiling;
Fort Worth ArcGIS alone is 200k+ records and takes more than 13 minutes. Budget 15–30 minutes for
the full seed. The pipeline commits every 250 permits, so an interrupted run leaves a consistent
partial dataset and re-running is idempotent (raw records are keyed by content hash). Do not
schedule an unbounded `ingest`; leave the recurring refresh bounded.

**Rate limiting and retries.** The default page cap is gentle on the public sources. Every
connector enforces a minimum gap of `min_seconds_between_requests` (1.0s) between requests,
retries transient HTTP failures (`429/500/502/503/504`) up to `max_retries` (3) with exponential
backoff (1s, 2s, 4s), uses a 60s timeout, and identifies itself with a descriptive
`User-Agent` (`config/sources.yaml`, `src/oppintel/connectors/base.py`). A deeper seed is slower
but still polite: the limiter is per-request, not per-page.

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
idempotent because raw records are keyed by content hash. On a host that runs the app's own
refresh loop, so there is no separate cron job to configure (see [operations](operations.md)).

## Deploying

The steps above (build -> initialise -> seed) work on any host that meets the hosting requirements.
Two knobs are all a host needs to wire up:

| Setting | Value |
|---|---|
| Build | `pip install -r requirements.txt` |
| Start | `bash ops/start.sh` |
| Health check | `/healthz` |
| Persistent directory | set `OPPINTEL_DATA_DIR` (+ `OPPINTEL_DB`) to it |

`ops/start.sh` runs the app *and* its own refresh loop in one process, so there is no separate
cron job, worker or scheduler to configure. On a supervised host it runs in the foreground; set
`FOREGROUND=1` (or let the platform set `$RENDER`) if the host expects the process to stay in the
foreground on its own `$PORT`.

`SECRET_KEY` must be generated once and kept, so logins survive a redeploy. `PYTHONPATH=src` makes
the app and an operator shell agree. No other secrets are required: the data sources are public and
unauthenticated.

On first boot the service starts empty and the refresh loop fills it. `/healthz` returns `200` with
`"status":"empty"` while the database is still filling, and `"status":"ok"` once projects are
assembled. An empty database is deliberately not a failure, so the probe never produces a restart
loop during the first fill. On a populated persistent path the existing database is reused as-is.

Keep **one instance only**: SQLite is a single writer, so two instances must not share the file.
Scaling out needs the database moved to a networked store first.

### Render blueprint (one hosting option)

`render.yaml` is a ready blueprint for Render, which is one way to satisfy the requirements above;
nothing in the code depends on it. In the Render dashboard choose **New -> Blueprint**, point it at
the repository, and it reads the file. The blueprint ships `plan: free` with the disk block
commented out so it always applies; to keep the dataset across deploys, enable a disk and set
`OPPINTEL_DATA_DIR` to its mount path (or move to any host with persistent storage).

### Custom domain and canonical URLs

`BASE_URL` overrides the public origin used in canonical links, Open Graph tags and the sitemap. If
the host injects its own external URL, leaving `BASE_URL` unset uses that. Set `BASE_URL` only to
override it, e.g. for a custom domain. Leaving both unset is not a failure - pages then emit
relative canonical paths - but a deployed service should end up with one or the other so the
sitemap carries absolute URLs.

### Verify after deploy

```bash
curl -s https://<host>/healthz
curl -s -o /dev/null -w '%{http_code}\n' https://<host>/
curl -s -o /dev/null -w '%{http_code}\n' https://<host>/opportunities
```

### Manual path (no blueprint)

Create a web service, connect the repository, set the build command to
`pip install -r requirements.txt`, the start command to `bash ops/start.sh`, and the health check
## The React/Vite scaffold

The repository contains a React/Vite scaffold (`package.json`, `vite.config.ts`, `src/*.tsx`,
`server.ts`). It is not the shipped product: the product is entirely server-rendered Jinja served
by Flask. The scaffold is retained from the AI Studio export and is not part of the deployment.
