"""D5 — Trends vs the public feed, read-only.

Answers, from stored rows only:
  * for projects with a permit date in the last 30 days: counts by classification and by
    procurement status, and for the non-public ones the gate that excludes them;
  * 10 sample non-public rows with the fields a reader needs to see why;
  * the permit-date month distribution of the public projects (how stale the public set is).

Usage:
    PYTHONPATH="vendor/python:src" python audit/scripts/wp1_d5_trends.py [db-path]

Default db path is /tmp/populated.db (a scratch copy; create it with
`cp data/oppintel.db /tmp/populated.db`). This script never writes.
"""

from __future__ import annotations

import os
import sqlite3
import sys
from datetime import date, timedelta

PUBLIC = (
    "classification IN ('HIGH','MEDIUM') AND "
    "procurement_status IN ('Confirmed open','Evidence found, status unclear','Not verified')"
)


def main() -> int:
    path = sys.argv[1] if len(sys.argv) > 1 else "/tmp/populated.db"
    if not os.path.exists(path):
        print(f"no database at {path}; copy one first")
        return 1
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    today = date.today()
    cutoff = (today - timedelta(days=29)).isoformat()

    def rows(sql, params=()):
        return conn.execute(sql, params).fetchall()

    print(f"db={path}  today={today.isoformat()}  window start={cutoff}")

    print("\n== projects with a permit date in the last 30 days ==")
    total = rows("SELECT COUNT(*) FROM project WHERE permit_date >= ?", (cutoff,))[0][0]
    print("total:", total)
    print("by classification:")
    for r in rows(
        "SELECT classification, COUNT(*) n FROM project WHERE permit_date >= ? "
        "GROUP BY 1 ORDER BY n DESC", (cutoff,)
    ):
        print("  ", dict(r))
    print("by procurement_status:")
    for r in rows(
        "SELECT procurement_status, COUNT(*) n FROM project WHERE permit_date >= ? "
        "GROUP BY 1 ORDER BY n DESC", (cutoff,)
    ):
        print("  ", dict(r))
    public_n = rows(
        f"SELECT COUNT(*) FROM project WHERE {PUBLIC} AND permit_date >= ?", (cutoff,)
    )[0][0]
    print("public among them:", public_n)

    print("\n== gate for the non-public ones ==")
    for r in rows(
        f"SELECT classification, procurement_status, mechanical_evidence_tier, COUNT(*) n "
        f"FROM project WHERE permit_date >= ? AND NOT ({PUBLIC}) GROUP BY 1,2,3 ORDER BY n DESC",
        (cutoff,),
    ):
        print("  ", dict(r))

    print("\n== 10 sample non-public rows ==")
    for r in rows(
        f"SELECT project_name, city, permit_date, classification, procurement_status, "
        f"mechanical_evidence_tier, project_type FROM project "
        f"WHERE permit_date >= ? AND NOT ({PUBLIC}) ORDER BY permit_date DESC LIMIT 10",
        (cutoff,),
    ):
        print("  ", dict(r))

    print("\n== public projects: permit_date by month ==")
    for r in rows(
        f"SELECT substr(permit_date,1,7) ym, COUNT(*) n FROM project WHERE {PUBLIC} "
        f"GROUP BY 1 ORDER BY 1"
    ):
        print("  ", dict(r))

    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
