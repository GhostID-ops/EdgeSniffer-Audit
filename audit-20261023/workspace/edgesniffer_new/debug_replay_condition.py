import json
from pathlib import Path

root = Path('/var/lib/8ycb-canonical')

def rows(p):
    for x in p.read_text().splitlines():
        try:
            yield json.loads(x)
        except Exception:
            pass

targets = {r['primary_key']: r for r in rows(root / 'launch-identity-ledger.jsonl') if r.get('primary_key')}
by = {}
for r in rows(root / 'pump-tape/evidence.jsonl'):
    by.setdefault(int(r['canonical_ordinal']), []).append(r)
h = json.loads((root / 'pump-tape/health.json').read_text())
tw = json.loads((root / 'pump-tape/target-window-coverage.json').read_text())
for key, target in targets.items():
    if int(target['canonical_ordinal']) < 143:
        continue
    rows_for_target = by.get(int(target['canonical_ordinal']), [])
    trades = [r for r in rows_for_target if r.get('evidence_kind') == 'TRADE_EVENT']
    creates = [r for r in rows_for_target if r.get('evidence_kind') == 'CREATE']
    required = all(r.get('source_received_ns') and r.get('raw_sha256') and r.get('virtual_sol_reserves_after') is not None and r.get('virtual_token_reserves_after') is not None for r in trades)
    atomic = next((r for r in trades if r.get('side') == 'buy' and r.get('transaction_signature') == target.get('signature') and r.get('wallet') == '8YcbyX92UHTU23HZv3ccP9o13qibErQkKaUjoxqxd7SJ'), None)
    horizon = (bool(h.get('source_scan_complete')) or (tw.get('status') == 'PASS' and int(target.get('canonical_ordinal', 0)) >= 143)) and int(h.get('max_source_slot') or 0) >= int(target.get('slot') or 0) + 1000
    print(target['canonical_ordinal'], len(creates), len(trades), required, bool(atomic), horizon, tw.get('status'), h.get('max_source_slot'), target['slot'])
