# Invalid First Implementation Run

Classification: `IMPLEMENTATION_INVALID_NOT_A_RESEARCH_RESULT`

The first adapter run used the correct frozen contract but incorrectly required
target-mint relevance on raw transaction-arrival frames before preserving Pump
signature arrival timestamps. Target relevance is established by later
canonical finalized reconciliation, so this ordering produced 483,202 false
missing-arrival joins, only 673 decoded target Pump events, and non-discriminative
fee-only outcomes.

No aggregate from that run may be interpreted as economics. The artifacts are
preserved only as implementation-audit evidence. The correction restores the
validated capture order: preserve Pump transaction arrivals first, classify and
join target relevance afterward.

The sandbox contract, development population, frozen policy, economic settings,
and sealed-validation boundary were not changed.
