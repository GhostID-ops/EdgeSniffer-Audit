import { getQuote, isJupiterRateLimitError } from "./jupiter.js";
import { getOpenSnipes } from "./snipePositions.js";
import { SnipeBot } from "./snipeBot.js";

const SOL_MINT = "So11111111111111111111111111111111111111112";

export interface TrailingLevel {
  /** When peak gain reaches this %, raise the effective stop to lockPct. */
  triggerPct: number;
  /** New stop level (in % vs entry). 0 = break-even, positive = locked-in profit. */
  lockPct: number;
}

export interface MonitorConfig {
  takeProfitPct: number;
  stopLossPct: number;
  /** Sorted ascending by triggerPct. Empty = no trailing, classic SL only. */
  trailingLevels: TrailingLevel[];
  intervalMs: number;
  slippageBps: number;
}

export function parseTrailingLevels(raw: string | undefined): TrailingLevel[] {
  if (!raw) return [];
  const out: TrailingLevel[] = [];
  for (const part of raw.split(",")) {
    const trimmed = part.trim();
    if (!trimmed) continue;
    const [trigger, lock] = trimmed.split(":");
    const triggerPct = parseFloat(trigger);
    const lockPct = parseFloat(lock);
    if (!Number.isFinite(triggerPct) || !Number.isFinite(lockPct)) continue;
    out.push({ triggerPct, lockPct });
  }
  out.sort((a, b) => a.triggerPct - b.triggerPct);
  return out;
}

const HEARTBEAT_MS = 30_000;

function ts(): string {
  return new Date().toISOString().slice(11, 19);
}

export class PositionMonitor {
  private timer: NodeJS.Timeout | null = null;
  private ticking = false;
  private readonly bot: SnipeBot;
  private readonly cfg: MonitorConfig;
  private readonly quoteFailures = new Map<string, number>();
  private readonly latestPnlPct = new Map<string, number>();
  private readonly peakPnlPct = new Map<string, number>();
  private lastHeartbeat = 0;

  constructor(bot: SnipeBot, cfg: MonitorConfig) {
    this.bot = bot;
    this.cfg = cfg;
  }

  start(): void {
    if (this.timer) return;
    const ms = Math.max(1000, this.cfg.intervalMs);
    this.timer = setInterval(() => {
      void this.tick();
    }, ms);
  }

  stop(): void {
    if (this.timer) {
      clearInterval(this.timer);
      this.timer = null;
    }
  }

  private async tick(): Promise<void> {
    if (this.ticking) return; // skip overlap if previous tick still running
    this.ticking = true;
    try {
      const open = getOpenSnipes();
      if (open.length === 0) return;
      for (const pos of open) {
        await this.evaluatePosition(pos);
      }
      this.maybeHeartbeat(open.map((p) => p.mint));
    } finally {
      this.ticking = false;
    }
  }

  private maybeHeartbeat(openMints: string[]): void {
    const now = Date.now();
    if (now - this.lastHeartbeat < HEARTBEAT_MS) return;
    this.lastHeartbeat = now;
    const parts = openMints.map((m) => {
      const pct = this.latestPnlPct.get(m);
      const v = pct == null ? "?" : `${pct >= 0 ? "+" : ""}${pct.toFixed(1)}%`;
      return `${m.slice(0, 8)}…=${v}`;
    });
    console.log(
      `[${ts()}] [monitor] ${openMints.length} open · ${parts.join(" ")}`
    );
  }

  private async evaluatePosition(pos: {
    mint: string;
    spentLamports: number;
    receivedRawTokens: number;
  }): Promise<void> {
    if (pos.spentLamports <= 0 || pos.receivedRawTokens <= 0) return;
    let lamportsOut: number;
    try {
      const quote = await getQuote(
        pos.mint,
        SOL_MINT,
        pos.receivedRawTokens,
        this.cfg.slippageBps
      );
      lamportsOut = quote.outAmount ? parseInt(quote.outAmount, 10) : 0;
      this.quoteFailures.delete(pos.mint);
    } catch (err) {
      if (isJupiterRateLimitError(err)) {
        console.warn(
          `[monitor] Jupiter quote rate limited for ${pos.mint.slice(0, 8)}...; will retry on next tick.`
        );
        return;
      }

      const fails = (this.quoteFailures.get(pos.mint) ?? 0) + 1;
      this.quoteFailures.set(pos.mint, fails);
      // After many consecutive quote failures, the pool is probably rugged or
      // illiquid — that's a real outcome, treat as SL.
      if (fails >= 6) {
        const m = err instanceof Error ? err.message : String(err);
        console.warn(
          `[monitor] ${pos.mint.slice(0, 8)}…: ${fails} consecutive quote failures (${m}); treating as SL`
        );
        void this.bot.closePosition(pos.mint, "SL");
      }
      return;
    }

    if (!Number.isFinite(lamportsOut) || lamportsOut < 0) return;

    const pnlPct =
      ((lamportsOut - pos.spentLamports) / pos.spentLamports) * 100;
    this.latestPnlPct.set(pos.mint, pnlPct);

    // Track peak gain for trailing logic.
    const prevPeak = this.peakPnlPct.get(pos.mint);
    if (prevPeak === undefined || pnlPct > prevPeak) {
      this.peakPnlPct.set(pos.mint, pnlPct);
    }
    const peak = this.peakPnlPct.get(pos.mint) ?? pnlPct;

    // Compute effective stop: start at -stopLossPct, ratchet up via trailing levels.
    let effectiveStopPct = -this.cfg.stopLossPct;
    let activeTrail: TrailingLevel | null = null;
    for (const level of this.cfg.trailingLevels) {
      if (peak >= level.triggerPct) {
        effectiveStopPct = level.lockPct;
        activeTrail = level;
      }
    }

    // Take profit cap.
    if (pnlPct >= this.cfg.takeProfitPct) {
      console.log(
        `[monitor] TP hit on ${pos.mint.slice(0, 8)}…: ${pnlPct.toFixed(1)}% >= ${this.cfg.takeProfitPct}%`
      );
      void this.bot.closePosition(pos.mint, "TP");
      return;
    }

    // Stop / trailing-stop check.
    if (pnlPct <= effectiveStopPct) {
      if (activeTrail) {
        console.log(
          `[monitor] TRAIL hit on ${pos.mint.slice(0, 8)}…: ${pnlPct.toFixed(1)}% <= ${effectiveStopPct.toFixed(1)}% (peak was +${peak.toFixed(1)}%, locked at +${activeTrail.lockPct}%)`
        );
        void this.bot.closePosition(pos.mint, "TRAIL");
      } else {
        console.log(
          `[monitor] SL hit on ${pos.mint.slice(0, 8)}…: ${pnlPct.toFixed(1)}% <= -${this.cfg.stopLossPct}%`
        );
        void this.bot.closePosition(pos.mint, "SL");
      }
      return;
    }
  }
}
