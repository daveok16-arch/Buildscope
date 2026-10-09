from oppintel.db import Database
d = Database('/tmp/populated.db')

print('project_change rows total:',
      d.conn.execute('SELECT COUNT(*) FROM project_change').fetchone()[0])
print('by change_kind:')
for r in d.conn.execute('SELECT change_kind, COUNT(*) n FROM project_change GROUP BY change_kind ORDER BY n DESC').fetchall():
    print('   ', r['change_kind'], r['n'])

print()
print('the /changes endpoint predicate (60-day cutoff computed at request time):')
cutoff = d.conn.execute("SELECT datetime('now','-60 days')").fetchone()[0]
print('   60-day cutoff =', cutoff)
q = """
SELECT COUNT(*) FROM project_change c
  JOIN project p ON p.id = c.project_id
 WHERE c.detected_at >= ?
   AND p.classification IN (?, ?)
   AND p.procurement_status IN (?, ?, ?)
"""
pc = ('HIGH', 'MEDIUM')
ps = ('CONFIRMED_OPEN', 'EVIDENCE_FOUND', 'NOT_VERIFIED')
n = d.conn.execute(q, (cutoff, *pc, *ps)).fetchone()[0]
print('   rows joining a public+discoverable project =', n)
print('   distinct projects =', d.conn.execute(
    "SELECT COUNT(DISTINCT c.project_id) FROM project_change c JOIN project p ON p.id=c.project_id "
    "WHERE c.detected_at >= ? AND p.classification IN ('HIGH','MEDIUM') "
    "AND p.procurement_status IN ('CONFIRMED_OPEN','EVIDENCE_FOUND','NOT_VERIFIED')",
    (cutoff,)).fetchone()[0])

print()
print('all project_change, public+discoverable, no time cutoff:')
print('   rows =', d.conn.execute(
    "SELECT COUNT(*) FROM project_change c JOIN project p ON p.id=c.project_id "
    "WHERE p.classification IN ('HIGH','MEDIUM') "
    "AND p.procurement_status IN ('CONFIRMED_OPEN','EVIDENCE_FOUND','NOT_VERIFIED')").fetchone()[0])
d.close()
