# BuildScope — portable container image.
#
# The app is a Python WSGI service (Flask + gunicorn) with a SQLite database and an in-process
# refresh loop. This image runs it the same way `ops/start.sh` does anywhere: one process that
# supervises the web server and runs the online-search pipeline on a background thread.
#
# Build:
#   docker build -t buildscope .
#
# Run (writes state to the host's ./data so the dataset survives the container):
#   docker run -p 8000:8000 \
#     -e SECRET_KEY="$(python -c 'import secrets;print(secrets.token_hex(32))')" \
#     -e BASE_URL="http://localhost:8000" \
#     -v "$PWD/data:/app/data" \
#     buildscope
#
# To keep the dataset across restarts, mount a volume at /app/data (or set OPPINTEL_DATA_DIR /
# OPPINTEL_DB to a mounted path). Without a mount, the database lives in the container layer and
# is lost when the container is replaced.
#
# See audit/RELEASE_NOTES_PORTABLE.md for the full environment-variable reference.

FROM python:3.13-slim

# `ops/start.sh` uses bash; the slim image ships dash as /bin/sh, so install bash explicitly.
RUN apt-get update \
    && apt-get install -y --no-install-recommends bash ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Only the requirements first, so a source edit does not invalidate the dependency layer.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# The application source, configuration, operations scripts and the stub frontend entrypoints.
COPY src/ ./src/
COPY config/ ./config/
COPY ops/ ./ops/
COPY run_server.py server.ts package.json index.html metadata.json ./

# The import root, matching the Render blueprint and the local run notes.
ENV PYTHONPATH=src \
    HOST=0.0.0.0 \
    PORT=8000 \
    OPPINTEL_DATA_DIR=/app/data

# The container host path: ops/start.sh execs the supervisor in the foreground when FOREGROUND=1
# (or RENDER is set), and binds $PORT. The supervisor never backgrounds, so the container stays
# in the foreground and the platform sees a live process.
ENV FOREGROUND=1

# Run as an unprivileged user; /app/data must be writable by it.
RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /app/data \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# ops/start.sh runs `initdb` and `init-app` (both idempotent), then the supervisor.
CMD ["bash", "ops/start.sh"]
