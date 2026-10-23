# EdgeSniffer research reassessment — 2026-10-22

## Decision

The largest uncertainty is whether independently selected opportunities retain
positive absolute AND control-adjusted returns after decision-ready time +1s,
when every failed or unresolved opportunity remains accounted for. Existing
reports do not answer this. Better exit tuning is currently lower information
value than a small, trustworthy delayed-response experiment.

Keep the teacher timing model as a benchmark. Prioritize a simple recent-flow
baseline and its persistence challenger. Do not require every teacher feature,
all venues, universal decoder coverage or actual live submissions to run these
research experiments. Restrict to a declared, verifiable population and report
what that excludes. A deployment-wide 99% agreement statistic is neither
necessary nor sufficient for validity of each admitted interval.

## What the previous repairs established

Probability-domain comparison, post-event feature wrapping and tie regressions
are real local improvements. The signed-flow correction addresses missing sell
quotes in one event-driven consumer. The variant matrix checks constants. None
of these proves historical financial impact, successful feature-enabled Rust
capture, complete frozen classifier parity or all-consumer flow correctness.

The previous 38 tests were insufficient. The actual frozen window builder ignored
the incomplete flag and manufactured a complete window; the lab parsed conditional
reserves and the scorer independently parsed them. Successful execution could
also label an assumed-fee replay EXECUTED. Even correct status for one transaction
does not certify its predecessor state, missing transitions or fee formula.

AUD-I01 now prevents known conditional/legacy replay from passing those consumers.
SUCCESS replay stays conditional. A real replay-to-scorer/window/entry/exit quote
regression exercises the failure path; 41 affected tests pass locally. Untagged
historical fixtures still parse: this is a negative-evidence guard, not universal
positive certification. Previously emitted windows with stripped lineage cannot
be retrospectively repaired by this guard. No deployment or VPS live test occurred.

The remaining max-cost repair is also narrower than claimed: explicit semantic
labels override raw instruction data; realized fields lack provenance checks.
The status parser now treats malformed booleans as UNKNOWN. Fixed 125bp math,
seed resets, omitted transitions, completion and account identity remain open.

Full F01 and F03 acceptance was premature. F01 is a timing benchmark with missing
composite rules. The scorer source_event still defaults missing quote to zero;
the frozen bridge's separate quote_sol omits sell proceeds. These are reopened.
F04A remains verified only for recognition, not current layouts or deployed capture.

## Audit coverage corrections

All twelve headline findings had register entries, but broad entries obscured
concrete tasks. The revised register maps native N1-N13, scorer 1-8, economics
E01-E13 and historical/governance appendices explicitly. Added named work for
checkpoint record incompatibility, source/deployment drift, cohort/split/holdout
governance, and historical forward-coverage/inventory assumptions.

The previous audit ZIP hash actually named the source bundle. Recomputed evidence
ZIP hash: 284EEDE0FC3864D4631592CACE076951065BFF71586F498BEEFF65DAC91D217A.
No claim is made that a sealed holdout was secretly read. Source code using a
holdout for selection consumes that test in that workflow; active wiring needs
evidence. Likewise a broken old reproduction API does not establish a successful
repair. Correct-behavior counterexamples are required.

## Evidence and strategy interpretation

Recalculated from preserved report rows using auditTeacherTimingPremiseV1.py:
76 episodes, 67 unique mints; selected-state median receipt lead 36.274923ms;
0/76 selected states lead by >=1s. First crossing exists in 64 episodes, with
median 108.9274025ms; 25/76 lead by >=1s. There are 3732 above-threshold states;
1156 occur in one episode. Output: teacher-timing-premise-v1.json, with input hash.
Counts and medians agree with the preserved summaries. These are artifact
recalculations, not reconstruction from raw market tape.

This challenges the idea that reproducing the final teacher state provides a
one-second entry advantage. It does not disprove persistent continuation or
earlier crossings. The teacher-selected population cannot estimate unmatched
alerts, independent opportunity frequency or net profit. Sigmoid scores from
class-balanced training are not calibrated probabilities of profit.

The prior positive Pump surfaces, positive independent subsets and negative
account-checkpoint reports all have material measurement/population limits.
They motivate research but do not rank mechanisms reliably. Excluding the slow
or missing exits selects on future information. Negative faulty replay results
also cannot justify eliminating an entire venue.

No new current-market performance analysis was completed in this review. Do not
assume the current regime resembles historical captures. Preserve day/time,
depth, phase, activity and feed-coverage strata; report rates and missingness
within each. Breadth in wallet addresses is not proof of independent buyers.

## Minimum infrastructure and what can run now

Required for economic claims: independent opportunity clock/universe; supported
active venue state and observed fees; evidence availability; coverage through
entry/exit; own-inventory/impact accounting; full attempt denominator; comparable
controls. An unsupported transition invalidates affected state until reconciled.
It need not invalidate unrelated fully observed tokens or periods.

Use successful processed transaction/account evidence when available at decision
time. Later finalized data may label outcomes; it cannot be moved back into
features. Actual submitted-exit telemetry is required for execution calibration,
not for a clearly labeled offline execution-model sensitivity study. Requiring
finality at every signal would unnecessarily redefine the strategy's latency.

Can run immediately: artifact timing/concentration (completed), cohort/selection
audit, source counterexamples, historical fee/impact sensitivity and timing
decomposition on retained complete tapes once their location/coverage is verified.
The supplied evidence ZIP contains no raw market tape. Do not manufacture a
historical returns study from summaries. Historical labels can reveal whether
continuation is worth pursuing without establishing native executability.

Needs additional capture if absent from retained sources: full-market available-at
events/fees/state, account continuity, fresh independent controls and confirmation.
Impossible-to-recover historical ingress clocks cannot be inferred from finality.

Defer exact CREATE/creator features, universal CPI reconstruction, broad venue
expansion, dynamic sizing, partial exits and live submission. Account snapshots
may establish state without decoding all prior history, but complete signed-flow
lookbacks still require all relevant executed trades. State and feature coverage
are distinct requirements.

## Three ranked experiments

1. **Teacher timing versus token selection decomposition (existing-data diagnosis).**
   Hypothesis: the lead contains persistent token-level continuation, rather than
   merely identifying the instant before a teacher action. Timing artifact phase
   is completed above. Next compare fixed-time responses on retained complete
   histories: first crossing, final selected state, and outcome-independent
   matched clocks. Use +1s entry, fixed15s hold +1s exit delivery, three sizes,
   observed fees and declared cost scenarios. Preserve missing paths. Sampling
   unit is mint/episode; controls use same clock/depth/phase without matching
   away the tested flow. Primary diagnostic is delayed absolute/control excess
   return and its decay; teacher-conditioned selection limits external validity.
   Stop after the predeclared preserved set; no picking the best horizon. Demote
   only if valid uncertainty bounds exclude worthwhile residual edge. Incomplete
   tape means unresolved, not a negative economic result. This efficiently
   separates selection from timing before paying for richer signal engineering.

2. **Independent burst residual edge (primary economic baseline).**
   Hypothesis: an independently observed recent buy burst predicts net continuation
   beyond local market exposure. Start with complete processed whole-transaction
   recent history, >=2 buyers/250ms, last two swaps buys and positive executed
   flow/price. These are unoptimized starting rules, not discovered alpha. First
   qualifying mint episode after warmup; decision at feature-ready, entry +1s,
   fixed15s hold and exit +1s. Sizes .1/.5/1 share seeds; .5 SOL is the user's
   practical capacity target and must be reported regardless of .1 performance.
   Include own impact, observed fees, costs/min-out, unsupported/failed/open
   outcomes. Same-period eligible controls, without future teacher conditioning.
   Primary endpoint net SOL per seed and matched excess; mint/time-block intervals,
   winner/day concentration and missingness bounds. A finite development block
   estimates variance; freeze effect threshold and count/calendar stopping rule
   before new confirmation. No success declaration from repeated PnL inspection.

3. **Persistence versus immediate burst (paired policy challenger).**
   Hypothesis: continued participation offsets the extra delay and worse price.
   Retain all experiment-2 seeds. At seed+2s check positive executed flow, two
   new buyers and retention of half the initial rise. Final decision then;
   entry no earlier than seed+3s. Use the SAME fixed15s holding/exit-cost rules
   as baseline initially, so exit choice does not confound confirmation. Report
   confirmation rejects as zero-exposure policy decisions, with opportunity
   costs; missing data remains unknown. Primary paired all-seed policy difference
   plus absolute net return at all sizes; controls/budget identical. Predeclare
   development block and fresh confirmation stopping after variance estimation.
   Relative improvement while both lose money does not support promotion.

Sell-shock recovery follows reliable sell capture; current sell missingness makes
it unusually expensive to test honestly. Deeper pools remain a conditional venue
alternative if early-curve delay/capacity fails. One adaptive exit can follow a
credible entry experiment; an exit sweep is not the next evidence priority.

## Next engineering decision

AUD-L01A: build the smallest opportunity/admission ledger using one supported
processed-state route. Preserve every seed across sizes and attach validity,
availability and rejection reasons before quoting. This lets us measure whether
the bottleneck is absent opportunity, missing evidence or uneconomic capacity.
Then attach fixed-delay own-state economics. Do not build a universal event
platform before obtaining that answer. Runtime/build provenance (D01) and retained
source inventory can proceed read-only alongside the single implementation task.

The audit's 100/500/2000 counts, 14 days, 1% min-out, 5% impact and exit horizons
are proposed design choices. They are not optimal settings or magic evidence
thresholds. Same-period controls, dependence, effect size and valuation uncertainty
determine whether a conclusion is supported. A narrow valid experiment is more
useful now than completing every general infrastructure improvement.
