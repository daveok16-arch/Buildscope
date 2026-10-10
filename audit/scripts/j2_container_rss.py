"""J2.1 — whole-container RSS: gunicorn (2 workers x 4 threads) + assemble + 20 concurrent requests.

Read-only w.r.t. the shipped database: everything runs against a scratch copy in a temp dir.

Reports, from /proc/<pid>/status VmRSS, sampled while a workload runs:
  * gunicorn idle (master + both workers, 4 threads each)
  * gunicorn while 20 concurrent clients hit /opportunities
  * the assemble child process on its own, and the combined peak (gunicorn + assemble)

Sampling `VmRSS` sums shared pages, so the combined figure is an upper bound; the honest number
is between the max single process and the sum. PSS (`/proc/<pid>/smaps_rollup`) is used when
available (needs no privilege on Linux 4.14+) and is reported alongside.

    PYTHONPATH=vendor/python:src python audit/scripts/j2_container_rss.py
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
PORT = int(os.environ.get("J2_PORT", "12003"))


def rss_kb(pid: int) -> int:
    try:
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1])
    except (FileNotFoundError, ProcessLookupError, ChildProcessError):
        pass
    return 0


def pss_kb(pid: int) -> int:
    try:
        for line in Path(f"/proc/{pid}/smaps_rollup").read_text().splitlines():
            if line.startswith("Pss:"):
                return int(line.split()[1])
    except (FileNotFoundError, ProcessLookupError, ChildProcessError):
        pass
    return 0


def gunicorn_pids(master: int) -> list[int]:
    pids = [master]
    try:
        out = subprocess.run(["pgrep", "-P", str(master)], capture_output=True, text=True).stdout
        pids += [int(p) for p in out.split()]
    except Exception:
        pass
    return [p for p in pids if Path(f"/proc/{p}").exists()]


def main() -> int:
    scratch = Path(tempfile.mkdtemp(prefix="j2container_"))
    db = scratch / "oppintel.db"
    shutil.copy(ROOT / "data" / "oppintel.db", db)
    print(f"scratch db: {db}  ({db.stat().st_size/1e6:.1f} MB)")

    env = dict(os.environ)
    env["PYTHONPATH"] = f"{ROOT/'vendor/python'}:{SRC}"
    env.update(SECRET_KEY="devonly", OPPINTEL_DB=str(db),
               BASE_URL=f"http://127.0.0.1:{PORT}", GZIP_ENABLED="1")

    # Seed the largest offline capture first, in this (parent) process, so the web workers below
    # serve a full-size database and the assemble is measured against realistic cardinality.
    seed = subprocess.run(
        [sys.executable, "audit/scripts/seed_from_capture.py", "--db", str(db)],
        cwd=str(ROOT), env=env, capture_output=True, text=True,
    )
    print("seed:", (seed.stdout or seed.stderr).strip().splitlines()[-1] if (seed.stdout or seed.stderr) else "")

    web = subprocess.Popen(
        [sys.executable, "-m", "gunicorn", "--workers", "2", "--threads", "4",
         "--bind", f"127.0.0.1:{PORT}", "--timeout", "120",
         "oppintel.app.wsgi:application"],
        cwd=str(ROOT), env=env,
        stdout=open(scratch / "web.log", "w"), stderr=subprocess.STDOUT,
    )
    time.sleep(7)
    import urllib.request
    urllib.request.urlopen(f"http://127.0.0.1:{PORT}/healthz", timeout=10).read()

    def snapshot(label: str) -> dict:
        pids = gunicorn_pids(web.pid)
        r = {p: rss_kb(p) for p in pids}
        p = {p: pss_kb(p) for p in pids}
        total_rss = sum(r.values())
        total_pss = sum(p.values())
        print(f"  {label:34} procs={len(pids)} sumRSS={total_rss/1024:.1f} MB "
              f"sumPSS={total_pss/1024:.1f} MB  per-proc RSS={[f'{v/1024:.0f}MB' for v in r.values()]}")
        return {"rss": total_rss, "pss": total_pss}

    print("\n[1] gunicorn idle (2 workers x 4 threads):")
    idle = snapshot("idle")

    print("\n[2] gunicorn serving 20 concurrent clients (no assemble):")
    peak_web = {"rss": 0, "pss": 0}
    stop = threading.Event()

    def sampler():
        while not stop.is_set():
            pids = gunicorn_pids(web.pid)
            peak_web["rss"] = max(peak_web["rss"], sum(rss_kb(p) for p in pids))
            peak_web["pss"] = max(peak_web["pss"], sum(pss_kb(p) for p in pids))
            time.sleep(0.05)

    def client():
        for _ in range(8):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{PORT}/opportunities", timeout=30).read()
            except Exception:
                pass

    st = threading.Thread(target=sampler); st.start()
    cs = [threading.Thread(target=client) for _ in range(20)]
    t0 = time.time()
    for c in cs: c.start()
    for c in cs: c.join()
    stop.set(); st.join()
    print(f"  peak sumRSS={peak_web['rss']/1024:.1f} MB  sumPSS={peak_web['pss']/1024:.1f} MB "
          f"(20 clients x 8 requests in {time.time()-t0:.1f}s)")

    print("\n[3] assemble child alone, then gunicorn+assemble concurrently:")
    # A real assemble over the same database, launched as a child (as ops/automate.py does).
    asm = subprocess.Popen(
        [sys.executable, "-m", "oppintel.cli", "assemble", "--db", str(db)],
        cwd=str(ROOT), env=env,
        stdout=open(scratch / "assemble.log", "w"), stderr=subprocess.STDOUT,
    )
    peak_asm = 0
    peak_combo_rss = 0
    peak_combo_pss = 0
    stop2 = threading.Event()

    def sample2():
        nonlocal peak_asm, peak_combo_rss, peak_combo_pss
        while not stop2.is_set():
            a = rss_kb(asm.pid) if Path(f"/proc/{asm.pid}").exists() else 0
            peak_asm = max(peak_asm, a)
            pids = gunicorn_pids(web.pid)
            peak_combo_rss = max(peak_combo_rss, a + sum(rss_kb(p) for p in pids))
            peak_combo_pss = max(peak_combo_pss, pss_kb(asm.pid) + sum(pss_kb(p) for p in pids))
            time.sleep(0.05)

    st2 = threading.Thread(target=sample2); st2.start()
    cs2 = [threading.Thread(target=client) for _ in range(20)]
    for c in cs2: c.start()
    asm.wait()
    for c in cs2: c.join()
    stop2.set(); st2.join()

    print(f"  assemble alone peak RSS            = {peak_asm/1024:.1f} MB")
    print(f"  combined peak (gunicorn+assemble) sumRSS = {peak_combo_rss/1024:.1f} MB")
    print(f"  combined peak (gunicorn+assemble) sumPSS = {peak_combo_pss/1024:.1f} MB")

    web.terminate(); web.wait(timeout=15)
    print(f"\ncontainer peak (upper bound, sumRSS) = {max(idle['rss']+0, peak_combo_rss)/1024:.1f} MB")
    print(f"container peak (shared-aware, sumPSS) = {peak_combo_pss/1024:.1f} MB")
    print(f"scratch left at {scratch} (delete when done)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
