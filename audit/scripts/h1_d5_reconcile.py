"""H1 — D5 reconciliation and completion. READ-ONLY.

Reconciles the four conflicting numbers and prints every sub-item the Director asked for.
Never writes: the connection is opened ``mode=ro`` against a copy of the audit DB.

    PYTHONPATH=vendor/python:src python audit/scripts/h1_d5_reconcile.py [db-path]

Default db path is /tmp/h1_readonly.db (``cp data/oppintel.db /tmp/h1_readonly.db``).
"""
from __future__ import annotations

import os
import sqlite3
import sys
from datetime import date, timedelta

# The public feed gate, exactly as the application applies it (service.py / trends.py).
PUBLIC_CLASS = "classification IN ('HIGH','MEDIUM')"
PUBLIC_PROC = (
    "procurement_status IN ('Confirmed open','Evidence found, status unclear','Not verified')"
)
PUBLIC = f"{PUBLIC_CLASS} AND {PUBLIC_PROC}"
# Trends uses the home page's population: classified and not Closed.
TRENDS_PUBLIC = f"{PUBLIC_CLASS} AND procurement_status <> 'Closed'"
VALID_DATE = "permit_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'"

TODAY = date(2026, 10, 9)


def main() -> int:
    path = sys.argv[1] if len(sys.argv) > 1 else "/tmp/h1_readonly.db"
    if not os.path.exists(path):
        print(f"no database at {path}; copy one first: cp data/oppintel.db {path}")
        return 1
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row

    def n(sql, params=()):
        return conn.execute(sql, params).fetchone()[0]

    def rows(sql, params=()):
        return conn.execute(sql, params).fetchall()

    print(f"db={path}  today={TODAY.isoformat()}")

    # ------------------------------------------------------------------ H1a
    print("\n############ H1a — the four numbers, side by side ############")
    # Window A: G1c/wp1_d5_window — permit_date >= today-30 (>= 2026-09-09), NO upper bound,
    #           no future exclusion, no valid-date guard.
    winA = (TODAY - timedelta(days=30)).isoformat()
    # Window B: Trends 30d — [today-29, tomorrow) i.e. [2026-09-10, 2026-10-10), valid dates,
    #           permit_date <= today.
    winB_start = (TODAY - timedelta(days=29)).isoformat()
    winB_end = (TODAY + timedelta(days=1)).isoformat()

    print(f"\nG1c window (wp1_d5_window.py): permit_date >= {winA}   (open-ended, no future guard)")
    print(f"Trends window (trends.py 30d): [{winB_start}, {winB_end})  permit_date <= {TODAY.isoformat()}, valid-date guard")

    g1c_total = n("SELECT COUNT(*) FROM project WHERE permit_date >= ?", (winA,))
    print(f"\n[1] G1c total in-window          = {g1c_total}")

    trends_total = n(
        f"SELECT COUNT(*) FROM project WHERE trade='commercial_hvac' AND {VALID_DATE} "
        "AND permit_date >= ? AND permit_date < ? AND permit_date <= ?",
        (winB_start, winB_end, TODAY.isoformat()),
    )
    print(f"[2] Trends projects_observed     = {trends_total}")

    pub_g1c = n(f"SELECT COUNT(*) FROM project WHERE permit_date >= ? AND {PUBLIC}", (winA,))
    print(f"[3] G1c 'public (both gates)'    = {pub_g1c}   (window >= {winA})")

    pub_trends = n(
        f"SELECT COUNT(*) FROM project WHERE permit_date >= ? AND {TRENDS_PUBLIC}", (winB_start,)
    )
    print(f"[4] Trends 'of which public'     = {pub_trends}   (window >= {winB_start}, not Closed)")

    # The Director's earlier C2 claim: "only 1 of 133 public projects has an in-window permit".
    pub_all = n(f"SELECT COUNT(*) FROM project WHERE {PUBLIC}")
    print(f"\n[?] all public projects (no window) = {pub_all}")
    for label, start in [
        ("last 7 days  (>= 2026-10-03)", (TODAY - timedelta(days=6)).isoformat()),
        ("this month   (>= 2026-10-01)", "2026-10-01"),
        ("last 14 days (>= 2026-09-26)", (TODAY - timedelta(days=13)).isoformat()),
    ]:
        c = n(f"SELECT COUNT(*) FROM project WHERE permit_date >= ? AND {PUBLIC}", (start,))
        print(f"    public in {label:30} = {c}")

    print("\n-- row-by-row delta --")
    print(f"  G1c {g1c_total}  minus  Trends {trends_total} = {g1c_total - trends_total}")
    print("    explained by:")
    no_trade = n(
        f"SELECT COUNT(*) FROM project WHERE permit_date >= ? AND trade <> 'commercial_hvac'",
        (winA,),
    )
    future_in_A = n(
        f"SELECT COUNT(*) FROM project WHERE permit_date >= ? AND permit_date > ?",
        (winA, TODAY.isoformat()),
    )
    in_sept09 = n(
        "SELECT COUNT(*) FROM project WHERE permit_date = '2026-09-09'",
    )
    print(f"      * non-commercial_hvac trade rows in G1c window : {no_trade}")
    print(f"      * permit_date > today inside G1c window        : {future_in_A}")
    print(f"      * permit_date == 2026-09-09 (G1c includes, Trends does not): {in_sept09}")
    print(f"      check {g1c_total} - {no_trade} - {future_in_A} = {g1c_total - no_trade - future_in_A}")
    print(f"  G1c public {pub_g1c}  vs  Trends public {pub_trends} = {pub_g1c - pub_trends}")

    # ------------------------------------------------------------------ H1b
    print("\n############ H1b — the min/max line ############")
    print("wp1_d5_window.py prints the FULL project permit_date range, not the window range:")
    r = rows("SELECT MIN(permit_date) mn, MAX(permit_date) mx FROM project")[0]
    print(f"  full project permit_date range: min={r['mn']}  max={r['mx']}")
    print("  the label 'full permit_date range' is correct; it is not a 30-day window.")

    # ------------------------------------------------------------------ H1c
    print("\n############ H1c — full breakdowns ############")
    print(f"\n-- in-window (>= {winA}) by classification --")
    for x in rows(
        "SELECT classification, COUNT(*) n FROM project WHERE permit_date >= ? "
        "GROUP BY 1 ORDER BY n DESC",
        (winA,),
    ):
        print(f"   {x['classification']!r:22} {x['n']}")
    print(f"\n-- in-window (>= {winA}) by procurement_status --")
    for x in rows(
        "SELECT procurement_status, COUNT(*) n FROM project WHERE permit_date >= ? "
        "GROUP BY 1 ORDER BY n DESC",
        (winA,),
    ):
        print(f"   {str(x['procurement_status'])!r:38} {x['n']}")

    print("\n-- exclusion gate for non-public in-window (first gate that fails) --")
    gate_class = n(
        f"SELECT COUNT(*) FROM project WHERE permit_date >= ? AND NOT ({PUBLIC_CLASS})", (winA,)
    )
    gate_proc = n(
        f"SELECT COUNT(*) FROM project WHERE permit_date >= ? AND {PUBLIC_CLASS} "
        f"AND NOT ({PUBLIC_PROC})",
        (winA,),
    )
    gate_evidence = n(
        f"SELECT COUNT(*) FROM project WHERE permit_date >= ? AND {PUBLIC_CLASS} "
        f"AND {PUBLIC_PROC} AND (mechanical_evidence_tier IS NULL)",
        (winA,),
    )
    print(f"   classification (not HIGH/MEDIUM)      : {gate_class}")
    print(f"   procurement status (not discoverable) : {gate_proc}")
    print(f"   missing evidence tier (among public)  : {gate_evidence}")

    print("\n-- 10 sample non-public in-window rows --")
    for x in rows(
        f"SELECT project_name, city, permit_date, classification, procurement_status, "
        f"mechanical_evidence_tier FROM project WHERE permit_date >= ? AND NOT ({PUBLIC}) "
        "ORDER BY permit_date DESC LIMIT 10",
        (winA,),
    ):
        reasons = []
        if x["classification"] not in ("HIGH", "MEDIUM"):
            reasons.append(f"classification={x['classification']}")
        if x["procurement_status"] not in (
            "Confirmed open", "Evidence found, status unclear", "Not verified"
        ):
            reasons.append(f"procurement={x['procurement_status']!r}")
        print(
            f"   {(x['project_name'] or '')[:32]:32} | {(x['city'] or '')[:11]:11} | "
            f"{x['permit_date']} | {str(x['classification']):18} | "
            f"{str(x['procurement_status'] or '')[:22]:22} | tier={x['mechanical_evidence_tier']} "
            f"| {'; '.join(reasons)}"
        )

    print(f"\n-- permit-date distribution by month for ALL public projects ({pub_all}) --")
    for x in rows(
        f"SELECT substr(permit_date,1,7) ym, COUNT(*) n FROM project WHERE {PUBLIC} "
        "AND " + VALID_DATE + " GROUP BY 1 ORDER BY 1"
    ):
        print(f"   {x['ym']}  {x['n']}")

    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
