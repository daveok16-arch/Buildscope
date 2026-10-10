"""J2d — peak RSS during ingest + assemble of the largest seed available offline.

Replays the shipped 200,000-row Fort Worth capture (no network) into a scratch DB and assembles,
reporting peak RSS via resource.getrusage. READ-ONLY against data/ except the scratch copy.

    PYTHONPATH="vendor/python:src" python audit/scripts/j2_rss.py
"""
from __future__ import annotations

import resource
import shutil
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CAPTURE = REPO / "data" / "raw" / "fort_worth_permits_20261010T172220.jsonl"


def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="j2rss_"))
    db_path = tmp / "oppintel.db"
    shutil.copyfile(REPO / "data" / "oppintel.db", db_path)
    print(f"scratch db: {db_path}")

    from oppintel.connectors.fort_worth_permits import FortWorthPermitsConnector
    from oppintel.db import Database
    from oppintel.pipeline import Pipeline

    db = Database(str(db_path))
    db.init_schema()
    db.init_app_schema()
    pipe = Pipeline(db)

    orig_land = FortWorthPermitsConnector.landing_path
    orig_fetch = FortWorthPermitsConnector.fetch_raw
    FortWorthPermitsConnector.landing_path = lambda self: CAPTURE
    FortWorthPermitsConnector.fetch_raw = lambda self, since=None, batch_size=None: iter(())
    try:
        t = time.time()
        r = pipe.ingest_source("fort_worth_permits", max_pages=None)
        print(f"ingest: status={r.status} rows_landed={r.rows_landed} permits={r.permits_created} "
              f"({time.time()-t:.1f}s)")
        t = time.time()
        a = pipe.assemble_and_classify()
        db.conn.commit()
        print(f"assemble: projects_written={a.projects_written} ({time.time()-t:.1f}s)")
    finally:
        FortWorthPermitsConnector.landing_path = orig_land
        FortWorthPermitsConnector.fetch_raw = orig_fetch

    permits = db.conn.execute("SELECT COUNT(*) FROM permit").fetchone()[0]
    projects = db.conn.execute("SELECT COUNT(*) FROM project").fetchone()[0]
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    print(f"\npermits={permits:,}  projects={projects:,}")
    print(f"peak RSS = {peak:,} KiB = {peak/1024:,.1f} MB")
    db.close()
    print(f"temp dir: {tmp}")


if __name__ == "__main__":
    main()
