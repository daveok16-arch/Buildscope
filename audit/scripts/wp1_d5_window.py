"""D5 evidence (WP3 gate): READ-ONLY analysis of recent filings vs the public feed.

Runs only SELECTs against a copy of the audit DB. Never writes, never changes
classification logic.

    PYTHONPATH=vendor/python:src python audit/scripts/wp1_d5_window.py /tmp/d5_readonly.db
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import date, timedelta

PUBLIC_CLASSIFICATIONS = ("HIGH", "MEDIUM")
DISCOVERABLE_PROCUREMENT = ("Confirmed open", "Evidence found, status unclear", "Not verified")


def main() -> int:
    db = sys.argv[1] if len(sys.argv) > 1 else "/tmp/d5_readonly.db"
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row

    today = date(2026, 10, 9)
    cutoff = (today - timedelta(days=30)).isoformat()
    print(f"today={today.isoformat()}  window = permit_date >= {cutoff}")

    def q(sql, params=()):
        return con.execute(sql, params).fetchall()

    total = q("SELECT COUNT(*) n FROM project WHERE permit_date >= ?", (cutoff,))[0]["n"]
    print(f"\n== projects with permit_date in the last 30 days: {total} ==")

    print("\n-- by classification --")
    for r in q(
        "SELECT classification, COUNT(*) n FROM project WHERE permit_date >= ? "
        "GROUP BY classification ORDER BY n DESC", (cutoff,)
    ):
        print(f"  {r['classification']!r:22} {r['n']}")

    print("\n-- by procurement_status --")
    for r in q(
        "SELECT procurement_status, COUNT(*) n FROM project WHERE permit_date >= ? "
        "GROUP BY procurement_status ORDER BY n DESC", (cutoff,)
    ):
        print(f"  {r['procurement_status']!r:36} {r['n']}")

    pub_class = "classification IN ('HIGH','MEDIUM')"
    pub_proc = "procurement_status IN ('Confirmed open','Evidence found, status unclear','Not verified')"
    public_n = q(
        f"SELECT COUNT(*) n FROM project WHERE permit_date >= ? AND {pub_class} AND {pub_proc}",
        (cutoff,),
    )[0]["n"]
    print(f"\n== public (both gates pass) in window: {public_n} ==")
    nonpublic = total - public_n
    print(f"== non-public in window: {nonpublic} ==")

    print("\n-- exclusion gate for the non-public ones (first gate that fails) --")
    gates = {
        "classification": q(
            f"SELECT COUNT(*) n FROM project WHERE permit_date >= ? AND classification NOT IN ('HIGH','MEDIUM')",
            (cutoff,),
        )[0]["n"],
    }
    gates["procurement (classification OK)"] = q(
        f"SELECT COUNT(*) n FROM project WHERE permit_date >= ? AND {pub_class} "
        f"AND procurement_status NOT IN ('Confirmed open','Evidence found, status unclear','Not verified') "
        "OR (permit_date >= ? AND classification IN ('HIGH','MEDIUM') AND procurement_status IS NULL)",
        (cutoff, cutoff),
    )[0]["n"]
    for k, v in gates.items():
        print(f"  {k:34} {v}")

    print("\n-- 10 sample non-public in-window rows --")
    rows = q(
        f"SELECT project_name, city, permit_date, classification, procurement_status, "
        f"mechanical_evidence_tier, classification_score "
        f"FROM project WHERE permit_date >= ? AND NOT ({pub_class} AND {pub_proc}) "
        "ORDER BY permit_date DESC LIMIT 10",
        (cutoff,),
    )
    for r in rows:
        reason = []
        if r["classification"] not in PUBLIC_CLASSIFICATIONS:
            reason.append(f"classification={r['classification']}")
        if r["procurement_status"] not in DISCOVERABLE_PROCUREMENT:
            reason.append(f"procurement={r['procurement_status']!r}")
        print(
            f"  {(r['project_name'] or '')[:34]:34} | {(r['city'] or '')[:12]:12} | "
            f"{r['permit_date']} | {r['classification']!r:20} | "
            f"{(r['procurement_status'] or '')[:24]:24} | tier={r['mechanical_evidence_tier']} "
            f"| {'; '.join(reason)}"
        )

    print(f"\n-- permit-date distribution by month for the {public_n} public in-window projects --")
    for r in q(
        f"SELECT substr(permit_date,1,7) ym, COUNT(*) n FROM project "
        f"WHERE permit_date >= ? AND {pub_class} AND {pub_proc} GROUP BY ym ORDER BY ym",
        (cutoff,),
    ):
        print(f"  {r['ym']}  {r['n']}")

    print("\n-- full permit_date range --")
    r = q("SELECT MIN(permit_date) mn, MAX(permit_date) mx FROM project")[0]
    print(f"  min={r['mn']}  max={r['mx']}")
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
