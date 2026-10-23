targets = {
    '2JQsnjCzrKs7WmYKiR98kYY42WXzDzS3ChqbjFkuQHjnFZ2Tz8VY5u4JhbKz1T2Ngxy9Fn4REqG1S4oLNVbb4C1g',
    '2Drx5nGGP796T2ezeRDHWuB6V5NWajzk9v3QdFhCJA6fxKc3tdzo3MYr4ctFttpm1HUo5MHxWJvSUtxk9KuLP1VL',
    '2wyWHGvtP2AdExKMcKNpxAajknVTAEtDF8Sfdbkn3QP9ohBDJVhrVzdHDvukL1A4sKD1WEtHSdc1CJpVvdF7Unbe',
    '3p3C6gi86PJSBksT9hfq3NR1nGdUghx9jsNsxtwArK9U2ktfk6k4p8E9r9DPwaFucGpwShMEJ9SyxHQst5pGKEeu',
    '4AQDpfW7GtYbb23eFzdU2qHgWTMvSZFLZyjL2nhJBV1yCcmfv5TZhyNopAx5Pn4TkpETfZZdTxwQCE69m7iBtYXm',
}
path = '/var/lib/pump-exact-evidence-ledger-v1/allenhark-arrivals-v1/pump-transactions.jsonl'
found = {}
with open(path, 'rb') as f:
    for line in f:
        for sig in tuple(targets - set(found)):
            if sig.encode() in line:
                found[sig] = line.decode(errors='replace').strip()
print('found', len(found))
for sig, line in found.items():
    print(line)
