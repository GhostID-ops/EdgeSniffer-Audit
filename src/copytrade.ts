import { startBot, stopBot, printPnlReport } from "./botService.js";
import { registerKeyboardShortcuts, restoreTerminal } from "./keyboard.js";

let shuttingDown = false;

function shutdown(signal: string): void {
  if (shuttingDown) {
    console.log("\nForce exit.");
    process.exit(1);
  }
  shuttingDown = true;
  console.log(`\nReceived ${signal}, shutting down...`);
  stopBot();
  printPnlReport();
  restoreTerminal();
  setTimeout(() => process.exit(0), 250);
}

process.on("SIGINT", () => shutdown("SIGINT"));
process.on("SIGTERM", () => shutdown("SIGTERM"));

registerKeyboardShortcuts(() => shutdown("keyboard"));

startBot().catch((err) => {
  console.error(err);
  process.exit(1);
});
