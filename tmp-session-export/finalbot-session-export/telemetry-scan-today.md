
> finalbot@1.0.0 telemetry:scan-today
> node scripts/scanTodayTelemetry.mjs

## Latest VPS Telemetry Scan - Wallet

Updated: 2026-06-26T13:13:09.130Z
Window: today (2026-06-26T00:00:00.000Z)

### Trade Sample

- Trades logged: 12
- Buys: 5
- Unique bought mints: 4
- Sells: 7
- Realized PnL: 0.0147 SOL
- Closed-trade win rate: 28.6%
- Trades with market cap recorded: 0
- Trades missing market cap: 12

### Execution Telemetry

- Telemetry rows: 0
- Success rows: 0 (0.0%)
- Failure rows: 0 (0.0%)
- Buy chains: 0
- Same-slot buys: 0 (0.0%)
- Next-slot buys: 0 (0.0%)
- 2-3 slot buys: 0 (0.0%)
- Late buys: 0 (0.0%)
- Unknown slot delta buys: 0

### Latency

- Provider -> broadcast p50/p90/p99: - / - / -
- Process -> broadcast p50/p90/p99: - / - / -
- Source slot -> landing slot p50/p90/p99: - / - / -

### Routes

_None._

### Detectors

_None._

### Failures

_None._

### Latest Buy Chains

_No buy chains with broadcast/slot data in this window._

### Read

- Same-slot/next-slot quality is measured by confirmed source slot -> landing slot delta.
- Provider -> broadcast shows bot speed before confirmation noise.
- Missing market-cap rows mean entry MC was not available in the trade log; use slot/latency plus source-wallet research before overfitting.
- If same-slot is low but provider -> broadcast is already low, the bottleneck is likely sender/fee/leader auction rather than detection.

