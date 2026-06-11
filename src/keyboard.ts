import {
  printPnlReport,
  resetTradingState,
  closeAllOpenPositionsSimulated,
  getBotState,
  sellAllPositions,
  sellAllWalletDust,
  sellPositionByIndex,
  sellPositionPercentByIndex,
  toggleMirrorSells,
  isMirrorSellsEnabled,
  toggleBuyOncePerToken,
  isBuyOncePerTokenEnabled,
  toggleShowMarketCap,
  isShowMarketCapEnabled,
} from "./botService.js";

type Shortcut = { key: string; label: string; action: () => void | Promise<void> };

function runShortcut(action: () => void | Promise<void>): void {
  void Promise.resolve()
    .then(action)
    .catch((err) => {
      const msg = err instanceof Error ? err.message : String(err);
      console.warn(`Shortcut failed: ${msg}`);
    });
}

function printHelp(shortcuts: Shortcut[]): void {
  console.log("");
  console.log("⌨️  Shortcuts:");
  for (const s of shortcuts) {
    console.log(`   ${s.key.padEnd(6)} ${s.label}`);
  }
  console.log("");
}

function printStatus(): void {
  const s = getBotState();
  console.log("");
  console.log("📡 Status:");
  console.log(`   Running: ${s.running ? "yes" : "no"}`);
  console.log(`   Mode:    ${s.simulationMode ? "SIMULATION" : "LIVE"}`);
  console.log(`   Wallet:  ${s.ourWallet ?? "—"}`);
  console.log(`   Tracked: ${s.trackedWallets.length} wallet(s)`);
  console.log(`   Balance: ${s.lastBalanceSol?.toFixed(4) ?? "—"} SOL`);
  console.log(`   Trades:  ${s.totals.tradesCount} (${s.totals.buysCount} buys, ${s.totals.sellsCount} sells)`);
  console.log(`   Mirror sells: ${isMirrorSellsEnabled() ? "ON" : "OFF"}`);
  console.log(`   Buy-once-per-token: ${isBuyOncePerTokenEnabled() ? "ON" : "OFF"}`);
  console.log(`   Show market cap: ${isShowMarketCapEnabled() ? "ON" : "OFF"}`);
  console.log("");
}

export function registerKeyboardShortcuts(onQuit: () => void): void {
  if (!process.stdin.isTTY) {
    return; // not a terminal (piped or detached) — skip silently
  }

  const shortcuts: Shortcut[] = [
    { key: "1", label: "Sell Trade #1 position", action: () => runShortcut(() => sellPositionByIndex(1)) },
    { key: "2", label: "Sell Trade #2 position", action: () => runShortcut(() => sellPositionByIndex(2)) },
    { key: "3", label: "Sell Trade #3 position", action: () => runShortcut(() => sellPositionByIndex(3)) },
    { key: "!", label: "Sell 50% of Trade #1 position", action: () => runShortcut(() => sellPositionPercentByIndex(1, 0.5)) },
    { key: "@", label: "Sell 50% of Trade #2 position", action: () => runShortcut(() => sellPositionPercentByIndex(2, 0.5)) },
    { key: "#", label: "Sell 50% of Trade #3 position", action: () => runShortcut(() => sellPositionPercentByIndex(3, 0.5)) },
    { key: "a", label: "Sell ALL open positions (sim/live)", action: () => runShortcut(sellAllPositions) },
    { key: "d", label: "Sell ALL wallet token dust/balances (live)", action: () => runShortcut(sellAllWalletDust) },
    { key: "e", label: "Close all open positions (sim) at market & report", action: () => runShortcut(closeAllOpenPositionsSimulated) },
    { key: "r", label: "Print PnL report", action: () => runShortcut(printPnlReport) },
    { key: "x", label: "Clear all positions & history", action: resetTradingState },
    { key: "s", label: "Show bot status", action: printStatus },
    { key: "m", label: "Toggle mirror-sells ON/OFF", action: toggleMirrorSells },
    { key: "b", label: "Toggle buy-once-per-token ON/OFF", action: toggleBuyOncePerToken },
    { key: "c", label: "Toggle market-cap display ON/OFF", action: toggleShowMarketCap },
    { key: "h or ?", label: "Show this help", action: () => printHelp(shortcuts) },
    { key: "q / Ctrl+C", label: "Quit (with PnL report)", action: onQuit },
  ];

  process.stdin.setRawMode(true);
  process.stdin.resume();
  process.stdin.setEncoding("utf8");

  printHelp(shortcuts);

  process.stdin.on("data", (raw: string) => {
    const key = raw.toLowerCase();
    // Ctrl+C is \u0003 in raw mode (doesn't fire SIGINT).
    if (key === "\u0003" || key === "q") {
      onQuit();
      return;
    }
    if (key === "1") runShortcut(() => sellPositionByIndex(1));
    else if (key === "2") runShortcut(() => sellPositionByIndex(2));
    else if (key === "3") runShortcut(() => sellPositionByIndex(3));
    else if (key === "!") runShortcut(() => sellPositionPercentByIndex(1, 0.5));
    else if (key === "@") runShortcut(() => sellPositionPercentByIndex(2, 0.5));
    else if (key === "#") runShortcut(() => sellPositionPercentByIndex(3, 0.5));
    else if (key === "a") runShortcut(sellAllPositions);
    else if (key === "d") runShortcut(sellAllWalletDust);
    else if (key === "e") runShortcut(closeAllOpenPositionsSimulated);
    else if (key === "r") runShortcut(printPnlReport);
    else if (key === "x") resetTradingState();
    else if (key === "s") printStatus();
    else if (key === "m") toggleMirrorSells();
    else if (key === "b") toggleBuyOncePerToken();
    else if (key === "c") toggleShowMarketCap();
    else if (key === "h" || key === "?") printHelp(shortcuts);
  });
}

/** Restore terminal to cooked mode before exit. */
export function restoreTerminal(): void {
  if (process.stdin.isTTY) {
    try {
      process.stdin.setRawMode(false);
      process.stdin.pause();
    } catch {
      // best-effort
    }
  }
}

