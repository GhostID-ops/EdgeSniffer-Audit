import struct
import json
import subprocess

p = '/var/lib/pump-exact-evidence-ledger-v1/partitions/2026/09/22/18/evidence.frames.zst'
q = subprocess.Popen(['/usr/bin/zstd', '-dc', p], stdout=subprocess.PIPE)
f = q.stdout
magic = f.read(8)
h = f.read(8)
a, b = struct.unpack('<II', h)
payload = f.read(a)
print('magic', magic, 'a', a, 'b', b, 'head', payload[:40], 'tail', payload[-100:])
row = json.loads(payload)
print('row keys', sorted(row), 'lengths', {k: row.get(k) for k in row if 'raw' in k.lower() or 'frame' in k.lower() or 'len' in k.lower() or 'size' in k.lower()})
print('next', f.read(200))
