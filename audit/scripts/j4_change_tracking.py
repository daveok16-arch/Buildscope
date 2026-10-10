"""J4 — local change-tracking proof.

Runs assemble twice on a *scratch copy* of the shipped database. Between the passes, one
project's mechanical tier is gained and its stored scope text changes. The second assemble diffs
the freshly-assembled project against the snapshot the first pass wrote and must record the
differences as `project_change` rows with `change_kind <> 'new_project'`.

READ-ONLY against `data/oppintel.db` (copied first). Writes only to the temp copy.

    PYTHONPATH="vendor/python:src" python audit/scripts/j4_change_tracking.py
"""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SRC_DB = REPO / "data" / "oppintel.db"


def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="j4_"))
    clone = tmp / "oppintel.db"
    shutil.copyfile(SRC_DB, clone)
    print(f"scratch clone : {clone}")

    from oppintel.db import Database
    from oppintel.pipeline import Pipeline

    db = Database(str(clone))
    db.init_schema()
    db.init_app_schema()
    pipe = Pipeline(db)

    def kinds():
        return dict(db.conn.execute(
            "SELECT change_kind, COUNT(*) FROM project_change GROUP BY change_kind"
        ).fetchall())

    # A project whose evidence is derived from its work description, so removing the mechanical
    # scope text from the underlying permit drops its tier — a real, reversible field change.
    row = db.conn.execute(
        "SELECT p.id, p.project_name, p.address, p.mechanical_evidence_tier, "
        "       pe.natural_key, substr(pe.work_description,1,60) "
        "FROM project p JOIN permit pe ON pe.address = p.address "
        "WHERE p.mechanical_evidence_tier = 2 AND pe.work_description IS NOT NULL "
        "ORDER BY p.id LIMIT 1"
    ).fetchone()
    assert row, "no Tier-2 project with a work description"
    pid, name, addr, tier_before, permit_key, desc_before = row
    print(f"target project #{pid} {str(name)[:40]!r} ({addr}) tier before = {tier_before}")
    print(f"  permit {permit_key} desc: {desc_before!r}")

    # --- pass 1: baseline assemble (writes the snapshot the next pass diffs) --
    pipe.assemble_and_classify()
    db.conn.commit()
    print("pass1 change kinds:", kinds())

    # --- change the underlying permit, then assemble again ------------------
    db.conn.execute(
        "UPDATE permit SET work_description = ? WHERE natural_key = ?",
        ("interior finish out; scope text amended", permit_key),
    )
    db.conn.commit()
    pipe.assemble_and_classify()
    db.conn.commit()
    print("pass2 change kinds:", kinds())

    real = db.conn.execute(
        "SELECT COUNT(*) FROM project_change WHERE change_kind <> 'new_project'"
    ).fetchone()[0]
    tier_after = db.conn.execute(
        "SELECT mechanical_evidence_tier FROM project WHERE id = ?", (pid,)
    ).fetchone()[0]
    print(f"\nproject #{pid} tier after = {tier_after}")
    print(f"project_change rows with change_kind <> 'new_project' = {real}\n")

    rows = db.conn.execute(
        "SELECT id, project_id, change_kind, field_name, previous_value, current_value, summary "
        "FROM project_change WHERE change_kind <> 'new_project' ORDER BY id LIMIT 12"
    ).fetchall()
    for r in rows:
        print(f"  #{r[0]} project {r[1]} | {r[2]}/{r[3]} : {r[4]!r} -> {r[5]!r}")
        print(f"      {r[6]}")

    db.close()
    print(f"\ntemp dir (safe to delete): {tmp}")


if __name__ == "__main__":
    main()
