import json
import gzip

ordinals = {1, 50, 100, 120, 130}
targets = {}
for line in open('/var/lib/8ycb-canonical/launch-identity-ledger.jsonl'):
    row = json.loads(line)
    if row['canonical_ordinal'] in ordinals:
        targets[row['signature']] = row
print('targets')
for sig,row in targets.items(): print(row['canonical_ordinal'],sig,row['mint'],row['slot'])
found = {}
path='/var/lib/pump-exact-evidence-ledger-v1/allenhark-arrivals-v1/pump-transactions.jsonl'
with open(path,'rb') as f:
    for line in f:
        for sig in tuple(set(targets)-set(found)):
            if sig.encode() in line: found[sig]=line.decode(errors='replace').strip()
print('found',len(found))
for sig,line in found.items(): print(line)
