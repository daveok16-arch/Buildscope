from oppintel.db import Database
d = Database('/tmp/populated.db')
rows = d.conn.execute('''
  SELECT source_id, permit_number, COUNT(*) n,
         COUNT(DISTINCT address_key) addr_n,
         COUNT(DISTINCT permit_date) date_n,
         COUNT(DISTINCT permit_type) type_n,
         COUNT(DISTINCT permit_subtype) sub_n,
         COUNT(DISTINCT status) status_n,
         COUNT(DISTINCT work_description) desc_n,
         COUNT(DISTINCT natural_key) nk_n,
         COUNT(DISTINCT job_value) jv_n,
         COUNT(DISTINCT square_footage) sq_n
    FROM permit
   WHERE permit_number IS NOT NULL AND permit_number <> ''
   GROUP BY source_id, permit_number HAVING COUNT(*)>1
''').fetchall()
print('total groups:', len(rows))
from collections import Counter
print('by source:', dict(Counter(r['source_id'] for r in rows)))
print('group size dist:', dict(Counter(r['n'] for r in rows)))
for r in rows[:5]:
    print(dict(r))
d.close()
