import json
root='/var/lib/8ycb-canonical'
report=json.load(open(root+'/canonical-report.json'))
ledger=[json.loads(x) for x in open(root+'/launch-identity-ledger.jsonl')]
for n in range(143,148):
 r=next(x for x in ledger if x['canonical_ordinal']==n)
 print(n,r.get('economic_finalization_status'),r.get('canonical_tape_outcome_present'),(r.get('canonical_tape_economic_result') or {}).get('primary',{}).get('net_roi_pct'))
print('metrics',report['closed_canonical_economic_outcomes'],report['metrics'])
print('v5_unrecoverable',len(report['v5_only_unrecoverable']))
for r in report['true_new_after_original_141']:
 if r.get('canonical_ordinal')>=143: print('report',r['canonical_ordinal'],r.get('economic_finalization_status'),r.get('canonical_tape_outcome_present'))
