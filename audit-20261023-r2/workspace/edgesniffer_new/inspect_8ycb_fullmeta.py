import json
import sqlite3

ledger = '/var/lib/8ycb-canonical/launch-identity-ledger.jsonl'
db = '/mnt/HC_Volume_106775198/edgesniffer-research/2cq-yellowstone-full-meta-prospective-v1/state-plane/full-meta-canonical-state.sqlite3'
targets = [json.loads(line) for line in open(ledger, encoding='utf-8') if line.strip()]
targets = [row for row in targets if row['canonical_ordinal'] >= 143]
con = sqlite3.connect('file:' + db + '?mode=ro', uri=True)
for row in targets:
    sig = row['signature']
    mint = row['mint']
    tx = con.execute('select signature,block_time_s,slot,tx_index,kinds_json,hinted_mints_json from transactions where signature=?', (sig,)).fetchone()
    creates = con.execute('select signature,mint from create_events where signature=? or mint=?', (sig, mint)).fetchall()
    causal = con.execute('select signature,instruction_index,mint,is_buy,user,creator,quote_lamports,token_raw,pre_virtual_sol,pre_virtual_token,pre_real_sol,pre_real_token,post_virtual_sol,post_virtual_token,post_real_sol,post_real_token from causal_events where signature=? or mint=? order by signature,instruction_index', (sig, mint)).fetchall()
    evidence = con.execute('select te.path,ef.size,ef.successful_tx_n,ef.pump_tx_n,ef.event_n,ef.create_n,ef.incomplete_n from transaction_evidence te join evidence_files ef on ef.path=te.path where te.signature=?', (sig,)).fetchall()
    incomplete = con.execute('select signature,mint,reason from incomplete_events where signature=? or mint=?', (sig, mint)).fetchall()
    print(json.dumps({'ordinal':row['canonical_ordinal'],'signature':sig,'mint':mint,'transaction':tx,'creates':creates,'causal_n':len(causal),'causal':causal[:20],'evidence':evidence,'incomplete':incomplete}, default=list, sort_keys=True))
