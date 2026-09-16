# 2CQ Raydium LaunchLab Coverage V1

Generated from the final canonical-only prospective snapshot at
`2026-09-12T05:04:44.174667Z`.

## Verdict

`RAYDIUM_LAUNCHLAB_EXACT_RESEARCH_COVERAGE_ACTIVE`

LaunchLab is now covered as a separate, outcome-blind research archetype. This
does not enable live LaunchLab trading.

## Historical Teacher Bootstrap

- Source decisions inspected: 836
- Teacher transactions invoking LaunchLab: 274
- Exact validated transitions: 274
- Failed validations: 0
- Known teacher pools: 197
- Instruction shapes: 251 buy-exact-in, 23 sell-exact-in
- Token-2022 inventory transfer-fee buys: 219
- Median curve-output to wallet-inventory loss on affected buys: 100 bps

## Prospective Exact Ledger

- Canonical finalized signatures: 15,843 / 15,843 reconciled
- Exact LaunchLab transitions: 3,048
- Exact transitions with causal AllenHark receipt time: 3,039
- Exact transitions missing realtime evidence: 9, fail closed
- Inexact/unsupported transitions excluded: 1
- Pools reconstructed: 206
- Reserve-continuity comparisons: 2,842
- Continuity gaps: 0
- Reconciliation errors: 0
- Unresolved backlog: 0
- Outcomes read: 0
- Validation rows read: 0
- Submissions: 0

## Semantics

- The finalized LaunchLab program-address index is the canonical transaction
  source, covering direct, CPI/router, and loaded-address transactions.
- The existing capture-all AllenHark compact journal supplies the original
  local millisecond receipt timestamp by exact signature.
- Constant-product reserve transitions are verified with integer arithmetic.
- Failed transactions mutate no state.
- Exact wallet token deltas remain authoritative when Token-2022 transfer fees
  make instruction/event output differ from received inventory.
- Missing realtime evidence, unsupported curve types, or broken reserve
  continuity fail closed.

## Runtime

- Reconciler timer: active, one minute
- Redundant protocol-specific observer: removed
- Existing AllenHark compact observer: unchanged and active
- Existing Pump exact ledger: unchanged and active
- Existing 2CQ capture-first collector: unchanged and active

## Boundary

This completes causal data coverage and exact transition reconstruction for
LaunchLab. It does not yet create a LaunchLab fingerprint, symmetric choice
set, prospective strategy lane, or live buy/sell route. Those remain separate
promotion stages.

## Provenance

- Raydium program: `LanMV9sAd7wArD4vJFi2qDdfnVhFxYSUg6eADduJ3uj`
- Pinned official IDL commit: `e7e0c96fe77bcf6a020b84a44c47a722aac8e359`
- IDL SHA-256: `963189409945069409f32e345d4adf2892e0b3ca1be504372577fe34a23c6cdb`
- Exact evaluator SHA-256 (VPS): `16466e970f68ac875a4ea7f947b405bdccaf00e3ac668f35fc6aa1991df8af84`
- Final reconciler SHA-256 (VPS):
  `7ccd11b6a6a82151549fdadde161bc15c87816c0760f19c15999c218e49858bb`

Official references:

- https://docs.raydium.io/products/launchlab/bonding-curve
- https://docs.raydium.io/products/launchlab/accounts
- https://docs.raydium.io/products/launchlab/instructions
- https://docs.raydium.io/sdk-api/anchor-idl
- https://github.com/raydium-io/raydium-idl/blob/master/raydium_launchpad/raydium_launchpad.json
