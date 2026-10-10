"""Seed a scratch database from the shipped 200k-row Fort Worth capture (offline, no network).

    PYTHONPATH=vendor/python:src python audit/scripts/seed_from_capture.py [--db PATH]

Used by j2_container_rss.py so the web workers serve a full-cardinality database. Writes only to
the --db path it is given (a scratch copy), never to data/oppintel.db.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    args = ap.parse_args()

    from oppintel.connectors.fort_worth_permits import FortWorthPermitsConnector
    from oppintel.db import Database
    from oppintel.pipeline import Pipeline

    capture = sorted((ROOT / "data" / "raw").glob("fort_worth_permits_*.jsonl"))[-1]
    os.environ["OPPINTEL_DB"] = args.db
    db = Database(args.db)
    db.init_schema()
    db.init_app_schema()
    orig_land, orig_fetch = (
        FortWorthPermitsConnector.landing_path,
        FortWorthPermitsConnector.fetch_raw,
    )
    FortWorthPermitsConnector.landing_path = lambda self: capture
    FortWorthPermitsConnector.fetch_raw = lambda self, since=None, batch_size=None: iter(())
    try:
        pipe = Pipeline(db)
        pipe.ingest_source("fort_worth_permits", max_pages=None)
        pipe.assemble_and_classify()
        db.conn.commit()
    finally:
        FortWorthPermitsConnector.landing_path = orig_land
        FortWorthPermitsConnector.fetch_raw = orig_fetch
    permits = db.conn.execute("SELECT COUNT(*) FROM permit").fetchone()[0]
    projects = db.conn.execute("SELECT COUNT(*) FROM project").fetchone()[0]
    print(f"seeded {args.db}: permits={permits:,} projects={projects:,}")
    db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
