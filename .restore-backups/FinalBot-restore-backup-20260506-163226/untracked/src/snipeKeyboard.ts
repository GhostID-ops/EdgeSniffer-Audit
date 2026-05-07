import { LAMPORTS_PER_SOL } from "@solana/web3.js";
import { getOpenSnipes, getSessionPnl } from "./snipePositions.js";
import { SnipeBot } from "./snipeBot.js";

type Shortcut = {
  key: string;
  label: string;
  action: () => void;
  confirm?: boolean;
};

export interface SnipeKeyboardRuntime {
  bot: SnipeBot;
  onQuit: () => void;
  isPaused: () => boolean;
  setPaused: (paused: boolean) => void;
  printRuntimeStatus: () => void;
}

const CONFIRM_WINDOW_MS = 2_000;

function singleKeyboardKey(raw: string): string | null {
  if (raw === "\u0003") return raw;
  if (raw.startsWith("\u001b")) return null;
  const chars = Array.from(raw);
  if (chars.length !== 1) return null;
  const key = chars[0]!;
  if (key < " " || key > "~") return null;
  return key;
}

function printHelp(shortcuts: Shortcut[]): void {
  console.log("");
  console.log("Keyboard shortcuts:");
  for (const s of shortcuts) {
    console.log(`   ${s.key.padEnd(10)} ${s.label}`);
  }
  console.log("");
}

function formatSol(lamports: number): string {
  return (lamports / LAMPORTS_PER_SOL).toFixed(4);
}

function printPositions(): void {
  const open = getOpenSnipes().sort((a, b) => a.boughtAt - b.boughtAt);
  console.log("");
  if (open.length === 0) {
    console.log("Open snipes: none");
    console.log("");
    return;
  }
  console.log(`Open snipes (${open.length}):`);
  open.forEach((pos, index) => {
    const ageMin = Math.round((Date.now() - pos.boughtAt) / 60000);
    const tag = pos.simulated ? "[SIM] " : "";
    console.log(
      `   ${index + 1}. ${tag}${pos.mint} | spent ${formatSol(pos.spentLamports)} SOL | ${ageMin}m ago`
    );
  });
  console.log("");
}

function printSessionPnl(): void {
  const pnl = getSessionPnl();
  const winRate =
    pnl.closedCount > 0
      ? `${((pnl.wins / pnl.closedCount) * 100).toFixed(1)}%`
      : "n/a";
  console.log("");
  console.log("Session PnL:");
  console.log(`   Closed:   ${pnl.closedCount} (${pnl.wins}W / ${pnl.losses}L, ${winRate})`);
  console.log(`   Open:     ${pnl.openCount}`);
  console.log(`   Spent:    ${formatSol(pnl.totalSpentLamports)} SOL`);
  console.log(`   Received: ${formatSol(pnl.totalReceivedLamports)} SOL`);
  console.log(`   Realized: ${formatSol(pnl.realizedPnlLamports)} SOL`);
  console.log("");
}

function sellPositionByIndex(bot: SnipeBot, index: number): void {
  const open = getOpenSnipes().sort((a, b) => a.boughtAt - b.boughtAt);
  const pos = open[index - 1];
  if (!pos) {
    console.log(`[snipe] no open position #${index}. Press 'l' to list positions.`);
    return;
  }
  console.log(`[snipe] selling position #${index}: ${pos.mint}`);
  void bot.closePosition(pos.mint, "MANUAL");
}

export function registerSnipeKeyboardShortcuts(runtime: SnipeKeyboardRuntime): void {
  if (!process.stdin.isTTY) {
    console.warn("Keyboard shortcuts disabled: stdin is not an interactive terminal.");
    return;
  }

  const shortcuts: Shortcut[] = [
    {
      key: "a",
      label: "Sell ALL open positions 100%",
      action: () => void runtime.bot.sellAllOpenPositions(),
      confirm: true,
    },
    { key: "1-9", label: "Sell open position by list number", action: () => undefined, confirm: true },
    {
      key: "p",
      label: "Pause/resume new buys",
      action: () => {
        const next = !runtime.isPaused();
        runtime.setPaused(next);
        console.log(`[snipe] new buys ${next ? "PAUSED" : "RESUMED"}.`);
      },
    },
    { key: "l", label: "List open positions", action: printPositions },
    { key: "s", label: "Print runtime status", action: runtime.printRuntimeStatus },
    { key: "r", label: "Print session PnL", action: printSessionPnl },
    { key: "t", label: "Send Telegram status message", action: () => void runtime.bot.sendStatus() },
    { key: "h or ?", label: "Show this help", action: () => printHelp(shortcuts) },
    { key: "q / Ctrl+C", label: "Quit cleanly", action: runtime.onQuit },
  ];

  process.stdin.setRawMode(true);
  process.stdin.resume();
  process.stdin.setEncoding("utf8");

  printHelp(shortcuts);

  const actions = new Map<string, Shortcut>();
  for (const shortcut of shortcuts) {
    if (shortcut.key === "1-9") {
      actions.set("1-9", shortcut);
      continue;
    }
    const keys = shortcut.key.split(" or ");
    for (const key of keys) {
      if (key.length === 1) actions.set(key, shortcut);
    }
  }

  let pendingConfirmation: { key: string; expiresAt: number } | null = null;

  process.stdin.on("data", (raw: string) => {
    const normalized = singleKeyboardKey(raw);
    if (!normalized) return;

    const key = normalized.toLowerCase();
    if (key === "\u0003" || key === "q") {
      runtime.onQuit();
      return;
    }

    const digit = /^[1-9]$/.test(key) ? parseInt(key, 10) : null;
    const shortcut = digit ? actions.get("1-9") : actions.get(key);
    if (!shortcut) return;

    if (shortcut.confirm) {
      const confirmKey = digit ? `sell-${digit}` : key;
      const now = Date.now();
      if (
        pendingConfirmation?.key === confirmKey &&
        pendingConfirmation.expiresAt >= now
      ) {
        pendingConfirmation = null;
        if (digit) sellPositionByIndex(runtime.bot, digit);
        else shortcut.action();
        return;
      }
      pendingConfirmation = {
        key: confirmKey,
        expiresAt: now + CONFIRM_WINDOW_MS,
      };
      const label = digit ? `Sell open position #${digit} 100%` : shortcut.label;
      console.log(
        `Press '${key}' again within ${CONFIRM_WINDOW_MS / 1000}s to confirm: ${label}`
      );
      return;
    }

    pendingConfirmation = null;
    shortcut.action();
  });
}

export function restoreSnipeTerminal(): void {
  if (process.stdin.isTTY) {
    try {
      process.stdin.setRawMode(false);
      process.stdin.pause();
    } catch {
      // best-effort
    }
  }
}
