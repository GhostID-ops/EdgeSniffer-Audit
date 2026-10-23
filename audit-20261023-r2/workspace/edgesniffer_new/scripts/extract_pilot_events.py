import json
import sys

want = set(sys.argv[2:])
with open(sys.argv[1], encoding="utf-8") as handle:
    for line in handle:
        if not line.strip():
            continue
        obj = json.loads(line)
        if obj.get("kind") in want:
            print(json.dumps(obj, sort_keys=True))
