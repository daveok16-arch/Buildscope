# Getting started

Install BuildScope, create a database, run the application, and run the tests.

## Requirements

- Python 3.10 or newer (the repository is developed on 3.13; see `.python-version`).
- No database server. BuildScope uses SQLite with the FTS5 extension, which ships with the
  standard Python `sqlite3` module.

## Install

```bash
git clone https://github.com/daveok16-arch/Buildscope.git && cd Buildscope
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

To run the tests as well, install `pytest`:

```bash
pip install pytest
```

## Configure

The application reads configuration from the environment. For local development only two
variables matter:

```bash
export PYTHONPATH=src
export SECRET_KEY=dev-only-not-for-production
```

`SECRET_KEY` signs sessions. Without it a random key is generated per process, so sessions do not
survive a restart. See `.env.example` for the full list and [deployment](deployment.md) for
production values.

## Create the database

BuildScope has two schemas: the intelligence schema (source records, permits, assembled
projects) and the application schema (accounts, saves, watches, pipeline). Both are created
idempotently.

```bash
python -m oppintel.cli initdb                          # intelligence schema
flask --app oppintel.app.wsgi init-app                 # application tables + plan catalogue
```

`init-app` is additive: it creates missing tables, migrates older application schema forward, and
seeds the plan catalogue. It never alters or drops an intelligence table.

## Run the application

```bash
python -m oppintel.app.wsgi            # http://127.0.0.1:5000
```

Or with the Flask CLI:

```bash
flask --app oppintel.app.wsgi run --debug
```

A fresh database is empty, so the homepage shows a real empty state. Populate it next.

## Populate the database

```bash
export PYTHONPATH=src

# List the configured sources and their verified coverage.
python -m oppintel.cli sources

# Ingest permit records (omit --source to fetch every enabled source).
python -m oppintel.cli ingest --source dallas_accela_permits --since 2026-01-01 --max-pages 300

# Assemble permits into projects and classify them.
python -m oppintel.cli assemble

# Rebuild the web search index and generate missing slugs.
flask --app oppintel.app.wsgi build-search-index
```

Ingestion is idempotent: raw records are keyed by content hash, so re-running writes nothing when
nothing changed. A partial ingestion is safe — the pipeline commits every 250 permits.

## Reports

Reports are generated from live data and are deterministic for a given database state.

```bash
python -m oppintel.cli report                                   # coverage, quality, validation
python -m oppintel.cli brief --limit 5 --prepared-for "Acme Mechanical"
```

## Run the tests

```bash
PYTHONPATH=src python -m pytest tests/ -q
```

See [testing](testing.md) for what the suite covers.

## Next steps

- [Architecture](architecture.md) — how the pieces fit together.
- [Data sources](data-sources.md) — what is ingested, and its limits.
- [Deployment](deployment.md) — run it for real.
- [Operations](operations.md) — keep the data fresh automatically.
