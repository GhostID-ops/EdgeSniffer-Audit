#!/usr/bin/env python3
import json
import os
import time
import urllib.request

SIGNATURE = "36CLA5v4Lr6bN11TD9UqWt8huHaw2dSaWQV5vM27fCjkrMiy1n7t1eFVdLdaa22GvB9WTLc5WSUmvhDa3AYAm89f"
WALLET = "2qfCBm5bNcQhDuikruVk91QQm6yVH9ZjjTaPfCRUGFyd"
MINT = "9Zr7Un5XvCtuVqbkqZNNCC2tMyxFKiiH5jHXuQ8Upump"
ATA = "Dq4YTiwV1bGr8pEG2K5jhCVSQQwihefxX7jHWpJjcpZc"
PROGRAMS = [
    "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
    "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb",
]


def load_env(path):
    out = {}
    with open(path, encoding="utf-8", errors="replace") as handle:
        for line in handle:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                out[key.strip()] = value.strip().strip("\"").strip("'")
    return out


RPC = load_env("/root/bot/FinalBot-cya/.env")["SECURE_RPC_URL"]


def rpc(method, params):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    req = urllib.request.Request(RPC, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as response:
        payload = json.load(response)
    if payload.get("error"):
        raise RuntimeError(payload["error"])
    return payload.get("result")


status = None
for _ in range(40):
    status = rpc("getSignatureStatuses", [[SIGNATURE], {"searchTransactionHistory": True}])["value"][0]
    if status and status.get("confirmationStatus") == "finalized":
        break
    time.sleep(0.25)

tx = rpc("getTransaction", [SIGNATURE, {"encoding": "jsonParsed", "commitment": "finalized", "maxSupportedTransactionVersion": 0}])
keys = tx["transaction"]["message"]["accountKeys"]
keys = [item.get("pubkey") if isinstance(item, dict) else item for item in keys]
wallet_index = keys.index(WALLET)
meta = tx["meta"]
wallet_delta = int(meta["postBalances"][wallet_index]) - int(meta["preBalances"][wallet_index])

nonzero = []
all_accounts = []
for program in PROGRAMS:
    result = rpc("getTokenAccountsByOwner", [WALLET, {"programId": program}, {"encoding": "jsonParsed", "commitment": "finalized"}])
    for item in result["value"]:
        info = item["account"]["data"]["parsed"]["info"]
        amount = int(info["tokenAmount"]["amount"])
        row = {
            "ata": item["pubkey"],
            "mint": info["mint"],
            "amountRaw": amount,
            "uiAmount": info["tokenAmount"].get("uiAmountString"),
            "program": program,
            "lamports": item["account"]["lamports"],
        }
        all_accounts.append(row)
        if amount:
            nonzero.append(row)

ata_info = rpc("getAccountInfo", [ATA, {"encoding": "jsonParsed", "commitment": "finalized"}])["value"]
print(json.dumps({
    "signatureStatus": status,
    "transactionSlot": tx["slot"],
    "transactionError": meta.get("err"),
    "transactionFeeLamports": meta.get("fee"),
    "walletDeltaLamports": wallet_delta,
    "actualProceedsLamports": wallet_delta + int(meta.get("fee") or 0),
    "mintBalanceRaw": sum(a["amountRaw"] for a in all_accounts if a["mint"] == MINT),
    "nonzeroTokenAccounts": nonzero,
    "ata": {
        "exists": ata_info is not None,
        "lamports": ata_info.get("lamports") if ata_info else None,
        "owner": ata_info.get("owner") if ata_info else None,
        "data": ata_info.get("data") if ata_info else None,
    },
}, indent=2))
