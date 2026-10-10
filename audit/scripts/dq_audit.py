"""Data-integrity audit queries. READ-ONLY (opened mode=ro).

Covers the founder's symptoms a-g against the audit DB copy. Every statement is SELECT only.
    PYTHONPATH=vendor/python:src python audit/scripts/dq_audit.py [/tmp/h1_readonly.db]
"""
from __future__ import annotations

import sqlite3
import sys

DB = sys.argv[1] if len(sys.argv) > 1 else "/tmp/h1_readonly.db"
TODAY = "2026-10-09"


def con() -> sqlite3.Connection:
    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    return c


def one(c, sql, p=()):
    return c.execute(sql, p).fetchone()[0]


def rows(c, sql, p=()):
    return c.execute(sql, p).fetchall()


def main() -> None:
    c = con()
    total = one(c, "SELECT COUNT(*) FROM project")
    pub = one(c, "SELECT COUNT(*) FROM project WHERE classification IN ('HIGH','MEDIUM')")
    print(f"DB={DB}  project total={total}  public(HIGH/MEDIUM)={pub}\n")

    # (e) future-dated permits
    print("=== (e) permit_date > today ===")
    n = one(c, "SELECT COUNT(*) FROM project WHERE permit_date > ?", (TODAY,))
    print(f"projects with permit_date > {TODAY}: {n}")
    for r in rows(
        c,
        "SELECT project_name, city, permit_date, classification, procurement_status "
        "FROM project WHERE permit_date > ? ORDER BY permit_date DESC LIMIT 8",
        (TODAY,),
    ):
        print(f"   {(r['project_name'] or '')[:44]:44} | {r['city']:12} | {r['permit_date']} | {r['classification']}")
    # permits table too
    pn = one(c, "SELECT COUNT(*) FROM permit WHERE permit_date > ?", (TODAY,))
    print(f"permit rows with permit_date > {TODAY}: {pn}")

    # (f) data-quality counts
    print("\n=== (f) null / 'Not verified' / duplicates / caps ===")
    def pct(x):
        return f"{x} ({100.0*x/total:.1f}%)"

    print(f"declared/procurement 'Not verified' : {pct(one(c, 'SELECT COUNT(*) FROM project WHERE procurement_status = \"Not verified\"'))}")
    print(f"estimated_project_value NULL        : {pct(one(c, 'SELECT COUNT(*) FROM project WHERE estimated_project_value IS NULL'))}")
    print(f"square_footage NULL                 : {pct(one(c, 'SELECT COUNT(*) FROM project WHERE square_footage IS NULL'))}")
    print(f"project_type NULL                   : {pct(one(c, 'SELECT COUNT(*) FROM project WHERE project_type IS NULL'))}")
    print(f"owner NULL                          : {pct(one(c, 'SELECT COUNT(*) FROM project WHERE owner IS NULL'))}")
    print(f"general_contractor NULL             : {pct(one(c, 'SELECT COUNT(*) FROM project WHERE general_contractor IS NULL'))}")
    print(f"architect NULL                      : {pct(one(c, 'SELECT COUNT(*) FROM project WHERE architect IS NULL'))}")
    print(f"project_name ALL CAPS (letters)     : {pct(one(c, 'SELECT COUNT(*) FROM project WHERE project_name = UPPER(project_name) AND project_name GLOB \"*[A-Za-z]*\"'))}")
    print(f"project_name ends with '...'        : {pct(one(c, 'SELECT COUNT(*) FROM project WHERE project_name LIKE \"%...\"'))}")
    print(f"projects with ZERO evidence rows    : {pct(one(c, 'SELECT COUNT(*) FROM project p WHERE NOT EXISTS (SELECT 1 FROM evidence e WHERE e.project_id = p.id)'))}")
    print(f"orphaned permits (no project link)  : {one(c, 'SELECT COUNT(*) FROM permit pe WHERE NOT EXISTS (SELECT 1 FROM project_permit pp WHERE pp.permit_id = pe.id)')}")
    print(f"permits total                       : {one(c, 'SELECT COUNT(*) FROM permit')}")

    # (f) duplicate projects: same normalized address + same city, >1
    print("\n=== (f) duplicate projects (same address+city) ===")
    dup = rows(
        c,
        "SELECT address, city, COUNT(*) n FROM project "
        "WHERE address IS NOT NULL AND TRIM(address) <> '' "
        "GROUP BY LOWER(TRIM(address)), LOWER(TRIM(city)) HAVING n > 1 "
        "ORDER BY n DESC LIMIT 10",
    )
    print(f"address+city groups with >1 project: {one(c, 'SELECT COUNT(*) FROM (SELECT 1 FROM project WHERE address IS NOT NULL GROUP BY LOWER(TRIM(address)), LOWER(TRIM(city)) HAVING COUNT(*)>1)')}")
    for r in dup:
        print(f"   x{r['n']:2}  {(r['address'] or '')[:40]:40} | {r['city']}")

    # date range
    print("\n=== (f) date ranges ===")
    print("permit_date  min/max:", one(c, "SELECT MIN(permit_date) FROM project"), "/", one(c, "SELECT MAX(permit_date) FROM project"))
    print("first_observed min/max:", one(c, "SELECT MIN(created_at) FROM project"), "/", one(c, "SELECT MAX(created_at) FROM project"))

    # (f) records per jurisdiction
    print("\n=== (f) projects per city (top 16) ===")
    for r in rows(c, "SELECT city, COUNT(*) n FROM project GROUP BY city ORDER BY n DESC LIMIT 16"):
        print(f"   {r['city']:16} {r['n']}")

    # (g) company roles
    print("\n=== (g) company/party role distribution ===")
    try:
        for r in rows(c, "SELECT role, COUNT(*) n FROM project_party GROUP BY role ORDER BY n DESC"):
            print(f"   {r['role']:20} {r['n']}")
    except sqlite3.OperationalError as e:
        print("   project_party:", e)
    try:
        for r in rows(c, "SELECT role, COUNT(*) n FROM entity_project GROUP BY role ORDER BY n DESC LIMIT 20"):
            print(f"   entity_project {r['role']:16} {r['n']}")
    except sqlite3.OperationalError as e:
        print("   entity_project:", e)
    # raw party columns on project
    for col in ("owner", "developer", "general_contractor", "architect"):
        print(f"   project.{col} non-null: {one(c, f'SELECT COUNT(*) FROM project WHERE {col} IS NOT NULL')}")
    c.close()


if __name__ == "__main__":
    main()
