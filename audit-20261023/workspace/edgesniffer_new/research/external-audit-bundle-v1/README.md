# EdgeSniffer independent strategy audit bundle

This bundle is for an independent code, data-lineage, and strategy-design review.
The central objective is to find a genuinely executable and profitable research
strategy for EdgeSniffer, beginning with reverse-engineering the preserved 2CQ
wallet evidence. The repository remains research-only: no trading credentials,
private keys, raw bulk evidence, or live execution authorization are included.

Important distinctions for the reviewer:

- Historical positive PnL is exploratory unless it uses native receive-time state,
  the one-second causal delay, executable fees/impact, failed or missed trades,
  and same-period controls.
- The preserved 2CQ scorer is a frozen hypothesis, not a validated strategy.
- The native launch-lineage gate currently has zero complete rows because the
  current native trade stream does not carry exact CREATE time, creator, and
  launch reserve state for the observed mints.
- Existing collectors and cohorts must remain preserved and receive-only.
- The correct deliverable is a ranked research plan and implementation advice,
  not a claim that the system is profitable.

The included roadmap and contracts are the authority for current gate status.
Generated outputs, raw evidence, caches, credentials, environment files, and
large build directories were intentionally excluded.
