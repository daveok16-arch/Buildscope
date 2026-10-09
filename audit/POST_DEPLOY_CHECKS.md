# Post-deploy checks (change tracking on live data)

Run these **24 hours after a deploy** to prove that change tracking is working on live data.
They are read-only. Substitute the path to the live database if the service runs with a disk
(`/var/data/oppintel.db`); otherwise open a Render shell on the running service first.

```bash
export PYTHONPATH=src
export OPPINTEL_DB="${OPPINTEL_DB:-/var/data/oppintel.db}"
```

If the service has no persistent disk, the container filesystem is ephemeral and the shell's
copy of the database is not the one the web process holds — in that case the check is best run
from the service's own refresh, which logs its steps to `data/automation.log`.

## 1. Count real differences (not first observations)

A row with `change_kind = 'new_project'` is a first observation, not a change. Anything else is
a real difference detected between two collection runs.

```bash
python - <<'PY'
from oppintel.db import Database
import os
db = Database(os.environ["OPPINTEL_DB"])
n = db.conn.execute(
    "SELECT COUNT(*) FROM project_change WHERE change_kind <> 'new_project'"
).fetchone()[0]
print("real differences:", n)
print("by kind:")
for r in db.conn.execute(
    "SELECT change_kind, COUNT(*) FROM project_change "
    "WHERE change_kind <> 'new_project' GROUP BY 1 ORDER BY 2 DESC"
):
    print(" ", r[0], r[1])
db.close()
PY
```

**Expected:** a number greater than zero once at least two collection passes have run against
the same records. If it is still `0`, the second refresh has not detected a difference — check
step 3 to confirm two passes actually happened.

## 2. Three example diffs with before/after values

```bash
python - <<'PY'
from oppintel.db import Database
import os
db = Database(os.environ["OPPINTEL_DB"])
rows = db.conn.execute(
    """
    SELECT c.detected_at, p.project_name AS project, c.field_name,
           c.previous_value, c.current_value, c.change_kind
      FROM project_change c JOIN project p ON p.id = c.project_id
     WHERE c.change_kind <> 'new_project'
     ORDER BY c.detected_at DESC
     LIMIT 3
    """
).fetchall()
for r in rows:
    print(f"{r['detected_at']}  {r['project']}")
    print(f"    field : {r['field_name']} ({r['change_kind']})")
    print(f"    before: {r['previous_value']}")
    print(f"    after : {r['current_value']}")
if not rows:
    print("No real differences yet.")
db.close()
PY
```

**Expected:** three rows, each with a non-empty `previous_value` and `current_value` that differ.

## 3. Confirm two collection passes actually ran

```bash
python - <<'PY'
from oppintel.db import Database
import os
db = Database(os.environ["OPPINTEL_DB"])
print("distinct retrieval dates per source:")
for r in db.conn.execute(
    "SELECT source_id, COUNT(DISTINCT retrieval_date) AS passes, "
    "MAX(retrieval_date) AS latest FROM source_coverage GROUP BY source_id"
):
    print(f"  {r['source_id']}: {r['passes']} pass(es), latest {r['latest']}")
db.close()
PY
```

**Expected:** at least one source with `passes >= 2`. A single pass cannot, by definition,
produce a difference — it only produces first observations.

Also read the last time any stored project row changed, which is the freshness bound every page
shows. If it is older than the refresh interval, the loop is not writing:

```bash
python - <<'PY'
from oppintel.db import Database
import os
db = Database(os.environ["OPPINTEL_DB"])
row = db.conn.execute(
    "SELECT MAX(updated_at) AS last_observed, COUNT(*) AS projects FROM project"
).fetchone()
print("last_observed:", row["last_observed"], "projects:", row["projects"])
db.close()
PY
```

## 4. Confirm the refresh loop is running

```bash
tail -n 40 data/automation.log
```

Look for repeated `ingest` → `assemble` → `build-search-index` → `monitor` step lines with
distinct timestamps. `/healthz` should report `"status":"ok"` and a non-empty database:

```bash
curl -s "$BASE_URL/healthz"
```

## Interpreting the result

* **Real differences > 0 and two example diffs** — change tracking is proven on live data.
* **Real differences = 0 but passes >= 2** — the municipal records genuinely did not move
  between passes. This is the honest "no fabricated events" behaviour, not a failure; widen the
  window or wait for the next refresh.
* **passes <= 1 after 24h** — the refresh loop is not completing. Inspect
  `data/automation.log` for a step timeout; the recurring refresh is bounded (`MAX_PAGES`,
  default 3) and should finish well inside the `REFRESH_SECONDS` interval (default 21600s).
