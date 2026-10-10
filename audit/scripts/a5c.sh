#!/usr/bin/env bash
# A5c: what happens on first boot when the disk is empty vs already populated.
set -u
cd /workspace/project/Buildscope

echo "===== CASE 1: mounted disk EMPTY (fresh /var/data) ====="
rm -rf /tmp/a5disk; mkdir -p /tmp/a5disk
export PORT=13050 OPPINTEL_DATA_DIR=/tmp/a5disk RENDER=true SECRET_KEY=dev
export PYTHONPATH="vendor/python:src"
python -m gunicorn --workers 1 \
  --bind 127.0.0.1:$PORT oppintel.app.wsgi:application > /tmp/a5disk/c1.log 2>&1 &
pid=$!
for _ in $(seq 1 60); do curl -sf "http://127.0.0.1:$PORT/healthz" >/dev/null 2>&1 && break; sleep 0.5; done
echo "healthz: $(curl -s http://127.0.0.1:$PORT/healthz | python -c 'import sys,json;d=json.load(sys.stdin);print({k:d.get(k) for k in ["status","projects","permits"]})')"
echo "db file created: $(ls -la /tmp/a5disk/*.db 2>/dev/null | awk '{print $5, $NF}')"
kill $pid 2>/dev/null; wait $pid 2>/dev/null

echo
echo "===== CASE 2: mounted disk POPULATED ====="
cp /tmp/populated.db /tmp/a5disk/oppintel.db
export PORT=13051
python -m gunicorn --workers 1 \
  --bind 127.0.0.1:$PORT oppintel.app.wsgi:application > /tmp/a5disk/c2.log 2>&1 &
pid=$!
for _ in $(seq 1 60); do curl -sf "http://127.0.0.1:$PORT/healthz" >/dev/null 2>&1 && break; sleep 0.5; done
echo "healthz: $(curl -s http://127.0.0.1:$PORT/healthz | python -c 'import sys,json;d=json.load(sys.stdin);print({k:d.get(k) for k in ["status","projects","permits"]})')"
kill $pid 2>/dev/null; wait $pid 2>/dev/null

echo
echo "===== CASE 3: disk path configured but not attached (should fail named) ====="
export PORT=13052 OPPINTEL_DATA_DIR=/proc/a5-nope-unattached OPPINTEL_DB=/proc/a5-nope-unattached/oppintel.db
bash ops/start.sh > /tmp/a5disk/c3.log 2>&1
echo "exit=$?"
echo "--- output ---"; cat /tmp/a5disk/c3.log | head -6
