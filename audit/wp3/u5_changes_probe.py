"""U5b — investigate the live "442 projects with a detected change" (read-only).

Runs against a byte-copy of the shipped DB, and against the LIVE snapshot when a copy is
provided. SELECT only; nothing is written.
"""
import sqlite3
import sys

DB = sys.argv[1] if len(sys.argv) > 1 else "/tmp/audit_u5.db"
TRADE = "commercial_hvac"

c = sqlite3.connect(DB)
c.row_factory = sqlite3.Row


def q(sql, p=()):
    return c.execute(sql, p).fetchall()


print(f"DB: {DB}\n")

print("== project_change schema ==")
for r in q("SELECT sql FROM sqlite_master WHERE name='project_change'"):
    print(r[0])
print()

print("== counts by change_kind ==")
for r in q("SELECT change_kind, COUNT(*) n, COUNT(DISTINCT project_id) dp FROM project_change GROUP BY change_kind ORDER BY n DESC"):
    print(f"  {r['change_kind']:24} rows={r['n']:6} distinct_projects={r['dp']}")
print()

print("== projects_changed predicate (change_kind<>'new_project') ==")
r = q("SELECT COUNT(DISTINCT pc.project_id) FROM project_change pc JOIN project p ON p.id=pc.project_id "
      "WHERE p.trade=? AND pc.change_kind<>'new_project'", (TRADE,))[0]
print("  distinct projects changed (trade-scoped):", r[0])
r = q("SELECT COUNT(DISTINCT project_id) FROM project_change WHERE change_kind<>'new_project'")[0]
print("  distinct projects changed (unscoped):", r[0])
print()

print("== detected_at range (all rows) ==")
for r in q("SELECT MIN(detected_at) a, MAX(detected_at) b, COUNT(*) n FROM project_change"):
    print("  ", dict(r))
print("== detected_at range by kind ==")
for r in q("SELECT change_kind, MIN(detected_at) a, MAX(detected_at) b, COUNT(*) n FROM project_change GROUP BY change_kind"):
    print("  ", dict(r))
print()

print("== created_at/updated_at range on project (observation window) ==")
for r in q("SELECT MIN(created_at) a, MAX(created_at) b, MIN(updated_at) c, MAX(updated_at) d FROM project"):
    print("  ", dict(r))
print()

print("== per-field change counts (non new_project) ==")
for r in q("SELECT field_name, change_kind, COUNT(*) n FROM project_change "
           "WHERE change_kind<>'new_project' GROUP BY field_name, change_kind ORDER BY n DESC"):
    print("  ", dict(r))
print()

print("== 10 sample diffs (non new_project) ==")
rows = q("SELECT project_id, field_name, change_kind, previous_value, current_value, detected_at "
         "FROM project_change WHERE change_kind<>'new_project' ORDER BY detected_at DESC LIMIT 10")
if not rows:
    print("  (none)")
for r in rows:
    print("  ", dict(r))
print()

print("== permit rows / orphan check ==")
print("  permits total:", q("SELECT COUNT(*) FROM permit")[0][0])
print("  permits linked:", q("SELECT COUNT(DISTINCT permit_id) FROM project_permit")[0][0])
print("  permits orphaned:", q("SELECT COUNT(*) FROM permit pm WHERE NOT EXISTS(SELECT 1 FROM project_permit pp WHERE pp.permit_id=pm.id)")[0][0])
print()

print("== project counts by trade ==")
for r in q("SELECT trade, COUNT(*) n FROM project GROUP BY trade ORDER BY n DESC LIMIT 10"):
    print("  ", dict(r))
