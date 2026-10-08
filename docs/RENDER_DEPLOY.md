# Deploying BuildScope to Render

The repository ships a Render blueprint (`render.yaml`), so the deploy is three steps. The only
thing this environment cannot do for you is create the GitHub repository and the Render account —
both need your credentials. Everything else is prepared and verified.

## 1. Put the code on GitHub

Render deploys from a Git repository. The code is already committed on branch `main` locally. If
the target repository does not exist yet, create it (the GitHub App token in this sandbox cannot
create repositories), then push:

```bash
git remote add origin https://github.com/<owner>/buildscope.git
git push -u origin main
```

The blueprint expects branch `main`, which is what is committed.

## 2. Create the Render Blueprint

In the Render dashboard: **New → Blueprint**, point it at the repository above, and apply. Render
reads `render.yaml` and creates the `buildscope` web service with:

| Setting | Value |
|---|---|
| Runtime | Python |
| Build | `pip install -r requirements.txt` |
| Start | `bash ops/start.sh` |
| Health check | `/healthz` |
| Plan | `free` (default in the blueprint) |

Render generates `SECRET_KEY` once and keeps it, so logins survive a redeploy. `PYTHONPATH=src` is
set for you. No other secrets are required: the data sources are public and unauthenticated.

## 3. First boot

On the first boot the service starts empty and the refresh loop fills it:

1. `ops/start.sh` runs `initdb` and `init-app` (idempotent), then starts the web server and the
   refresh loop in one process.
2. The refresh loop runs `ingest → assemble → build-search-index → monitor` immediately, then every
   `REFRESH_SECONDS` (default 6h). It fetches the newest `MAX_PAGES` pages per source.
3. `/healthz` returns `200` with `"status":"empty"` while the database is still filling, and
   `"status":"ok"` once projects are assembled. An empty database is deliberately not a failure, so
   the probe never produces a restart loop during the first fill.

Verified locally against a fresh empty data directory: the first pass went from `0` projects to
`1,780` in about 90 seconds, and `/healthz` flipped to `"status":"ok"`.

## Keeping the dataset across deploys

The default Free plan has **no persistent disk**, so the container filesystem — and the database in
it — is replaced on every deploy. That is fine for a first look. To keep the assembled dataset:

1. Change `plan` in `render.yaml` from `free` to `starter` (or higher).
2. Uncomment the `OPPINTEL_DATA_DIR` and `OPPINTEL_DB` variables and set them to `/var/data`.
3. Uncomment the `disk:` block at the bottom of `render.yaml`.

Keep `numInstances: 1`: a Render disk attaches to a single instance, and two instances would run
two refresh loops against one SQLite file.

Without a disk, leave `OPPINTEL_DATA_DIR` unset. The app then writes to the checkout's `data/`
directory, which always exists. Pointing it at `/var/data` with no disk attached is the one way to
break the start, because that path is never created.

## Custom domain and canonical URLs

`BASE_URL` overrides the public origin used in canonical links, Open Graph tags and the sitemap. On
Render you usually do not set it: the app falls back to `RENDER_EXTERNAL_URL`, the service's own
`onrender.com` URL. Set `BASE_URL` only to override that, e.g. for a custom domain.

## Verify after deploy

```bash
curl -s https://<service>.onrender.com/healthz
curl -s -o /dev/null -w '%{http_code}\n' https://<service>.onrender.com/
curl -s -o /dev/null -w '%{http_code}\n' https://<service>.onrender.com/opportunities
```

## Manual path (no Blueprint)

If you prefer to configure by hand: create a **Web Service**, connect the repository, set the build
command to `pip install -r requirements.txt`, the start command to `bash ops/start.sh`, the health
check path to `/healthz`, and add the environment variables listed in the `envVars` block of
`render.yaml` (`PYTHONPATH=src`, `SECRET_KEY` generated, `SESSION_COOKIE_SECURE=true`,
`CSRF_ENABLED=true`).
