# SAME_ANCHOR_CAUSAL_SEPARABILITY_V1

Contract SHA256: `b5637c1031f787efcd868af6c4391939f6ec03a84846a4ad6df962849801b082`
Feature registry SHA256: `1819f2f5be5df04f3f08b882dacdbc55f98cf183313675b4b10ccafd6415bff5`

This is a discovery-only univariate separability report, not a strategy or threshold search.

## Population

| Anchor | N | Policy >=10 | Policy positive | Opportunity >=10 |
|---|---:|---:|---:|---:|
| EXTERNAL_GROSS_BUY_GE_1_SOL | 1518 | 179 | 342 | 242 |
| EXTERNAL_GROSS_BUY_GE_2_SOL | 1209 | 159 | 300 | 217 |
| EXTERNAL_GROSS_BUY_GE_5_SOL | 662 | 114 | 203 | 151 |

## Primary Anchor: strongest univariate POLICY_GE_10 associations

| Feature | Family | Coverage | Direction | AUC | 95% CI | Tail enrichment | Stable blocks | FDR q |
|---|---|---:|---|---:|---:|---:|---:|---:|
| net_external_flow_sol | PATH_TO_ANCHOR | 100.0% | HIGHER | 0.664 | 0.615-0.714 | 2.51x | 5/5 | 6.17e-10 |
| actual_cumulative_external_gross_buy_sol | PATH_TO_ANCHOR | 100.0% | HIGHER | 0.662 | 0.615-0.709 | 2.51x | 5/5 | 6.17e-10 |
| overshoot_sol | PATH_TO_ANCHOR | 100.0% | HIGHER | 0.662 | 0.614-0.713 | 2.51x | 5/5 | 6.17e-10 |
| last_buy_size_vs_prior_median | TRANSACTION_PATH_SEQUENCE | 69.8% | HIGHER | 0.622 | 0.565-0.687 | 2.39x | 4/5 | 0.00138 |
| buy_size_progression_ratio | TRANSACTION_PATH_SEQUENCE | 69.8% | HIGHER | 0.619 | 0.556-0.689 | 2.61x | 5/5 | 0.00166 |
| trailing15m_external_flow_percentile | MARKET_RELATIVE_CONTEXT | 41.1% | HIGHER | 0.611 | 0.528-0.688 | 1.87x | 5/5 | 0.0219 |
| gross_buy_sol_per_successful_tx | PATH_TO_ANCHOR | 100.0% | HIGHER | 0.608 | 0.553-0.659 | 2.32x | 5/5 | 0.000111 |
| buyer_hhi | PARTICIPATION_STRUCTURE | 100.0% | HIGHER | 0.570 | 0.521-0.621 | 1.25x | 5/5 | 0.0219 |
| largest_buyer_share | PARTICIPATION_STRUCTURE | 100.0% | HIGHER | 0.568 | 0.517-0.618 | 1.25x | 5/5 | 0.0235 |
| successful_transaction_count | TRANSACTION_PATH_SEQUENCE | 100.0% | LOWER | 0.560 | 0.510-0.613 | 1.50x | 4/5 | 0.0423 |
| median_event_interarrival_ms | TRANSACTION_PATH_SEQUENCE | 63.4% | HIGHER | 0.556 | 0.496-0.617 | 1.15x | 4/5 | 0.112 |
| external_buy_event_count | TRANSACTION_PATH_SEQUENCE | 100.0% | LOWER | 0.556 | 0.501-0.606 | 1.63x | 4/5 | 0.0624 |
| unique_external_buyers | PARTICIPATION_STRUCTURE | 100.0% | LOWER | 0.555 | 0.501-0.606 | 1.63x | 4/5 | 0.0684 |
| real_token_remaining_fraction | EXACT_ECONOMIC_STATE | 100.0% | LOWER | 0.548 | 0.490-0.598 | 1.94x | 3/5 | 0.105 |
| buyer_size_cv | PARTICIPATION_STRUCTURE | 100.0% | LOWER | 0.544 | 0.498-0.593 | 1.13x | 4/5 | 0.105 |

No model, feature combination, trading threshold, shadow lane, or live submission was produced.
