#!/usr/bin/env bash
# Write a dated, consistent snapshot of the BuildScope SQLite database onto the disk.
#
# SQLite's online backup API is used (via `oppintel backup`), so the snapshot is consistent even
# while the refresh loop is writing. The WAL is checkpointed into the snapshot first.
#
# Backups land in $OPPINTEL_DATA_DIR/backups/ (default: <checkout>/data/backups) and the newest
# $KEEP files are retained. Nothing outside that directory is touched.
#
# Schedule it however the host schedules things. This container has no cron or systemd, so the
# usual call is a shell loop, or a line in the platform's job runner. Example (every 6 hours):
#
#     while true; do ops/backup.sh; sleep 21600; done
#
# On Render there is no cron either; run it from the shell or add it to the refresh loop's host
# tooling. See docs/deployment.md for the restore procedure.
set -u

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

export PYTHONPATH="src${PYTHONPATH:+:$PYTHONPATH}"
# A local checkout vendors its dependencies under vendor/python; a host installs them from
# requirements.txt. Add the vendored path only when it exists, so the same script runs both ways.
if [ -d "$REPO_ROOT/vendor/python" ]; then
    export PYTHONPATH="$REPO_ROOT/vendor/python:$PYTHONPATH"
fi
export OPPINTEL_DATA_DIR="${OPPINTEL_DATA_DIR:-$REPO_ROOT/data}"
export OPPINTEL_DB="${OPPINTEL_DB:-$OPPINTEL_DATA_DIR/oppintel.db}"

KEEP="${KEEP:-7}"
BACKUP_DIR="${BACKUP_DIR:-$OPPINTEL_DATA_DIR/backups}"

if [ ! -f "$OPPINTEL_DB" ]; then
    echo "error: database not found at $OPPINTEL_DB" >&2
    echo "  Set OPPINTEL_DB or run an ingest/seed first." >&2
    exit 1
fi

python -m oppintel.cli --db "$OPPINTEL_DB" backup --dir "$BACKUP_DIR" --keep "$KEEP"
