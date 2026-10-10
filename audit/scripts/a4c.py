from oppintel.db import Database
from collections import Counter
d = Database('/tmp/populated.db')
rows = d.conn.execute('''
  SELECT source_id, permit_number, COUNT(*) n,
         COUNT(DISTINCT address_key) addr_n,
         COUNT(DISTINCT permit_date) date_n,
         COUNT(DISTINCT permit_type) type_n,
         COUNT(DISTINCT work_description) desc_n,
         COUNT(DISTINCT natural_key) nk_n,
         GROUP_CONCAT(DISTINCT permit_type) types,
         GROUP_CONCAT(DISTINCT permit_subtype) subs,
         GROUP_CONCAT(DISTINCT status) statuses
    FROM permit
   WHERE permit_number IS NOT NULL AND permit_number <> ''
   GROUP BY source_id, permit_number HAVING COUNT(*)>1
''').fetchall()

# Look at permit types across all groups
types = Counter()
for r in rows:
    for t in (r['types'] or '').split(','):
        types[t] += 1
print('permit_type occurrences in dup groups:', dict(types))
print()
# the group of 20
big = max(rows, key=lambda r: r['n'])
print('biggest group:', dict(big))
for p in d.conn.execute("SELECT address_key, permit_date, permit_type, natural_key FROM permit WHERE source_id=? AND permit_number=? ORDER BY permit_date", (big['source_id'], big['permit_number'])).fetchall():
    print('   ', dict(p))
d.close()
