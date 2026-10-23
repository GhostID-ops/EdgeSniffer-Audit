import base64,json,struct
from pathlib import Path
for p in sorted(Path('/var/lib/8ycb-canonical/backfill-rpc').glob('*.json'))[:1]:
 x=json.loads(p.read_text())['result']
 for line in x['meta']['logMessages']:
  if 'Program data: vdt/' in line:
   b=base64.b64decode(line.split('Program data: ',1)[1])[8:]
   print('len',len(b))
   for i in range(0,160,8): print(i,b[i:i+8].hex(),struct.unpack('<Q',b[i:i+8])[0])
   print('bytes 32-80',list(b[32:80]))
   for off in [81,89,97,105,113,121,129,137,145,153,161,169,177,185,193,201,209,217,225,233,241,249,257,265,273,281,289,297,305,313,321,329,337,345,353]:
    if off+8<=len(b): print('unaligned',off,struct.unpack_from('<Q',b,off)[0])
   break
