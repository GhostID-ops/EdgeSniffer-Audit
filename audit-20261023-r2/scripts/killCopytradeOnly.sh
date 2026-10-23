#!/usr/bin/env bash
#
# killCopytradeOnly.sh - stop ONLY FinalBot copytrading, never EdgeSniffer or pmarke.
#
# WHY THIS IS NOT `pkill node`:
#   * The EdgeSniffer dust lane (launchPaperTrader.ts, cwd /root/bot/EdgeSniffer) SPAWNS the
#     FinalBot-cya rust binary /root/bot/FinalBot-cya/rust-sidecar/target/release/pumpfun-sidecar.
#     A blind `pkill pumpfun-sidecar` would kill EdgeSniffer's sidecar and break dust collection.
#     So sidecars are protected by PARENT lineage, not by binary name.
#   * pmarke (/opt/pmarke, dist/index.js) is a Polymarket paper bot and must never be touched.
#
# WHAT IT STOPS:
#   * The known finalbot-* copytrade systemd units (explicit allowlist).
#   * Stray copytrade node/npm/tsx processes whose cwd is a FinalBot copytrade directory.
#   * pumpfun-sidecar processes ONLY when their parent is one of those copytrade processes.
#
# USAGE:
#   ./killCopytradeOnly.sh            # DRY RUN - prints what it would stop, changes nothing
#   ./killCopytradeOnly.sh --yes      # actually stop
#
set -uo pipefail

DRY_RUN=1
[ "${1:-}" = "--yes" ] && DRY_RUN=0

# Explicit allowlist of copytrade units. EdgeSniffer / pmarke units are deliberately absent.
COPYTRADE_UNITS=(
  finalbot.service
  finalbot-cya.service
  finalbot-control.service
  finalbot-4bdk.service
  finalbot-5qn.service
  finalbot-cya-shadow-tp30.service
  finalbot-cya-shadow-tp50.service
  finalbot-cya-shadow-tp75.service
  finalbot-marcus.service
  finalbot-mark.service
  finalbot-omego-shadow-tp30.service
)

# A process is copytrade iff its cwd matches one of these roots.
COPYTRADE_CWD_RE='(/root/bot/FinalBot-cya|/root/bot/finalbot-instances/|/root/bot/FinalBot-omego|/root/bot/FinalBot($|/))'
# Anything under these is protected no matter what.
PROTECTED_CWD_RE='(/root/bot/EdgeSniffer|/opt/pmarke)'
PROTECTED_CMD_RE='(launchPaperTrader|serveDashboard|/opt/pmarke|dist/index\.js)'

say()  { echo "$@"; }
act()  { if [ "$DRY_RUN" -eq 1 ]; then echo "  DRY-RUN would: $*"; else echo "  running: $*"; "$@"; fi; }

procwd()  { readlink -f "/proc/$1/cwd" 2>/dev/null; }
# Guard the redirection: a process can exit between `ps` and this read, and the shell
# reports the missing /proc/<pid>/cmdline before tr's 2>/dev/null can suppress it.
proccmd() { [ -r "/proc/$1/cmdline" ] || return 0; tr '\0' ' ' < "/proc/$1/cmdline" 2>/dev/null; }

say "=== killCopytradeOnly ($([ "$DRY_RUN" -eq 1 ] && echo DRY-RUN || echo LIVE)) ==="
say ""
say "1) Stopping copytrade systemd units (EdgeSniffer + pmarke untouched):"
for u in "${COPYTRADE_UNITS[@]}"; do
  state=$(systemctl is-active "$u" 2>/dev/null)
  if [ "$state" = "active" ] || [ "$state" = "activating" ]; then
    act systemctl stop "$u"
  else
    say "  skip $u (state=$state)"
  fi
done

say ""
say "2) Scanning stray copytrade processes (protecting EdgeSniffer + pmarke):"
COPY_PIDS=()
while read -r pid ppid rest; do
  [ -z "$pid" ] && continue
  case "$pid" in ''|*[!0-9]*) continue;; esac
  cmd=$(proccmd "$pid"); cwd=$(procwd "$pid")
  # Only consider node/tsx/npm copytrade entrypoints.
  echo "$cmd" | grep -qE 'src/(copytrade|botService|demoInstance|friendWeb|web|server)\.ts|npm run (bot|start|dev|friend|friend:demo|web)\b' || continue
  if echo "$cwd" | grep -qE "$PROTECTED_CWD_RE" || echo "$cmd" | grep -qE "$PROTECTED_CMD_RE"; then
    say "  PROTECT pid $pid (cwd=$cwd) - EdgeSniffer/pmarke, leaving running"
    continue
  fi
  if echo "$cwd" | grep -qE "$COPYTRADE_CWD_RE"; then
    say "  KILL    pid $pid  cwd=$cwd"
    say "          cmd=$(echo "$cmd" | cut -c1-120)"
    COPY_PIDS+=("$pid")
  fi
done < <(ps -eo pid=,ppid=,args=)

for pid in "${COPY_PIDS[@]:-}"; do
  [ -z "${pid:-}" ] && continue
  act kill -TERM "$pid"
done

say ""
say "3) Sidecars: kill ONLY those parented by a copytrade process we stopped:"
while read -r pid ppid rest; do
  [ -z "$pid" ] && continue
  case "$pid" in ''|*[!0-9]*) continue;; esac
  echo "$rest" | grep -q 'pumpfun-sidecar' || continue
  pcmd=$(proccmd "$ppid"); pcwd=$(procwd "$ppid")
  if echo "$pcwd" | grep -qE "$PROTECTED_CWD_RE" || echo "$pcmd" | grep -qE "$PROTECTED_CMD_RE"; then
    say "  PROTECT sidecar pid $pid (parent $ppid is EdgeSniffer/pmarke) - leaving running"
    continue
  fi
  if echo "$pcwd" | grep -qE "$COPYTRADE_CWD_RE" || [ "$ppid" = "1" ]; then
    # Parent is copytrade, or was reparented to init after we TERMed its copytrade parent.
    say "  KILL    sidecar pid $pid (parent $ppid cwd=$pcwd)"
    act kill -TERM "$pid"
  else
    say "  skip    sidecar pid $pid (parent $ppid cwd=$pcwd not classified)"
  fi
done < <(ps -eo pid=,ppid=,args=)

say ""
if [ "$DRY_RUN" -eq 1 ]; then
  say "DRY RUN complete - nothing was stopped. Re-run with --yes to apply."
else
  say "Done. EdgeSniffer (edgesniffer-*, launchPaperTrader) and pmarke were not touched."
  say "Verify with: npm run vps:doctor"
fi
