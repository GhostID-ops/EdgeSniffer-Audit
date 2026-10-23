import sqlite3
import json

p = '/var/lib/pump-exact-evidence-ledger-v1/state/evidence-ids.bin'
db = '/var/lib/pump-exact-evidence-ledger-v1/ledger.sqlite3'
for candidate in [db, '/var/lib/pump-exact-evidence-ledger-v1/state/ledger.sqlite3', '/var/lib/pump-exact-evidence-ledger-v1/pump-exact-evidence.sqlite3']:
    try:
        con = sqlite3.connect('file:' + candidate + '?mode=ro', uri=True)
        print('DB', candidate)
        for row in con.execute("select name,sql from sqlite_master where type='table'"):
            print(row[0], row[1][:800])
        print('counts')
        for table in ['transactions','events','causal_events','create_events','evidence_files']:
            try: print(table, con.execute('select count(*) from '+table).fetchone()[0])
            except Exception: pass
        con.close()
    except Exception as e:
        print('NO', candidate, str(e))
