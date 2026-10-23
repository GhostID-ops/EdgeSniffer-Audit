import json
from collections import Counter
root='/var/lib/8ycb-canonical'
rows=[json.loads(x) for x in open(root+'/pump-tape/evidence.jsonl')]
for n in range(143,148):
 r=[x for x in rows if x.get('canonical_ordinal')==n and x.get('evidence_kind')=='TRADE_EVENT']
 print(n,len(r),Counter(x.get('wallet') for x in r).most_common(5),r[0] if r else None)
led=[json.loads(x) for x in open(root+'/launch-identity-ledger.jsonl')]
for x in led:
 if x.get('canonical_ordinal') in range(143,148): print('ledger',x['canonical_ordinal'],{k:x.get(k) for k in ['primary_key','source_receive_ns','slot','signature','mint']},len(x.get('source_records',[])),x.get('sources'))
