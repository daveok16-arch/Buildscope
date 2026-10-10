from oppintel.db import Database
from collections import Counter
d = Database('/tmp/populated.db')
rows = d.conn.execute('''
  SELECT source_id, permit_number, COUNT(*) n,
         COUNT(DISTINCT address_key) addr_n,
         COUNT(DISTINCT permit_date) date_n,
         COUNT(DISTINCT permit_type) type_n,
         COUNT(DISTINCT work_description) desc_n,
         COUNT(DISTINCT natural_key) nk_n
    FROM permit
   WHERE permit_number IS NOT NULL AND permit_number <> ''
   GROUP BY source_id, permit_number HAVING COUNT(*)>1
''').fetchall()

cat = Counter()
examples = {}
for r in rows:
    pn = r['permit_number']
    if r['addr_n'] == 1 and r['date_n'] == 1:
        c = 'identical_dup'
    elif r['date_n'] > 1 and r['addr_n'] == 1:
        c = 'revision_same_address'
    elif r['addr_n'] > 1:
        c = 'distinct_addresses'
    else:
        c = 'other'
    cat[c] += 1
    examples.setdefault(c, []).append(dict(r))

print('CATEGORY COUNTS:')
for c, n in cat.most_common():
    excess = sum(x['n'] - 1 for x in examples[c])
    print(f'  {c}: {n} groups, {excess} excess rows')
print('sum of categories:', sum(cat.values()))
print()
for c, xs in examples.items():
    print(f'=== {c}: 5 examples ===')
    for x in xs[:5]:
        print('   ', x['source_id'], x['permit_number'], 'n=', x['n'],
              'addr_n=', x['addr_n'], 'date_n=', x['date_n'],
              'type_n=', x['type_n'], 'desc_n=', x['desc_n'], 'nk_n=', x['nk_n'])
d.close()
