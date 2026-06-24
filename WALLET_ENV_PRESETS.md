# Wallet Env Presets

These presets update `.env` with the best-known settings from the wallet research files.
They use `scripts/upsertEnv.mjs`, so duplicate entries for touched keys are removed.

## Commands

List presets:

```bash
npm run env:wallet -- list
```

Apply a preset:

```bash
npm run env:wallet -- cya
npm run env:wallet -- 4vw54
npm run env:wallet -- tefs
npm run env:wallet -- omego
npm run env:wallet -- 5qn-eaz
npm run env:wallet -- 4bdk
npm run env:wallet -- gug
```

Direct aliases:

```bash
npm run env:wallet:cya
npm run env:wallet:4vw54
npm run env:wallet:tefs
npm run env:wallet:omego
npm run env:wallet:5qn-eaz
npm run env:wallet:4bdk
npm run env:wallet:gug
```

Check after applying:

```bash
npm run vps:doctor
grep -nE '^(TRACKED_WALLETS|COPY_PERCENTAGE|MIN_BUY_SOL|MAX_BUY_SOL|MIRROR_SELLS|HYBRID_EXIT|STOP_LOSS|TRAILING_STEPS|TIP_LAMPORTS|PRIORITY_FEE_MICRO_LAMPORTS)=' .env
```

## Notes

- `cya` uses the CyA hybrid/source-exit posture with no MC filter in the hot path.
- `4vw54` uses source-exit mirroring plus hybrid partial profit.
- `tefs` allows scale-ins and forces no tips/priority because the edge depends on low round-trip cost.
- `omego` uses bot-managed trailing instead of source-exit mirroring.
- `5qn-eaz` uses aggressive same-slot fee settings because late fills damage the edge.
- `4bdk` uses proportional sell mirroring.
- `gug` currently tracks `5qn + EAz`, because the GUg research said GUg is better treated as a cluster confirmation wallet, not the entry source.
