#!/usr/bin/env python
"""A4: reconcile the 61 (source_id, permit_number) groups against WP1's 7 true-dup groups."""
from oppintel.db import Database

DB = '/tmp/populated.db'
d = Database(DB)

SQL61 = """
SELECT COUNT(*) FROM (
  SELECT source_id, permit_number
    FROM permit
   WHERE permit_number IS NOT NULL AND permit_number <> ''
   GROUP BY source_id, permit_number
  HAVING COUNT(*) > 1)
"""

SQL7 = """
SELECT COUNT(*) FROM (
  SELECT source_id, permit_number, address_key, permit_date
    FROM permit
   WHERE permit_number IS NOT NULL AND permit_number <> ''
     AND address_key IS NOT NULL AND permit_date IS NOT NULL
   GROUP BY source_id, permit_number, address_key, permit_date
  HAVING COUNT(*) > 1)
"""

print('SQL-61:', ' '.join(SQL61.split()))
print('  count =', d.conn.execute(SQL61).fetchone()[0])
print()
print('SQL-7:', ' '.join(SQL7.split()))
print('  count =', d.conn.execute(SQL7).fetchone()[0])
print()

def category(pn, addr_n, date_n):
    placeholder = not pn.split('-')[-1].isdigit()
    if addr_n == 1 and date_n == 1:
        return 'identical_same_address_date'
    if addr_n == 1:
        return 'same_address_multiple_dates (revision/inspection)'
    if placeholder:
        return 'placeholder_number_distinct_addresses'
    return 'real_number_distinct_addresses'

groups = d.conn.execute("""
  SELECT permit_number, COUNT(*) n, COUNT(DISTINCT address_key) a, COUNT(DISTINCT permit_date) dt
    FROM permit WHERE permit_number IS NOT NULL AND permit_number <> ''
   GROUP BY source_id, permit_number HAVING COUNT(*)>1
""").fetchall()

from collections import Counter, defaultdict
cat = Counter(); ex = defaultdict(list); excess = Counter()
for r in groups:
    c = category(r['permit_number'], r['a'], r['dt'])
    cat[c] += 1; excess[c] += r['n'] - 1
    if len(ex[c]) < 5:
        ex[c].append((r['permit_number'], r['n'], r['a'], r['dt']))

print('CATEGORY BREAKDOWN OF THE 61 GROUPS')
for c, n in cat.most_common():
    print(f'  {c}: {n} groups / {excess[c]} excess rows')
print('  SUM =', sum(cat.values()))
print()
for c, rows in ex.items():
    print(f'  5 examples [{c}]:')
    for pn, n, a, dt in rows:
        print(f'     {pn}: rows={n} distinct_addresses={a} distinct_dates={dt}')
    print()

# which of the 7 come from which category
seven = d.conn.execute("""
  SELECT permit_number, address_key, permit_date, COUNT(*) n
    FROM permit WHERE permit_number IS NOT NULL AND permit_number <> ''
     AND address_key IS NOT NULL AND permit_date IS NOT NULL
   GROUP BY source_id, permit_number, address_key, permit_date HAVING COUNT(*)>1
""").fetchall()
agg = {r['permit_number']: category(r['permit_number'], r['a'], r['dt'])
       for r in groups}
sev_cat = Counter(agg[r['permit_number']] for r in seven)
print('THE 7 TRUE-DUP GROUPS BY CATEGORY:', dict(sev_cat))
d.close()
