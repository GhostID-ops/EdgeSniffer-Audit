#!/usr/bin/env python3
import base64
import json
import time
import urllib.request
from pathlib import Path

ROOT = Path('/var/lib/8ycb-canonical')
LEDGER = ROOT / 'launch-identity-ledger.jsonl'
V4_SIGNALS = Path('/mnt/HC_Volume_106775198/edgesniffer-derived/8ycb-prospective-shadow-v4/signal_events.jsonl.gz')
OUT = ROOT / 'economics' / 'quote-semantic-audit-v1.jsonl'
PUMP = '6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P'
TARGET = '8YcbyX92UHTU23HZv3ccP9o13qibErQkKaUjoxqxd7SJ'
ALPHABET = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz'

def b58decode(value):
    n = 0
    for char in value:
        n = n * 58 + ALPHABET.index(char)
    raw = n.to_bytes((n.bit_length() + 7) // 8, 'big') if n else b''
    return b'\0' * (len(value) - len(value.lstrip('1'))) + raw

def rpc(url, signatures):
    reqs = []
    for i, sig in enumerate(signatures):
        reqs.append({'jsonrpc': '2.0', 'id': i, 'method': 'getTransaction', 'params': [sig, {'encoding': 'jsonParsed', 'commitment': 'finalized', 'maxSupportedTransactionVersion': 1}]})
    body = json.dumps(reqs).encode()
    request = urllib.request.Request(url, data=body, headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=60) as response:
        rows = json.load(response)
    return {r.get('id'): r for r in rows}

def main():
    url = next(line.strip().split('=', 1)[1] for line in open('/etc/8ycb-sniper/wallet.env') if line.startswith('SECURE_RPC_URL='))
    public_url = 'https://api.mainnet-beta.solana.com'
    ledger = [json.loads(x) for x in LEDGER.read_text().splitlines() if x.strip()]
    legacy = {}
    import gzip
    with gzip.open(V4_SIGNALS, 'rt', encoding='utf-8') as f:
        for line in f:
            row = json.loads(line)
            legacy[row['signature']] = (row.get('atomic_initial_buy') or {}).get('quote_lamports')
    results = {}
    for start in range(0, len(ledger), 10):
        batch = ledger[start:start + 10]
        for attempt in range(5):
            try:
                replies = rpc(url, [r['signature'] for r in batch])
                break
            except Exception:
                if attempt == 4:
                    replies = {}
                time.sleep(1.5 * (attempt + 1))
        missing_ids = [i for i, sig in enumerate([r['signature'] for r in batch]) if not replies.get(i, {}).get('result')]
        if missing_ids:
            missing_sigs = [[r['signature'] for r in batch][i] for i in missing_ids]
            for attempt in range(5):
                try:
                    public_replies = rpc(public_url, missing_sigs)
                    for local_id, reply in public_replies.items():
                        if reply.get('result'):
                            replies[missing_ids[local_id]] = reply
                    break
                except Exception:
                    if attempt == 4:
                        break
                    time.sleep(1.5 * (attempt + 1))
            still_missing = [i for i in missing_ids if not replies.get(i, {}).get('result')]
            for local_id in still_missing:
                for attempt in range(4):
                    try:
                        single = rpc(public_url, [[r['signature'] for r in batch][local_id]])
                        if single.get(0, {}).get('result'):
                            replies[local_id] = single[0]
                        break
                    except Exception:
                        time.sleep(1.0 * (attempt + 1))
        for idx, item in enumerate(batch):
            reply = replies.get(idx, {})
            tx = reply.get('result')
            row = {'canonical_ordinal': item['canonical_ordinal'], 'signature': item['signature'], 'mint': item['mint'], 'legacy_v4_input_lamports': legacy.get(item['signature']), 'rpc_error': reply.get('error')}
            if tx is None:
                row['evidence_status'] = 'INSUFFICIENT_TRANSACTION_EVIDENCE'
                results[item['signature']] = row
                continue
            meta = tx.get('meta') or {}
            message = (tx.get('transaction') or {}).get('message') or {}
            buy = None
            for ix_index, instruction in enumerate(message.get('instructions') or []):
                if instruction.get('programId') != PUMP or not instruction.get('data'):
                    continue
                raw = b58decode(instruction['data'])
                if len(raw) >= 25 and raw[:8].hex() == '66063d1201daebea':
                    buy = {'outer_instruction_index': ix_index, 'raw_data_hex': raw.hex(), 'token_amount_raw': int.from_bytes(raw[8:16], 'little'), 'max_sol_cost_lamports': int.from_bytes(raw[16:24], 'little'), 'final_flag': raw[24]}
            transfers = []
            groups = [message.get('instructions') or []] + [x.get('instructions') or [] for x in (meta.get('innerInstructions') or [])]
            for group in groups:
                for instruction in group:
                    parsed = instruction.get('parsed') or {}
                    info = parsed.get('info') if isinstance(parsed, dict) else None
                    if instruction.get('program') == 'system' and isinstance(info, dict) and info.get('source') == TARGET and info.get('lamports') is not None:
                        transfers.append({'lamports': int(info['lamports']), 'destination': info.get('destination'), 'type': parsed.get('type')})
            curve_candidates = [x for x in transfers if x['lamports'] >= 100_000_000]
            actual = max(curve_candidates, key=lambda x: x['lamports']) if curve_candidates else None
            row.update({'evidence_status': 'COMPLETE' if buy and actual else 'INSUFFICIENT_BUY_EVIDENCE', 'slot': tx.get('slot'), 'block_time': tx.get('blockTime'), 'transaction_error': meta.get('err'), 'buy_instruction': buy, 'curve_transfer': actual, 'wallet_total_debit_lamports': (meta.get('preBalances') or [0])[0] - (meta.get('postBalances') or [0])[0], 'network_fee_lamports': meta.get('fee'), 'pump_fee_transfers_lamports': [x['lamports'] for x in transfers if x['lamports'] == 14_250_000]})
            if buy and actual:
                row['executed_curve_quote_lamports'] = actual['lamports']
                row['legacy_used_max_cost_instead'] = row['legacy_v4_input_lamports'] == buy['max_sol_cost_lamports'] and row['legacy_v4_input_lamports'] != actual['lamports']
                row['legacy_matches_executed_quote'] = row['legacy_v4_input_lamports'] == actual['lamports']
                row['max_cost_minus_executed_lamports'] = buy['max_sol_cost_lamports'] - actual['lamports']
            results[item['signature']] = row
        time.sleep(0.2)
    OUT.write_text(''.join(json.dumps(results[r['signature']], sort_keys=True) + '\n' for r in ledger))
    from collections import Counter
    rows = list(results.values())
    print(json.dumps({'total': len(rows), 'complete': sum(r.get('evidence_status') == 'COMPLETE' for r in rows), 'insufficient': sum(r.get('evidence_status') != 'COMPLETE' for r in rows), 'legacy_error': sum(r.get('legacy_used_max_cost_instead') is True for r in rows), 'not_affected': sum(r.get('legacy_matches_executed_quote') is True for r in rows), 'legacy_values': Counter(r.get('legacy_v4_input_lamports') for r in rows if r.get('legacy_v4_input_lamports') is not None), 'path': str(OUT)}, sort_keys=True, indent=2))

if __name__ == '__main__':
    main()
