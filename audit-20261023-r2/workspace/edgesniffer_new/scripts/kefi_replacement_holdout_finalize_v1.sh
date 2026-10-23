#!/usr/bin/env bash
# Freeze the replacement holdout only after the observer has sealed exactly one
# completed run. Any non-target exit fails this post-stop step closed.
set -euo pipefail

MANIFEST=${KEFI_REPLACEMENT_HOLDOUT_MANIFEST:?KEFI_REPLACEMENT_HOLDOUT_MANIFEST is required}
mapfile -t BINDINGS < <(/usr/bin/python3 - "$MANIFEST" <<'PY'
import json, sys
m=json.load(open(sys.argv[1]))
print(m['cohort_root'])
print(m['ledger_path'])
print(m['boundary_path'])
PY
)
ROOT=${BINDINGS[0]}
LEDGER=${BINDINGS[1]}
BOUNDARY=${BINDINGS[2]}
# The same manifest verifier used at service start also protects post-stop
# freezing from environment/path drift.
/usr/bin/python3 /root/bot/EdgeSniffer/tools/kefi_replacement_holdout_launch_wrapper_v1.py --manifest "$MANIFEST" --verify-only --allow-ledger-growth
mapfile -t SEALED < <(find "$ROOT" -mindepth 1 -maxdepth 1 -type d -name 'validation-*' -printf '%p\n' | sort)

test -f "$BOUNDARY"
test -f "$LEDGER"
test "${#SEALED[@]}" -eq 1
exec /usr/bin/python3 /root/bot/EdgeSniffer/tools/kefi_replacement_holdout_freeze_v1.py \
  --boundary "$BOUNDARY" \
  --sealed-observer-root "${SEALED[0]}" \
  --ledger "$LEDGER"
