# 2CQ Joint WHAT + WHEN V1

## Built

- `twoCqSymmetricCrossSectionalPolicyV1.py` now accepts the event-level timing candidate and assembles one explicit joint policy after the one-time sealed WHAT validation.
- `twoCqJointWhatWhenScorerV1.py` evaluates complete causal event snapshots and always emits no-submit decisions.
- Timing is evaluated in logistic-probability space; cross-mint ranking is evaluated in standardized logistic-logit space.
- Missing timing state, non-canonical order, incomplete/non-finite features, missing changed mint, or a top-score tie all fail closed.

## Frozen Sequence

1. Continue the current V2 symmetric cohort unchanged.
2. At N350, fit exactly one WHAT model on ordinals 1-350.
3. At N500, evaluate that frozen WHAT model exactly once on sealed ordinals 351-500.
4. Assemble the joint WHAT + WHEN candidate without reading economics.
5. Start an isolated prospective no-submit observer to validate the complete joint behavior.
6. Only after behavioral validation, freeze and run exact-curve economics.

## Current Boundary

The WHAT model cannot be fit before N350. The WHEN component remains a development candidate until prospective no-submit validation. No service, live path, threshold, collector, or validation seal is changed by this build.
