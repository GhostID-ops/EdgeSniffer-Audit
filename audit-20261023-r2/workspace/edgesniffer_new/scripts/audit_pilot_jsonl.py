import collections
import json
import sys

path = sys.argv[1]
counts = collections.Counter()
rows = []
with open(path, encoding="utf-8") as handle:
    for line in handle:
        if not line.strip():
            continue
        obj = json.loads(line)
        counts[obj.get("kind", "NONE")] += 1
        rows.append(obj)
print("COUNTS", dict(counts))
for obj in rows:
    if any(key in obj for key in ("rejection_reason", "signal_id", "submission_count", "mint")):
        keys = ("kind", "signature", "signal_id", "mint", "rejection_reason", "submission_count", "stage", "error", "route")
        print(json.dumps({key: obj.get(key) for key in keys if key in obj}, sort_keys=True))
