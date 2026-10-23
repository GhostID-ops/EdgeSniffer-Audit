#!/usr/bin/env bash
set -euo pipefail
TS=$(date -u +%Y%m%dT%H%M%SZ)
OUT=/root/2cq-v3-workspace-audit-bundle-$TS
ARCHIVE=$OUT.tar.gz
BASE=/mnt/HC_Volume_106775198/edgesniffer-research/2cq-yellowstone-full-meta-prospective-v1
REPO=/root/bot/EdgeSniffer
mkdir -p "$OUT"/{source,systemd,process,health,receipts,diagnostics,metadata}

# Small source/config artifacts only; no ledgers, raw partitions, databases, or secrets.
copy_tree(){ src=$1; dest=$2; [ -e "$src" ] || return 0; mkdir -p "$(dirname "$dest")"; cp -a "$src" "$dest"; }
copy_tree "$BASE/autonomous-when-v2/source" "$OUT/source/autonomous-when-v2/source"
copy_tree "$BASE/autonomous-when-v3" "$OUT/source/autonomous-when-v3"
copy_tree "$REPO/rust-exact-ledger/src" "$OUT/source/rust-exact-ledger/src"
copy_tree "$REPO/rust-exact-ledger/Cargo.toml" "$OUT/source/rust-exact-ledger/Cargo.toml"
copy_tree "$REPO/rust-exact-ledger/Cargo.lock" "$OUT/source/rust-exact-ledger/Cargo.lock"
copy_tree /root/v3_adapter_diagnostic_wrapper.py "$OUT/source/diagnostics/v3_adapter_diagnostic_wrapper.py"
copy_tree /root/v3_adapter_diagnostic_run.sh "$OUT/source/diagnostics/v3_adapter_diagnostic_run.sh"
copy_tree /usr/local/lib/edgesniffer-2cq-v3-health-check.py "$OUT/source/diagnostics/edgesniffer-2cq-v3-health-check.py"

for unit in $(systemctl list-unit-files --no-legend 'edgesniffer*2cq*' 2>/dev/null | awk '{print $1}'); do
  safe=$(echo "$unit" | tr / _)
  systemctl cat "$unit" >"$OUT/systemd/$safe.cat.txt" 2>&1 || true
  systemctl show "$unit" -p ExecStart -p WorkingDirectory -p EnvironmentFile -p Environment -p Restart -p Requires -p Wants -p After -p PartOf -p KillMode -p TimeoutStartUSec -p TimeoutStopUSec >"$OUT/systemd/$safe.show.txt" 2>&1 || true
done
sed -E 's/(=).*/\1<REDACTED>/' "$REPO/.env" | sed -E 's/^#.*//; /^$/d' >"$OUT/systemd/env-variable-names-redacted.txt" || true

ps -eo pid,ppid,lstart,etimes,user,cmd --forest >"$OUT/process/ps-tree.txt"
ps -eo pid,ppid,lstart,etimes,user,cmd | grep -Ei '2cq|yellowstone|full-meta|materializer|finalizer|monitor|reconnect|adapter|autonomous-when|v2|v3' >"$OUT/process/relevant-processes.txt" || true
ss -tpn >"$OUT/process/sockets.txt" || true
for pid in $(awk 'NR>1{print $1}' "$OUT/process/relevant-processes.txt" | grep -E '^[0-9]+$' || true); do
  { echo "PID=$pid"; readlink "/proc/$pid/cwd" || true; tr '\0' '\n' <"/proc/$pid/environ" 2>/dev/null | sed -E 's/(=).*/=<REDACTED>/' | sort; } >"$OUT/process/pid-$pid.txt" 2>&1 || true
done

for r in /root/v3-a-trace-* /root/v3-b-trace-* /root/v3-both-down-* /root/v3-a-replay-interrupt-* /root/v3-fullstack-* /root/v3-adapter-forensic-*; do
  [ -d "$r" ] || continue
  d="$OUT/diagnostics/$(basename "$r")"; mkdir -p "$d"
  find "$r" -maxdepth 3 -type f \( -name '*.json' -o -name '*.log' -o -name '*.txt' -o -name 'boundary.json' \) -size -5M -print0 | while IFS= read -r -d '' f; do
    rel=${f#$r/}; mkdir -p "$d/$(dirname "$rel")"; cp -a "$f" "$d/$rel"
  done
done

find "$BASE" -maxdepth 3 -type f \( -name 'health.json' -o -name '*receipt*.json' -o -name '*manifest*.json' \) -size -5M -print0 | while IFS= read -r -d '' f; do
  rel=${f#$BASE/}; mkdir -p "$OUT/health/$(dirname "$rel")"; cp -a "$f" "$OUT/health/$rel"
done

for db in $(find /root -maxdepth 4 -path '/root/v3-*' -name '*.sqlite3' -size -100M 2>/dev/null); do
  d="$OUT/metadata/$(basename "$(dirname "$db")")-$(basename "$db")"
  { echo "PATH=$db"; stat "$db"; sha256sum "$db"; sqlite3 "$db" 'PRAGMA integrity_check; .tables' 2>&1 || true; } >"$d.txt"
  cp -a "$db" "$OUT/metadata/" || true
done

{
  echo "OS:"; cat /etc/os-release; echo "KERNEL:"; uname -a; echo "PYTHON:"; python3 --version; echo "RUST:"; /root/.cargo/bin/rustc --version || true; /root/.cargo/bin/cargo --version || true
  echo "SYSTEMD:"; systemctl --version | head -1; echo "MOUNTS:"; findmnt -T "$BASE"; echo "DISK:"; df -h "$BASE" /root; df -i "$BASE" /root; echo "MEMORY:"; free -h; echo "TIME:"; timedatectl status || true
} >"$OUT/metadata/RUNTIME_ENVIRONMENT.txt" 2>&1

{ echo "RELEVANT TREE"; find "$OUT/source" -maxdepth 6 -printf '%y %p\n' | sort; } >"$OUT/metadata/RELEVANT_TREE.txt"
{ echo "V3 EXIT PATH SEARCH"; grep -RInE 'sys\.exit|SystemExit|os\._exit|raise |assert|subprocess|check=True|kill|terminate|SIGTERM|SIGKILL|SIGINT|return 1|exit 1|fail_closed|FAIL_CLOSED' "$OUT/source" 2>&1 || true; } >"$OUT/metadata/V3_EXIT_PATH_SEARCH.txt"
{
 echo "DIRECT IMPORTS:"; grep -nE '^(import|from) ' "$OUT/source/autonomous-when-v3/source/twoCqAutonomousWhenV3.py" || true
 echo "SUBPROCESS CHILDREN:"; grep -nE 'subprocess|normalizer|sqlite3|health\.json|boundary' "$OUT/source/autonomous-when-v3/source/twoCqAutonomousWhenV3.py" || true
} >"$OUT/metadata/V3_IMPORT_MAP.txt"
if [ -d "$REPO/.git" ]; then
 { echo "ROOT=$REPO"; git -C "$REPO" branch --show-current; git -C "$REPO" rev-parse HEAD; git -C "$REPO" status --short; } >"$OUT/metadata/GIT_INFO.txt" 2>&1 || true
fi
(cd "$OUT" && find . -type f -print0 | sort -z | while IFS= read -r -d '' f; do stat -c '%n|%s|%y' "$f"; sha256sum "$f"; done) >"$OUT/FILE_INVENTORY.txt"
tar -C /root -czf "$ARCHIVE" "$(basename "$OUT")"
sha256sum "$ARCHIVE" >"$ARCHIVE.sha256"
printf '%s\n' "$ARCHIVE"
