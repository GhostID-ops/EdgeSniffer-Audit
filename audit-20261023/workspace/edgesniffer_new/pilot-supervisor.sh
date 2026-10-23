#!/usr/bin/env bash
set -euo pipefail

state=${STATE_PATH:?STATE_PATH required}
status=${PILOT_STATUS_PATH:?PILOT_STATUS_PATH required}
binary=/usr/local/lib/8ycb-sniper/eight-ycb-sniper

"$binary" &
child=$!

while kill -0 "$child" 2>/dev/null; do
  if [ -f "$state" ]; then
    /usr/bin/python3 - "$state" "$status" <<'PY'
import json,os,sys,tempfile,time
state_path,status_path=sys.argv[1:]
try:
    s=json.load(open(state_path))
except Exception:
    raise SystemExit(0)
count=int(s.get('submission_call_count',0) or 0)
used=int(s.get('live_calibration_trades_used',0) or 0)
enabled=s.get('new_entries_enabled') is True
if count>0 or used>0 or not enabled:
    out={'schema':'8ycb-live-dust-pilot-status-v1','armed':False,'submission_count':count,'live_trades_used':used,'reason':'AUTO_DISARM_AFTER_FIRST_SUBMISSION_OR_CONSUMED_LATCH','updated_unix_ms':int(time.time()*1000)}
    d=os.path.dirname(status_path)
    fd,tmp=tempfile.mkstemp(prefix='.pilot-status.',dir=d,text=True)
    with os.fdopen(fd,'w') as f:
        json.dump(out,f,sort_keys=True); f.write('\\n'); f.flush(); os.fsync(f.fileno())
    os.replace(tmp,status_path)
PY
  fi
  sleep 0.05
done
wait "$child"
rc=$?
/usr/bin/python3 - "$status" "$rc" <<'PY'
import json,os,sys,tempfile,time
p,rc=sys.argv[1:]
out={'schema':'8ycb-live-dust-pilot-status-v1','armed':False,'submission_count':0,'live_trades_used':0,'reason':'PROCESS_EXIT_FAIL_CLOSED','process_exit_code':int(rc),'updated_unix_ms':int(time.time()*1000)}
d=os.path.dirname(p)
fd,tmp=tempfile.mkstemp(prefix='.pilot-status.',dir=d,text=True)
with os.fdopen(fd,'w') as f:
    json.dump(out,f,sort_keys=True); f.write('\\n'); f.flush(); os.fsync(f.fileno())
os.replace(tmp,p)
PY
exit "$rc"
