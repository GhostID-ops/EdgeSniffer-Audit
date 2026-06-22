# Why this copy-bot can't print money *right now* — the honest numbers

> Plain-language writeup for friends. No spin. Every number below is from our own
> on-chain data (Omego's wallet cache + our live fills), reconstructed with the
> scripts in `scripts/abCycleCapSim.mjs`, `scripts/omegoSelectivityBuckets.mjs`,
> and `scripts/analyzeOmegoDayWeek.mjs`. You can re-run them yourself.

## TL;DR

We copy a trader ("Omego") at **20% of his size**. Our bot's execution is genuinely
elite — it detects his trades in **7–22 milliseconds** and fills in **under 100ms**.
Speed is **not** our problem.

The problem is arithmetic:

- **Omego's own edge is ~+0.9% per trade.** He's a thin-margin, high-volume scalper.
- **Pump.fun charges ~1% per trade — and every position is a buy *and* a sell, so ~2% per round trip.**
- **~2% fee ≥ ~2% edge.** The fee eats essentially the entire edge.

So even with *perfect* execution and *zero* tips (we run no tips), copying his full
flow comes out to roughly **break-even, leaning slightly negative.** Not because the
bot is slow or broken — because the math doesn't leave anything on the table.

---

## 1. Who Omego is (from his own wallet, one Sunday, 1,306 round trips)

| Metric | Value | What it means |
|---|---:|---|
| Round trips | 1,306 | He trades constantly |
| Tokens touched | 207 | Spray-and-pray volume |
| Total turnover | **986 SOL** | Huge churn |
| Realized profit | **+8.77 SOL** | **= +0.89% on turnover** |
| Win rate | **39.4%** | He *loses* more often than he wins |
| Median trade | **−2.4%** | The typical trade is a small loss |
| Median hold time | **0.9 minutes** | In and out in under a minute |

**Read this carefully:** Omego made +8.77 SOL by pushing **986 SOL** of volume. That's a
**0.89% margin**. He is not a genius picking moonshots — he's a machine skimming a sliver
off enormous turnover. His profit comes from *doing it 1,306 times*, not from any single
trade being good. **39% of his trades win; 61% lose.** A handful of winners carry the day.

> Note on "runners": people assume copy-trading wins via the occasional +500% moonshot.
> Omego's data says otherwise. Only **~16%** of his tokens ever reach +100% after entry,
> only **~4%** reach +200%, and +500% is vanishingly rare — and he *scalps out at +0.9%
> median anyway*, so he doesn't even hold them. **There is no fat tail to harvest here.**
> He is a consistent grinder, not a lottery player.

---

## 2. What happens when WE copy him at 20% (our simulation, 4,229 cycles)

We replayed every one of his closed cycles as a 20% follower, using **our real fill
costs** (no tips — we run none — and the slippage our fast execution actually produces).

| Line item | SOL | Note |
|---|---:|---|
| Capital deployed | 630 | 20% of his size, across 4,229 cycles |
| **Gross edge** (his price replay) | **+12.3** | what we'd make with *zero* costs |
| Pump.fun protocol fee | **−12.7** | ~1% per leg × 2 legs — **unavoidable, hard-coded** |
| Slippage (our fast fills, ~0.4%) | −5.1 | being a half-second behind him on both legs |
| Tips | −0.0 | **we pay none** |
| **NET RESULT** | **−5.5** | after all real costs |

And here's the killer line. If we had a **physically impossible perfect copy** — zero
slippage, zero tips, instant fills — the result is **still −0.4 SOL**, because:

> **The pump.fun protocol fee alone (12.7 SOL) is *larger* than our entire gross edge (12.3 SOL).**

There is no execution improvement, no faster server, no better code that fixes this.
The fee is bigger than the edge. Full stop.

---

## 3. "Just trade bigger / faster" — why that doesn't work

- **Bigger size doesn't help.** The fee is a **percentage** (2%). Trade 5× bigger and you
  pay 5× the fee on 5× the gross — the *ratio* is unchanged. You'd just be break-even at a
  larger scale (and risk more). Size changes the dollar amounts, not the math.
- **Faster doesn't help.** We're already at the **slippage floor** — detection in 7–22ms,
  fills under 100ms, 0.00% price impact on entries. We've already won the speed race. The
  −5.5 → −0.4 gap is the *most* that speed can ever buy, and we've traveled almost all of it.
- **The 20% sizing isn't the villain.** It just makes the (already tiny) edge small in
  absolute SOL. At 100% size the *percentage* outcome is identical — break-even minus slippage.

---

## 4. Why we're confident this is the truth, not pessimism

Our trade logs show it directly, every session: the bot reports a **"trade-basis" PnL**
(green — the price-vs-price result) and a **"wallet-delta" PnL** (the SOL that actually
moved). They diverge by exactly the fee. Example from a real session:

```
Realized PnL:      +0.9062 SOL   (trade-basis — looks like a winning day)
Wallet SOL delta:  -0.9197 SOL   (all-in, after fees — actually down)
Balance: 13.251 -> 12.332 SOL
```

Same wallet, same day: **+0.9 on paper, −0.9 in reality.** The gap is the fee. This is not
a one-off; it's the structural signature of copying a 2%-edge wallet through a 2% fee.

---

## 5. So what *would* make this profitable?

We are not saying it's hopeless. We're saying the lever is **not** more size, more speed, or
more effort on execution (all maxed). The only things that can create real profit:

1. **A higher-edge source wallet.** Omego's edge ≈ the fee. If we find a wallet whose edge
   *clearly clears 2%*, our elite execution finally has something worth executing. (We have
   10k-trade caches on four other wallets ready to test the same way.)
2. **Selectivity.** Omego's profit is concentrated in a minority of trades; ~65% of cycles
   don't pay for their own fee. If we can identify *at entry* which cohort to skip (by market
   cap, his buy size, etc.), we stop paying fee on the dead trades. Early analysis shows we
   can trim the worst buckets — but not (yet) cross into clear profit on Omego alone.
3. **Fee avoidance** — only possible by trading venues/pairs with lower protocol fees than
   pump.fun's 1%/leg. Structural, not a quick toggle.

---

## Bottom line for the group

- The bot **works** and is **fast** — that part is done and genuinely good.
- We are copying a trader who makes **~0.9% per trade**, through a venue that charges **~2%
  per round trip.** The fee is bigger than the edge.
- Therefore, **at this moment, copying Omego is structurally break-even — we will not make
  significant profit no matter how we tune size, speed, or take-profits.**
- The path forward is **a better wallet to copy** and/or **trading only his profitable
  subset** — not more capital and not more speed.

*This is the honest picture. We'd rather know it now than discover it after sizing up.*

---

*Generated from: `data/omego-day-weekend-analysis.md`, `data/omego-ab-cycle-cap-sim.md`,
`data/omego-selectivity-buckets.md`. Re-runnable via the scripts in `/scripts`.*
