import { installConsolePatch } from "./logBuffer.js";
installConsolePatch();

import { startServer } from "./server.js";
import { stopBot, printPnlReport } from "./botService.js";
import { registerKeyboardShortcuts, restoreTerminal } from "./keyboard.js";

let shuttingDown = false;

async function shutdown(signal: string): Promise<void> {
  if (shuttingDown) {
    console.log("\nForce exit.");
    process.exit(1);
  }
  shuttingDown = true;
  console.log(`\nReceived ${signal}, shutting down...`);
  stopBot();
  await printPnlReport({
    settlePendingFastBuysMs: 30_000,
    settlementReason: `${signal} shutdown`,
  });
  restoreTerminal();
  setTimeout(() => process.exit(0), 250);
}

process.on("SIGINT", () => void shutdown("SIGINT"));
process.on("SIGTERM", () => void shutdown("SIGTERM"));

registerKeyboardShortcuts(() => void shutdown("keyboard"));

startServer();
