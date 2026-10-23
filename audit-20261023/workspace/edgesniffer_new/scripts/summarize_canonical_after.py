import json
import sys

threshold = int(sys.argv[2])
for line in open(sys.argv[1], encoding="utf-8"):
    obj = json.loads(line)
    if obj.get("record_type") == "canonical_launch_identity" and int(obj.get("source_receive_ns", 0)) > threshold:
        print(json.dumps({k: obj.get(k) for k in ("canonical_ordinal", "signature", "mint", "slot", "source_receive_ns", "v5_signal", "v4_outcome_present", "network_submission_count")}, sort_keys=True))
