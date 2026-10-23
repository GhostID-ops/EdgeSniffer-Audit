# EdgeSniffer independent strategy lab v1

This is the parallel research lane for finding an executable strategy while
native-matched 2CQ anchors continue collecting. It is intentionally separate
from the old 2CQ-anchor-conditioned Pump curve lab.

The materializer starts from the market-wide normalized receive-only stream,
not from a teacher-wallet buy. It admits a window only when the route and
exact Pump curve reserve state are present. The evaluator then applies the
real one-second causal entry delay, venue fees, integer curve impact, declared
slippage stress, network cost, unavailable exits, and fixed/dynamic exit
rules at 0.1, 0.5, and 1.0 SOL.

The current output is discovery-only. It cannot freeze a policy, promote a
candidate, or enable trading. Teacher-wallet matches may be retained as a
label for later analysis, but they never create an independent signal.

## Commands

```text
python scripts/materializeIndependentStrategyWindowsV1.py \
  --input <normalized-observations.jsonl> \
  --output research/edgesniffer-independent-strategy-lab-v1/windows.jsonl

python scripts/runIndependentStrategyLabV1.py \
  --input research/edgesniffer-independent-strategy-lab-v1/windows.jsonl \
  --output research/edgesniffer-independent-strategy-lab-v1/discovery-report.json
```

If the current stream has no explicit route/reserve state, the materializer
will produce zero admitted windows and an explicit rejection count. That is a
coverage finding, not a negative trading result. No approximation is allowed.

## Native state bridge

`scripts/enrichNativePumpCurveStateV1.py` runs as
`edgesniffer-independent-pump-state-bridge-v1.service`. It starts at the live
tail, preserves the native receive timestamp, waits for finalized RPC
availability, retries transient finality gaps, and writes terminal decoder or
failed-transaction rows to a separate rejection file. Only observations with
the canonical Pump program in their preserved account lineage are admitted.

`refreshIndependentStrategyLabV1.py` runs as
`edgesniffer-independent-strategy-lab-refresh-v1.service`, refreshing the
windows and discovery report every 60 seconds. Both services are read-only;
neither can submit or enable execution.
