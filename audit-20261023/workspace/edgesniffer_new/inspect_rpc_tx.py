import json
from pathlib import Path

for path in sorted(Path('/var/lib/8ycb-canonical/backfill-rpc').glob('*.json')):
    x = json.loads(path.read_text())['result']
    tx = x['transaction']
    meta = x['meta']
    print('\nORD', path.name, 'slot', x['slot'], 'time', x['blockTime'], 'err', meta['err'])
    print('message keys', tx['message'].keys())
    print('accountKeys', len(tx['message']['accountKeys']))
    print('instructions', len(tx['message']['instructions']), 'inner', len(meta.get('innerInstructions') or []), 'logs', len(meta.get('logMessages') or []))
    for i, ix in enumerate(tx['message']['instructions']):
        print('outer', i, ix)
    for group in meta.get('innerInstructions') or []:
        for i, ix in enumerate(group['instructions']):
            if ix.get('program') == 'pump.fun' or ix.get('programId') == '6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P':
                print('inner', group['index'], i, ix)
    for line in meta.get('logMessages') or []:
        if 'Program data' in line or 'pump' in line.lower(): print('log', line)
