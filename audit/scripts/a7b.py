import os
os.environ.setdefault('SECRET_KEY', 'dev')
os.environ['OPPINTEL_DB'] = '/tmp/populated.db'
from oppintel.db import Database
from oppintel.config import active_market, active_trade
from oppintel.service import OpportunityService

d = Database('/tmp/populated.db')
print('detected_at sample:', d.conn.execute('SELECT detected_at FROM project_change LIMIT 3').fetchall())
print()
print('recent_changes_count(60):', OpportunityService(d, active_market(), active_trade()).recent_changes_count(days=60))
print('recent_changes_count(30):', OpportunityService(d, active_market(), active_trade()).recent_changes_count(days=30))

# classification / procurement distribution of projects that own a change
print()
print('projects owning >=1 change, by classification:')
for r in d.conn.execute("SELECT p.classification, COUNT(DISTINCT p.id) n FROM project_change c JOIN project p ON p.id=c.project_id GROUP BY p.classification").fetchall():
    print('   ', r['classification'], r['n'])
print('projects owning >=1 change, by procurement_status:')
for r in d.conn.execute("SELECT p.procurement_status, COUNT(DISTINCT p.id) n FROM project_change c JOIN project p ON p.id=c.project_id GROUP BY p.procurement_status").fetchall():
    print('   ', r['procurement_status'], r['n'])
d.close()
