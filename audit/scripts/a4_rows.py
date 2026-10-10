from oppintel.db import Database
d = Database('/tmp/populated.db')

def show(pn, where=''):
    print(f'=== permit_number={pn} {where} ===')
    for r in d.conn.execute(
        "SELECT id, natural_key, permit_type, permit_date, address_key, work_description, status, job_value, square_footage FROM permit WHERE permit_number=? ORDER BY id", (pn,)
    ).fetchall():
        print('  ', dict(r))

for pn in ('26-PLOT', '26-PLANS', '25-004694', '26-PERMIT'):
    show(pn)
    print()
d.close()
