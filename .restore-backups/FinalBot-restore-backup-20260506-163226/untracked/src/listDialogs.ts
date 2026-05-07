import "dotenv/config";
import { TelegramClient } from "telegram";
import { StringSession } from "telegram/sessions/index.js";

function readEnvNumber(name: string): number {
  const raw = process.env[name];
  if (!raw) throw new Error(`${name} is not set`);
  const n = parseInt(raw, 10);
  if (!Number.isFinite(n)) throw new Error(`${name} is not a number`);
  return n;
}

function readEnvString(name: string): string {
  const raw = process.env[name];
  if (!raw) throw new Error(`${name} is not set`);
  return raw;
}

async function main(): Promise<void> {
  const apiId = readEnvNumber("TELEGRAM_API_ID");
  const apiHash = readEnvString("TELEGRAM_API_HASH");
  const sessionString = readEnvString("TELEGRAM_USER_SESSION");

  const client = new TelegramClient(
    new StringSession(sessionString),
    apiId,
    apiHash,
    { connectionRetries: 5, useWSS: true }
  );
  await client.connect();

  const dialogs = await client.getDialogs({});
  console.log(`Found ${dialogs.length} dialogs.\n`);
  console.log(["Type".padEnd(8), "ID".padEnd(22), "Title"].join(" | "));
  console.log("-".repeat(80));

  for (const d of dialogs) {
    const entity = d.entity;
    if (!entity) continue;
    const className = entity.className ?? "?";
    const id = entity.id?.toString() ?? "?";
    const title =
      "title" in entity && typeof entity.title === "string"
        ? entity.title
        : "username" in entity && typeof entity.username === "string"
        ? `@${entity.username}`
        : "(unnamed)";
    console.log([className.padEnd(8), id.padEnd(22), title].join(" | "));
  }

  console.log(
    "\nFor channels/megagroups, prefix the ID with -100 in .env (e.g. id 1234567890 → -1001234567890)."
  );

  await client.disconnect();
  process.exit(0);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
