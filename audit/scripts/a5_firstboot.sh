#!/usr/bin/env bash
# A5 first-boot: empty mounted disk must start (200, status "empty") and self-seed.
set -u
cd /workspace/project/Buildscope
export SECRET_KEY=dev RENDER=true PYTHONPATH="vendor/python:src"

echo "===== FIRST BOOT, EMPTY DISK ====="
rm -rf /tmp/a5empty; mkdir -p /tmp/a5empty
export OPPINTEL_DATA_DIR=/tmp/a5empty OPPINTEL_DB=/tmp/a5empty/oppintel.db PORT=13070
python -m gunicorn --workers 1 --bind 127.0.0.1:$PORT oppintel.app.wsgi:application >/tmp/a5empty/g.log 2>&1 &
pid=$!
for i in $(seq 1 60); do curl -sf http://127.0.0.1:$PORT/healthz >/dev/null 2>&1 && break; sleep 0.5; done
echo "HTTP status: $(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:$PORT/healthz)"
echo "healthz: $(curl -s http://127.0.0.1:$PORT/healthz | python -c 'import sys,json;d=json.load(sys.stdin);print({k:d.get(k) for k in ["status","projects","permits","database"]})')"
echo "home project count: $(curl -s http://127.0.0.1:$PORT/ | grep -o 'evidence-stat-num">[^<]*' | head -1)"
echo "db created: $(ls -la /tmp/a5empty/oppintel.db | awk '{print $5}') bytes"
echo "refresh loop running (automate): $(ls /tmp/a5empty/*.json /tmp/a5empty/*.log 2>/dev/null | tr '\n' ' ')"
kill $pid 2>/dev/null; wait $pid 2>/dev/null
echo "after stop, db size: $(ls -la /tmp/a5empty/oppintel.db | awk '{print $5}') bytes"

echo
echo "===== UNATTACHED PATH -> named error ====="
export OPPINTEL_DATA_DIR=/proc/a5-none OPPINTEL_DB=/proc/a5-none/oppintel.db PORT=13071
bash ops/start.sh >/tmp/a5empty/err.log 2>&1; echo "exit=$?"
cat /tmp/a5empty/err.log
