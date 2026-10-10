#!/usr/bin/env bash
set -u
cd /workspace/project/Buildscope
export PYTHONPATH="vendor/python:src" SECRET_KEY=dev RENDER=true
for n in 1 2 3; do
  dir=/tmp/a5x$n
  rm -rf $dir; mkdir -p $dir
  cp /tmp/populated.db $dir/oppintel.db
  export OPPINTEL_DB=$dir/oppintel.db OPPINTEL_DATA_DIR=$dir PORT=$((13080+n))
  python -m gunicorn --workers 1 --bind 127.0.0.1:$PORT oppintel.app.wsgi:application >$dir/g.log 2>&1 &
  pid=$!
  for i in $(seq 1 40); do curl -sf http://127.0.0.1:$PORT/healthz >/dev/null 2>&1 && break; sleep 0.5; done
  echo "===== RUN $n (port $PORT) ====="
  echo -n "/ home stats: "; curl -s http://127.0.0.1:$PORT/ | grep -oE 'evidence-stat-num">[0-9,]+</span>[[:space:]]*<span class="evidence-stat-label">[^<]+' | sed -E 's/evidence-stat-num">//; s|</span>[[:space:]]*<span class="evidence-stat-label">| |' | tr '\n' '; '
  echo
  echo -n "/healthz: "; curl -s http://127.0.0.1:$PORT/healthz | python -c 'import sys,json;d=json.load(sys.stdin);print({k:d.get(k) for k in ["status","projects","permits"]})'
  echo -n "/api/statistics statistics: "; curl -s http://127.0.0.1:$PORT/api/statistics | python -c 'import sys,json;s=json.load(sys.stdin)["statistics"];print({k:s[k] for k in ["public_projects","projects_with_mechanical_evidence","permit_records","linked_permits","active_jurisdictions","projects_total"]})'
  echo -n "/trends cards: "; curl -s http://127.0.0.1:$PORT/trends | grep -oE '<span class="stat-value">[^<]+' | sed -E 's/<span class="stat-value">//' | tr '\n' ',' 
  echo
  kill $pid 2>/dev/null; wait $pid 2>/dev/null
done
