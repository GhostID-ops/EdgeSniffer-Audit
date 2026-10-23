import json, pathlib, sys
sys.path.insert(0, str(pathlib.Path(__file__).parent))
import pumpSwapExactEconomicsV1 as p
from materializeWalletRouteCensusV1 import pumpswap_tx_with_extension
path=pathlib.Path(sys.argv[1]); wallet=sys.argv[2]
counts={"pump":0,"valid":0,"buys":0,"wallet_buys":0,"errors":{}}
for line in path.open(encoding="utf-8"):
    row=json.loads(line)
    if row.get("program_family") != "PUMPSWAP_PROGRAM": continue
    counts["pump"]+=1
    try:
        tx, _=pumpswap_tx_with_extension(row.get("transaction") or {})
        transitions=p.validate_transaction(tx)
        counts["valid"]+=1
        counts["buys"]+=sum(t.get("side")=="BUY" for t in transitions)
        counts["wallet_buys"]+=sum(t.get("side")=="BUY" and t.get("user")==wallet for t in transitions)
    except Exception as e:
        key=type(e).__name__+":"+str(e)
        counts["errors"][key]=counts["errors"].get(key,0)+1
print(json.dumps(counts,sort_keys=True))
