import {
  TELEGRAM_BOT_TOKEN,
  TELEGRAM_CHAT_ID,
  TELEGRAM_BROADCAST_CHAT_ID,
  simulationMode,
} from "./config.js";

export interface TelegramMessage {
  text: string;
  parse_mode?: "HTML" | "Markdown";
  reply_markup?: InlineKeyboardMarkup;
}

type InlineKeyboardButton = {
  text: string;
  url: string;
};

type InlineKeyboardMarkup = {
  inline_keyboard: InlineKeyboardButton[][];
};

function escapeHtml(value: unknown): string {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

function shortAddress(value: string): string {
  return value.length > 14 ? `${value.slice(0, 6)}...${value.slice(-6)}` : value;
}

function solscanTxUrl(signature: string): string {
  return `https://solscan.io/tx/${encodeURIComponent(signature)}`;
}

function solscanTokenUrl(mint: string): string {
  return `https://solscan.io/token/${encodeURIComponent(mint)}`;
}

function dexScreenerUrl(mint: string): string {
  return `https://dexscreener.com/solana/${encodeURIComponent(mint)}`;
}

function axiomUrl(mint: string): string {
  return `https://axiom.trade/t/${encodeURIComponent(mint)}`;
}

function pumpFunUrl(mint: string): string {
  return `https://pump.fun/coin/${encodeURIComponent(mint)}`;
}

function riskExitKind(reason?: string): "stopLoss" | "takeProfit" | null {
  const lower = reason?.toLowerCase() ?? "";
  if (lower.includes("stop loss")) return "stopLoss";
  if (lower.includes("take profit")) return "takeProfit";
  return null;
}

export interface TradeNotification {
  type: "buy" | "sell";
  symbol: string;
  amount: string;
  solAmount: string;
  sourceAmount?: string;
  sourceSolAmount?: string;
  marketCap?: string;
  marketCapCheckedAt?: number;
  isSimulated: boolean;
  wallet: string;
  tokenAddress: string;
  pnl?: string;
  positionPnl?: string;
  note?: string;
  quotedPnlPct?: string;
  txSignature?: string;
}

export interface PnlNotification {
  totalTrades: number;
  totalBuys: number;
  totalSells: number;
  realizedPnl: string;
  unrealizedPnl: string;
  totalPnl: string;
  winRate: string;
  avgWin: string;
  avgLoss: string;
  openPositions: number;
}

export interface RiskExitNotification {
  status: "triggered" | "failed";
  symbol: string;
  reason: string;
  quotedSol?: string;
  costBasisSol?: string;
  pnlPct?: string;
  error?: string;
  tokenAddress: string;
  isSimulated: boolean;
}

class TelegramService {
  private botToken: string;
  private chatId: string;
  private broadcastChatId: string;
  private enabled: boolean;
  private broadcastEnabled: boolean;

  constructor() {
    this.botToken = TELEGRAM_BOT_TOKEN || "";
    this.chatId = TELEGRAM_CHAT_ID || "";
    this.broadcastChatId = TELEGRAM_BROADCAST_CHAT_ID || "";
    this.enabled = !!(this.botToken && this.chatId);
    this.broadcastEnabled = !!(this.botToken && this.broadcastChatId);
    
    if (!this.enabled) {
      console.log("ðŸ“± Telegram notifications disabled (missing bot token or chat ID)");
    } else {
      console.log("ðŸ“± Telegram notifications enabled");
    }
  }

  private async sendMessage(
    message: TelegramMessage,
    chatId: string = this.chatId
  ): Promise<boolean> {
    if (!this.botToken || !chatId) return false;

    try {
      const url = `https://api.telegram.org/bot${this.botToken}/sendMessage`;
      
      const response = await fetch(url, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          chat_id: chatId,
          text: message.text,
          parse_mode: message.parse_mode || "HTML",
          disable_web_page_preview: true,
          ...(message.reply_markup ? { reply_markup: message.reply_markup } : {}),
        }),
        signal: AbortSignal.timeout(5000),
      });

      if (!response.ok) {
        const error = await response.text();
        console.warn(`ðŸ“± Telegram send failed: ${response.status} ${error}`);
        return false;
      }

      return true;
    } catch (err) {
      console.warn(`ðŸ“± Telegram error:`, err instanceof Error ? err.message : String(err));
      return false;
    }
  }

  private async sendToAudience(message: TelegramMessage): Promise<boolean> {
    const targets = new Set<string>();
    if (this.enabled) targets.add(this.chatId);
    if (this.broadcastEnabled) targets.add(this.broadcastChatId);
    if (targets.size === 0) return false;

    const results = await Promise.all(
      [...targets].map((chatId) => this.sendMessage(message, chatId))
    );
    return results.some(Boolean);
  }

  private tokenButtons(mint: string, txSignature?: string): InlineKeyboardMarkup {
    const rows: InlineKeyboardButton[][] = [
      [
        { text: "📊 DexScreener", url: dexScreenerUrl(mint) },
        { text: "⚡ Axiom", url: axiomUrl(mint) },
      ],
      [{ text: "🔎 Solscan CA", url: solscanTokenUrl(mint) }],
    ];

    if (mint.endsWith("pump")) {
      rows[1]!.push({ text: "🚀 Pump.fun", url: pumpFunUrl(mint) });
    }

    if (txSignature) {
      rows.push([{ text: "🧾 Solscan Tx", url: solscanTxUrl(txSignature) }]);
    }

    return { inline_keyboard: rows };
  }

  async notifyTrade(trade: TradeNotification): Promise<boolean> {
    const mode = trade.isSimulated ? "SIMULATION" : "LIVE";
    const sideLabel = trade.type === "buy" ? "BUY" : "SELL";
    const sideIcon = trade.type === "buy" ? "🟢" : "🔴";
    const modeIcon = trade.isSimulated ? "🎭" : "💰";
    const riskKind = trade.type === "sell" ? riskExitKind(trade.note) : null;
    const sourceLabel =
      trade.wallet === "manual" || trade.wallet === "manual-sell" || trade.wallet === "risk" || trade.wallet === "risk-sell"
        ? "Actor"
        : "Copied from";
    const title =
      riskKind === "stopLoss"
        ? `🔴 <b>SELL EXECUTED — STOP LOSS</b> ${modeIcon} <b>${mode}</b>`
        : riskKind === "takeProfit"
        ? `🟢 <b>SELL EXECUTED — TAKE PROFIT</b> ${modeIcon} <b>${mode}</b>`
        : trade.type === "buy"
        ? `${sideIcon} <b>MIRROR BUY EXECUTED</b> ${modeIcon} <b>${mode}</b>`
        : `${sideIcon} <b>${sideLabel}</b> ${modeIcon} <b>${mode}</b>`;
    const symbol = escapeHtml(trade.symbol);
    const tokenAddress = escapeHtml(trade.tokenAddress);
    const wallet = escapeHtml(shortAddress(trade.wallet));
    const amountLabel = trade.type === "buy" ? "Tokens bought" : "Tokens sold";
    const solLabel = trade.type === "buy" ? "SOL spent" : "SOL received";

    const lines = [
      title,
      "Morpheus Trade Feed",
      "",
      `🪙 <b>Token</b>: <code>${symbol}</code>`,
    ];

    if (riskKind) {
      if (trade.quotedPnlPct) {
        lines.push(`📉 <b>Quoted PnL</b>: ${escapeHtml(trade.quotedPnlPct)}`);
      }
      if (trade.pnl) lines.push(`💰 <b>Fill PnL</b>: ${escapeHtml(trade.pnl)}`);
      if (trade.positionPnl) lines.push(`📊 <b>Position PnL</b>: ${escapeHtml(trade.positionPnl)}`);
      lines.push(`💵 <b>${solLabel}</b>: ${escapeHtml(trade.solAmount)} SOL`);
      lines.push(`📦 <b>${amountLabel}</b>: <code>${escapeHtml(trade.amount)}</code>`);
      if (trade.note) lines.push(`🎯 <b>Reason</b>: ${escapeHtml(trade.note)}`);
    } else {
      lines.push(`📦 <b>${amountLabel}</b>: ${escapeHtml(trade.amount)}`);
      if (trade.type === "buy" && trade.sourceAmount && trade.sourceAmount !== trade.amount) {
        lines.push(`👥 <b>Source tokens</b>: ${escapeHtml(trade.sourceAmount)}`);
      }
      lines.push(`◎ <b>${solLabel}</b>: ${escapeHtml(trade.solAmount)} SOL`);
      if (trade.type === "buy" && trade.sourceSolAmount && trade.sourceSolAmount !== trade.solAmount) {
        lines.push(`👥 <b>Source spent</b>: ${escapeHtml(trade.sourceSolAmount)} SOL`);
      }
    }

    if (trade.marketCap) {
      lines.push(`📊 <b>Market cap</b>: ${escapeHtml(trade.marketCap)}`);
      if (trade.marketCapCheckedAt) {
        lines.push(`🕒 <b>MC checked</b>: ${escapeHtml(new Date(trade.marketCapCheckedAt).toLocaleString())}`);
      }
    }

    if (!riskKind && trade.pnl) lines.push(`📈 <b>PnL</b>: ${escapeHtml(trade.pnl)}`);
    if (!riskKind && trade.note) lines.push(`📝 <b>Note</b>: ${escapeHtml(trade.note)}`);

    lines.push("");
    lines.push(`👤 <b>${sourceLabel}</b>: <code>${wallet}</code>`);
    lines.push(`🔗 <b>CA</b>: <code>${tokenAddress}</code>`);

    if (trade.txSignature) {
      lines.push(`🧾 <b>Tx</b>: <a href="${solscanTxUrl(trade.txSignature)}">${escapeHtml(shortAddress(trade.txSignature))}</a>`);
    }

    return this.sendToAudience({
      text: lines.join("\n"),
      parse_mode: "HTML",
      reply_markup: this.tokenButtons(trade.tokenAddress, trade.txSignature),
    });
  }
  async notifyPnlReport(pnl: PnlNotification): Promise<boolean> {
    console.warn("Telegram PnL notification suppressed; use /pnl to request it.");
    return false;
    const totalPnlValue = parseFloat(pnl.totalPnl.replace(/[^0-9.-]/g, ""));
    const pnlColor = totalPnlValue >= 0 ? "ðŸŸ¢" : "ðŸ”´";

    let message = `${pnlColor} ðŸ“Š PnL REPORT\n\n`;
    message += `ðŸ“ˆ Total PnL: <b>${pnl.totalPnl}</b>\n`;
    message += `ðŸ’° Realized: ${pnl.realizedPnl}\n`;
    message += `ðŸ“Š Unrealized: ${pnl.unrealizedPnl}\n\n`;
    
    message += `ðŸ“Š Stats:\n`;
    message += `â€¢ Total Trades: ${pnl.totalTrades}\n`;
    message += `â€¢ Buys: ${pnl.totalBuys} | Sells: ${pnl.totalSells}\n`;
    message += `â€¢ Win Rate: ${pnl.winRate}\n`;
    message += `â€¢ Avg Win: ${pnl.avgWin}\n`;
    message += `â€¢ Avg Loss: ${pnl.avgLoss}\n`;
    message += `â€¢ Open Positions: ${pnl.openPositions}\n`;

    return this.sendMessage({ text: message, parse_mode: "HTML" });
  }

  async notifyError(error: string, context?: string): Promise<boolean> {
    const detail = context ? `${context} | ${error}` : error;
    console.warn(`Telegram error notification suppressed: ${detail}`);
    return false;
  }

  async notifyRiskExit(risk: RiskExitNotification): Promise<boolean> {
    const tag = risk.isSimulated ? " (SIM)" : "";
    const reasonLower = risk.reason.toLowerCase();
    const isTakeProfit = reasonLower.includes("take profit");
    const isStopLoss = reasonLower.includes("stop loss");
    const triggerIcon = isTakeProfit ? "🟢" : isStopLoss ? "🔴" : "🚦";
    const triggerLabel = isTakeProfit
      ? "TAKE PROFIT TRIGGERED"
      : isStopLoss
      ? "STOP LOSS TRIGGERED"
      : "RISK EXIT TRIGGERED";
    let message =
      risk.status === "triggered"
        ? `${triggerIcon} <b>${triggerLabel}${tag}</b>\n\n`
        : `⚠️ <b>RISK EXIT FAILED${tag}</b>\n\n`;

    message += `Token: <code>${escapeHtml(risk.symbol)}</code>\n`;
    message += `Reason: ${escapeHtml(risk.reason)}\n`;
    if (risk.pnlPct) message += `Quoted PnL: ${escapeHtml(risk.pnlPct)}\n`;
    if (risk.quotedSol) message += `Quoted exit: ${escapeHtml(risk.quotedSol)} SOL\n`;
    if (risk.costBasisSol) message += `Cost basis: ${escapeHtml(risk.costBasisSol)} SOL\n`;
    if (risk.error) message += `Error: <code>${escapeHtml(risk.error)}</code>\n`;
    message += `CA: <a href="${solscanTokenUrl(risk.tokenAddress)}">${escapeHtml(risk.tokenAddress)}</a>\n`;

    return this.sendToAudience({
      text: message,
      parse_mode: "HTML",
      reply_markup: this.tokenButtons(risk.tokenAddress),
    });
  }

  async notifyBotStatus(status: "started" | "stopped" | "reset"): Promise<boolean> {
    if (!this.broadcastEnabled) {
      console.warn(`Telegram bot status notification suppressed: ${status}`);
      return false;
    }
    const statusIcon =
      status === "started" ? "🟢" : status === "stopped" ? "🔴" : "🔄";
    const modeIcon = simulationMode ? "🎭" : "💰";
    const message =
      `${statusIcon} <b>Morpheus Trading Bot ${status.toUpperCase()}</b>\n\n` +
      `${modeIcon} <b>Mode</b>: ${simulationMode ? "SIMULATION" : "LIVE"}\n` +
      `📡 <b>Feed</b>: Trade alerts, risk exits, and session status`;

    return this.sendMessage(
      { text: message, parse_mode: "HTML" },
      this.broadcastChatId
    );
  }

  async testConnection(): Promise<boolean> {
    if (!this.enabled) return false;

    const message = "🤖 <b>FinalBot is online</b>\n\nReady to monitor trades.";
    return this.sendMessage({ text: message, parse_mode: "HTML" });
  }

  async notifyInfo(title: string, details?: string): Promise<boolean> {
    const lines = [`ℹ️ <b>${escapeHtml(title)}</b>`];
    if (details) lines.push("", escapeHtml(details));
    return this.sendMessage({ text: lines.join("\n"), parse_mode: "HTML" });
  }
}

// Export singleton instance
export const telegram = new TelegramService();

// Helper function to format PnL for Telegram
export function formatPnlForTelegram(pnlLamports: number): string {
  const pnlSol = pnlLamports / 1e9;
  const sign = pnlSol >= 0 ? "+" : "";
  return `${sign}${pnlSol.toFixed(4)} SOL`;
}

