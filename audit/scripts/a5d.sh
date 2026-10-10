#!/usr/bin/env bash
set -u
cd /workspace/project/Buildscope
rm -rf /tmp/a5d; mkdir -p /tmp/a5d
export OPPINTEL_DATA_DIR=/tmp/a5d OPPINTEL_DB=/tmp/a5d/oppintel.db RENDER=true SECRET_KEY=dev
export PYTHONPATH="vendor/python:src"
python -m gunicorn --workers 1 --bind 127.0.0.1:13055 oppintel.app.wsgi:application >/tmp/a5d/g.log 2>&1 &
pid=$!
for i in $(seq 1 40); do curl -sf http://127.0.0.1:13055/healthz >/dev/null 2>&1 && break; sleep 0.5; done
echo "healthz: $(curl -s http://127.0.0.1:13055/healthz)"
echo "files:"; ls -la /tmp/a5d/
kill $pid 2>/dev/null; wait $pid 2>/dev/null
