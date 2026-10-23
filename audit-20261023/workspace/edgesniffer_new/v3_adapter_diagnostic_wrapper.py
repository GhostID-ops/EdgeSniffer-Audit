#!/usr/bin/env python3
"""Disposable process-lifecycle recorder for the V3 adapter."""
from __future__ import annotations
import argparse, json, os, pathlib, signal, subprocess, sys, time, traceback
from datetime import datetime, timezone

def stamp(): return datetime.now(timezone.utc).isoformat().replace('+00:00','Z')
def write(path, value):
    tmp=path.with_suffix(path.suffix+'.tmp')
    tmp.write_text(json.dumps(value,sort_keys=True,indent=2)+'\n')
    os.replace(tmp,path)
def main():
    p=argparse.ArgumentParser(); p.add_argument('--trace',type=pathlib.Path,required=True); p.add_argument('--health',type=pathlib.Path,required=True); p.add_argument('command',nargs=argparse.REMAINDER); a=p.parse_args()
    if not a.command or a.command[0] != '--': raise SystemExit('COMMAND_MUST_FOLLOW_DOUBLE_DASH')
    cmd=a.command[1:]; a.trace.mkdir(parents=True,exist_ok=False)
    env=os.environ.copy(); env['PYTHONFAULTHANDLER']='1'; env['PYTHONUNBUFFERED']='1'
    meta={'startedUtc':stamp(),'command':cmd,'cwd':os.getcwd(),'parentPid':os.getpid(),'envKeys':sorted(k for k in env if not any(x in k.upper() for x in ('TOKEN','SECRET','KEY')))}
    write(a.trace/'start.json',meta)
    out=(a.trace/'stdout.log').open('wb'); err=(a.trace/'stderr.log').open('wb')
    child=subprocess.Popen(cmd,stdout=out,stderr=err,env=env,start_new_session=True)
    write(a.trace/'process.json',{'adapterPid':child.pid,'processGroup':child.pid,'startedUtc':stamp()})
    ticks=0
    try:
        while child.poll() is None:
            ticks+=1; snapshot={'atUtc':stamp(),'heartbeat':ticks,'adapterPid':child.pid}
            try: snapshot['health']=json.loads(a.health.read_text())
            except Exception as exc: snapshot['healthReadError']=repr(exc)
            write(a.trace/'heartbeat.json',snapshot)
            time.sleep(1)
    except BaseException as exc:
        write(a.trace/'wrapper-exception.json',{'atUtc':stamp(),'error':repr(exc),'traceback':traceback.format_exc()})
        os.killpg(child.pid,signal.SIGTERM); child.wait(timeout=20)
        raise
    finally:
        out.flush(); os.fsync(out.fileno()); out.close(); err.flush(); os.fsync(err.fileno()); err.close()
    code=child.returncode
    write(a.trace/'termination.json',{'finishedUtc':stamp(),'adapterPid':child.pid,'exitCode':code if code >= 0 else None,'signal':None if code >= 0 else -code,'wrapperExitCode':0 if code == 0 else 1,'finalHealth':json.loads(a.health.read_text()) if a.health.exists() else None})
    return 0 if code == 0 else 1
if __name__=='__main__': raise SystemExit(main())
