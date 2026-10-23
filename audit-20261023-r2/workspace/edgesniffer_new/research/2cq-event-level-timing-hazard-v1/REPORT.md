# 2CQ Event-Level Timing Hazard V1

Development-only, outcome-blind, sealed validation untouched.

## Assessment

| Policy | N | AUC | Teacher recall | Exact first trigger | Premature first trigger | P75 lead |
|---|---:|---:|---:|---:|---:|---:|
| Uniform / 90% train recall | 76 | 0.760 | 80.26% | 19.74% | 64.47% | 14425.5ms |
| Proximal hazard / exact-first threshold | 76 | 0.735 | 69.74% | 28.95% | 51.32% | 13768.6ms |

## Near-Teacher Capture

- First trigger within 50ms of teacher: **21.05%**
- First trigger within 100ms of teacher: **38.16%**
- First trigger within 250ms of teacher: **46.05%**
- First trigger within 500ms of teacher: **47.37%**
- First trigger within 1000ms of teacher: **47.37%**

## Integrity

- Development ordinals: **1-350 only**
- Internal train/assessment timing episodes: **177 / 76**
- Validation rows read: **0**
- Outcomes read: **0**
- Submissions: **0**
- Canonical state order preserved: **yes**

Verdict: `2CQ_EVENT_LEVEL_TIMING_HAZARD_DEVELOPMENT_CANDIDATE_IMPROVED`
