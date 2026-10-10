# BuildScope deploy runbook (non-technical)

This is the exact procedure to put BuildScope online and fill it with data. Follow the steps in
order. Anything in `code font` is typed exactly as shown.

The one thing to understand before starting: BuildScope's data is a single database file, and
**persistent storage is what keeps it between deploys**. On a host with no persistent storage the
database is thrown away on every deploy and rebuilt from a small, partial collection. That is the
root cause of headline numbers that disagree between visits.

---

## 1. Deploy the service (about 10 minutes)

Deploy to any host that meets the requirements in `docs/deployment.md` ("Hosting requirements"): a
long-running Python process, a writable persistent directory, HTTPS, and environment variables. A
container host (Render, Fly, Railway, a VPS) fits; plain shared hosting usually does not.

1. Create an account with the host and connect the GitHub repository that holds this project.
2. Create a **web service** from the repository.
3. Set the build command to `pip install -r requirements.txt`.
4. Set the start command to `bash ops/start.sh`. On a supervised host set `FOREGROUND=1` (or let the
   platform set `$RENDER`); it then runs in the foreground and binds the host's `$PORT`.
5. Set the **health check** path to `/healthz`.
6. **Attach persistent storage** (a disk, volume or durable directory) and point the app at it:
   * `OPPINTEL_DATA_DIR` = the persistent mount path
   * `OPPINTEL_DB` = `<that path>/oppintel.db`
   Without persistent storage the database is rebuilt from a partial collection on every deploy and
   the headline numbers change between visits.
7. Set these environment variables:
   * `MAX_PAGES` = `3`
   * `REFRESH_SECONDS` = `21600`
   * `SESSION_COOKIE_SECURE` = `true`, `CSRF_ENABLED` = `true`
   * `SECRET_KEY` — generate once and keep it. Do not change it after launch, or everyone is signed
     out.
   * `BASE_URL` — leave unset if the host provides its own external URL; set it to the full
     `https://…` origin if you use a custom domain.
8. Deploy. The host builds and starts the service.

### Mail backend (password-reset emails)

Password reset uses `app/mailer.py`. By default (`MAIL_BACKEND` unset or `console`) it only logs
that a reset was requested and **does not send an email**. To actually send reset mail, set
`MAIL_BACKEND=smtp` and provide `SMTP_HOST` (plus credentials). Until then, reset requests do
nothing user-visible — this is intentional and not a bug.

---

## 2. First boot: what to expect

**Empty persistent directory.** The schema is created on first boot and the refresh loop runs its
first pass.

* `/healthz` returns **HTTP 200** with `"status":"empty"`. This is correct. A host's health check
  must not kill the process for an empty database, so an empty database is a `200` by design.
* The home page shows zeros for a short time.
* The service's own refresh loop starts automatically and runs `ingest → assemble →
  build-search-index → monitor`. Within roughly a minute or two the pages begin to fill.
* Once the first pass completes, `/healthz` reports `"status":"ok"` with non-zero counts.

**Already-populated directory.** The existing database is reused as-is; nothing is wiped and the
counts are stable across a restart or a deploy.

You do **not** need to do anything for this first fill. The one-time deeper seed (below) is
optional and only makes the dataset larger.

---

## 3. One-time deeper seed (optional, fills more history)

The refresh loop is bounded to the newest pages (`MAX_PAGES=3`) so it stays quick and gentle.
To pull deeper history once, open a **shell on the running service** and run these in
order. Each consumes what the previous one produced.

First, **pause the refresh loop** so the seed cannot overlap it (see §4). Then:

```bash
export PYTHONPATH=src
export OPPINTEL_DB="${OPPINTEL_DB:-$OPPINTEL_DATA_DIR/oppintel.db}"
ls -la "$OPPINTEL_DB"          # confirm the shell sees the same file the site serves

python -m oppintel.cli ingest                 # deeper ingest (each source's own page cap)
python -m oppintel.cli assemble               # build projects, classify, diff
flask --app oppintel.app.wsgi build-search-index   # rebuild search index + stat snapshot
flask --app oppintel.app.wsgi monitor         # raise alerts from detected changes

python -m oppintel.cli stats                  # confirm the snapshot moved
curl -s "$BASE_URL/api/statistics" | python -m json.tool | head -20
```

Measured on the audit machine (this sandbox, Python 3.13, wired network): a **full** unbounded
ingest is slow and uneven. Fort Worth's ArcGIS service alone is 200k+ records and took
**more than 13 minutes**; Dallas and the other sources add several more. Budget **15–30 minutes**
for a full seed and expect it to vary by host. If you only need more than the refresh gives,
run a bounded ingest instead: `python -m oppintel.cli ingest --max-pages 20`.

The pipeline commits every 250 permits, so an interrupted seed leaves a consistent partial
dataset and re-running it is safe (raw records are keyed by content hash).

After the seed, confirm `statistics.computed_at` in `/api/statistics` advanced. If it did not,
`build-search-index` ran against a different database file — re-check the `OPPINTEL_DB` line.

---

## 4. How to pause and resume the refresh loop

The seed must not run at the same time as the refresh loop. The lock that enforces this is in
`src/oppintel/locks.py` (a file lock the refresh job holds for its whole cycle). If you start a
seed while the refresh is mid-cycle, the seed waits for the lock rather than corrupting anything,
but the cleanest approach is:

* **Pause:** set the `REFRESH_SECONDS` env var to a very large number (for example `604800`, one
  week) and save. The running loop finishes its current cycle, then sleeps for a week.
* **Resume:** set `REFRESH_SECONDS` back to `21600` (six hours) and save.

---

## 5. Verification checklist (do this after every deploy)

1. **Health:** open `/healthz`. Expect `"status":"ok"` (or `"empty"` briefly on a brand-new disk).
2. **Stable numbers:** open the home page and `/api/statistics`, then **reload each three
   times**. The four headline figures (public projects, projects with mechanical evidence, cities
   with at least one public project, permit records) must be identical every time. If they move
   between reloads, the disk is not attached.
3. **Honest change status:** open `/changes`. The status line names how many entries are first
   observations versus real differences between passes. On a fresh database the real-difference
   count is `0` and the page says so — that is correct, not a failure.
4. **No fabricated alerts:** `/alerts` (signed in) should be empty until a watched project
   actually changes.

---

## 6. Rollback

* **Bad code deploy:** redeploy the last good revision from the host's deploy history. The
  persistent directory is untouched, so the data survives.
* **Bad data (seed went wrong):** the persistent directory keeps old files. Before a risky seed,
  copy the database file: `cp "$OPPINTEL_DB" "$OPPINTEL_DB.bak"`. To roll back, stop the service,
  `cp "$OPPINTEL_DB.bak" "$OPPINTEL_DB"`, and start it again.
* **Total reset:** delete `$OPPINTEL_DB` and redeploy; the service recreates the schema and the
  refresh loop refills it from the public sources.

---

## 7. Things that will break the start

* Pointing `OPPINTEL_DATA_DIR` (or the database's directory) at a path that is **not writable**.
  The start script exits with a named error. Unset both `OPPINTEL_*` variables to fall back to the
  checkout's own `data/`.
* Running more than **one instance**. SQLite is a single writer, and a second instance would run a
  second refresh loop against the same file. Keep one instance.
* Changing `SECRET_KEY` after launch signs every user out.
