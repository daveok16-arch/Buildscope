#!/usr/bin/env bash
# A5: start the app from a COPY of the DB in a fresh process, three times, restarting between.
set -u
cd /workspace/project/Buildscope
RUN_DIR=/tmp/a5run
rm -rf "$RUN_DIR"; mkdir -p "$RUN_DIR"

for i in 1 2 3; do
  cp /tmp/populated.db "$RUN_DIR/run$i.db"
  PORT=$((13000 + i))
  echo "############ RUN $i (fresh process, port $PORT, DB=run$i.db copy) ############"
  OPPINTEL_DB="$RUN_DIR/run$i.db" SECRET_KEY=dev PORT=$PORT HOST=127.0.0.1 \
    PYTHONPATH="vendor/python:src" python -m gunicorn --workers 1 \
    --bind 127.0.0.1:$PORT oppintel.app.wsgi:application > "$RUN_DIR/run$i.log" 2>&1 &
  pid=$!
  # wait for readiness
  for _ in $(seq 1 40); do
    curl -sf "http://127.0.0.1:$PORT/healthz" >/dev/null 2>&1 && break
    sleep 0.25
  done
  echo "--- /healthz ---"
  curl -s "http://127.0.0.1:$PORT/healthz" | python -m json.tool | head -8
  echo "--- / headline numbers ---"
  curl -s "http://127.0.0.1:$PORT/" | grep -oiE '[0-9,]+ (commercial|projects|records|jurisdictions)[^<]*' | head -8
  echo "--- /api/statistics key figures ---"
  curl -s "http://127.0.0.1:$PORT/api/statistics" | python -c "import sys,json; d=json.load(sys.stdin)['statistics']; print({k:d[k] for k in ['public_projects','permit_records','linked_permits','active_jurisdictions','projects_total']})"
  echo "--- /trends headline ---"
  curl -s "http://127.0.0.1:$PORT/trends" | grep -oiE 'Projects with [^<]*|[0-9,]+ (projects|permit records|detected changes)' | head -6
  kill $pid 2>/dev/null; wait $pid 2>/dev/null
  echo
done
