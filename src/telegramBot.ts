import { existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID } from "./config.js";
import { getPositions, getTotals } from "./pnl.js";
import {
  areBuysPaused,
  formatPnl,
  getBotState,
  getSessionWalletPnlSnapshot,
  isBuyOncePerTokenEnabled,
  isMirrorSellsEnabled,
  isShowMarketCapEnabled,
  recentSwaps,
  resetTradingState,
  sellAllPositions,
  sellPositionByIndex,
  setBotSimulationMode,
  setBuysPaused,
  stopBot,
  toggleBuyOncePerToken,
  toggleMirrorSells,
  toggleShowMarketCap,
} from "./botService.js";
import { formatMarketCap, formatSolAmount, formatTokenAmount } from "./tokenRegistry.js";
import { getSOLReserveAmount } from "./solReserve.js";

interface TelegramUpdate {
  update_id: number;
  message?: TelegramMessageUpdate;
  callback_query?: TelegramCallbackQuery;
}

interface TelegramMessageUpdate {
  message_id: number;
  chat: { id: number; type: string };
  text?: string;
  date: number;
}

interface TelegramCallbackQuery {
  id: string;
  message?: {
    message_id: number;
    chat: { id: number; type: string };
  };
  data?: string;
}

type InlineKeyboardButton = {
  text: string;
  callback_data: string;
};

type InlineKeyboardMarkup = {
  inline_keyboard: InlineKeyboardButton[][];
};

type SendOptions = {
  reply_markup?: InlineKeyboardMarkup;
};

const POLL_TIMEOUT_SECONDS = 25;
const POLL_RETRY_MS = 5_000;
const MESSAGE_LIMIT = 3_900;
const POLLING_LOCK_FILE = resolve("data", "telegram-polling.lock");

function escapeHtml(value: unknown): string {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

function shortAddress(value: string): string {
  return `${value.slice(0, 6)}...${value.slice(-6)}`;
}

function commandName(text: string): string {
  return text.trim().split(/\s+/, 1)[0]!.split("@", 1)[0]!.toLowerCase();
}

class TelegramBotHandler {
  private readonly botToken: string;
  private readonly chatId: string;
  private enabled: boolean;
  private offset = 0;
  private pollingStarted = false;
  private lockAcquired = false;

  constructor() {
    this.botToken = TELEGRAM_BOT_TOKEN || "";
    this.chatId = TELEGRAM_CHAT_ID || "";
    this.enabled = Boolean(this.botToken && this.chatId);

    if (!this.enabled) {
      console.log("Telegram command handlers disabled (missing bot token or chat ID).");
      return;
    }

    if (!this.acquirePollingLock()) {
      this.enabled = false;
      return;
    }

    console.log("Telegram command handlers enabled.");
    void this.startPolling();
  }

  private acquirePollingLock(): boolean {
    mkdirSync(dirname(POLLING_LOCK_FILE), { recursive: true });

    if (existsSync(POLLING_LOCK_FILE)) {
      const ownerPid = Number(readFileSync(POLLING_LOCK_FILE, "utf8").trim());
      if (Number.isInteger(ownerPid) && ownerPid > 0 && ownerPid !== process.pid) {
        try {
          process.kill(ownerPid, 0);
          console.warn(
            `Telegram command handlers disabled; process ${ownerPid} already owns polling.`
          );
          return false;
        } catch {
          // Stale lock from a crashed process; replace it below.
        }
      }
    }

    writeFileSync(POLLING_LOCK_FILE, String(process.pid));
    this.lockAcquired = true;
    process.once("exit", () => this.releasePollingLock());
    return true;
  }

  private releasePollingLock(): void {
    if (!this.lockAcquired) return;
    try {
      if (existsSync(POLLING_LOCK_FILE)) {
        const ownerPid = Number(readFileSync(POLLING_LOCK_FILE, "utf8").trim());
        if (ownerPid === process.pid) rmSync(POLLING_LOCK_FILE);
      }
    } catch {
      // Best-effort cleanup only.
    }
  }

  private async startPolling(): Promise<void> {
    if (this.pollingStarted) return;
    this.pollingStarted = true;
    try {
      await this.configureBotMenu();
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      console.warn(`Telegram menu setup failed: ${msg}`);
    }

    while (this.enabled) {
      try {
        const updates = await this.getUpdates();
        for (const update of updates) {
          this.offset = update.update_id + 1;
          await this.handleUpdate(update);
        }
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        console.warn(`Telegram polling error: ${msg}`);
        await this.sleep(POLL_RETRY_MS);
      }
    }
  }

  private async getUpdates(): Promise<TelegramUpdate[]> {
    const params = new URLSearchParams({
      offset: String(this.offset),
      timeout: String(POLL_TIMEOUT_SECONDS),
      allowed_updates: JSON.stringify(["message", "callback_query"]),
    });
    const response = await fetch(
      `https://api.telegram.org/bot${this.botToken}/getUpdates?${params.toString()}`,
      { signal: AbortSignal.timeout((POLL_TIMEOUT_SECONDS + 5) * 1_000) }
    );

    const data = (await response.json()) as {
      ok?: boolean;
      result?: TelegramUpdate[];
      description?: string;
    };

    if (!data.ok) {
      throw new Error(data.description ?? "getUpdates failed");
    }

    return data.result ?? [];
  }

  private async configureBotMenu(): Promise<void> {
    const commands = [
      { command: "menu", description: "Open the control menu" },
      { command: "positions", description: "Show positions and close buttons" },
      { command: "trades", description: "Show recent transactions" },
      { command: "sell", description: "Close a position by number" },
      { command: "sellall", description: "Sell every open position" },
      { command: "panic", description: "🛑 Stop the bot and sell everything" },
      { command: "pause", description: "⏸️ Pause new buys (still manages positions)" },
      { command: "resume", description: "▶️ Resume new buys" },
      { command: "status", description: "Show bot status" },
      { command: "settings", description: "Show runtime settings" },
      { command: "mode", description: "Switch simulation/live mode" },
    ];

    await Promise.all([
      this.telegramApi("setMyCommands", { commands }),
      this.telegramApi("setChatMenuButton", {
        chat_id: Number(this.chatId),
        menu_button: { type: "commands" },
      }),
    ]);
  }

  private async handleUpdate(update: TelegramUpdate): Promise<void> {
    if (update.message) {
      const chatId = update.message.chat.id.toString();
      if (chatId !== this.chatId) return;
      const text = update.message.text?.trim();
      if (!text) return;
      await this.handleMessage(update.message.chat.id, text);
      return;
    }

    if (update.callback_query) {
      const chatId = update.callback_query.message?.chat.id;
      if (chatId == null || chatId.toString() !== this.chatId) return;
      await this.answerCallbackQuery(update.callback_query.id);
      await this.handleCallback(chatId, update.callback_query.data ?? "");
    }
  }

  private async handleMessage(chatId: number, text: string): Promise<void> {
    if (!text.startsWith("/")) return;

    const cmd = commandName(text);
    const parts = text.trim().split(/\s+/);

    switch (cmd) {
      case "/start":
      case "/menu":
        await this.sendMainMenu(chatId);
        return;
      case "/help":
        await this.sendHelp(chatId);
        return;
      case "/positions":
      case "/portfolio":
        await this.sendPositions(chatId);
        return;
      case "/trades":
        await this.sendRecentTrades(chatId);
        return;
      case "/pnl":
        await this.sendPnlReport(chatId);
        return;
      case "/status":
        await this.sendBotStatus(chatId);
        return;
      case "/settings":
        await this.sendSettings(chatId);
        return;
      case "/mode":
        await this.sendModeMenu(chatId);
        return;
      case "/sellall":
        await this.sendSellAllConfirmation(chatId);
        return;
      case "/panic":
        await this.sendPanicConfirmation(chatId);
        return;
      case "/pause":
        await this.setBuysPausedAndRefresh(chatId, true);
        return;
      case "/resume":
        await this.setBuysPausedAndRefresh(chatId, false);
        return;
      case "/sell": {
        const index = Number.parseInt(parts[1] ?? "", 10);
        if (!Number.isInteger(index) || index < 1) {
          await this.sendMessage(chatId, "Usage: <code>/sell 1</code>\n\nUse /positions to see position numbers.");
          return;
        }
        await this.sendSellPositionConfirmation(chatId, index);
        return;
      }
      case "/reset":
        await this.sendResetConfirmation(chatId);
        return;
      default:
        await this.sendMessage(chatId, "Unknown command. Use /help to see available commands.");
    }
  }

  private async handleCallback(chatId: number, data: string): Promise<void> {
    if (data.startsWith("sell_pos:")) {
      await this.sendSellPositionConfirmation(chatId, Number(data.slice("sell_pos:".length)));
      return;
    }

    if (data.startsWith("confirm_sell_pos:")) {
      await this.executeSellPosition(chatId, Number(data.slice("confirm_sell_pos:".length)));
      return;
    }

    switch (data) {
      case "menu":
        await this.sendMainMenu(chatId);
        return;
      case "positions":
        await this.sendPositions(chatId);
        return;
      case "trades":
        await this.sendRecentTrades(chatId);
        return;
      case "pnl":
        await this.sendPnlReport(chatId);
        return;
      case "status":
        await this.sendBotStatus(chatId);
        return;
      case "settings":
        await this.sendSettings(chatId);
        return;
      case "mode":
        await this.sendModeMenu(chatId);
        return;
      case "set_mode_sim":
        await this.executeSetMode(chatId, true);
        return;
      case "confirm_set_mode_live":
        await this.sendLiveModeConfirmation(chatId);
        return;
      case "set_mode_live":
        await this.executeSetMode(chatId, false);
        return;
      case "sell_all":
        await this.sendSellAllConfirmation(chatId);
        return;
      case "confirm_sell_all":
        await this.executeSellAll(chatId);
        return;
      case "panic":
        await this.sendPanicConfirmation(chatId);
        return;
      case "confirm_panic":
        await this.executePanic(chatId);
        return;
      case "pause_buys":
        await this.setBuysPausedAndRefresh(chatId, true);
        return;
      case "resume_buys":
        await this.setBuysPausedAndRefresh(chatId, false);
        return;
      case "reset":
        await this.sendResetConfirmation(chatId);
        return;
      case "confirm_reset":
        await this.executeReset(chatId);
        return;
      case "toggle_mirror_sells":
        toggleMirrorSells();
        await this.sendSettings(chatId);
        return;
      case "toggle_buy_once":
        toggleBuyOncePerToken();
        await this.sendSettings(chatId);
        return;
      case "toggle_market_cap":
        toggleShowMarketCap();
        await this.sendSettings(chatId);
        return;
      default:
        await this.sendMessage(chatId, "That action is no longer available. Use /menu.");
    }
  }

  private menuStatusLine(): string {
    const state = getBotState();
    const open = getPositions().filter((p) => p.heldRaw > 0).length;
    const bal = state.lastBalanceSol == null ? "?" : `${state.lastBalanceSol.toFixed(3)} SOL`;
    const walletPnl = getSessionWalletPnlSnapshot();
    const pnl = walletPnl.deltaLamports != null ? formatPnl(walletPnl.deltaLamports) : "n/a";
    const mode = state.simulationMode ? "SIM" : "LIVE";
    const entries = state.buysPaused ? "⏸️ buys PAUSED" : state.running ? "▶️ buying" : "⏹️ stopped";
    return `💰 ${bal} · 📊 ${open} open · PnL ${escapeHtml(pnl)} · ${mode} · ${entries}`;
  }

  private async sendMainMenu(chatId: number): Promise<void> {
    const state = getBotState();
    const pauseToggle = state.buysPaused
      ? { text: "▶️ Resume buys", callback_data: "resume_buys" }
      : { text: "⏸️ Pause buys", callback_data: "pause_buys" };
    await this.sendMessage(
      chatId,
      ["<b>FinalBot Controls</b>", "", this.menuStatusLine()].join("\n"),
      {
        reply_markup: {
          inline_keyboard: [
            [
              { text: "📊 Positions", callback_data: "positions" },
              { text: "🧾 Trades", callback_data: "trades" },
            ],
            [
              { text: "💹 PnL", callback_data: "pnl" },
              { text: "ℹ️ Status", callback_data: "status" },
            ],
            [pauseToggle, { text: "💸 Sell All", callback_data: "sell_all" }],
            [{ text: "🛑 PANIC — stop & sell all", callback_data: "panic" }],
            [
              { text: "⚙️ Settings", callback_data: "settings" },
              { text: "🔀 Mode", callback_data: "mode" },
            ],
            [{ text: "🔄 Refresh", callback_data: "menu" }],
          ],
        },
      }
    );
  }

  private async sendPositions(chatId: number): Promise<void> {
    const open = getPositions().filter((p) => p.heldRaw > 0);
    if (open.length === 0) {
      await this.sendMessage(chatId, "<b>Positions</b>\n\nNo open positions.");
      return;
    }

    const lines = [`<b>Positions (${open.length} open)</b>`, ""];
    const keyboard: InlineKeyboardButton[][] = [];

    open.slice(0, 20).forEach((position, i) => {
      const n = i + 1;
      const held = formatTokenAmount(position.heldRaw, position.decimals);
      const spent = formatSolAmount(position.totalSolSpentLamports);
      const pnl = formatPnl(position.realizedPnlLamports);

      lines.push(`<b>#${n} $${escapeHtml(position.symbol)}</b>`);
      lines.push(`Held: ${escapeHtml(held)}`);
      lines.push(`Spent: ${spent} SOL`);
      lines.push(`Realized PnL: ${escapeHtml(pnl)}`);
      lines.push(`CA: <code>${escapeHtml(position.mint)}</code>`);
      lines.push("");

      keyboard.push([
        {
          text: `Close #${n} $${position.symbol}`.slice(0, 60),
          callback_data: `sell_pos:${n}`,
        },
      ]);
    });

    keyboard.push([{ text: "Sell All", callback_data: "sell_all" }]);
    keyboard.push([{ text: "Back", callback_data: "menu" }]);

    if (open.length > 20) {
      lines.push(`Showing first 20 positions. ${open.length - 20} more are open.`);
    }

    await this.sendMessage(chatId, lines.join("\n"), {
      reply_markup: { inline_keyboard: keyboard },
    });
  }

  private async sendRecentTrades(chatId: number): Promise<void> {
    const trades = recentSwaps.slice(0, 10);
    if (trades.length === 0) {
      await this.sendMessage(chatId, "<b>Recent Trades</b>\n\nNo trades this session.");
      return;
    }

    const lines = ["<b>Recent Trades</b>", ""];
    for (const trade of trades) {
      const tag = trade.isSimulated ? "SIM" : "LIVE";
      const time = new Date(trade.ts).toLocaleTimeString();
      lines.push(`<b>${trade.side.toUpperCase()} ${tag}</b> - ${escapeHtml(time)}`);
      lines.push(`${escapeHtml(trade.input.display)} -> ${escapeHtml(trade.output.display)}`);
      if (trade.marketCapUsd != null) {
        const mcTime = trade.marketCapCheckedAt
          ? new Date(trade.marketCapCheckedAt).toLocaleString()
          : "unknown time";
        lines.push(`MC: ${escapeHtml(formatMarketCap(trade.marketCapUsd))} at ${escapeHtml(mcTime)}`);
      }
      lines.push(`Wallet: <code>${escapeHtml(shortAddress(trade.wallet))}</code>`);
      if (trade.error) lines.push(`Error: <code>${escapeHtml(trade.error)}</code>`);
      lines.push("");
    }

    await this.sendMessage(chatId, lines.join("\n"));
  }

  private async sendPnlReport(chatId: number): Promise<void> {
    const totals = getTotals();
    const positions = getPositions();
    const walletPnl = getSessionWalletPnlSnapshot();
    const lines = [
      "<b>PnL Report</b>",
      "",
      `Trades: ${totals.tradesCount} (${totals.buysCount} buys, ${totals.sellsCount} sells)`,
      `SOL spent: ${formatSolAmount(totals.totalSolSpentLamports)} SOL`,
      `SOL received: ${formatSolAmount(totals.totalSolReceivedLamports)} SOL`,
      `Realized PnL: ${escapeHtml(formatPnl(totals.realizedPnlLamports))}`,
      walletPnl.deltaLamports != null
        ? `Wallet SOL delta: ${escapeHtml(formatPnl(walletPnl.deltaLamports))}`
        : "Wallet SOL delta: unavailable",
      `Open positions: ${totals.openPositions}`,
      "",
    ];

    const top = [...positions]
      .sort((a, b) => Math.abs(b.realizedPnlLamports) - Math.abs(a.realizedPnlLamports))
      .slice(0, 8);

    if (top.length > 0) {
      lines.push("<b>Top Positions</b>");
      for (const p of top) {
        lines.push(`$${escapeHtml(p.symbol)}: ${escapeHtml(formatPnl(p.realizedPnlLamports))}`);
      }
    }

    await this.sendMessage(chatId, lines.join("\n"));
  }

  private async sendBotStatus(chatId: number): Promise<void> {
    const state = getBotState();
    const lines = [
      "<b>Bot Status</b>",
      "",
      `Running: ${state.running ? "yes" : "no"}`,
      `Buys: ${areBuysPaused() ? "⏸️ PAUSED" : "active"}`,
      `Mode: ${state.simulationMode ? "SIMULATION" : "LIVE"}`,
      `Wallet: ${state.ourWallet ? `<code>${escapeHtml(shortAddress(state.ourWallet))}</code>` : "not loaded"}`,
      `Tracked wallets: ${state.trackedWallets.length}`,
      `Balance: ${state.lastBalanceSol == null ? "unknown" : `${state.lastBalanceSol.toFixed(4)} SOL`}`,
      `Min buy: ${state.minBuySol} SOL`,
      `Max buy: ${state.maxBuySol} SOL`,
      `SOL reserve: ${getSOLReserveAmount().toFixed(3)} SOL`,
      `Poll interval: ${state.pollIntervalMs} ms`,
      `Risk exits: ${state.riskControlsEnabled ? "ON" : "OFF"}`,
      `Stop loss: ${state.stopLossPct == null ? "off" : `${state.stopLossPct}%`}`,
      `Take profit: ${state.takeProfitPct == null ? "off" : `${state.takeProfitPct}%`}`,
      `Risk check interval: ${state.riskCheckIntervalMs} ms`,
      `Mirror sells: ${isMirrorSellsEnabled() ? "ON" : "OFF"}`,
      `Buy once/token: ${isBuyOncePerTokenEnabled() ? "ON" : "OFF"}`,
      `Market cap display: ${isShowMarketCapEnabled() ? "ON" : "OFF"}`,
    ];

    await this.sendMessage(chatId, lines.join("\n"), {
      reply_markup: {
        inline_keyboard: [
          [
            { text: "Refresh", callback_data: "status" },
            { text: "Settings", callback_data: "settings" },
          ],
          [{ text: "Back", callback_data: "menu" }],
        ],
      },
    });
  }

  private async sendSettings(chatId: number): Promise<void> {
    const state = getBotState();
    await this.sendMessage(
      chatId,
      [
        "<b>Runtime Settings</b>",
        "",
        `Mode: ${state.simulationMode ? "SIMULATION" : "LIVE"}`,
        `Mirror sells: ${isMirrorSellsEnabled() ? "ON" : "OFF"}`,
        `Buy once/token: ${isBuyOncePerTokenEnabled() ? "ON" : "OFF"}`,
        `Market cap display: ${isShowMarketCapEnabled() ? "ON" : "OFF"}`,
        `Risk exits: ${state.riskControlsEnabled ? "ON" : "OFF"} (set STOP_LOSS_PCT / TAKE_PROFIT_PCT in .env)`,
        "",
        "These toggles affect the current running session. Persistent defaults still come from .env.",
      ].join("\n"),
      {
        reply_markup: {
          inline_keyboard: [
            [{ text: "Change Mode", callback_data: "mode" }],
            [{ text: "Toggle Mirror Sells", callback_data: "toggle_mirror_sells" }],
            [{ text: "Toggle Buy Once", callback_data: "toggle_buy_once" }],
            [{ text: "Toggle Market Cap", callback_data: "toggle_market_cap" }],
            [{ text: "Back", callback_data: "menu" }],
          ],
        },
      }
    );
  }

  private async sendModeMenu(chatId: number): Promise<void> {
    const state = getBotState();
    await this.sendMessage(
      chatId,
      [
        "<b>Trading Mode</b>",
        "",
        `Current mode: <b>${state.simulationMode ? "SIMULATION" : "LIVE"}</b>`,
        `Open positions: ${state.positions.filter((p) => p.heldRaw > 0).length}`,
        "",
        "Mode changes are blocked while positions are open.",
      ].join("\n"),
      {
        reply_markup: {
          inline_keyboard: [
            [
              { text: state.simulationMode ? "Simulation Active" : "Switch to Simulation", callback_data: "set_mode_sim" },
              { text: state.simulationMode ? "Switch to Live" : "Live Active", callback_data: "confirm_set_mode_live" },
            ],
            [{ text: "Back", callback_data: "menu" }],
          ],
        },
      }
    );
  }

  private async sendLiveModeConfirmation(chatId: number): Promise<void> {
    const state = getBotState();
    if (!state.simulationMode) {
      await this.sendModeMenu(chatId);
      return;
    }

    await this.sendMessage(
      chatId,
      [
        "<b>Confirm LIVE Mode</b>",
        "",
        "LIVE mode sends real transactions from your configured wallet.",
        `Open positions: ${state.positions.filter((p) => p.heldRaw > 0).length}`,
      ].join("\n"),
      {
        reply_markup: {
          inline_keyboard: [
            [
              { text: "Confirm LIVE", callback_data: "set_mode_live" },
              { text: "Cancel", callback_data: "mode" },
            ],
          ],
        },
      }
    );
  }

  private async executeSetMode(chatId: number, simulationEnabled: boolean): Promise<void> {
    const result = setBotSimulationMode(simulationEnabled);
    await this.sendMessage(chatId, escapeHtml(result.message));
    await this.sendModeMenu(chatId);
  }

  private async sendSellPositionConfirmation(chatId: number, index: number): Promise<void> {
    const open = getPositions().filter((p) => p.heldRaw > 0);
    const position = open[index - 1];
    if (!position) {
      await this.sendMessage(chatId, `No open position #${index}. Use /positions to refresh.`);
      return;
    }

    await this.sendMessage(
      chatId,
      [
        "<b>Confirm Position Close</b>",
        "",
        `Position: #${index} $${escapeHtml(position.symbol)}`,
        `Held: ${escapeHtml(formatTokenAmount(position.heldRaw, position.decimals))}`,
        `CA: <code>${escapeHtml(position.mint)}</code>`,
        "",
        "This will market-sell this position through Jupiter.",
      ].join("\n"),
      {
        reply_markup: {
          inline_keyboard: [
            [
              { text: `Confirm Close #${index}`, callback_data: `confirm_sell_pos:${index}` },
              { text: "Cancel", callback_data: "positions" },
            ],
          ],
        },
      }
    );
  }

  private async sendSellAllConfirmation(chatId: number): Promise<void> {
    const open = getPositions().filter((p) => p.heldRaw > 0);
    if (open.length === 0) {
      await this.sendMessage(chatId, "<b>Sell All</b>\n\nNo open positions to sell.");
      return;
    }

    await this.sendMessage(
      chatId,
      [
        "<b>Confirm Sell All</b>",
        "",
        `Open positions: ${open.length}`,
        "This will attempt to market-sell every open position through Jupiter.",
      ].join("\n"),
      {
        reply_markup: {
          inline_keyboard: [
            [
              { text: "Confirm Sell All", callback_data: "confirm_sell_all" },
              { text: "Cancel", callback_data: "positions" },
            ],
          ],
        },
      }
    );
  }

  private async sendPanicConfirmation(chatId: number): Promise<void> {
    const open = getPositions().filter((p) => p.heldRaw > 0).length;
    await this.sendMessage(
      chatId,
      [
        "🛑 <b>Confirm PANIC</b>",
        "",
        this.menuStatusLine(),
        "",
        "This emergency kill-switch will immediately:",
        "• <b>stop the bot</b> (halt every new buy)",
        `• <b>market-sell all ${open} open position(s)</b>`,
        "",
        "You'll need to restart the bot afterwards to trade again.",
      ].join("\n"),
      {
        reply_markup: {
          inline_keyboard: [
            [{ text: "🛑 YES — STOP & SELL ALL", callback_data: "confirm_panic" }],
            [{ text: "Cancel", callback_data: "menu" }],
          ],
        },
      }
    );
  }

  private async executePanic(chatId: number): Promise<void> {
    await this.sendMessage(chatId, "🛑 <b>PANIC</b> — stopping bot and selling everything...");
    try {
      setBuysPaused(true);
      stopBot();
      await sellAllPositions();
      await this.sendMessage(
        chatId,
        "🛑 PANIC done — bot stopped and sell-all requested.\n\nVerify with /positions, and restart the bot when you want to trade again."
      );
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      await this.sendMessage(
        chatId,
        `⚠️ PANIC hit an error: <code>${escapeHtml(msg)}</code>\n\nThe bot is stopped. Check /positions — some sells may need a manual retry.`
      );
    }
  }

  private async setBuysPausedAndRefresh(chatId: number, paused: boolean): Promise<void> {
    setBuysPaused(paused);
    await this.sendMessage(
      chatId,
      paused
        ? "⏸️ Buys <b>paused</b> — no new entries. Open positions are still managed and sold."
        : "▶️ Buys <b>resumed</b> — new entries re-enabled."
    );
    await this.sendMainMenu(chatId);
  }

  private async sendResetConfirmation(chatId: number): Promise<void> {
    await this.sendMessage(
      chatId,
      [
        "<b>Confirm Reset</b>",
        "",
        "This clears in-memory positions, recent trades, and processed signatures for this session.",
      ].join("\n"),
      {
        reply_markup: {
          inline_keyboard: [
            [
              { text: "Confirm Reset", callback_data: "confirm_reset" },
              { text: "Cancel", callback_data: "menu" },
            ],
          ],
        },
      }
    );
  }

  private async executeSellPosition(chatId: number, index: number): Promise<void> {
    await this.sendMessage(chatId, `Closing position #${index}...`);
    try {
      await sellPositionByIndex(index);
      await this.sendMessage(chatId, `Close request for position #${index} finished. Use /positions to refresh.`);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      await this.sendMessage(chatId, `Close failed: <code>${escapeHtml(msg)}</code>`);
    }
  }

  private async executeSellAll(chatId: number): Promise<void> {
    await this.sendMessage(chatId, "Selling all open positions...");
    try {
      await sellAllPositions();
      await this.sendMessage(chatId, "Sell-all request finished. Use /positions to refresh.");
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      await this.sendMessage(chatId, `Sell all failed: <code>${escapeHtml(msg)}</code>`);
    }
  }

  private async executeReset(chatId: number): Promise<void> {
    resetTradingState();
    await this.sendMessage(chatId, "Session trading state reset.");
  }

  private async sendHelp(chatId: number): Promise<void> {
    await this.sendMessage(
      chatId,
      [
        "<b>Commands</b>",
        "",
        "/menu - Show control menu",
        "/positions - Show open positions with close buttons",
        "/sell 1 - Close position #1",
        "/sellall - Confirm sell all positions",
        "🛑 /panic - Stop the bot and sell everything",
        "⏸️ /pause - Pause new buys (positions still managed)",
        "▶️ /resume - Resume new buys",
        "/trades - Recent copied/manual trades",
        "/pnl - PnL report",
        "/status - Bot status",
        "/settings - Runtime toggles",
        "/mode - Switch simulation/live mode",
        "/reset - Confirm session reset",
      ].join("\n")
    );
  }

  private async sendMessage(chatId: number, text: string, options?: SendOptions): Promise<void> {
    const chunks = this.chunkMessage(text);
    for (let i = 0; i < chunks.length; i++) {
      const body = {
        chat_id: chatId,
        text: chunks[i],
        parse_mode: "HTML",
        disable_web_page_preview: true,
        ...(i === chunks.length - 1 ? options : {}),
      };

      const response = await fetch(`https://api.telegram.org/bot${this.botToken}/sendMessage`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
        signal: AbortSignal.timeout(8_000),
      });

      if (!response.ok) {
        const error = await response.text();
        console.warn(`Telegram send failed: ${response.status} ${error}`);
      }
    }
  }

  private async answerCallbackQuery(callbackId: string): Promise<void> {
    try {
      await this.telegramApi("answerCallbackQuery", { callback_query_id: callbackId }, 5_000);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      console.warn(`Telegram callback ack failed: ${msg}`);
    }
  }

  private async telegramApi(method: string, body: unknown, timeoutMs = 8_000): Promise<void> {
    const response = await fetch(`https://api.telegram.org/bot${this.botToken}/${method}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(timeoutMs),
    });

    if (!response.ok) {
      const error = await response.text();
      console.warn(`Telegram ${method} failed: ${response.status} ${error}`);
    }
  }

  private chunkMessage(text: string): string[] {
    if (text.length <= MESSAGE_LIMIT) return [text];

    const chunks: string[] = [];
    let current = "";
    for (const line of text.split("\n")) {
      if ((current + "\n" + line).length > MESSAGE_LIMIT) {
        chunks.push(current);
        current = line;
      } else {
        current = current ? `${current}\n${line}` : line;
      }
    }
    if (current) chunks.push(current);
    return chunks;
  }

  private sleep(ms: number): Promise<void> {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }
}

export const telegramBot = new TelegramBotHandler();
