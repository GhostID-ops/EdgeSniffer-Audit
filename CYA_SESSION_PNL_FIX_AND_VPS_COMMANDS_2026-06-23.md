# CyA Session PNL, Error Fix, and VPS Commands - 2026-06-23

## 0.2 SOL fixed-buy simulation

This uses the closed trades parsed from the attached CyA session log and scales each closed buy to exactly 0.2 SOL.

| Bucket | Closed trades | Deployed SOL | Sim PNL SOL | ROI | Wins |
|---|---:|---:|---:|---:|---:|
| All closed trades | 33 | 6.6000 | +0.6034 | +9.1% | 15 |
| Hybrid-triggered trades | 12 | 2.4000 | +1.0105 | +42.1% | 11 |
| Non-hybrid trades | 21 | 4.2000 | -0.4071 | -9.7% | 4 |

Read: the hybrid model was the profitable part of the day. At a flat 0.2 SOL per closed trade, it would have carried the session, while non-hybrid entries would have dragged.

## Errors spotted in the attached log

Approximate counts from the session log:

| Error / event | Count | Meaning |
|---|---:|---|
| NaN / BigInt buy failures | 68 | Bad numeric amount reached BigInt conversion in the buy path. |
| Invalid copy-sized amount | 6 | Mirrored amount was not finite or was <= 0. |
| Pump.fun Rust sell command unsupported | 46 | VPS sidecar binary was older than the TypeScript command bridge. Rebuild/restart needed. |
| Prebuy inferred market-cap unavailable, fail-closed | 9 | The local inferred MC filter skipped because it could not compute MC. |
| Prebuy market-cap > 10k skipped | 24 | The market-cap ceiling did its job. |
| PumpSwap output quote unavailable | 2 | Pool quote was unavailable for that sell path. |
| No parsed tx | 101 | Detector saw txs that parser could not turn into usable swaps. |
| Telegram polling errors | 7 | Notification/control-plane issue, not trade execution. |
| Confirmed inventory zero sell skips | 27 | Sell came in but local confirmed token balance was zero. |
| Already-bought duplicate skips | 175 | Same mint duplicate protection was active. |

## Permanent NaN BigInt fix added locally

Code changed:

- `src/rustSidecar.ts`: added a Rust executor command validation gate. Any lamport/token field sent to Rust must now be finite, safe, and integer before JSON serialization. This prevents JavaScript `NaN` from becoming JSON `null` and later crashing in BigInt conversion.
- `src/botService.ts`: added a second hot-sniper backstop resolution check after filters and before reserve/executor handling. This closes the race where the Rust hot sniper buys first, then the delayed backstop continues and tries to process the same mint with malformed sizing.

Why this is the right class of fix: MDN documents that BigInt conversion only accepts integer numbers, and `NaN` is not valid for BigInt conversion. MDN also warns that BigInt and Number behavior requires care, and `Number.isSafeInteger()` is the right guard when converting JavaScript numbers into integer amounts.

Docs checked:

- https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/BigInt
- https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Errors/Cant_convert_x_to_BigInt
- https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/Number/isSafeInteger

## Would Rust help?

Yes for execution correctness and latency, but Rust alone is not the whole fix. The bug was at the TypeScript to Rust command boundary: a bad JavaScript number could still be serialized before Rust ever had a chance to enforce types. Rust helps once the command is valid. The permanent approach is exactly this: keep hot execution in Rust, but validate TypeScript boundary values before emitting the command.

## Entries near CyA market cap

Without tips/priority fees, your biggest non-fee lever is filtering, not speed. The attached session says the cleanest buckets were:

- Hybrid-triggered entries: strongest bucket.
- Unknown inferred MC: profitable, but less interpretable.
- 5k-10k inferred MC: modestly profitable.
- 0-5k inferred MC: negative in this sample.
- Non-hybrid: negative in this sample.

For lower entries near his market cap without increasing fees, I would run hybrid-only or hybrid-preferred, keep the inferred MC ceiling at 10k, and avoid forcing tiny 1% buys below practical execution size. The 1% test is fine for latency measurement, but it exaggerates fees and sizing edge cases.

## Reasonable fee posture

No-tip/no-priority is okay for dry/live micro testing, but it will lose some buy races. A conservative live-testing fee posture:

- `TIP_LAMPORTS=200000` to `500000`
- `PRIORITY_FEE_MICRO_LAMPORTS=200000` to `1000000`
- `DYNAMIC_PRIORITY_FEE_ENABLED=true`
- `DYNAMIC_PRIORITY_FEE_MAX_MICROLAMPORTS=10000000`
- If using direct Jito routing: `USE_JITO_DIRECT=true`, `USE_JITO_FALLBACK=true`, `JITO_SEND_MODE=race`

For very competitive same-slot buys, your existing cheatsheet shows much larger values, but I would not jump there while validating sizing and PNL.

## VPS command - deploy code fix

Run on VPS from the bot directory, adjusting the path/service name if yours differ:

```bash
cd /root/FinalBot
git pull origin main
npm install
npm run build
npm run rust-sidecar:jito-build
systemctl restart finalbot
journalctl -u finalbot -f
```

If your CyA service/path is separate, use this pattern instead:

```bash
cd /root/FinalBot-cya
git pull origin main
npm install
npm run build
npm run rust-sidecar:jito-build
systemctl restart finalbot-cya
journalctl -u finalbot-cya -f
```

## VPS command - fixed 0.2 SOL test profile

This matches the requested 0.2 SOL per buy simulation. It intentionally uses a fixed buy size, not 1% copy size.

```bash
cd /root/FinalBot-cya
set_env() { key="$1"; value="$2"; if grep -q "^${key}=" .env; then sed -i "s|^${key}=.*|${key}=${value}|" .env; else printf "\n%s=%s\n" "$key" "$value" >> .env; fi; }

set_env RUST_ONLY_BUYS true
set_env RUST_ONLY_SELLS true
set_env JUPITER_FALLBACK_ENABLED false
set_env COPY_BUY_PERCENTAGE 100
set_env MIN_BUY_SOL 0.2
set_env MAX_BUY_SOL 0.2
set_env MAX_SOL_PER_TOKEN 0.2
set_env MAX_SESSION_DEPLOYED_SOL 2.0
set_env MAX_OPEN_POSITIONS 2
set_env PREBUY_INFERRED_MC_FILTER_ENABLED true
set_env PREBUY_MAX_MARKET_CAP_USD 10000
set_env PREBUY_FILTER_FAIL_OPEN false
set_env DYNAMIC_PRIORITY_FEE_ENABLED true
set_env DYNAMIC_PRIORITY_FEE_MAX_MICROLAMPORTS 10000000
set_env TIP_LAMPORTS 200000
set_env PRIORITY_FEE_MICRO_LAMPORTS 200000

npm run build
npm run rust-sidecar:jito-build
systemctl restart finalbot-cya
journalctl -u finalbot-cya -f
```

## VPS command - percentage-copy profile, Omego-style

Use this if you want to choose only copy percentage and avoid min/max clamping as the sizing mechanism. Keep separate safety caps enabled.

```bash
cd /root/FinalBot-cya
set_env() { key="$1"; value="$2"; if grep -q "^${key}=" .env; then sed -i "s|^${key}=.*|${key}=${value}|" .env; else printf "\n%s=%s\n" "$key" "$value" >> .env; fi; }

set_env RUST_ONLY_BUYS true
set_env RUST_ONLY_SELLS true
set_env JUPITER_FALLBACK_ENABLED false
set_env COPY_BUY_PERCENTAGE 25
set_env MIN_BUY_SOL 0.01
set_env MAX_BUY_SOL 1.25
set_env MAX_SOL_PER_TOKEN 1.25
set_env MAX_SESSION_DEPLOYED_SOL 5.0
set_env MAX_OPEN_POSITIONS 2
set_env PREBUY_INFERRED_MC_FILTER_ENABLED true
set_env PREBUY_MAX_MARKET_CAP_USD 10000
set_env PREBUY_FILTER_FAIL_OPEN false
set_env DYNAMIC_PRIORITY_FEE_ENABLED true
set_env DYNAMIC_PRIORITY_FEE_MAX_MICROLAMPORTS 10000000
set_env TIP_LAMPORTS 200000
set_env PRIORITY_FEE_MICRO_LAMPORTS 200000

npm run build
npm run rust-sidecar:jito-build
systemctl restart finalbot-cya
journalctl -u finalbot-cya -f
```

Note: the current config parser defaults `MIN_BUY_SOL` and `MAX_BUY_SOL` when blank, so this profile uses a small min and a safety max rather than blank values.
