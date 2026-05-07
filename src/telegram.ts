import { TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, simulationMode } from "./config.js";

export interface TelegramMessage {
  text: string;
  parse_mode?: "HTML" | "Markdown";
}

function escapeHtml(value: unknown): string {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

export interface TradeNotification {
  type: "buy" | "sell";
  symbol: string;
  amount: string;
  solAmount: string;
  marketCap?: string;
  marketCapCheckedAt?: number;
  isSimulated: boolean;
  wallet: string;
  tokenAddress: string;
  pnl?: string;
  note?: string;
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
  private enabled: boolean;

  constructor() {
    this.botToken = TELEGRAM_BOT_TOKEN || "";
    this.chatId = TELEGRAM_CHAT_ID || "";
    this.enabled = !!(this.botToken && this.chatId);
    
    if (!this.enabled) {
      console.log("📱 Telegram notifications disabled (missing bot token or chat ID)");
    } else {
      console.log("📱 Telegram notifications enabled");
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
        console.warn(`📱 Telegram send failed: ${response.status} ${error}`);
        return false;
      }

      return true;
    } catch (err) {
      console.warn(`📱 Telegram error:`, err instanceof Error ? err.message : String(err));
      return false;
    }
  }

  async notifyTrade(trade: TradeNotification): Promise<boolean> {
    const emoji = trade.isSimulated ? "🎭" : "💰";
    const typeEmoji = trade.type === "buy" ? "🟢" : "🔴";
    const simTag = trade.isSimulated ? " (SIM)" : "";

    let message = `${emoji} ${typeEmoji} ${trade.type.toUpperCase()}${simTag}\n\n`;
    message += `💎 Token: <code>${trade.symbol}</code>\n`;
    message += `💰 Amount: ${trade.amount}\n`;
    message += `🪙 SOL: ${trade.solAmount}\n`;
    
    if (trade.marketCap) {
      message += `📊 MC: ${trade.marketCap}\n`;
      if (trade.marketCapCheckedAt) {
        message += `MC time: ${new Date(trade.marketCapCheckedAt).toLocaleString()}\n`;
      }
    }
    
    message += `👤 Wallet: <code>${trade.wallet}</code>\n`;
    message += `🔗 CA: <code>${trade.tokenAddress}</code>\n`;
    
    if (trade.pnl) {
      message += `📈 PnL: ${trade.pnl}\n`;
    }
    
    if (trade.note) {
      message += `Note: ${escapeHtml(trade.note)}\n`;
    }

    if (trade.txSignature) {
      message += `📝 TX: <code>${trade.txSignature}</code>\n`;
    }

    return this.sendMessage({ text: message, parse_mode: "HTML" });
  }

  async notifyPnlReport(pnl: PnlNotification): Promise<boolean> {
    console.warn("Telegram PnL notification suppressed; use /pnl to request it.");
    return false;
    const totalPnlValue = parseFloat(pnl.totalPnl.replace(/[^0-9.-]/g, ""));
    const pnlColor = totalPnlValue >= 0 ? "🟢" : "🔴";

    let message = `${pnlColor} 📊 PnL REPORT\n\n`;
    message += `📈 Total PnL: <b>${pnl.totalPnl}</b>\n`;
    message += `💰 Realized: ${pnl.realizedPnl}\n`;
    message += `📊 Unrealized: ${pnl.unrealizedPnl}\n\n`;
    
    message += `📊 Stats:\n`;
    message += `• Total Trades: ${pnl.totalTrades}\n`;
    message += `• Buys: ${pnl.totalBuys} | Sells: ${pnl.totalSells}\n`;
    message += `• Win Rate: ${pnl.winRate}\n`;
    message += `• Avg Win: ${pnl.avgWin}\n`;
    message += `• Avg Loss: ${pnl.avgLoss}\n`;
    message += `• Open Positions: ${pnl.openPositions}\n`;

    return this.sendMessage({ text: message, parse_mode: "HTML" });
  }

  async notifyError(error: string, context?: string): Promise<boolean> {
    const detail = context ? `${context} | ${error}` : error;
    console.warn(`Telegram error notification suppressed: ${detail}`);
    return false;
  }

  async notifyRiskExit(risk: RiskExitNotification): Promise<boolean> {
    const tag = risk.isSimulated ? " (SIM)" : "";
    let message =
      risk.status === "triggered"
        ? `ðŸš¦ RISK EXIT TRIGGERED${tag}\n\n`
        : `âš ï¸ RISK EXIT FAILED${tag}\n\n`;

    message += `Token: <code>${escapeHtml(risk.symbol)}</code>\n`;
    message += `Reason: ${escapeHtml(risk.reason)}\n`;
    if (risk.pnlPct) message += `Quoted PnL: ${escapeHtml(risk.pnlPct)}\n`;
    if (risk.quotedSol) message += `Quoted exit: ${escapeHtml(risk.quotedSol)} SOL\n`;
    if (risk.costBasisSol) message += `Cost basis: ${escapeHtml(risk.costBasisSol)} SOL\n`;
    if (risk.error) message += `Error: <code>${escapeHtml(risk.error)}</code>\n`;
    message += `CA: <code>${escapeHtml(risk.tokenAddress)}</code>\n`;

    return this.sendMessage({ text: message, parse_mode: "HTML" });
  }

  async notifyBotStatus(status: "started" | "stopped" | "reset"): Promise<boolean> {
    console.warn(`Telegram bot status notification suppressed: ${status}`);
    return false;
    const statusEmoji = {
      started: "🟢",
      stopped: "🔴", 
      reset: "🔄"
    }[status];

    const message = `${statusEmoji} Bot ${status.toUpperCase()}\n\nMode: ${simulationMode ? "SIMULATION" : "LIVE"}`;

    return this.sendMessage({ text: message });
  }

  async testConnection(): Promise<boolean> {
    if (!this.enabled) return false;

    const message = "🤖 FinalBot is online and ready to trade! 🚀";
    return this.sendMessage({ text: message });
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

