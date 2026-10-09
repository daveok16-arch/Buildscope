#!/usr/bin/env bash
# A5b: capture the exact headline figures each run for side-by-side comparison.
set -u
cd /workspace/project/Buildscope
RUN_DIR=/tmp/a5run; mkdir -p "$RUN_DIR"
for i in 1 2 3; do
  cp /tmp/populated.db "$RUN_DIR/run$i.db"
  PORT=$((13010 + i))
  OPPINTEL_DB="$RUN_DIR/run$i.db" SECRET_KEY=dev PORT=$PORT HOST=127.0.0.1 \
    PYTHONPATH="vendor/python:src" python -m gunicorn --workers 1 \
    --bind 127.0.0.1:$PORT oppintel.app.wsgi:application > "$RUN_DIR/b$i.log" 2>&1 &
  pid=$!
  for _ in $(seq 1 40); do curl -sf "http://127.0.0.1:$PORT/healthz" >/dev/null 2>&1 && break; sleep 0.25; done
  echo "### RUN $i"
  python - "$PORT" <<'PY'
import sys, json, urllib.request, re
port = sys.argv[1]
def get(p, raw=False):
    r = urllib.request.urlopen(f"http://127.0.0.1:{port}{p}")
    b = r.read().decode()
    return b if raw else json.loads(b)
api = get("/api/statistics")["statistics"]
health = get("/healthz")
home = get("/", raw=True)
def stat(home, label):
    # find the stat block value preceding the label
    m = re.search(r'>([0-9][0-9,]*)\s*<[^>]*>?\s*[^<]*' + re.escape(label), home)
    return m.group(1) if m else "?"
print("  /api/statistics :", {k: api[k] for k in ['public_projects','permit_records','linked_permits','active_jurisdictions','projects_total']})
print("  /healthz        :", {k: health[k] for k in ['status','projects','permits']})
for lbl in ["Commercial projects","commercial projects","records","Permit records","jurisdictions"]:
    v = stat(home, lbl)
    if v != "?": print(f"  / home {lbl!r}: {v}")
PY
  kill $pid 2>/dev/null; wait $pid 2>/dev/null
done
