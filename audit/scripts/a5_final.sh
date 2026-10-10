#!/usr/bin/env bash
# A5: start the app from a fresh copy of a populated DB in 3 separate processes on the wp1 branch.
set -u
cd /workspace/project/Buildscope
export PYTHONPATH="vendor/python:src" SECRET_KEY=dev RENDER=true
for n in 1 2 3; do
  dir=/tmp/a5run$n
  rm -rf $dir; mkdir -p $dir
  cp /tmp/populated.db $dir/oppintel.db
  export OPPINTEL_DB=$dir/oppintel.db OPPINTEL_DATA_DIR=$dir PORT=$((13060+n))
  python -m gunicorn --workers 1 --bind 127.0.0.1:$PORT oppintel.app.wsgi:application >$dir/g.log 2>&1 &
  pid=$!
  for i in $(seq 1 40); do curl -sf http://127.0.0.1:$PORT/healthz >/dev/null 2>&1 && break; sleep 0.5; done
  echo "--- run $n (port $PORT) ---"
  echo "/api/statistics: $(curl -s http://127.0.0.1:$PORT/api/statistics)"
  echo "/healthz:        $(curl -s http://127.0.0.1:$PORT/healthz | python -c 'import sys,json;d=json.load(sys.stdin);print({k:d.get(k) for k in ["status","projects","permits"]})')"
  kill $pid 2>/dev/null; wait $pid 2>/dev/null
done
