from oppintel.db import Database
d = Database('/tmp/populated.db')

# The 7 true-dup groups (WP1 duplicate_permit definition)
true_dups = [r['permit_number'] for r in d.conn.execute('''
  SELECT permit_number FROM permit
   WHERE permit_number IS NOT NULL AND permit_number <> ''
     AND address_key IS NOT NULL AND permit_date IS NOT NULL
   GROUP BY source_id, permit_number, address_key, permit_date HAVING COUNT(*)>1
''').fetchall()]
print('true dup groups:', len(true_dups), true_dups)

rows = d.conn.execute('''
  SELECT permit_number, COUNT(*) n,
         COUNT(DISTINCT address_key) addr_n,
         COUNT(DISTINCT permit_date) date_n,
         COUNT(DISTINCT permit_type) type_n
    FROM permit
   WHERE permit_number IS NOT NULL AND permit_number <> ''
   GROUP BY source_id, permit_number HAVING COUNT(*)>1
''').fetchall()

cats = {}
for r in rows:
    pn = r['permit_number']
    # placeholder-style numbers have a non-numeric suffix
    placeholder = not pn.split('-')[-1].isdigit()
    same_addr_date = r['addr_n'] == 1 and r['date_n'] == 1
    if same_addr_date:
        c = 'identical_same_address_date'
    elif r['addr_n'] == 1:
        c = 'same_address_multiple_dates(revision_inspection)'
    elif placeholder:
        c = 'placeholder_number_distinct_addresses'
    else:
        c = 'real_number_distinct_addresses'
    cats.setdefault(c, []).append(dict(r) | {'placeholder': placeholder})

tot = 0
for c, xs in sorted(cats.items()):
    tot += len(xs)
    print(f'{c}: {len(xs)} groups, {sum(x["n"]-1 for x in xs)} excess rows')
print('TOTAL:', tot)
print()
for c, xs in sorted(cats.items()):
    print(f'--- {c} (5 ex) ---')
    for x in xs[:5]:
        print('   ', x['permit_number'], 'n=', x['n'], 'addr=', x['addr_n'], 'date=', x['date_n'], 'type=', x['type_n'])
d.close()
