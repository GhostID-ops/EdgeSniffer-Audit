import { TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, tradingMode } from "./config.js";

export interface TelegramMessage {
  text: string;
  parse_mode?: "HTML" | "Markdown";
}

export interface TradeNotification {
  type: "buy" | "sell";
  symbol: string;
  amount: string;
  solAmount: string;
  marketCap?: string;
  isSimulated: boolean;
  isDryRun?: boolean;
  wallet: string;
  tokenAddress: string;
  pnl?: string;
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

function escapeHtml(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

function truncate(value: string, maxLength: number): string {
  return value.length > maxLength ? `${value.slice(0, maxLength)}...` : value;
}

class TelegramService {
  private botToken: string;
  private chatId: string;
  private enabled: boolean;

  constructor() {
    this.botToken = TELEGRAM_BOT_TOKEN || "";
    this.chatId = TELEGRAM_CHAT_ID || "";
    this.enabled = !!(this.botToken && this.chatId);

    if (!this.enabled) {
      console.log("Telegram notifications disabled (missing bot token or chat ID)");
    } else {
      console.log("Telegram notifications enabled");
    }
  }

  private async sendMessage(message: TelegramMessage): Promise<boolean> {
    if (!this.enabled) return false;

    try {
      const url = `https://api.telegram.org/bot${this.botToken}/sendMessage`;

      const response = await fetch(url, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          chat_id: this.chatId,
          text: message.text,
          parse_mode: message.parse_mode || "HTML",
          disable_web_page_preview: true,
        }),
        signal: AbortSignal.timeout(5000),
      });

      if (!response.ok) {
        const error = await response.text();
        console.warn(`Telegram send failed: ${response.status} ${error}`);
        return false;
      }

      return true;
    } catch (err) {
      console.warn("Telegram error:", err instanceof Error ? err.message : String(err));
      return false;
    }
  }

  async notifyTrade(trade: TradeNotification): Promise<boolean> {
    const modeTag = trade.isDryRun ? "DRY RUN" : trade.isSimulated ? "SIM" : "LIVE";
    const simTag = trade.isDryRun ? " (DRY)" : trade.isSimulated ? " (SIM)" : "";

    let message = `${modeTag} ${trade.type.toUpperCase()}${simTag}\n\n`;
    message += `Token: <code>${escapeHtml(trade.symbol)}</code>\n`;
    message += `Amount: ${escapeHtml(trade.amount)}\n`;
    message += `SOL: ${escapeHtml(trade.solAmount)}\n`;

    if (trade.marketCap) {
      message += `MC: ${escapeHtml(trade.marketCap)}\n`;
    }

    message += `Wallet: <code>${escapeHtml(trade.wallet)}</code>\n`;
    message += `CA: <code>${escapeHtml(trade.tokenAddress)}</code>\n`;

    if (trade.pnl) {
      message += `PnL: ${escapeHtml(trade.pnl)}\n`;
    }

    if (trade.txSignature) {
      message += `TX: <code>${escapeHtml(trade.txSignature)}</code>\n`;
    }

    return this.sendMessage({ text: message, parse_mode: "HTML" });
  }

  async notifyPnlReport(pnl: PnlNotification): Promise<boolean> {
    const totalPnlValue = parseFloat(pnl.totalPnl.replace(/[^0-9.-]/g, ""));
    const pnlColor = totalPnlValue >= 0 ? "PROFIT" : "LOSS";

    let message = `${pnlColor} PnL REPORT\n\n`;
    message += `Total PnL: <b>${escapeHtml(pnl.totalPnl)}</b>\n`;
    message += `Realized: ${escapeHtml(pnl.realizedPnl)}\n`;
    message += `Unrealized: ${escapeHtml(pnl.unrealizedPnl)}\n\n`;

    message += `Stats:\n`;
    message += `- Total Trades: ${pnl.totalTrades}\n`;
    message += `- Buys: ${pnl.totalBuys} | Sells: ${pnl.totalSells}\n`;
    message += `- Win Rate: ${escapeHtml(pnl.winRate)}\n`;
    message += `- Avg Win: ${escapeHtml(pnl.avgWin)}\n`;
    message += `- Avg Loss: ${escapeHtml(pnl.avgLoss)}\n`;
    message += `- Open Positions: ${pnl.openPositions}\n`;

    return this.sendMessage({ text: message, parse_mode: "HTML" });
  }

  async notifyError(error: string, context?: string): Promise<boolean> {
    let message = `ERROR\n\n`;
    if (context) {
      message += `Context: ${escapeHtml(truncate(context, 1000))}\n`;
    }
    message += `Error: <code>${escapeHtml(truncate(error, 2500))}</code>`;

    return this.sendMessage({ text: message, parse_mode: "HTML" });
  }

  async notifyBotStatus(status: "started" | "stopped" | "reset"): Promise<boolean> {
    const message = `Bot ${status.toUpperCase()}\n\nMode: ${tradingMode}`;

    return this.sendMessage({ text: message });
  }

  async testConnection(): Promise<boolean> {
    return this.sendMessage({ text: "FinalBot is online and ready to trade!" });
  }
}

export const telegram = new TelegramService();

export function formatPnlForTelegram(pnlLamports: number): string {
  const pnlSol = pnlLamports / 1e9;
  const sign = pnlSol >= 0 ? "+" : "";
  return `${sign}${pnlSol.toFixed(4)} SOL`;
}
