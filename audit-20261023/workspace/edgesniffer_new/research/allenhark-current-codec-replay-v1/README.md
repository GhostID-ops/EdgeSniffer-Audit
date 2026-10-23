# AllenHark current-codec replay gate

This is an offline-only replay utility for preserved UDP/20000 raw-shred captures.
It uses the current Jito Solana Merkle-shred parser and `wincode` `Entry` decoder at
revision `58704cca75f4139cbab4778a45b6b81eff3cc5db`. It does not bind a socket,
connect to the proxy, restart a service, or write to a live collector namespace.

Example:

```text
allenhark-current-codec-replay-v1 \
  --pcap raw-shreds-variant-audit-20261020T213500Z.pcap \
  --out current-codec-replay-v1.json \
  --target 9qz7cWBqYBP2TnGfzMmxX64G4PQip7HtogjPBBnMhWpw
```

The output records pcap SHA-256, packet/shred parse counts, complete deshred
ranges, current `wincode` successes/failures, entry/transaction counts, and
target-wallet account/fee-payer matches. A zero target count is a coverage
result for that capture; it is not evidence that the decoder is broken.

The same binary also supports the receive-only shadow mode used by
`edgesniffer-2cq-current-codec-live-shadow-v1.service`:

```text
allenhark-current-codec-replay-v1 \
  --live-interface eth0 \
  --target 9qz7cWBqYBP2TnGfzMmxX64G4PQip7HtogjPBBnMhWpw \
  --receipt-out /path/to/target-receipts.jsonl \
  --target-transaction-out /path/to/target-transactions.jsonl
```

Live mode reads an AF_PACKET copy of UDP/20000, never binds or consumes the
proxy socket, and writes only a separate target-receipt JSONL plus health file.

Live mode retains both data and coding shreds by current Merkle FEC set. Once
32 distinct shards are available it invokes the current-Jito replay recovery
primitive, adds reconstructed data shreds to the slot state, and retries entry
ranges that previously lacked a complete current-codec payload. Recovery
attempts, successes, failures, reconstructed shreds, and decode counters are
included in the health JSON. The deployed research clone exports this primitive
through the companion patch `current-jito-replay-export.patch`; the production
ShredStream proxy is not changed by this tool.

New target receipts use schema `ALLENHARK_CURRENT_CODEC_TARGET_RECEIPT_V2` and
include native Unix/monotonic receive clocks, slot, and an explicit
direct-versus-FEC-recovery source label. Older V1 rows remain valid coverage
evidence but intentionally have no native clock fields.

When enabled, `--target-transaction-out` writes a separate append-only
`ALLENHARK_CURRENT_CODEC_TARGET_TRANSACTION_V1` tape containing the native
receive clocks, resolved static account keys, and raw compiled instruction
bytes for target fee-payer transactions. It does not alter the receipt file
or the ShredStream proxy. The downstream target-state materializer uses this
tape only when a pre-receipt native curve snapshot also carries all four
reserves and its fee schedule; otherwise it records the missing field and
fails closed.
