import subprocess

p = '/var/lib/pump-exact-evidence-ledger-v1/partitions/2026/09/22/18/evidence.frames.zst'
q = subprocess.Popen(['/usr/bin/zstd', '-dc', p], stdout=subprocess.PIPE)
d = q.stdout.read(2000000)
needle = b'{"schemaVersion"'
positions = []
i = 0
while True:
    j = d.find(needle, i)
    if j < 0:
        break
    positions.append(j)
    i = j + 1
print(positions[:10])
for pos in positions[:3]:
    print(pos, d[pos:pos+24].hex())
print('around_next', d[3540:3635].hex())
print('frame1_tail_lengths', d[3550:3602].hex())
