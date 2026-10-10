# Deploy day — numbered checklist (host-neutral)

Run top to bottom. Nothing here names a specific host, plan or price. "The service" is whatever
runs `bash ops/start.sh`. Commands assume `export PYTHONPATH=src` and, on the service,
`export OPPINTEL_DB="${OPPINTEL_DB:-${OPPINTEL_DATA_DIR:-./data}/oppintel.db}"`.

Tick each box only when the pasted output matches the expectation.

## A. Before you deploy (on your machine)

1. `git status` — working tree clean except intended changes; on a feature branch, not `main`.
2. `git log --oneline -5` — the commits you expect to ship.
3. `env -u BASE_URL -u OPPINTEL_DB -u OPPINTEL_DATA_DIR -u SECRET_KEY -u PORT \
   PYTHONPATH="vendor/python:src" python -m pytest tests/ -q` — **green, 0 failed**.
4. `pip install -r requirements.txt` from a clean venv succeeds (requirements resolve).
5. `python -c "import yaml, flask, gunicorn, requests"` — all imports OK.

## B. Provision (one time)

6. Create the service from the repository; confirm the **build** command is
   `pip install -r requirements.txt` and the **start** command is `bash ops/start.sh`.
7. Attach **persistent storage** and set `OPPINTEL_DATA_DIR` to its mount; set
   `OPPINTEL_DB=<mount>/oppintel.db`. (No persistent storage ⇒ data is lost on every deploy.)
8. Set `SECRET_KEY` (generate once, never rotate casually), `SESSION_COOKIE_SECURE=true`,
   `CSRF_ENABLED=true`, `MAX_PAGES=3`, `REFRESH_SECONDS=21600`.
9. Set `BASE_URL` only if you use a custom domain; otherwise leave it unset so the host's own
   external URL is used. Confirm it never resolves to `http://127.0.0.1`.
10. Health check path = `/healthz`. Instances = **1** (SQLite is a single writer).

## C. First boot

11. `curl -s "$BASE_URL/healthz"` → **200** with `"status":"empty"` while filling. **200**, not
    503, is correct for an empty database — a 503 here would restart-loop the service.
12. Wait for the service's own first refresh (`ingest → assemble → build-search-index →
    monitor`). Re-check step 11 until `"status":"ok"`.
13. `curl -s "$BASE_URL/" | grep -o 'projects' | head` and `curl -s "$BASE_URL/api/statistics"`
    — counts are non-zero and **the same number** appears on `/` and in `/api/statistics`.

## D. Verify the live surface

14. `curl -s -o /dev/null -w '%{http_code}\n' "$BASE_URL/opportunities"` → **200**.
15. `curl -s "$BASE_URL/robots.txt"` and `curl -s "$BASE_URL/sitemap.xml"` → both present; the
    sitemap URLs start with the real `https://` origin (not loopback).
16. Open `/`, `/opportunities`, `/trends`, `/companies` in a browser — they render, no 500s.

## E. Change-tracking proof (run ~24h later)

17. Follow `audit/POST_DEPLOY_CHECKS.md`. Success = `project_change` rows with
    `change_kind <> 'new_project'` **> 0** and at least one source with `passes >= 2`.
18. `tail -n 40 data/automation.log` — repeated step lines with distinct timestamps.

## F. Rollback if anything is wrong

19. Bad code: redeploy the previous revision (persistent storage is untouched).
20. Bad data: `cp "$OPPINTEL_DB" "$OPPINTEL_DB.bak"` **before** risky work; restore by copying
    back while the service is stopped.
21. Total reset: delete `"$OPPINTEL_DB"` and redeploy; the loop refills it.

## Moving to real hosting (when the founder is ready)

* The app is hosting-neutral by design: a long-running Python process, a writable persistent
  directory, HTTPS, and environment variables. Any container host or VPS satisfies this.
* Sizing: a full backfill (`ingest` with no page cap) holds all permits in memory, so allow
  **≥ 2 GB RAM** for a one-time full seed; the recurring refresh is bounded and light. A
  **recommended 5 GB** persistent disk covers the database, the WAL and the raw JSONL archive
  (`docs/deployment.md`, `audit/scripts/h2_estimate.py`).
* No code change is required to move. Set `OPPINTEL_DATA_DIR` / `OPPINTEL_DB` / `SECRET_KEY` /
  `BASE_URL` on the new host and copy the database file across; the schema is created on first
  boot and migrations run automatically.
* Keep **one instance**. Scaling out requires moving SQLite to a networked store first.
