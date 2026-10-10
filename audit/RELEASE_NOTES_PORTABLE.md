# BuildScope — Portable Release Notes

Status: **preview**. This document is the environment-variable contract and the portability
notes for the BuildScope web service. It is written for whoever deploys it next, on any host.

BuildScope is a Python 3.13 WSGI application (Flask + gunicorn) backed by a SQLite database and
an in-process refresh loop. There is no separate worker, cron job, scheduler, message broker or
external database. One process serves the site and periodically refreshes the dataset.

## What runs

| Piece | File | Notes |
| --- | --- | --- |
| WSGI entry point | `src/oppintel/app/wsgi.py` | gunicorn imports `oppintel.app.wsgi:application`. |
| Web factory | `src/oppintel/app/main.py` | `create_app()`; routes, security, template helpers. |
| Refresh supervisor | `ops/automate.py` | Supervises the web server and runs the pipeline on a thread. |
| Start script | `ops/start.sh` | Two modes: foreground (container host) and background (local shell). |
| Health probe | `/healthz` | 503 only when the database is unreachable; 200 `"empty"` on a first deploy. |

## Deployment targets

### 1. Render preview (the shipped blueprint) — `render.yaml`

Free plan, no disk. The container filesystem is ephemeral, so the database is rebuilt on every
deploy and the app self-seeds from the public sources on first boot. `PREVIEW_MODE=1` shows a
fixed banner — "Preview build: data may reset on redeploy." — on every page so a visitor is not
misled by a reset dataset.

A `disk:` block must **not** be added to `render.yaml`: a Blueprint that declares a disk on a
Free instance does not apply at all.

### 2. Persistent production example — `render.production.example.yaml`

A paid instance (`plan: starter`) with a 1 GB disk mounted at `/var/data` and
`OPPINTEL_DATA_DIR=/var/data` / `OPPINTEL_DB=/var/data/oppintel.db`, so a deploy replaces the
code, not the data. This file is an example, not the active blueprint — copy it over
`render.yaml` to go persistent. See `docs/deployment.md` for the sizing rationale.

### 3. Portable container — `Dockerfile`

```bash
docker build -t buildscope .
docker run -p 8000:8000 \
  -e SECRET_KEY="$(python -c 'import secrets;print(secrets.token_hex(32))')" \
  -e BASE_URL="http://localhost:8000" \
  -v "$PWD/data:/app/data" \
  buildscope
```

The container sets `FOREGROUND=1`, so `ops/start.sh` `exec`s the supervisor in the foreground
and binds `$PORT`. Mount a volume at `/app/data` (or set `OPPINTEL_DATA_DIR`) to keep the
dataset across container replacements; without a mount the database lives in the container layer.

## Environment variables

| Variable | Required | Default | Purpose |
| --- | --- | --- | --- |
| `SECRET_KEY` | production: yes | random per process | Signs sessions. Unset logs users out on restart; the app logs a warning. |
| `BASE_URL` | deployed: yes | `RENDER_EXTERNAL_URL` → loopback | Canonical links, Open Graph, sitemap. Loopback must never reach a deployment. |
| `PORT` | container: yes | 12000 (local) | The port to bind. A platform assigns it. |
| `HOST` | no | `0.0.0.0` | Bind address. |
| `SESSION_COOKIE_SECURE` | recommended | on outside debug | Force the `Secure` cookie flag. |
| `CSRF_ENABLED` | no | on outside debug | CSRF + rate limiting. |
| `GZIP_ENABLED` | no | on outside debug | Compresses eligible text/JSON responses (gzip; brotli if installed). |
| `PREVIEW_MODE` | preview: yes | off | Shows the "data may reset on redeploy" banner. |
| `OPPINTEL_DATA_DIR` | persistent host: yes | checkout `data/` | Directory for the database, logs and state. Must be writable. |
| `OPPINTEL_DB` | persistent host: yes | `<data>/oppintel.db` | The SQLite file. Point the app and the CLI at the same path. |
| `REFRESH_SECONDS` | no | 21600 (6 h) | Refresh interval. |
| `MAX_PAGES` | no | 3 | Pages per source per refresh (bounded for a small instance). |
| `GROW_BACKFILL` | no | 1 | `1` lets the page cap double after each clean pass; `0` pins it. |
| `LOG_LEVEL` | no | INFO | Logging verbosity. |
| `MAIL_BACKEND` | no | `console` | Password-reset delivery: `console`, `smtp` (needs `SMTP_HOST`), `null`. |

## First boot

Both schema commands are idempotent, so `ops/start.sh` runs them on every boot:

```bash
python -m oppintel.cli initdb                  # intelligence schema + sources
python -m flask --app oppintel.app.wsgi init-app   # application schema + migrations
```

Then the supervisor starts the web server and the first refresh (`ingest → assemble →
build-search-index → monitor`). The first pass on an empty database takes roughly 90 seconds to
a few minutes depending on the page cap. `/healthz` reports `"status":"empty"` (HTTP 200) while
it fills, then `"ok"` — a 503 there would only cause a restart loop.

## Ingestion window

The permit-date window the site states is derived from `config/sources.yaml`
(`since_months` per source; currently 24 months). The footer and the home/markets/how-it-works
pages print "Permits filed since <date>" from that configuration, so the disclosure cannot drift
from what the pipeline actually ingests. To change the window, edit the source config — not the
templates.

## Known limits (preview)

* **No persistent storage on the shipped blueprint.** The dataset resets on redeploy. Use the
  disk example or a mounted volume for a real deployment.
* **Single instance only.** SQLite is a single writer; a second instance would run a second
  refresh loop against the same file. Scaling out means moving the database to a networked store
  first.
* **No outbound email in the preview.** Password-reset delivery defaults to the non-sending
  `console` backend; alerts are in-app only.
* **Bounded refresh.** The default 24-month window and 3-page cap keep the first pass small; a
  full historical backfill is a deliberate, separate operation.

## Verification

```bash
export PYTHONPATH="vendor/python:src"
env -u BASE_URL -u OPPINTEL_DB -u OPPINTEL_DATA_DIR -u SECRET_KEY -u PORT -u GZIP_ENABLED \
  python -m pytest tests/ -q
```

The suite covers the release contracts in `tests/test_release_config.py` (preview mode, the
deployment assets, the collection window) and `tests/test_gzip.py` (compression negotiation).
