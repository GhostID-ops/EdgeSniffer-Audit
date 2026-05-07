import { Keypair, LAMPORTS_PER_SOL, PublicKey } from "@solana/web3.js";
import { TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID } from "./config.js";
import { getTokenBalance } from "./balance.js";
import { sellTokenToSol } from "./buyToken.js";
import { getQuote } from "./jupiter.js";
import {
  findSnipeByMintPrefix,
  getOpenSnipes,
  getSessionPnl,
  getSnipe,
  recordSnipeExit,
  type SnipePosition,
} from "./snipePositions.js";

export type ExitReason = "MANUAL" | "TP" | "SL" | "TRAIL";

const SOL_MINT = "So11111111111111111111111111111111111111112";

interface TelegramUpdate {
  update_id: number;
  message?: {
    chat: { id: number };
    text?: string;
  };
}

interface SnipeRuntimeStatus {
  channelTitle: string;
  buySol: number;
  slippageBps: number;
  dryRun: boolean;
  minBoostAmount: number;
  minTotalBoosts: number;
  walletAddress: string;
}

export class SnipeBot {
  private readonly token: string;
  private readonly chatId: string;
  private readonly enabled: boolean;
  private readonly keypair: Keypair;
  private readonly slippageBps: number;
  private readonly statusFn: () => SnipeRuntimeStatus;
  private offset = 0;
  private polling = false;
  private sellInFlight = new Set<string>();
  private sendMutedUntilMs = 0;
  private lastMutedLogMs = 0;

  constructor(args: {
    keypair: Keypair;
    slippageBps: number;
    statusFn: () => SnipeRuntimeStatus;
  }) {
    this.token = TELEGRAM_BOT_TOKEN;
    this.chatId = TELEGRAM_CHAT_ID;
    this.enabled = !!(this.token && this.chatId);
    this.keypair = args.keypair;
    this.slippageBps = args.slippageBps;
    this.statusFn = args.statusFn;
  }

  isEnabled(): boolean {
    return this.enabled;
  }

  start(): void {
    if (!this.enabled) {
      console.log(
        "[snipeBot] disabled (TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID missing)"
      );
      return;
    }
    if (this.polling) return;
    this.polling = true;
    void this.poll();
    void this.send(
      `<b>Sniper online</b>\nUse /snipehelp for commands. /sellall closes everything.`
    );
  }

  async send(text: string): Promise<void> {
    if (!this.enabled) return;
    try {
      if (this.isSendMuted()) return;

      const res = await fetch(`https://api.telegram.org/bot${this.token}/sendMessage`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          chat_id: this.chatId,
          text,
          parse_mode: "HTML",
          disable_web_page_preview: true,
        }),
        signal: AbortSignal.timeout(8000),
      });
      if (!res.ok) {
        const body = await res.text();
        if (!this.handleSendFailure(res.status, body)) {
          console.warn(`[snipeBot] send failed: ${res.status} ${body}`);
        }
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      console.error(`[snipeBot] send failed: ${msg}`);
    }
  }

  async notifyBuy(args: {
    mint: string;
    spentLamports: number;
    receivedRaw: string | undefined;
    signature: string;
  }): Promise<void> {
    const sol = (args.spentLamports / LAMPORTS_PER_SOL).toFixed(4);
    const out = args.receivedRaw ?? "?";
    const text =
      `<b>SNIPE BUY</b>\n` +
      `Mint: <code>${args.mint}</code>\n` +
      `Spent: ${sol} SOL\n` +
      `Received: ${out} (raw)\n` +
      `Tx: <a href="https://solscan.io/tx/${args.signature}">${args.signature.slice(0, 16)}…</a>\n\n` +
      `/sell ${args.mint.slice(0, 8)} to exit · /sellall · /positions`;
    await this.send(text);
  }

  async notifyBuyFailed(mint: string, err: string): Promise<void> {
    await this.send(
      `<b>SNIPE BUY FAILED</b>\nMint: <code>${mint}</code>\nError: ${escape(err)}`
    );
  }

  async notifyDryRun(
    mint: string,
    sol: number,
    quotedOutRaw?: string
  ): Promise<void> {
    const out = quotedOutRaw ?? "?";
    await this.send(
      `<b>[SIM] PAPER BUY</b>\n` +
        `Mint: <code>${mint}</code>\n` +
        `Spent: ${sol} SOL (simulated)\n` +
        `Quoted: ${out} (raw)\n\n` +
        `Use /sell ${mint.slice(0, 8)} to paper-sell · /sessionpnl for totals`
    );
  }

  async notifyFiltered(
    mint: string,
    boostAmount: number,
    totalBoosts: number
  ): Promise<void> {
    console.log(
      `[snipe] filtered ${mint.slice(0, 16)}... boost=${boostAmount} total=${totalBoosts}`
    );
  }

  private isSendMuted(): boolean {
    const now = Date.now();
    if (now >= this.sendMutedUntilMs) return false;

    if (now - this.lastMutedLogMs > 60_000) {
      this.lastMutedLogMs = now;
      const remainingSec = Math.ceil((this.sendMutedUntilMs - now) / 1_000);
      console.warn(
        `[snipeBot] Telegram flood wait active; suppressing sends for ${remainingSec}s.`
      );
    }
    return true;
  }

  private handleSendFailure(status: number, body: string): boolean {
    if (status !== 429) return false;

    const retryAfter = parseTelegramRetryAfter(body);
    if (retryAfter <= 0) return false;

    this.sendMutedUntilMs = Math.max(
      this.sendMutedUntilMs,
      Date.now() + retryAfter * 1_000
    );
    console.warn(
      `[snipeBot] Telegram rate limited; pausing sends for ${retryAfter}s.`
    );
    return true;
  }

  private async poll(): Promise<void> {
    while (this.polling) {
      try {
        const url = `https://api.telegram.org/bot${this.token}/getUpdates?offset=${this.offset}&timeout=25`;
        const res = await fetch(url, { signal: AbortSignal.timeout(35000) });
        const json = (await res.json()) as {
          ok: boolean;
          result?: TelegramUpdate[];
        };
        if (!json.ok || !json.result) {
          await sleep(2000);
          continue;
        }
        for (const update of json.result) {
          this.offset = update.update_id + 1;
          const msg = update.message;
          if (!msg?.text) continue;
          if (msg.chat.id.toString() !== this.chatId) continue;
          await this.handleCommand(msg.text.trim());
        }
      } catch (err) {
        const m = err instanceof Error ? err.message : String(err);
        console.error(`[snipeBot] poll error: ${m}`);
        await sleep(3000);
      }
    }
  }

  private async handleCommand(text: string): Promise<void> {
    const [cmd, ...rest] = text.split(/\s+/);
    const arg = rest.join(" ").trim();

    switch (cmd.toLowerCase()) {
      case "/start":
      case "/snipehelp":
      case "/help":
        await this.send(this.helpText());
        return;
      case "/snipestatus":
        await this.sendStatus();
        return;
      case "/positions":
      case "/snipes":
        await this.sendPositions();
        return;
      case "/sessionpnl":
      case "/pnl":
        await this.sendSessionPnl();
        return;
      case "/sellall":
        await this.sellAllOpenPositions();
        return;
      case "/sell":
        await this.handleSell(arg);
        return;
      default:
        return;
    }
  }

  private helpText(): string {
    return (
      `<b>Sniper commands</b>\n` +
      `/snipestatus — current config\n` +
      `/positions — open snipe positions\n` +
      `/sessionpnl — realized PnL summary this session\n` +
      `/sell &lt;mint or prefix&gt; — sell that position 100%\n` +
      `/sellall — sell every open snipe\n\n` +
      `<i>In dry-run mode, sells use Jupiter quotes — no SOL is spent or received.</i>`
    );
  }

  async sendSessionPnl(): Promise<void> {
    const p = getSessionPnl();
    const spent = (p.totalSpentLamports / LAMPORTS_PER_SOL).toFixed(4);
    const received = (p.totalReceivedLamports / LAMPORTS_PER_SOL).toFixed(4);
    const realized = (p.realizedPnlLamports / LAMPORTS_PER_SOL).toFixed(4);
    const winRate =
      p.closedCount > 0
        ? ((p.wins / p.closedCount) * 100).toFixed(1) + "%"
        : "n/a";
    await this.send(
      `<b>Session PnL</b>\n` +
        `Closed: ${p.closedCount} (${p.wins}W / ${p.losses}L · ${winRate})\n` +
        `Open: ${p.openCount}\n` +
        `Spent: ${spent} SOL\n` +
        `Received: ${received} SOL\n` +
        `Realized: <b>${realized} SOL</b>`
    );
  }

  async sendStatus(): Promise<void> {
    const s = this.statusFn();
    await this.send(
      `<b>Sniper status</b>\n` +
        `Wallet: <code>${s.walletAddress}</code>\n` +
        `Channel: ${escape(s.channelTitle)}\n` +
        `Buy size: ${s.buySol} SOL\n` +
        `Slippage: ${s.slippageBps} bps\n` +
        `DryRun: ${s.dryRun}\n` +
        `Filter: boostAmount&gt;=${s.minBoostAmount}, totalBoosts&gt;=${s.minTotalBoosts}\n` +
        `Open snipes: ${getOpenSnipes().length}`
    );
  }

  async sendPositions(): Promise<void> {
    const open = getOpenSnipes();
    if (open.length === 0) {
      await this.send("<b>No open snipe positions.</b>");
      return;
    }
    const lines = open.map((p) => {
      const spent = (p.spentLamports / LAMPORTS_PER_SOL).toFixed(4);
      const ageMin = Math.round((Date.now() - p.boughtAt) / 60000);
      const tag = p.simulated ? "[SIM] " : "";
      return `• ${tag}<code>${p.mint}</code>\n  spent ${spent} SOL · ${ageMin}m ago`;
    });
    await this.send(`<b>Open snipes (${open.length})</b>\n\n${lines.join("\n")}`);
  }

  async sellAllOpenPositions(): Promise<void> {
    const open = getOpenSnipes();
    if (open.length === 0) {
      await this.send("Nothing to sell.");
      console.log("[snipe] no open positions to sell.");
      return;
    }
    console.log(`[snipe] selling ${open.length} open position(s)...`);
    await this.send(`Selling ${open.length} positions…`);
    for (const pos of open) {
      await this.executeSell(pos);
    }
  }

  private async handleSell(arg: string): Promise<void> {
    if (!arg) {
      await this.send("Usage: <code>/sell &lt;mint or prefix&gt;</code>");
      return;
    }
    const pos = findSnipeByMintPrefix(arg);
    if (!pos) {
      await this.send(`No open snipe matching <code>${escape(arg)}</code>`);
      return;
    }
    await this.executeSell(pos);
  }

  /** Public entry point used by the position monitor (TP/SL). Skips if already selling or closed. */
  async closePosition(mint: string, reason: ExitReason): Promise<void> {
    const pos = getSnipe(mint);
    if (!pos || pos.closed) return;
    if (this.sellInFlight.has(mint)) return;
    await this.executeSell(pos, reason);
  }

  private async executeSell(
    pos: SnipePosition,
    reason: ExitReason = "MANUAL"
  ): Promise<void> {
    if (this.sellInFlight.has(pos.mint)) {
      await this.send(`Sell already in flight for <code>${pos.mint}</code>`);
      return;
    }
    this.sellInFlight.add(pos.mint);
    try {
      if (pos.simulated) {
        await this.executeSimulatedSell(pos, reason);
      } else {
        await this.executeLiveSell(pos, reason);
      }
    } catch (err) {
      const m = err instanceof Error ? err.message : String(err);
      const tag = pos.simulated ? "[SIM] " : "";
      await this.send(
        `<b>${tag}SELL FAILED</b> <code>${pos.mint}</code>\n${escape(m)}`
      );
    } finally {
      this.sellInFlight.delete(pos.mint);
    }
  }

  private async executeSimulatedSell(
    pos: SnipePosition,
    reason: ExitReason
  ): Promise<void> {
    const quote = await getQuote(
      pos.mint,
      SOL_MINT,
      pos.receivedRawTokens,
      this.slippageBps
    );
    const lamports = quote.outAmount ? parseInt(quote.outAmount, 10) : 0;
    if (!Number.isFinite(lamports) || lamports < 0) {
      throw new Error("Quote returned invalid outAmount");
    }
    recordSnipeExit(pos.mint, `sim-sell-${reason.toLowerCase()}`, lamports);
    const sol = (lamports / LAMPORTS_PER_SOL).toFixed(4);
    const spent = (pos.spentLamports / LAMPORTS_PER_SOL).toFixed(4);
    const delta = lamports - pos.spentLamports;
    const pnl = (delta / LAMPORTS_PER_SOL).toFixed(4);
    const pct =
      pos.spentLamports > 0
        ? ((delta / pos.spentLamports) * 100).toFixed(1) + "%"
        : "n/a";
    const reasonTag = reason === "MANUAL" ? "" : ` (${reason})`;
    await this.send(
      `<b>[SIM] PAPER SOLD${reasonTag}</b> <code>${pos.mint}</code>\n` +
        `Out: ${sol} SOL (in ${spent} SOL)\n` +
        `PnL: ${pnl} SOL (${pct})`
    );
  }

  private async executeLiveSell(
    pos: SnipePosition,
    reason: ExitReason
  ): Promise<void> {
    const liveBalance = await getTokenBalance(this.keypair.publicKey, pos.mint);
    if (liveBalance <= 0) {
      await this.send(
        `Wallet has 0 of <code>${pos.mint}</code>; marking position closed.`
      );
      recordSnipeExit(pos.mint, "no-balance", 0);
      return;
    }
    const result = await sellTokenToSol(
      pos.mint,
      liveBalance,
      this.slippageBps,
      this.keypair
    );
    const lamports = result.outAmount ? parseInt(result.outAmount, 10) : 0;
    recordSnipeExit(pos.mint, result.signature, lamports);
    const sol = (lamports / LAMPORTS_PER_SOL).toFixed(4);
    const spent = (pos.spentLamports / LAMPORTS_PER_SOL).toFixed(4);
    const delta = lamports - pos.spentLamports;
    const pnl = (delta / LAMPORTS_PER_SOL).toFixed(4);
    const pct =
      pos.spentLamports > 0
        ? ((delta / pos.spentLamports) * 100).toFixed(1) + "%"
        : "n/a";
    const reasonTag = reason === "MANUAL" ? "" : ` (${reason})`;
    await this.send(
      `<b>SOLD${reasonTag}</b> <code>${pos.mint}</code>\n` +
        `Out: ${sol} SOL (in ${spent} SOL)\n` +
        `PnL: ${pnl} SOL (${pct})\n` +
        `Tx: <a href="https://solscan.io/tx/${result.signature}">${result.signature.slice(0, 16)}…</a>`
    );
  }
}

function escape(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function sleep(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms));
}

function parseTelegramRetryAfter(body: string): number {
  try {
    const json = JSON.parse(body) as {
      parameters?: { retry_after?: unknown };
    };
    const retryAfter = json.parameters?.retry_after;
    return typeof retryAfter === "number" && Number.isFinite(retryAfter)
      ? Math.max(0, retryAfter)
      : 0;
  } catch {
    const match = /retry after (\d+)/i.exec(body);
    return match ? parseInt(match[1]!, 10) : 0;
  }
}

// Type harmony: PublicKey is needed indirectly via keypair.publicKey
export type { PublicKey };
