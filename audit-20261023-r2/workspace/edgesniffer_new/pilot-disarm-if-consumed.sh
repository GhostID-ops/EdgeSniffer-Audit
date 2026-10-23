#!/usr/bin/env bash
set -euo pipefail
state=/var/lib/8ycb-live-dust-pilot/state.json
arm=/etc/8ycb-live-dust-pilot/ARMED
if [ -f "$state" ] && /usr/bin/python3 - "$state" <<'PY'
import json,sys
r=json.load(open(sys.argv[1]))
raise SystemExit(0 if int(r.get('submission_call_count',0) or 0)>0 or int(r.get('live_calibration_trades_used',0) or 0)>0 else 1)
PY
then
  rm -f -- "$arm"
fi
exit 0
