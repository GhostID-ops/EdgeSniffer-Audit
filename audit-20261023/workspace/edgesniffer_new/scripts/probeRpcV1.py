import json
import os
import urllib.request

url = os.environ.get("SECURE_RPC_URL") or os.environ.get("SOLANA_RPC_URL")
if not url:
    for line in open("/root/bot/EdgeSniffer/.env", encoding="utf-8", errors="ignore"):
        if line.startswith("SECURE_RPC_URL=") or line.startswith("SOLANA_RPC_URL="):
            url = line.split("=", 1)[1].strip().strip('"').strip("'")
            break
body = json.dumps({"jsonrpc":"2.0","id":1,"method":"getBlocks","params":[458891955,458892855,"finalized"]}).encode()
request = urllib.request.Request(url, data=body, headers={"Content-Type":"application/json"})
with urllib.request.urlopen(request, timeout=20) as response:
    print(response.read(500).decode())
