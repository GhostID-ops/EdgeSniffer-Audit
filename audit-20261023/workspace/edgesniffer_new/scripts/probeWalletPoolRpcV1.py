import json, pathlib, sys, os
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from captureWalletPoolWindowsV1 import Rpc, exact_buy
path=pathlib.Path(sys.argv[1]); url=os.environ.get("SECURE_RPC_URL") or os.environ.get("SOLANA_RPC_URL")
if not url:
    for line in open("/root/bot/EdgeSniffer/.env", encoding="utf-8", errors="ignore"):
        if line.startswith("SECURE_RPC_URL=") or line.startswith("SOLANA_RPC_URL="):
            url=line.split("=",1)[1].strip().strip('"').strip("'"); break
client=Rpc(url)
for line in path.open(encoding="utf-8"):
    row=json.loads(line)
    if row.get("schema") != "WALLET_FINALIZED_ROUTE_RAW_TX_V1": continue
    transition=exact_buy(row)
    if transition:
        pool=transition["pool"]
        print(json.dumps({"pool":pool,"result":client.call("getSignaturesForAddress",[pool,{"commitment":"finalized","limit":5}])},sort_keys=True))
        break
