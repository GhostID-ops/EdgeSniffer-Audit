# 2CQ Decision System Decomposition V1

Development-only, outcome-blind decomposition of the frozen first 350 eligible
entries from `2CQ_PROSPECTIVE_TIER_A_ENTRY_CENSUS_V3`.

The study separates:

- same-mint entry timing (`WHEN`)
- contemporaneous mint selection (`WHAT`)
- natural first-buy size (`HOW MUCH`)

The causal decision state is the state immediately after the final external
event preceding 2CQ's buy. The teacher transaction timestamp itself is not a
feature. Entries with no earlier negative state remain usable for sizing but
are excluded from timing identification.

The validation artifact is not an accepted input. The first materialization
builds the corrected entry-decision tape and same-mint timing controls. The
cross-sectional choice tape is completed only after every receive-hour covering
the first 350 entries has finalized exact-ledger evidence.
