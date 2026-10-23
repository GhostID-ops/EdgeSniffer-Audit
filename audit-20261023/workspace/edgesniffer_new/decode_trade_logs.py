import base64
import json
import struct
from pathlib import Path

for path in sorted(Path('/var/lib/8ycb-canonical/backfill-rpc').glob('*.json')):
    x = json.loads(path.read_text())['result']
    print('ORD', path.name)
    for line in x['meta'].get('logMessages') or []:
        if 'Program data: vdt/' not in line:
            continue
        raw = base64.b64decode(line.split('Program data: ',1)[1])
        print('len',len(raw),'disc',raw[:8].hex())
        body=raw[8:]
        print('body first',body[:80].hex())
        for off in [0,32,40,48,56,64,72,80,88,96,104,112,120,128]:
            if off+8 <= len(body):
                print(off, struct.unpack_from('<Q',body,off)[0])
        break
