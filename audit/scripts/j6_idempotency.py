"""J6 — idempotency proof.

On a *scratch copy* of the database:
  (a) assemble three times with no underlying data change; `project_change` and
      `project_state_snapshot` row counts must not grow after the first pass.
  (b) ingest a source, then replay the SAME source again (same records) and confirm permit,
      project and raw_record counts do not duplicate.

READ-ONLY against `data/oppintel.db` (copied first).

    PYTHONPATH="vendor/python:src" python audit/scripts/j6_idempotency.py
"""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SRC_DB = REPO / "data" / "oppintel.db"


def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="j6_"))
    clone = tmp / "oppintel.db"
    shutil.copyfile(SRC_DB, clone)
    print(f"scratch clone : {clone}\n")

    from oppintel.db import Database
    from oppintel.pipeline import Pipeline

    db = Database(str(clone))
    db.init_schema()
    db.init_app_schema()
    pipe = Pipeline(db)

    def counts():
        c = db.conn
        return {
            "permit": c.execute("SELECT COUNT(*) FROM permit").fetchone()[0],
            "raw_record": c.execute("SELECT COUNT(*) FROM raw_record").fetchone()[0],
            "project": c.execute("SELECT COUNT(*) FROM project").fetchone()[0],
            "project_change": c.execute("SELECT COUNT(*) FROM project_change").fetchone()[0],
            "project_state_snapshot": c.execute(
                "SELECT COUNT(*) FROM project_state_snapshot").fetchone()[0],
        }

    # --- (a) assemble three times ------------------------------------------
    print("(a) assemble idempotency")
    before = counts()
    print("    before any pass      :", before)
    for i in (1, 2, 3):
        pipe.assemble_and_classify()
        db.conn.commit()
        print(f"    after pass {i}          :", counts())

    a1 = counts()

    # --- (b) ingest, then replay the same source ---------------------------
    print("\n(b) ingest idempotency (replay the same source twice)")
    from oppintel.connectors.fort_worth_permits import FortWorthPermitsConnector

    src_raw = REPO / "data" / "raw"
    latest = sorted(src_raw.glob("fort_worth_permits_*.jsonl"))[-1]
    replay = tmp / "replay.jsonl"
    # Cap the replay so this stays fast; take the newest 400 lines.
    lines = latest.read_text(encoding="utf-8").splitlines()
    replay.write_text("\n".join(lines[-400:]) + "\n", encoding="utf-8")
    print(f"    replaying {latest.name} (last 400 of {len(lines)} records)")

    orig_land = FortWorthPermitsConnector.landing_path
    orig_fetch = FortWorthPermitsConnector.fetch_raw
    FortWorthPermitsConnector.landing_path = lambda self: replay
    FortWorthPermitsConnector.fetch_raw = lambda self, since=None, batch_size=None: iter(())
    try:
        before_i = counts()
        print("    before replay        :", before_i)
        for i in (1, 2):
            r = pipe.ingest_source("fort_worth_permits", max_pages=None)
            pipe.assemble_and_classify()
            db.conn.commit()
            print(f"    after replay {i}       :", counts(), f"(status={r.status})")
    finally:
        FortWorthPermitsConnector.landing_path = orig_land

    print(f"\n(a) assemble: project_change {before['project_change']} -> {a1['project_change']} "
          f"over 3 passes (expect no growth after pass 1)")
    db.close()
    print(f"temp dir (safe to delete): {tmp}")


if __name__ == "__main__":
    main()
