import { startBot, stopBot, printPnlReport } from "./botService.js";
import { registerKeyboardShortcuts, restoreTerminal } from "./keyboard.js";
import { startServer } from "./server.js";
import { startSolPriceUpdater } from "./solPrice.js";

let shuttingDown = false;

async function shutdown(signal: string): Promise<void> {
  if (shuttingDown) {
    console.log("\nForce exit.");
    process.exit(1);
  }
  shuttingDown = true;
  console.log(`\nReceived ${signal}, shutting down...`);
  stopBot();
  await printPnlReport();
  restoreTerminal();
  setTimeout(() => process.exit(0), 250);
}

process.on("SIGINT", () => void shutdown("SIGINT"));
process.on("SIGTERM", () => void shutdown("SIGTERM"));

registerKeyboardShortcuts(() => void shutdown("keyboard"));

const controlApiEnabled = (process.env.BOT_CONTROL_API_ENABLED ?? "false").trim().toLowerCase();
if (controlApiEnabled === "true" || controlApiEnabled === "1" || controlApiEnabled === "yes") {
  startServer({ optional: true });
}

startSolPriceUpdater();

startBot().catch((err) => {
  console.error(err);
  process.exit(1);
});
