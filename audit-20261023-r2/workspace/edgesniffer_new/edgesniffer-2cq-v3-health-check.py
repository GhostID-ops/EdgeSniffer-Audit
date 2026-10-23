#!/usr/bin/env python3
"""Read-only V3 health assertion for monitors/timers; never restarts services."""
from __future__ import annotations
import json, pathlib, sys, time

root = pathlib.Path(sys.argv[1])
health = json.loads((root / "health.json").read_text())
g = health.get("global", {})
obs = health.get("observers", {})
now = time.time_ns()
for name in ("A", "B"):
    row = obs.get(name, {})
    receipt = int(row.get("lastLiveReceiptNs") or 0)
    if row.get("state") == "LIVE" and now - receipt > 90_000_000_000:
        raise SystemExit(f"STALE_LIVE_WITNESS:{name}")
if health.get("globalState") == "FAIL_CLOSED" or g.get("terminalReason"):
    raise SystemExit("TERMINAL_SCIENTIFIC_FAILURE")
if not any(obs.get(name, {}).get("state") == "LIVE" for name in ("A", "B")):
    raise SystemExit("NO_LIVE_OBSERVER")
print(json.dumps({"ok": True, "globalState": health.get("globalState"), "sourceFrontier": g.get("sourceFrontier")}, sort_keys=True))
