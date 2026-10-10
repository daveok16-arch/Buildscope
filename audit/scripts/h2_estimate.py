"""H2 — disk, memory and backup estimates. READ-ONLY.

Measures the real per-row byte cost on the audit DB (via dbstat), extrapolates to a full seed,
and prints the arithmetic. Nothing is written: the DB is opened ``mode=ro``.

    PYTHONPATH=vendor/python:src python audit/scripts/h2_estimate.py [db-path]
"""
from __future__ import annotations

import os
import sqlite3
import sys

FULL_SEED_PERMITS = 195_161 + 12_250 + 19_963   # Fort Worth + Collin + Dallas (D6 measurement)
REFRESH_MAX_PAGES = 3
PAGE_SIZE = 1000            # config/sources.yaml page_size
REFRESH_HOURS = 6


def main() -> int:
    path = sys.argv[1] if len(sys.argv) > 1 else "/tmp/h1_readonly.db"
    if not os.path.exists(path):
        print(f"no database at {path}")
        return 1
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)

    def one(sql, params=()):
        return conn.execute(sql, params).fetchone()[0]

    db_bytes = os.path.getsize(path)
    projects = one("SELECT COUNT(*) FROM project")
    permits = one("SELECT COUNT(*) FROM permit")
    raw = one("SELECT COUNT(*) FROM raw_record")

    live = one("SELECT COALESCE(SUM(pgsize),0) FROM dbstat")
    fts = one("SELECT COALESCE(SUM(pgsize),0) FROM dbstat WHERE name LIKE '%search%'")

    print(f"db={path}")
    print(f"file bytes            : {db_bytes:>14,}")
    print(f"live bytes (dbstat)   : {live:>14,}")
    print(f"free/unused (file-live): {db_bytes - live:>13,}")
    print(f"projects={projects:,}  permits={permits:,}  raw_record={raw:,}")

    bpp = db_bytes / permits
    bppr = db_bytes / projects
    print(f"\nbytes / permit  = {db_bytes:,} / {permits:,} = {bpp:,.1f}")
    print(f"bytes / project = {db_bytes:,} / {projects:,} = {bppr:,.1f}")

    # ---- full-seed extrapolation -------------------------------------------
    print("\n=== full-seed DB size (linear on bytes/permit) ===")
    print(f"full seed permits (D6): {FULL_SEED_PERMITS:,}")
    est = FULL_SEED_PERMITS * bpp
    print(f"  {FULL_SEED_PERMITS:,} x {bpp:,.1f} B = {est/1e6:,.1f} MB  (linear, pessimistic)")
    # Projects scale roughly with permits too; raw_record is 1:1 with permits landed.
    print("  components (per-row measured on the audit DB):")
    for table, per in (("permit", 414), ("raw_record", 1012), ("evidence", 271),
                       ("evidence_history", 353), ("project_event", 344)):
        n = FULL_SEED_PERMITS if table in ("permit", "raw_record") else FULL_SEED_PERMITS
        print(f"    {table:18} {n:,} rows x {per} B = {n*per/1e6:,.1f} MB (upper bound: 1:1)")
    print("  NOTE: projects are assembled from permits, so evidence/event rows are far fewer")
    print("        than one-per-permit; the linear figure above is the conservative ceiling.")

    # ---- FTS ---------------------------------------------------------------
    print("\n=== FTS index ===")
    print(f"audit FTS bytes = {fts:,} over {projects:,} projects = {fts/projects:,.1f} B/project")
    print(f"  full-seed FTS (if project count scales to ~{FULL_SEED_PERMITS//3:,}): "
          f"{FULL_SEED_PERMITS//3 * fts/projects/1e6:,.1f} MB")

    # ---- raw jsonl ---------------------------------------------------------
    print("\n=== data/raw/*.jsonl ===")
    raw_dir = os.path.join(os.path.dirname(path) or ".", "..", "data", "raw")
    # measured bytes/line from the shipped captures
    measured = {
        "fort_worth_permits": (200_000, 162_604_305),
        "collin_cad_permits": (12_250, 13_772_274),
        "dallas_accela_permits": (19_963, 20_635_148),
    }
    total_seed_bytes = 0
    for name, (lines, size) in measured.items():
        total_seed_bytes += size
        print(f"  {name:22} {lines:>8,} lines  {size:>13,} B  ({size/lines:,.0f} B/line)")
    print(f"  FULL SEED raw jsonl total = {total_seed_bytes:,} B = {total_seed_bytes/1e6:,.1f} MB")

    refresh_rows = REFRESH_MAX_PAGES * PAGE_SIZE
    avg_bpl = sum(s for _, s in measured.values()) / sum(l for l, _ in measured.values())
    refresh_bytes = refresh_rows * avg_bpl
    print(f"  one 6-hourly refresh: {REFRESH_MAX_PAGES} pages x {PAGE_SIZE} rows = {refresh_rows:,} rows/source")
    print(f"    x {len(measured)} sources x {avg_bpl:,.0f} B/line = {refresh_bytes*len(measured)/1e6:,.1f} MB/refresh")
    per_day = refresh_bytes * len(measured) * (24 / REFRESH_HOURS)
    print(f"  per day   = {per_day/1e6:,.1f} MB")
    print(f"  per week  = {per_day*7/1e6:,.1f} MB")
    print(f"  per month = {per_day*30/1e6:,.1f} MB")
    print(f"  RAW ARCHIVE after 1 month (no retention) = {total_seed_bytes/1e6 + per_day*30/1e6:,.1f} MB")

    # ---- WAL ceiling -------------------------------------------------------
    print("\n=== WAL ceiling ===")
    print("  wal_autocheckpoint default = 1000 pages = 4 MB (SQLite default; db.py:719 leaves it)")
    print("  the pipeline commits every 250 permits (docs/deployment.md); a long ingest")
    print("  checkpoints at 4 MB, so the WAL is bounded near 4 MB, not the whole ingest.")

    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
