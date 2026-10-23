/**
 * One-command launcher for the Obsidian Labs demo console (/friend).
 *
 *   npm run friend
 *
 * Pre-sets the control API flag and a default web port, then hands off to the
 * normal web server entry (src/web.ts) so shutdown handling is reused.
 */
process.env.BOT_CONTROL_API_ENABLED = process.env.BOT_CONTROL_API_ENABLED ?? "true";
if (!process.env.WEB_PORT || !process.env.WEB_PORT.trim()) {
  process.env.WEB_PORT = "3000";
}

const port = process.env.WEB_PORT;
console.log("Obsidian Labs - demo console");
console.log(`  -> open http://localhost:${port}/friend`);
console.log("  (Ctrl+C to stop)\n");

await import("./web.js");
