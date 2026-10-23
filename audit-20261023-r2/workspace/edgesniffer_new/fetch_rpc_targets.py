import json
import urllib.request
from pathlib import Path

targets = [
    ('143', '2JQsnjCzrKs7WmYKiR98kYY42WXzDzS3ChqbjFkuQHjnFZ2Tz8VY5u4JhbKz1T2Ngxy9Fn4REqG1S4oLNVbb4C1g'),
    ('144', '2Drx5nGGP796T2ezeRDHWuB6V5NWajzk9v3QdFhCJA6fxKc3tdzo3MYr4ctFttpm1HUo5MHxWJvSUtxk9KuLP1VL'),
    ('145', '2wyWHGvtP2AdExKMcKNpxAajknVTAEtDF8Sfdbkn3QP9ohBDJVhrVzdHDvukL1A4sKD1WEtHSdc1CJpVvdF7Unbe'),
    ('146', '3p3C6gi86PJSBksT9hfq3NR1nGdUghx9jsNsxtwArK9U2ktfk6k4p8E9r9DPwaFucGpwShMEJ9SyxHQst5pGKEeu'),
    ('147', '4AQDpfW7GtYbb23eFzdU2qHgWTMvSZFLZyjL2nhJBV1yCcmfv5TZhyNopAx5Pn4TkpETfZZdTxwQCE69m7iBtYXm'),
]
out = Path('/var/lib/8ycb-canonical/backfill-rpc')
out.mkdir(parents=True, exist_ok=True)
for ordinal, signature in targets:
    req = urllib.request.Request(
        'https://api.mainnet-beta.solana.com',
        data=json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'getTransaction', 'params': [signature, {'commitment': 'finalized', 'encoding': 'jsonParsed', 'maxSupportedTransactionVersion': 0}]}).encode(),
        headers={'Content-Type': 'application/json'},
    )
    with urllib.request.urlopen(req, timeout=30) as response:
        value = json.load(response)
    (out / f'{ordinal}-{signature}.json').write_text(json.dumps(value, sort_keys=True) + '\n', encoding='utf-8')
    result = value.get('result') or {}
    print(json.dumps({'ordinal': ordinal, 'signature': signature, 'found': bool(result), 'slot': result.get('slot'), 'err': (result.get('meta') or {}).get('err')}, sort_keys=True))
