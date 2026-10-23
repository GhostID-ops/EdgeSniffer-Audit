# 2CQ Prospective WHEN Readiness V1

Generated from the outcome-blind development prefix at 2026-09-14T02:07:38Z.

## Scope

- Source cohort: `2CQ_PROSPECTIVE_SYMMETRIC_CHOICE_CENSUS_V2`
- Development rows read: ordinals 1-237 only
- Validation rows read: 0
- Outcomes read: 0
- Models fit: 0
- Thresholds tuned: 0
- Submissions: 0
- Target development checkpoint: N350

## Current Result

| Metric | Value |
|---|---:|
| Development choice rows | 237 |
| Remaining to N350 | 113 |
| Temporal examples reconstructed | 211 |
| Timing-identifiable examples | 147 |
| Timing-identifiable share | 62.03% |
| Reconstructed rows with no earlier same-mint control | 64 |
| Reconstruction exclusions | 26 |
| Base causal features | 48 |
| Transition features | 16 |
| States missing transition features | 0 |

All 26 exclusions are `NO_PREENTRY_EXTERNAL_STATE` and are exactly eligible
ordinals 1-26. Their choice timestamps precede the indexed causal-state plane:

- excluded prefix: 2026-09-11T06:17:48Z through 2026-09-11T18:25:44Z
- indexed state-plane start: 2026-09-11T18:39:19Z
- ordinal 27+ prefix start: 2026-09-11T18:59:27Z

This is historical index-provenance attrition, not evidence of a current
collector failure. Six queried transaction signatures were absent from the
AllenHark arrival journal, but no admitted temporal state was given a fabricated
timestamp.

## Interpretation

The symmetric cohort is the correct population for the cross-sectional WHAT
policy. It is not automatically a complete replacement population for WHEN:
only entries with an earlier same-mint external state identify a transition
decision. At current N, 147/237 satisfy that condition.

The existing development-only frozen transition/timing component therefore
remains the timing candidate to carry forward. This preview is useful for
prospective portability and coverage checks; it must not be used to refit or
tune timing before the frozen gate.

## Gate

`readyToFreezeWhenAtN350 = false`

Reason: the development choice cohort is still collecting and the current
prospective timing population has expected structural/provenance attrition.
The next strategy action remains unchanged: reach N350, fit/freeze exactly one
cross-sectional WHAT policy, test it once on the sealed N150, then combine it
with the already-preserved timing component before exact economics and
prospective no-submit evaluation.

## Artifacts

VPS report:

`/mnt/HC_Volume_106775198/edgesniffer-research/2cq-prospective-when-readiness-v1/report.json`

VPS preview:

`/mnt/HC_Volume_106775198/edgesniffer-research/2cq-prospective-when-readiness-v1/same-mint-temporal-preview.jsonl.gz`

Preview SHA-256:

`5ed9250196a5ff9da5be46b6659b7fb98da5854a02a160456c271ed43607b33f`
