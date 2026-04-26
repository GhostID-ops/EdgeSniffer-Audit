import { TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID } from "./config.js";
import { getPositions, getTotals } from "./pnl.js";
import { getBotState, recentSwaps, formatPnl } from "./botService.js";
import { formatSolAmount, formatTokenAmount } from "./tokenRegistry.js";
import { getSOLReserveAmount } from "./solReserve.js";

interface TelegramUpdate {
  update_id: number;
  message?: {
    message_id: number;
    from: {
      id: number;
      first_name: string;
      username?: string;
    };
    chat: {
      id: number;
      type: string;
    };
    text: string;
    date: number;
  };
  callback_query?: {
    id: string;
    from: {
      id: number;
      first_name: string;
      username?: string;
    };
    message: {
      message_id: number;
      chat: {
        id: number;
        type: string;
      };
      text: string;
      date: number;
    };
    data: string;
  };
}

interface InlineKeyboardButton {
  text: string;
  callback_data: string;
}

interface InlineKeyboardMarkup {
  inline_keyboard: InlineKeyboardButton[][];
}

class TelegramBotHandler {
  private botToken: string;
  private chatId: string;
  private enabled: boolean;
  private offset: number = 0;

  constructor() {
    this.botToken = TELEGRAM_BOT_TOKEN || "";
    this.chatId = TELEGRAM_CHAT_ID || "";
    this.enabled = !!(this.botToken && this.chatId);
    
    if (!this.enabled) {
      console.log("🤖 Telegram bot handlers disabled (missing bot token or chat ID)");
    } else {
      console.log("🤖 Telegram bot handlers enabled");
      this.startPolling();
    }
  }

  private async startPolling(): Promise<void> {
    console.log("🔄 Starting Telegram bot polling...");
    
    while (this.enabled) {
      try {
        const updates = await this.getUpdates();
        
        for (const update of updates) {
          this.offset = update.update_id + 1;
          
          if (update.message && update.message.chat.id.toString() === this.chatId) {
            await this.handleMessage(update.message);
          } else if (update.callback_query && update.callback_query.message.chat.id.toString() === this.chatId) {
            await this.handleCallback(update.callback_query);
          }
        }
        
        // Wait before next poll
        await new Promise(resolve => setTimeout(resolve, 1000));
      } catch (error) {
        console.error("❌ Telegram polling error:", error);
        await new Promise(resolve => setTimeout(resolve, 5000));
      }
    }
  }

  private async getUpdates(): Promise<TelegramUpdate[]> {
    const url = `https://api.telegram.org/bot${this.botToken}/getUpdates?offset=${this.offset}&timeout=30`;
    const response = await fetch(url);
    const data = await response.json() as { ok: boolean; result: TelegramUpdate[] };
    
    return data.ok ? data.result : [];
  }

  private async handleMessage(message: any): Promise<void> {
    const text = message.text;
    
    if (text.startsWith('/')) {
      await this.handleCommand(text, message.chat.id);
    }
  }

  private async handleCallback(callback: any): Promise<void> {
    const data = callback.data;
    const chatId = callback.message.chat.id;
    
    // Acknowledge the callback
    await this.answerCallbackQuery(callback.id);
    
    switch (data) {
      case 'portfolio':
        await this.sendPortfolio(chatId);
        break;
      case 'trades':
        await this.sendRecentTrades(chatId);
        break;
      case 'pnl':
        await this.sendPnlReport(chatId);
        break;
      case 'status':
        await this.sendBotStatus(chatId);
        break;
      case 'actions':
        await this.sendQuickActions(chatId);
        break;
      case 'settings':
        await this.sendSettings(chatId);
        break;
      case 'sell_all':
        await this.sendSellAllConfirmation(chatId);
        break;
      case 'reset_positions':
        await this.sendResetConfirmation(chatId);
        break;
      case 'toggle_mode':
        await this.sendToggleMode(chatId);
        break;
      case 'view_wallets':
        await this.sendViewWallets(chatId);
        break;
      case 'confirm_sell_all':
        await this.executeSellAll(chatId);
        break;
      case 'confirm_reset':
        await this.executeReset(chatId);
        break;
      case 'confirm_toggle_mode':
        await this.executeToggleMode(chatId);
        break;
      case 'notifications':
        await this.sendNotificationSettings(chatId);
        break;
      case 'risk_settings':
        await this.sendRiskSettings(chatId);
        break;
      case 'menu':
        await this.sendMainMenu(chatId);
        break;
    }
  }

  private async handleCommand(command: string, chatId: number): Promise<void> {
    switch (command) {
      case '/start':
        await this.sendMainMenu(chatId);
        break;
      case '/menu':
        await this.sendMainMenu(chatId);
        break;
      case '/portfolio':
        await this.sendPortfolio(chatId);
        break;
      case '/trades':
        await this.sendRecentTrades(chatId);
        break;
      case '/pnl':
        await this.sendPnlReport(chatId);
        break;
      case '/status':
        await this.sendBotStatus(chatId);
        break;
      case '/help':
        await this.sendHelp(chatId);
        break;
      default:
        await this.sendMessage(chatId, "❌ Unknown command. Use /help to see available commands.");
    }
  }

  private async sendMainMenu(chatId: number): Promise<void> {
    const keyboard: InlineKeyboardMarkup = {
      inline_keyboard: [
        [
          { text: "📊 Portfolio", callback_data: "portfolio" },
          { text: "📈 Recent Trades", callback_data: "trades" }
        ],
        [
          { text: "💰 PnL Report", callback_data: "pnl" },
          { text: "🤖 Bot Status", callback_data: "status" }
        ],
        [
          { text: "🎯 Quick Actions", callback_data: "actions" },
          { text: "⚙️ Settings", callback_data: "settings" }
        ],
        [
          { text: "🔄 Refresh Menu", callback_data: "menu" }
        ]
      ]
    };

    const message = `
🤖 <b>FinalBot Menu</b>

Welcome to your copytrading bot dashboard! Choose an option below:

📊 <b>Portfolio</b> - View current positions and holdings
📈 <b>Recent Trades</b> - See latest buy/sell activity  
💰 <b>PnL Report</b> - Complete profit/loss summary
🤖 <b>Bot Status</b> - Current bot state and settings

<b>Commands:</b>
/start - Show this menu
/portfolio - View portfolio
/trades - Recent trades
/pnl - PnL report
/status - Bot status
/help - Help
    `.trim();

    await this.sendMessage(chatId, message, { reply_markup: keyboard });
  }

  private async sendPortfolio(chatId: number): Promise<void> {
    const positions = getPositions();
    const openPositions = positions.filter(p => p.heldRaw > 0);
    
    if (openPositions.length === 0) {
      await this.sendMessage(chatId, "📊 <b>Portfolio</b>\n\nNo open positions currently held.");
      return;
    }

    let message = `📊 <b>Portfolio (${openPositions.length} open)</b>\n\n`;
    
    for (const pos of openPositions.slice(0, 10)) { // Limit to 10 positions
      const heldTokens = formatTokenAmount(pos.heldRaw, pos.decimals);
      const spentSol = formatSolAmount(pos.totalSolSpentLamports);
      const avgPrice = pos.avgCostLamportsPerTokenRaw 
        ? (pos.avgCostLamportsPerTokenRaw / 1_000_000_000).toFixed(9)
        : "0";
      
      message += `💎 <b>$${pos.symbol}</b>\n`;
      message += `🪙 Held: ${heldTokens}\n`;
      message += `💰 Spent: ${spentSol} SOL\n`;
      message += `📊 Avg: ${avgPrice} SOL per token\n`;
      message += `🔗 CA: <code>${pos.mint.slice(0, 8)}…</code>\n\n`;
    }

    if (openPositions.length > 10) {
      message += `... and ${openPositions.length - 10} more positions`;
    }

    await this.sendMessage(chatId, message);
  }

  private async sendRecentTrades(chatId: number): Promise<void> {
    const recentTradeSwaps = recentSwaps.slice(-10).reverse(); // Last 10 trades
    
    if (recentTradeSwaps.length === 0) {
      await this.sendMessage(chatId, "📈 <b>Recent Trades</b>\n\nNo trades executed yet.");
      return;
    }

    let message = `📈 <b>Recent Trades (Last 10)</b>\n\n`;
    
    for (const swap of recentTradeSwaps) {
      const icon = swap.side === "buy" ? "🟢" : "🔴";
      const simTag = swap.isSimulated ? " (SIM)" : "";
      const time = new Date(swap.ts).toLocaleTimeString();
      
      message += `${icon} <b>${swap.side.toUpperCase()}${simTag}</b>\n`;
      message += `🪙 ${swap.input.display} → ${swap.output.display}\n`;
      message += `👤 ${swap.wallet.slice(0, 4)}…${swap.wallet.slice(-4)}\n`;
      message += `⏰ ${time}\n`;
      
      if (swap.error) {
        message += `❌ Error: ${swap.error}\n`;
      }
      
      message += "\n";
    }

    await this.sendMessage(chatId, message);
  }

  private async sendPnlReport(chatId: number): Promise<void> {
    const totals = getTotals();
    const positions = getPositions();
    
    let message = `💰 <b>PnL Report</b>\n\n`;
    
    // Summary
    message += `📊 <b>Summary</b>\n`;
    message += `📈 Total Trades: ${totals.tradesCount}\n`;
    message += `🟢 Buys: ${totals.buysCount} | 🔴 Sells: ${totals.sellsCount}\n`;
    message += `💸 SOL Spent: ${formatSolAmount(totals.totalSolSpentLamports)} SOL\n`;
    message += `💰 SOL Received: ${formatSolAmount(totals.totalSolReceivedLamports)} SOL\n`;
    message += `📊 Realized PnL: ${formatPnl(totals.realizedPnlLamports)}\n`;
    message += `🏷️ Open Positions: ${totals.openPositions}\n\n`;
    
    // Top performers
    const sortedPositions = [...positions].sort((a, b) => 
      Math.abs(b.realizedPnlLamports) - Math.abs(a.realizedPnlLamports)
    ).slice(0, 5);
    
    if (sortedPositions.length > 0) {
      message += `🏆 <b>Top Performers</b>\n`;
      for (const pos of sortedPositions) {
        const pnlIcon = pos.realizedPnlLamports > 0 ? "🟢" : pos.realizedPnlLamports < 0 ? "🔴" : "⚪";
        const pnl = formatPnl(pos.realizedPnlLamports);
        const pct = pos.totalSolSpentLamports > 0 
          ? `(${((pos.realizedPnlLamports / pos.totalSolSpentLamports) * 100).toFixed(1)}%)`
          : "";
        
        message += `${pnlIcon} $${pos.symbol}: ${pnl} ${pct}\n`;
      }
    }

    await this.sendMessage(chatId, message);
  }

  private async sendBotStatus(chatId: number): Promise<void> {
    const botState = getBotState();
    
    let message = `🤖 <b>Bot Status</b>\n\n`;
    
    message += `🟢 <b>Status:</b> ${botState.running ? "Running" : "Stopped"}\n`;
    message += `🎭 <b>Mode:</b> ${botState.simulationMode ? "Simulation" : "Live"}\n`;
    
    if (botState.ourWallet) {
      message += `👛 <b>Wallet:</b> <code>${botState.ourWallet.slice(0, 8)}…${botState.ourWallet.slice(-8)}</code>\n`;
    }
    
    message += `👥 <b>Tracking:</b> ${botState.trackedWallets.length} wallets\n`;
    message += `💰 <b>Min Buy:</b> ${botState.minBuySol} SOL\n`;
    message += `⛽ <b>SOL Reserve:</b> ${getSOLReserveAmount().toFixed(3)} SOL\n`;
    
    if (botState.startedAt) {
      const uptime = Date.now() - botState.startedAt;
      const hours = Math.floor(uptime / 3600000);
      const minutes = Math.floor((uptime % 3600000) / 60000);
      message += `⏱️ <b>Uptime:</b> ${hours}h ${minutes}m\n`;
    }

    await this.sendMessage(chatId, message);
  }

  private async sendQuickActions(chatId: number): Promise<void> {
    const keyboard: InlineKeyboardMarkup = {
      inline_keyboard: [
        [
          { text: "💸 Sell All Positions", callback_data: "sell_all" },
          { text: "🔄 Reset All Data", callback_data: "reset_positions" }
        ],
        [
          { text: "🎭 Toggle Sim/Live", callback_data: "toggle_mode" },
          { text: "👥 View Tracked Wallets", callback_data: "view_wallets" }
        ],
        [
          { text: "🔙 Back to Menu", callback_data: "menu" }
        ]
      ]
    };

    const message = `
🎯 <b>Quick Actions</b>

Execute common bot operations instantly:

💸 <b>Sell All Positions</b>
• Sell all open positions immediately
• Works in both simulation and live mode

🔄 <b>Reset All Data</b>
• Clear all positions and trade history
• Reset PnL tracking
• ⚠️ This action cannot be undone

🎭 <b>Toggle Sim/Live Mode</b>
• Switch between simulation and live trading
• Current mode affects all future trades

👥 <b>View Tracked Wallets</b>
• See all wallets being tracked
• View wallet addresses and status
    `.trim();

    await this.sendMessage(chatId, message, { reply_markup: keyboard });
  }

  private async sendSettings(chatId: number): Promise<void> {
    const keyboard: InlineKeyboardMarkup = {
      inline_keyboard: [
        [
          { text: "🎭 Trading Mode", callback_data: "toggle_mode" },
          { text: "👥 Wallet Settings", callback_data: "view_wallets" }
        ],
        [
          { text: "📊 Notification Settings", callback_data: "notifications" },
          { text: "⚠️ Risk Settings", callback_data: "risk_settings" }
        ],
        [
          { text: "🔙 Back to Menu", callback_data: "menu" }
        ]
      ]
    };

    const message = `
⚙️ <b>Bot Settings</b>

Configure your copytrading bot settings:

🎭 <b>Trading Mode</b>
• Switch between Simulation and Live trading
• Simulation: Test without real money
• Live: Real trades with actual SOL

👥 <b>Wallet Settings</b>
• Manage tracked wallets
• View wallet addresses
• Add/remove tracked wallets

📊 <b>Notification Settings</b>
• Configure Telegram notifications
• Trade alerts and PnL updates
• Error notifications

⚠️ <b>Risk Settings</b>
• Set buy limits and caps
• Configure position sizing
• Risk management parameters
    `.trim();

    await this.sendMessage(chatId, message, { reply_markup: keyboard });
  }

  private async sendSellAllConfirmation(chatId: number): Promise<void> {
    const keyboard: InlineKeyboardMarkup = {
      inline_keyboard: [
        [
          { text: "✅ Yes, Sell All", callback_data: "confirm_sell_all" },
          { text: "❌ Cancel", callback_data: "actions" }
        ]
      ]
    };

    const positions = getPositions();
    const openPositions = positions.filter(p => p.heldRaw > 0);
    
    let message = `⚠️ <b>Sell All Positions Confirmation</b>\n\n`;
    
    if (openPositions.length === 0) {
      message += `No open positions to sell.\n\nPress "❌ Cancel" to return.`;
    } else {
      message += `You have <b>${openPositions.length}</b> open positions:\n\n`;
      
      for (const pos of openPositions.slice(0, 5)) {
        const heldTokens = formatTokenAmount(pos.heldRaw, pos.decimals);
        message += `• $${pos.symbol}: ${heldTokens}\n`;
      }
      
      if (openPositions.length > 5) {
        message += `• ... and ${openPositions.length - 5} more\n`;
      }
      
      message += `\n<b>⚠️ This will sell ALL positions immediately!</b>`;
      message += `\nAre you sure you want to continue?`;
    }

    await this.sendMessage(chatId, message, { reply_markup: keyboard });
  }

  private async sendResetConfirmation(chatId: number): Promise<void> {
    const keyboard: InlineKeyboardMarkup = {
      inline_keyboard: [
        [
          { text: "✅ Yes, Reset All", callback_data: "confirm_reset" },
          { text: "❌ Cancel", callback_data: "actions" }
        ]
      ]
    };

    const message = `
⚠️ <b>Reset All Data Confirmation</b>

This will permanently delete:
• All position data
• Complete trade history  
• PnL tracking
• Processed signatures cache

<b>⚠️ This action CANNOT be undone!</b>

Are you absolutely sure you want to reset everything?
    `.trim();

    await this.sendMessage(chatId, message, { reply_markup: keyboard });
  }

  private async sendToggleMode(chatId: number): Promise<void> {
    const botState = getBotState();
    const currentMode = botState.simulationMode ? "Simulation" : "Live";
    const newMode = botState.simulationMode ? "Live" : "Simulation";
    
    const keyboard: InlineKeyboardMarkup = {
      inline_keyboard: [
        [
          { text: "✅ Confirm Switch", callback_data: "confirm_toggle_mode" },
          { text: "❌ Cancel", callback_data: "settings" }
        ]
      ]
    };

    const message = `
🎭 <b>Toggle Trading Mode</b>

Current mode: <b>${currentMode}</b>
New mode: <b>${newMode}</b>

${botState.simulationMode 
  ? "🟢 <b>Live Mode:</b> Real trades with actual SOL"
  : "🎭 <b>Simulation Mode:</b> Test trades without real money"
}

Are you sure you want to switch modes?
    `.trim();

    await this.sendMessage(chatId, message, { reply_markup: keyboard });
  }

  private async sendViewWallets(chatId: number): Promise<void> {
    const botState = getBotState();
    
    let message = `👥 <b>Tracked Wallets (${botState.trackedWallets.length})</b>\n\n`;
    
    if (botState.trackedWallets.length === 0) {
      message += "No wallets are currently being tracked.";
    } else {
      for (let i = 0; i < botState.trackedWallets.length; i++) {
        const wallet = botState.trackedWallets[i];
        const shortWallet = `${wallet.slice(0, 4)}…${wallet.slice(-4)}`;
        message += `${i + 1}. <code>${shortWallet}</code>\n`;
        message += `   Full: <code>${wallet}</code>\n\n`;
      }
    }

    if (botState.ourWallet) {
      message += `\n👛 <b>Our Wallet:</b>\n`;
      message += `<code>${botState.ourWallet}</code>`;
    }

    const keyboard: InlineKeyboardMarkup = {
      inline_keyboard: [
        [
          { text: "🔙 Back to Settings", callback_data: "settings" }
        ]
      ]
    };

    await this.sendMessage(chatId, message, { reply_markup: keyboard });
  }

  private async executeSellAll(chatId: number): Promise<void> {
    // Import the sellAllPositions function
    const { sellAllPositions } = await import("./botService.js");
    
    try {
      await sellAllPositions();
      await this.sendMessage(chatId, "✅ <b>Sell All Executed</b>\n\nAll positions have been sold successfully!");
    } catch (error) {
      await this.sendMessage(chatId, `❌ <b>Sell All Failed</b>\n\nError: ${error}`);
    }
  }

  private async executeReset(chatId: number): Promise<void> {
    // Import the resetTradingState function
    const { resetTradingState } = await import("./botService.js");
    
    try {
      resetTradingState();
      await this.sendMessage(chatId, "✅ <b>Reset Complete</b>\n\nAll data has been cleared successfully!");
    } catch (error) {
      await this.sendMessage(chatId, `❌ <b>Reset Failed</b>\n\nError: ${error}`);
    }
  }

  private async executeToggleMode(chatId: number): Promise<void> {
    // Note: This would require implementing a toggle function in botService
    // For now, just show a message that this feature is coming soon
    const botState = getBotState();
    const currentMode = botState.simulationMode ? "Simulation" : "Live";
    
    await this.sendMessage(chatId, 
      `🔄 <b>Mode Toggle</b>\n\n` +
      `Currently in <b>${currentMode}</b> mode.\n\n` +
      `⚠️ Mode toggle feature coming soon!\n\n` +
      `For now, you can change modes by editing the .env file and restarting the bot.`
    );
  }

  private async sendNotificationSettings(chatId: number): Promise<void> {
    const keyboard: InlineKeyboardMarkup = {
      inline_keyboard: [
        [
          { text: "🔙 Back to Settings", callback_data: "settings" }
        ]
      ]
    };

    const message = `
📊 <b>Notification Settings</b>

Configure your Telegram notifications:

🔔 <b>Trade Notifications</b>
• Buy/sell alerts ✅ Enabled
• PnL updates ✅ Enabled
• Error alerts ✅ Enabled

📊 <b>Report Notifications</b>
• PnL reports ✅ Enabled
• Bot status ✅ Enabled

⚠️ <b>Note:</b> Notification settings are currently managed through code configuration.
Advanced notification controls coming soon!
    `.trim();

    await this.sendMessage(chatId, message, { reply_markup: keyboard });
  }

  private async sendRiskSettings(chatId: number): Promise<void> {
    const botState = getBotState();
    
    const keyboard: InlineKeyboardMarkup = {
      inline_keyboard: [
        [
          { text: "🔙 Back to Settings", callback_data: "settings" }
        ]
      ]
    };

    const message = `
⚠️ <b>Risk Settings</b>

Current risk management settings:

💰 <b>Buy Limits</b>
• Min Buy: ${botState.minBuySol} SOL
• Max Buy: ${botState.maxBuySol || 'Not set'} SOL

📊 <b>Position Management</b>
• Position sizing: Automatic
• Stop loss: Not implemented
• Take profit: Not implemented

⚠️ <b>Note:</b> Risk settings are currently managed through .env configuration.
Advanced risk controls coming soon!
    `.trim();

    await this.sendMessage(chatId, message, { reply_markup: keyboard });
  }

  private async sendHelp(chatId: number): Promise<void> {
    const message = `
❓ <b>Help - FinalBot Commands</b>

<b>Menu Commands:</b>
/start - Show main menu
/menu - Show main menu

<b>Information Commands:</b>
/portfolio - View current positions
/trades - Show recent trades
/pnl - View PnL report
/status - Bot status

<b>How it works:</b>
• Bot automatically copies trades from tracked wallets
• All notifications are sent to this chat
• Use simulation mode to test without real trades
• Press buttons in the main menu for quick access

<b>Need help?</b>
Check your bot configuration and ensure it's running properly.
    `.trim();

    await this.sendMessage(chatId, message);
  }

  private async sendMessage(
    chatId: number, 
    text: string, 
    options?: { reply_markup?: InlineKeyboardMarkup }
  ): Promise<void> {
    try {
      const url = `https://api.telegram.org/bot${this.botToken}/sendMessage`;
      const body = {
        chat_id: chatId,
        text: text,
        parse_mode: "HTML",
        ...options
      };

      await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body)
      });
    } catch (error) {
      console.error("❌ Failed to send Telegram message:", error);
    }
  }

  private async answerCallbackQuery(callbackId: string): Promise<void> {
    try {
      const url = `https://api.telegram.org/bot${this.botToken}/answerCallbackQuery`;
      await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ callback_query_id: callbackId })
      });
    } catch (error) {
      console.error("❌ Failed to answer callback query:", error);
    }
  }
}

// Start the bot handler
export const telegramBot = new TelegramBotHandler();
