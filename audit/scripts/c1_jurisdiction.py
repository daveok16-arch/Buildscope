from oppintel.db import Database
from oppintel.config import active_market

d = Database('/tmp/populated.db')
m = active_market()
print('configured city_names:', list(m.city_names))
print('configured cities (name -> aliases):', [(c.name, list(c.aliases)) for c in m.cities])
print()

public = ("p.classification IN ('HIGH','MEDIUM') "
          "AND p.procurement_status IN ('CONFIRMED_OPEN','EVIDENCE_FOUND','NOT_VERIFIED')")

print('DISTINCT public-project cities (all, by raw city value):')
rows = d.conn.execute(
    f"SELECT p.city, COUNT(*) n FROM project p WHERE {public} AND p.city IS NOT NULL "
    "GROUP BY p.city ORDER BY p.city").fetchall()
for r in rows:
    print('   ', repr(r['city']), r['n'])
print('   TOTAL distinct raw values:', len(rows))

# Apply config aliases the way stat_snapshot.compute_metrics does.
accepted = list(m.city_names)
for c in m.cities:
    accepted.extend(c.aliases)
accepted_lower = {c.strip().lower() for c in accepted}
matched = sorted({r['city'] for r in rows if (r['city'] or '').strip().lower() in accepted_lower})
excluded = sorted({r['city'] for r in rows if (r['city'] or '').strip().lower() not in accepted_lower})
print()
print('in-market (accepted, incl aliases):', len(matched), matched)
print('excluded (out-of-market):', excluded)
d.close()
