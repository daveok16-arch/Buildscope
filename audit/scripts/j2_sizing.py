"""J2 — disk, seed-scope and backup sizing. READ-ONLY.

Parses the shipped raw captures to get the real permit-date distribution and per-record payload
size, then projects DB / FTS / WAL / raw-archive sizes for three seed scopes (all history,
24 months, 12 months) and the retention-on 1-week / 1-month steady state. Prints all arithmetic.

    PYTHONPATH=vendor/python:src python audit/scripts/j2_sizing.py
"""
from __future__ import annotations

import gzip
import json
import os
import sqlite3
from collections import defaultdict
from datetime import date

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RAW = os.path.join(REPO, "data", "raw")
DB = "/tmp/h1_readonly.db"
TODAY = date(2026, 10, 9)

# Largest shipped capture per source, and the field carrying the permit date.
CAPTURES = {
    "fort_worth_permits": ("fort_worth_permits_20261010T172220.jsonl", "File_Date"),
    "collin_cad_permits": ("collin_cad_permits_20261009T225935.jsonl", "permitissueddate"),
    "dallas_accela_permits": ("dallas_accela_permits_20261009T225949.jsonl", "record_date"),
}
# All-history totals per source, from the D6 full-seed measurement / capture line counts.
ALL_HISTORY = {
    "fort_worth_permits": 200_000,
    "collin_cad_permits": 12_250,
    "dallas_accela_permits": 19_963,
}


def month_of(value: str) -> str | None:
    if not value:
        return None
    v = str(value)
    if v[:4].isdigit() and v[4:5] == "-":
        return v[:7]
    # MM/DD/YYYY (Accela)
    parts = v.split("/")
    if len(parts) == 3 and parts[2][:4].isdigit():
        return f"{parts[2][:4]}-{int(parts[0]):02d}"
    return None


def scan(source: str):
    """Return (rows_by_month, bytes_by_month, total_rows, total_bytes)."""
    name, field = CAPTURES[source]
    path = os.path.join(RAW, name)
    rows = defaultdict(int)
    byts = defaultdict(int)
    total_rows = total_bytes = 0
    with open(path, "rb") as fh:
        for line in fh:
            total_rows += 1
            total_bytes += len(line)
            try:
                payload = json.loads(line)["payload"]
            except Exception:
                continue
            m = month_of(payload.get(field, ""))
            if m:
                rows[m] += 1
                byts[m] += len(line)
    return rows, byts, total_rows, total_bytes


def window_months(n: int) -> str:
    mi = TODAY.month - 1 - n
    y = TODAY.year + mi // 12
    mo = mi % 12 + 1
    return f"{y}-{mo:02d}"


def main() -> None:
    print(f"today={TODAY}  window 24m starts {window_months(24)}  window 12m starts {window_months(12)}")

    # ---- 1. real distribution from the captures ---------------------------
    print("\n=== 1. permit-date distribution and payload bytes (largest capture per source) ===")
    allrows = {}
    allbytes = {}
    for src in CAPTURES:
        rows, byts, tr, tb = scan(src)
        allrows[src] = rows
        allbytes[src] = byts
        months = sorted(rows)
        print(f"\n{src}: {tr:,} rows, {tb:,} B, {tb/tr:,.0f} B/row")
        if months:
            print(f"  month range: {months[0]} .. {months[-1]}")
            for m in months:
                print(f"    {m}  rows={rows[m]:>7,}  bytes={byts[m]:>12,}")

    # ---- 2. rows and raw bytes inside each scope --------------------------
    print("\n=== 2. scope sizes: rows and raw JSONL bytes ===")
    w24, w12 = window_months(24), window_months(12)
    scope_rows = {"all": 0, "24m": 0, "12m": 0}
    scope_bytes = {"all": 0, "24m": 0, "12m": 0}
    for src in CAPTURES:
        rows, byts = allrows[src], allbytes[src]
        # Captures are the newest N pages; treat a month as fully captured. For a month inside a
        # scope, use the capture's count scaled to the all-history total by the capture ratio.
        cap_total = sum(rows.values()) or 1
        scale = ALL_HISTORY[src] / cap_total
        for m, n in rows.items():
            for label, start in (("24m", w24), ("12m", w12)):
                if m >= start:
                    scope_rows[label] += int(n * scale)
                    scope_bytes[label] += int(byts[m] * scale)
        scope_rows["all"] += ALL_HISTORY[src]
    # all-history raw bytes: measured full captures
    scope_bytes["all"] = 162_604_305 + 13_772_274 + 20_635_148
    for label in ("all", "24m", "12m"):
        print(f"  {label:>4}: rows={scope_rows[label]:>9,}  raw JSONL={scope_bytes[label]/1e6:>9,.1f} MB")

    # ---- 3. DB size projection --------------------------------------------
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    live = conn.execute("SELECT COALESCE(SUM(pgsize),0) FROM dbstat").fetchone()[0]
    fts = conn.execute("SELECT COALESCE(SUM(pgsize),0) FROM dbstat WHERE name LIKE '%search%'").fetchone()[0]
    permits = conn.execute("SELECT COUNT(*) FROM permit").fetchone()[0]
    projects = conn.execute("SELECT COUNT(*) FROM project").fetchone()[0]
    bpp = live / permits
    fts_pp = fts / projects
    conn.close()
    print("\n=== 3. DB size projection (measured bytes/row on the audit DB) ===")
    print(f"  live bytes={live:,}  permits={permits:,}  projects={projects:,}")
    print(f"  bytes/permit={bpp:,.1f}   FTS bytes/project={fts_pp:,.1f}")
    print("  projected DB (pessimistic: all tables scale with permits):")
    for label in ("all", "24m", "12m"):
        db_mb = scope_rows[label] * bpp / 1e6
        # projects scale ~0.30 of permits (current assembled ratio), FTS on projects
        proj = int(scope_rows[label] * projects / permits)
        fts_mb = proj * fts_pp / 1e6
        print(f"    {label:>4}: {scope_rows[label]:>9,} permits x {bpp:,.1f} B = {db_mb:>8,.1f} MB "
              f"(FTS {fts_mb:,.1f} MB over ~{proj:,} projects)")

    # ---- 4. WAL ceiling ----------------------------------------------------
    print("\n=== 4. WAL ceiling ===")
    print("  wal_autocheckpoint = 1000 pages x 4096 B = 4.0 MB (SQLite default; db.py leaves it)")
    print("  pipeline commits every 250 permits; the WAL checkpoints at 4 MB, so ceiling ~4 MB.")

    # ---- 5. refresh + retention -------------------------------------------
    print("\n=== 5. recurring refresh (bounded: MAX_PAGES=3 x page_size 1000) ===")
    per_source_rows = 3 * 1000
    avg_bpr = scope_bytes["all"] / scope_rows["all"]
    refresh_rows = per_source_rows * len(CAPTURES)
    refresh_bytes = refresh_rows * avg_bpr
    print(f"  {per_source_rows:,} rows/source x {len(CAPTURES)} = {refresh_rows:,} rows/refresh")
    print(f"  x {avg_bpr:,.0f} B/row = {refresh_bytes/1e6:,.1f} MB raw/refresh (every 6h)")
    per_day = refresh_bytes * 4
    print(f"  raw per day = {per_day/1e6:,.1f} MB ; per week = {per_day*7/1e6:,.1f} MB ; "
          f"per month = {per_day*30/1e6:,.1f} MB")
    # retention: keep_last=8 files/source + gzip_after_days=2 (~0.15 gzip ratio measured below)
    gz = _gzip_ratio()
    print(f"  measured gzip ratio on a capture: {gz:.3f}")
    keep = 8 * len(CAPTURES)
    print(f"  retention keep_last=8/source -> {keep} files; gzipped after 2 days:")
    print(f"    steady raw archive ~= {keep * refresh_bytes / 1e6:,.1f} MB uncompressed, "
          f"~{keep * refresh_bytes * gz / 1e6:,.1f} MB gzipped")
    print(f"  with retention ON, 1 week of growth adds ~{per_day*7/1e6:,.1f} MB, then is pruned to "
          f"the keep_last window (bounded).")
    print(f"  with retention OFF, 1 month adds ~{per_day*30/1e6:,.1f} MB to the archive.")

    # ---- 6. backup sizing --------------------------------------------------
    print("\n=== 6. backup sizing ===")
    for label in ("all", "24m", "12m"):
        db_mb = scope_rows[label] * bpp / 1e6
        print(f"  {label:>4}: one uncompressed DB backup ~= {db_mb:,.1f} MB; "
              f"3 kept ~= {db_mb*3:,.1f} MB; gzipped(x{gz:.2f}) 3 kept ~= {db_mb*3*gz:,.1f} MB")

    # ---- 7. recommended disk ----------------------------------------------
    print("\n=== 7. recommended disk (all-history seed, retention on) ===")
    db_all = scope_rows["all"] * bpp / 1e6
    raw_all = scope_bytes["all"] / 1e6
    wal = 4.0
    archive_steady = keep * refresh_bytes * gz / 1e6
    backups = db_all * 3 * gz
    total = db_all + raw_all + wal + archive_steady + backups
    print(f"  DB {db_all:,.1f} + raw {raw_all:,.1f} + WAL {wal:.1f} + archive {archive_steady:,.1f} "
          f"+ 3 gz backups {backups:,.1f} = {total:,.1f} MB")
    print(f"  round up with 2x headroom -> recommend {_round_up(total*2)} MB disk")


def _gzip_ratio() -> float:
    name = CAPTURES["fort_worth_permits"][0]
    path = os.path.join(RAW, name)
    # Compress a 5 MB slice for a fast, representative ratio.
    with open(path, "rb") as fh:
        data = fh.read(5_000_000)
    return len(gzip.compress(data, 6)) / len(data)


def _round_up(mb: float) -> int:
    for step in (1024, 2048, 4096, 5120, 8192, 10240, 20480):
        if mb <= step:
            return step
    return int(mb)


if __name__ == "__main__":
    main()
