from oppintel.db import Database
d = Database('/tmp/populated.db')

# The 7 true-dup groups as (permit_number, address_key, permit_date)
td = d.conn.execute('''
  SELECT source_id, permit_number, address_key, permit_date, COUNT(*) n
    FROM permit
   WHERE permit_number IS NOT NULL AND permit_number <> ''
     AND address_key IS NOT NULL AND permit_date IS NOT NULL
   GROUP BY source_id, permit_number, address_key, permit_date HAVING COUNT(*)>1
''').fetchall()
print('the 7 true-dup groups:')
for r in td:
    print('   ', r['permit_number'], '|', r['address_key'], '|', r['permit_date'], '| n=', r['n'])
print()

# Classify each true-dup permit_number into a 61-category
def category(pn, addr_n, date_n):
    placeholder = not pn.split('-')[-1].isdigit()
    if addr_n == 1 and date_n == 1:
        return 'identical_same_address_date'
    if addr_n == 1:
        return 'same_address_multiple_dates'
    if placeholder:
        return 'placeholder_number_distinct_addresses'
    return 'real_number_distinct_addresses'

agg = {}
for r in d.conn.execute('''
  SELECT permit_number, COUNT(*) n, COUNT(DISTINCT address_key) a, COUNT(DISTINCT permit_date) dt
    FROM permit WHERE permit_number IS NOT NULL AND permit_number <> ''
   GROUP BY source_id, permit_number HAVING COUNT(*)>1
''').fetchall():
    agg[r['permit_number']] = category(r['permit_number'], r['a'], r['dt'])

from collections import Counter
c = Counter(agg[r['permit_number']] for r in td)
print('the 7 broken down by 61-category:', dict(c))
d.close()
