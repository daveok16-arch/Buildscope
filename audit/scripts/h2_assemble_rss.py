"""H2c — measure peak RSS of the `assemble` stage. READ-ONLY on the DB it is given.

`assemble` loads every permit into memory (`load_permits_for_assembly`), so peak RSS scales with
the permit count. This measures it on the supplied DB and reports the arithmetic to extrapolate
to a full Fort Worth seed.

    PYTHONPATH=vendor/python:src python audit/scripts/h2_assemble_rss.py [db-path]
"""
from __future__ import annotations

import os
import resource
import sys
import tempfile
import time
from pathlib import Path


def main() -> int:
    # Operate on a private copy so the caller's database is never written.
    src = sys.argv[1] if len(sys.argv) > 1 else "data/oppintel.db"
    if not os.path.exists(src):
        print(f"no database at {src}")
        return 1
    work = Path(tempfile.mkdtemp()) / "assemble.db"
    import shutil
    shutil.copyfile(src, work)

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
    from oppintel.db import Database
    from oppintel.pipeline import Pipeline

    with Database(work) as db:
        base = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0
        t_load = time.time()
        loaded = db.load_permits_for_assembly()
        permits = len(loaded)
        after_load = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0
        t0 = time.time()
        pipeline = Pipeline(db)
        pipeline._register_sources()
        report = pipeline.assemble_and_classify()
        elapsed = time.time() - t0
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0

    print(f"db                     : {src}")
    print(f"permits loaded         : {permits:,}")
    print(f"baseline RSS (interp.) : {base:,.1f} MB")
    print(f"RSS after permit load  : {after_load:,.1f} MB  (delta {after_load-base:,.1f} MB)")
    print(f"peak RSS               : {peak:,.1f} MB")
    print(f"assemble time          : {elapsed:,.1f}s (load {time.time()-t_load:,.1f}s incl.)")
    print(f"projects               : {report.as_dict().get('projects', '?')}")

    if permits:
        load_per_1000 = (after_load - base) / (permits / 1000.0)
        peak_per_1000 = (peak - base) / (permits / 1000.0)
        full = 195_161  # Fort Worth alone (D6)
        print(f"\nRSS per 1,000 permits (load delta) = {after_load-base:,.1f} / "
              f"{permits/1000:.2f} = {load_per_1000:,.2f} MB")
        print(f"RSS per 1,000 permits (peak delta) = {peak-base:,.1f} / "
              f"{permits/1000:.2f} = {peak_per_1000:,.2f} MB")
        print(f"extrapolated to {full:,} permits:")
        print(f"  load-delta method = {base + load_per_1000*full/1000:,.0f} MB")
        print(f"  peak-delta method = {base + peak_per_1000*full/1000:,.0f} MB")
        print("  a 512 MB host is NOT safe for a full in-process seed if either figure")
        print("  exceeds ~350 MB (leaves headroom for Python + the web worker).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
